"""
Inspect or print the current export of a StickerPix project.

Default: offline preview. --send submits one physical print-and-cut job.
Use --copies to print several identical sheets with one upload.
Load sticker media before sending.
"""

import json
import math
import os
import shlex
import socket
import sys
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..arguments import ArgumentParser
from ..configuration import load_printer
from ..jobs import monitor, submit
from ..paths import PRINTER_CONFIG, ROOT
from ..progress import ProgressDisplay, duration
from ..projects import command_argument, resolve
from ..sheets import PrintSheet
from ..simulation import PlaybackClock, simulate
from ..transport import Client, resolve_rfcomm_channel
from ..ui import command_hints, console, fields, heading, notice
from .logging import EventLog


def _parse_args(argv: list[str] | None) -> Namespace:
    """Parse and validate print or monitor command options."""
    parser = ArgumentParser(prog="crycut print", description=__doc__)
    parser.add_argument(
        "job", nargs="?", help="Project name in projects/, or a project directory"
    )
    parser.add_argument(
        "--json", action="store_true", help="Structured offline project inspection"
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--send", action="store_true", help="Submit a physical print-and-cut job"
    )
    action.add_argument(
        "--simulate",
        action="store_true",
        help="Preview live printing without a printer",
    )
    parser.add_argument(
        "--speed",
        type=float,
        help="Simulation speed (default: 10; use 1 for real time)",
    )
    action.add_argument(
        "--monitor",
        type=int,
        metavar="JOB_ID",
        help="Monitor an existing job; no submission",
    )
    parser.add_argument(
        "--copies", type=int, help="Number of identical sheets (default: 1)"
    )
    parser.add_argument(
        "--channel", type=int, help="Override RFCOMM channel (default: service lookup)"
    )
    parser.add_argument(
        "--timeout", type=float, default=10, help="Per-request timeout in seconds"
    )
    parser.add_argument(
        "--interval", type=float, default=2, help="Poll interval in seconds"
    )
    parser.add_argument(
        "--max-wait",
        type=float,
        help="Maximum monitoring duration for the whole job (default: 900 seconds per copy)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="JSONL event log; defaults to logs/print-TIMESTAMP.jsonl",
    )
    args = parser.parse_args(argv)
    if args.speed is not None:
        if not args.simulate:
            parser.error("--speed requires --simulate")
        if not math.isfinite(args.speed) or args.speed <= 0:
            parser.error("Simulation speed must be finite and positive")
    else:
        args.speed = 10
    if args.simulate and (
        args.json
        or args.log is not None
        or args.channel is not None
        or args.max_wait is not None
    ):
        parser.error(
            "--simulate cannot be used with --json, --log, --channel, or --max-wait"
        )
    if args.copies is not None:
        if args.copies < 1:
            parser.error("Copies must be a positive integer")
        if args.monitor is not None:
            parser.error(
                "--copies selects a print quantity; it cannot be used with --monitor"
            )
    else:
        args.copies = 1
    if args.json and (args.send or args.monitor is not None):
        parser.error(
            "--json is for offline inspection; physical runs write JSONL logs automatically"
        )
    if args.monitor is not None:
        if args.job:
            parser.error("Monitoring takes a printer job ID, not a project")
    elif not args.job:
        parser.error("Select a project name/directory (see: crycut projects)")
    if any(
        v is not None and (not math.isfinite(v) or v <= 0)
        for v in (args.timeout, args.interval, args.max_wait)
    ):
        parser.error("Timeouts and polling interval must be finite and positive")
    if (args.channel is not None and not 1 <= args.channel <= 30) or (
        args.monitor is not None and args.monitor <= 0
    ):
        parser.error("Use RFCOMM channel 1-30 and a positive job ID")
    return args


def _show_preview(
    directory: Path,
    sheet: PrintSheet,
    address: str | None,
    channel: int | None,
    as_json: bool,
    copies: int = 1,
) -> None:
    """Display a validated export without opening a printer connection."""
    if as_json:
        print(
            json.dumps(
                {
                    "address": address,
                    "project": str(directory.resolve()),
                    "preview": str((directory / "preview.png").resolve()),
                    "rfcomm_channel": channel or "auto (SPP service lookup)",
                    **sheet.preview(copies),
                },
                indent=2,
            )
        )
        return
    out = console()
    heading("Print preview", out=out)
    summary = sheet.preview(copies)
    rows = [
        ("Sheet", summary.get("name", directory.name)),
        ("Image", summary["image"]),
        ("Cuts", f"{sheet.cut_count} outlines"),
        ("Copies", copies),
    ]
    seconds = summary["estimated_execution_seconds"]
    rows.append(("Estimate", f"~{duration(seconds)} per sheet"))
    out.print(fields(rows))
    out.print()
    paths = [("Project", os.path.relpath(directory.resolve()))]
    if (directory / "preview.png").is_file():
        paths.append(
            ("Preview", os.path.relpath((directory / "preview.png").resolve()))
        )
    out.print(fields(paths))
    out.print()
    command = f"uv run crycut print {shlex.quote(command_argument(directory))} --send"
    if channel is not None:
        command += f" --channel {channel}"
    hints = [("Print", command if copies == 1 else f"{command} --copies {copies}")]
    if copies == 1:
        hints.append(("Print 3 copies", f"{command} --copies 3"))
    command_hints(hints, out=out)
    out.print()


def main(argv: list[str] | None = None) -> int:
    """Inspect, submit, or monitor a sheet and preserve its event log."""
    args = _parse_args(argv)
    client: Client | None = None
    log: EventLog | None = None
    job_id = args.monitor
    submitting = False
    connecting = False
    playback = PlaybackClock(args.speed) if args.simulate else None
    display = (
        ProgressDisplay(sys.stdout, clock=playback)
        if playback is not None
        else ProgressDisplay(sys.stdout)
    )
    out = display.console
    try:
        directory = None
        if args.monitor is not None:
            sheet = None
        else:
            directory = resolve(args.job)
            sheet = PrintSheet.load(directory)
            display.cut_total = sheet.cut_count
        if args.simulate:
            assert sheet is not None and playback is not None
            heading(f"Print & cut (simulation, {args.speed:g}x)", out=out)
            display.set_summary(
                [
                    ("Printer", "StickerPix (simulated)"),
                    ("Sheet", sheet.name),
                    ("Cuts", f"{sheet.cut_count} outlines"),
                    ("Estimate", f"~{duration(sheet.estimate_seconds)} per sheet"),
                ],
                copies=args.copies,
            )

            def simulated_event(event: str, **details: Any) -> None:
                display.show(event, details)

            simulate(
                sheet,
                simulated_event,
                clock=playback,
                copies=args.copies,
                interval=args.interval,
            )
            return 0
        if not args.send and args.monitor is None:
            assert directory is not None and sheet is not None
            address = (
                load_printer(PRINTER_CONFIG).address
                if PRINTER_CONFIG.exists()
                else None
            )
            _show_preview(
                directory, sheet, address, args.channel, args.json, args.copies
            )
            return 0
        config = load_printer(PRINTER_CONFIG)
        address = config.address
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise ValueError("Use /usr/bin/python3 for Bluetooth socket support")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = args.log or ROOT / f"logs/print-{stamp}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        log = EventLog(path)
        heading("Print & cut" if sheet is not None else "Monitor print", out=out)
        rows = [("Printer", config.name)]
        if sheet is not None:
            assert directory is not None
            summary = sheet.preview(args.copies)
            timing = summary["timing_estimate"]
            rows.extend(
                [
                    ("Sheet", sheet.name),
                    ("Cuts", f"{sheet.cut_count} outlines"),
                    ("Estimate", f"~{duration(timing['total_seconds'])} per sheet"),
                ]
            )
            log.write(
                {
                    "event": "selected_sheet",
                    "directory": str(directory.resolve()),
                    **summary,
                }
            )
        else:
            rows.append(("Job", args.monitor))
        display.set_summary(
            rows,
            copies=args.copies if sheet is not None else None,
            log_path=os.path.relpath(path.resolve()),
        )

        def emit(event: str, **details: Any) -> None:
            nonlocal job_id, submitting
            if event == "accepted":
                job_id = details["job_id"]
            if event == "submitting":
                submitting = True
            log.write(
                {"time": datetime.now(UTC).isoformat(), "event": event, **details}
            )
            display.show(event, details)

        connecting = True
        emit("connecting", address=address)
        channel = resolve_rfcomm_channel(address, args.channel, args.timeout)
        with socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
        ) as connection:
            connection.settimeout(args.timeout)
            connection.connect((address, channel))
            client = Client(connection, args.timeout)
            client.handshake()
            emit("connected", address=address, rfcomm_channel=channel)
            connecting = False
            if args.monitor:
                result = monitor(
                    client,
                    args.monitor,
                    emit,
                    interval=args.interval,
                    max_wait=900 if args.max_wait is None else args.max_wait,
                )
            else:
                assert sheet is not None
                result = submit(
                    client,
                    sheet,
                    emit,
                    copies=args.copies,
                    interval=args.interval,
                    max_wait=args.max_wait,
                )
        return 0 if result.get("job-state") == 9 else 1
    except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        connection_failed = connecting and not isinstance(error, KeyboardInterrupt)
        if connection_failed:
            display.show("connection_failed", {})
        display.finish()
        detail = (
            ("Simulation interrupted" if args.simulate else "Monitoring interrupted")
            if isinstance(error, KeyboardInterrupt)
            else str(error)
        )
        error_out = console(sys.stderr)
        error_out.print()
        if connection_failed and isinstance(error, OSError):
            notice(
                "Cannot connect to the printer. Check power and Bluetooth.",
                "error",
                out=error_out,
            )
        else:
            notice(
                f"Stopped: {detail}",
                "warning" if isinstance(error, KeyboardInterrupt) else "error",
                out=error_out,
            )
        if job_id is not None:
            recovery = f"uv run crycut monitor {job_id}"
            if args.channel is not None:
                recovery += f" --channel {args.channel}"
            notice(
                f"Job {job_id} was not cancelled and may still be running. "
                f"Check it with {recovery}.",
                "warning",
                out=console(sys.stderr),
            )
        elif submitting:
            notice(
                "Submission outcome is unknown; check the printer before retrying.",
                "warning",
                out=console(sys.stderr),
            )
        if log:
            log.write({"event": "stopped", "detail": detail, "job_id": job_id})
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    finally:
        display.finish()
        if args.simulate:
            out.print()
        if log:
            log.close()
            print(flush=True)
        if client and log:
            log.save_protocol(client.events)
