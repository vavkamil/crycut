"""Find a StickerPix printer, check its connection, and save local settings."""

import os
import re
import socket
import sys
from contextlib import suppress

from ..arguments import ArgumentParser
from ..configuration import PrinterConfig, load_printer, save_printer
from ..discovery import DiscoveredPrinter, discover_printers
from ..paths import PRINTER_CONFIG
from ..status import read_status
from ..transport import Client, resolve_rfcomm_channel


def _select(printers: list[DiscoveredPrinter]) -> DiscoveredPrinter:
    width = max(12, *(len(printer.name) for printer in printers))
    print(f"   {'Printer':<{width}}  Address")
    for index, printer in enumerate(printers, 1):
        print(f"{index:>2} {printer.name:<{width}}  {printer.address}")
    print()
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
        print("Enter a number from the list.\n")


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
    print("\nPrinter setup\n")
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
            print(
                f"Searching for StickerPix printers… ({args.timeout} seconds)\n",
                flush=True,
            )
            printers = discover_printers(args.timeout)
            if not printers:
                print(
                    "No StickerPix printers found.\n\nTurn on the printer and make it discoverable, then try again.\nIf you know its address: uv run crycut setup --mac ADDRESS\n"
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
            print(
                f"Saved printer: {existing.name + ' (' + existing.address + ')' if existing else 'Invalid configuration'}"
            )
            if input("Replace saved printer? [y/N]: ").strip().casefold() not in (
                "y",
                "yes",
            ):
                print("\nSetup cancelled. Configuration unchanged.\n")
                return 0
        print(
            f"{'Printer:':<15}{selected.name}\n{'Address:':<15}{selected.address}\n",
            flush=True,
        )
        print("Checking connection…", flush=True)
        connecting = True
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
        print(
            f"\n{'Connection:':<15}Ready\n{'Model:':<15}{status['model']}\n{'Firmware:':<15}{status['firmware']}\n{'Saved:':<15}{os.path.relpath(PRINTER_CONFIG.resolve())}"
        )
        print("\nCheck status:  uv run crycut status\n")
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.\n")
        return 130
    except (OSError, ValueError, KeyError, TypeError) as error:
        detail = (
            "Cannot connect to the selected printer. Check its power and Bluetooth pairing."
            if connecting and isinstance(error, OSError)
            else str(error)
        )
        print(f"\nSetup failed: {detail}\n", file=sys.stderr)
        return 1
