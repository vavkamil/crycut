"""
Inspect or print the current export of a StickerPix project.

Default: offline preview. --send submits one physical print-and-cut job.
Load sticker media before sending.
"""

import json
import math
import os
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
from ..projects import resolve
from ..sheets import PrintSheet
from ..transport import Client, resolve_rfcomm_channel
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
    action.add_argument("--send", action="store_true", help="Submit one physical sheet")
    action.add_argument(
        "--monitor",
        type=int,
        metavar="JOB_ID",
        help="Monitor an existing job; no submission",
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
        default=900,
        help="Maximum monitoring duration in seconds",
    )
    parser.add_argument(
        "--log",
        type=Path,
        help="JSONL event log; defaults to logs/print-TIMESTAMP.jsonl",
    )
    args = parser.parse_args(argv)
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
        not math.isfinite(v) or v <= 0
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
                    **sheet.preview(),
                },
                indent=2,
            )
        )
        return
    print("\nOffline preview\n")
    summary = sheet.preview()
    rows = [
        ("Sheet", summary.get("name", directory.name)),
        ("Image", summary["image"]),
        ("Copies", 1),
        ("Cuts", f"{sheet.cut_count} outlines"),
    ]
    seconds = summary["estimated_execution_seconds"]
    rows.append(("Estimate", f"~{max(1, round(seconds / 60))} minutes after upload"))
    print("\n".join(f"{label + ':':<15}{value}" for label, value in rows))
    print(f"\n{'Project:':<15}{os.path.relpath(directory.resolve())}")
    if (directory / "preview.png").is_file():
        print(
            f"{'Preview:':<15}{os.path.relpath((directory / 'preview.png').resolve())}"
        )
    print("\nAdd --send to print this sheet.\n")


def main(argv: list[str] | None = None) -> int:
    """Inspect, submit, or monitor a sheet and preserve its event log."""
    args = _parse_args(argv)
    client: Client | None = None
    log: EventLog | None = None
    job_id = args.monitor
    submitting = False
    display = ProgressDisplay(sys.stdout)
    try:
        directory = None
        if args.monitor is not None:
            sheet = None
        else:
            directory = resolve(args.job)
            sheet = PrintSheet.load(directory)
            display.cut_total = sheet.cut_count
        if not args.send and args.monitor is None:
            assert directory is not None and sheet is not None
            address = (
                load_printer(PRINTER_CONFIG).address
                if PRINTER_CONFIG.exists()
                else None
            )
            _show_preview(directory, sheet, address, args.channel, args.json)
            return 0
        config = load_printer(PRINTER_CONFIG)
        address = config.address
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise ValueError("Use /usr/bin/python3 for Bluetooth socket support")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = args.log or ROOT / f"logs/print-{stamp}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        log = EventLog(path)
        print("\nPrint & cut\n" if sheet is not None else "\nMonitor print\n")
        rows = [("Printer", config.name)]
        if sheet is not None:
            assert directory is not None
            summary = sheet.preview()
            timing = summary["timing_estimate"]
            rows.extend(
                [
                    ("Sheet", sheet.name),
                    ("Copies", 1),
                    ("Cuts", f"{sheet.cut_count} outlines"),
                    ("Estimate", f"~{duration(timing['total_seconds'])} after upload"),
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
        print("\n".join(f"{label + ':':<15}{value}" for label, value in rows))
        print(f"\n{'Log:':<15}{os.path.relpath(path.resolve())}\n", flush=True)

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
            if args.monitor:
                result = monitor(
                    client,
                    args.monitor,
                    emit,
                    interval=args.interval,
                    max_wait=args.max_wait,
                )
            else:
                assert sheet is not None
                result = submit(
                    client, sheet, emit, interval=args.interval, max_wait=args.max_wait
                )
        return 0 if result.get("job-state") == 9 else 1
    except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        display.finish()
        detail = (
            "Monitoring interrupted"
            if isinstance(error, KeyboardInterrupt)
            else str(error)
        )
        if log and client is None and isinstance(error, OSError):
            print(
                "Cannot connect to the printer. Check power and the notebook's Bluetooth.",
                file=sys.stderr,
            )
        else:
            print(f"Stopped: {detail}", file=sys.stderr)
        if job_id is not None:
            recovery = f"uv run crycut monitor {job_id}"
            if args.channel is not None:
                recovery += f" --channel {args.channel}"
            print(
                f"Job {job_id} was not cancelled and may still be running. "
                f"Check it with {recovery}.",
                file=sys.stderr,
            )
        elif submitting:
            print(
                "Submission outcome is unknown; check the printer before retrying.",
                file=sys.stderr,
            )
        if log:
            log.write({"event": "stopped", "detail": detail, "job_id": job_id})
        return 130 if isinstance(error, KeyboardInterrupt) else 1
    finally:
        display.finish()
        if log:
            log.close()
            print(flush=True)
        if client and log:
            log.save_protocol(client.events)
