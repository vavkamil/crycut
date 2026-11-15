"""Find a StickerPix printer, check its connection, and save local settings."""

import os
import re
import socket
import sys
from contextlib import suppress

from rich.text import Text

from ..arguments import ArgumentParser
from ..configuration import PrinterConfig, load_printer, save_printer
from ..discovery import DiscoveredPrinter, discover_printers
from ..paths import PRINTER_CONFIG
from ..status import read_status
from ..transport import Client, resolve_rfcomm_channel
from ..ui import busy, command_hints, console, fields, heading, listing, notice


def _select(printers: list[DiscoveredPrinter]) -> DiscoveredPrinter:
    out = console()
    table = listing("#", "Printer", "Address", right=("#",))
    for index, printer in enumerate(printers, 1):
        table.add_row(Text(str(index)), Text(printer.name), Text(printer.address))
    out.print(table)
    out.print()
    if len(printers) == 1:
        return printers[0]
    if not sys.stdin.isatty():
        raise ValueError(
            "Several printers found. Run setup --mac ADDRESS to select one."
        )
    while True:
        choice = input(f"Select printer [1-{len(printers)}], or q to cancel: ").strip()
        if choice.casefold() in ("q", "quit"):
            raise KeyboardInterrupt
        if choice.isdecimal() and 1 <= int(choice) <= len(printers):
            return printers[int(choice) - 1]
        notice("Enter a number from the list.", "warning", out=out)


def main(argv: list[str] | None = None) -> int:
    """Discover or select a printer, verify it, and save the configuration."""
    parser = ArgumentParser(prog="crycut setup", description=__doc__)
    parser.add_argument("--mac", help="Use a known address instead of scanning")
    parser.add_argument(
        "--timeout",
        type=int,
        default=12,
        help="Scan/connect timeout in seconds (default: 12)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 60:
        parser.error("Timeout must be 1-60 seconds")
    if args.mac is not None and not re.fullmatch(
        r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", args.mac
    ):
        parser.error("Use a Bluetooth address such as AA:BB:CC:DD:EE:FF")
    if not all(hasattr(socket, name) for name in ("AF_BLUETOOTH", "BTPROTO_RFCOMM")):
        parser.error("Run with /usr/bin/python3; this Python lacks Bluetooth support")
    out = console()
    heading("Printer setup", out=out)
    connecting = False
    try:
        existing = None
        if PRINTER_CONFIG.exists():
            # Setup can replace invalid configuration after confirmation.
            with suppress(ValueError):
                existing = load_printer(PRINTER_CONFIG)
        if args.mac is not None:
            address = args.mac.upper()
            name = (
                existing.name
                if existing and existing.address.upper() == address
                else "StickerPix"
            )
            selected = DiscoveredPrinter(address, name)
        else:
            with busy(f"Searching for StickerPix printers… ({args.timeout} seconds)"):
                printers = discover_printers(args.timeout)
            if not printers:
                notice(
                    "No StickerPix printers found.\n\nTurn on the printer and make it discoverable, then try again.\nIf you know its address: uv run crycut setup --mac ADDRESS\n",
                    "warning",
                    out=out,
                )
                return 1
            selected = _select(printers)
        same_printer = (
            existing is not None and existing.address.upper() == selected.address
        )
        if PRINTER_CONFIG.exists() and not same_printer:
            if not sys.stdin.isatty():
                raise ValueError(
                    "A different or invalid configuration already exists. Run setup in a terminal to replace it."
                )
            out.print(
                f"Saved printer: {existing.name + ' (' + existing.address + ')' if existing else 'Invalid configuration'}"
            )
            if input("Replace saved printer? [y/N]: ").strip().casefold() not in (
                "y",
                "yes",
            ):
                notice("Setup cancelled. Configuration unchanged.", out=out)
                return 0
        out.print(fields([("Printer", selected.name), ("Address", selected.address)]))
        out.print()
        connecting = True
        with busy("Checking connection…"):
            channel = resolve_rfcomm_channel(selected.address, timeout=args.timeout)
            with socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
            ) as connection:
                connection.settimeout(args.timeout)
                connection.connect((selected.address, channel))
                client = Client(connection, args.timeout)
                client.handshake()
                status = read_status(client, selected.address, selected.name)
        connecting = False
        capacity = existing.ribbon_capacity if same_printer and existing else 36
        save_printer(
            PRINTER_CONFIG, PrinterConfig(selected.address, selected.name, capacity)
        )
        out.print(
            fields(
                [
                    ("Connection", Text("Ready", style="success")),
                    ("Model", status["model"]),
                    ("Firmware", status["firmware"]),
                    ("Saved", os.path.relpath(PRINTER_CONFIG.resolve())),
                ]
            )
        )
        out.print()
        notice("Printer configured.", "success", out=out)
        command_hints([("Check status", "uv run crycut status")], out=out)
        out.print()
        return 0
    except (EOFError, KeyboardInterrupt):
        notice("Setup cancelled.", "warning", out=out)
        return 130
    except (OSError, ValueError, KeyError, TypeError) as error:
        detail = (
            "Cannot connect to the selected printer. Check its power and Bluetooth pairing."
            if connecting and isinstance(error, OSError)
            else str(error)
        )
        notice(f"Setup failed: {detail}", "error", out=console(sys.stderr))
        return 1
