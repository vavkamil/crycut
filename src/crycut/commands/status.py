"""Read the saved StickerPix printer's status over classic Bluetooth RFCOMM."""

import json
import math
import socket
import sys

from ..arguments import ArgumentParser
from ..configuration import load_printer
from ..models import JsonObject
from ..paths import PRINTER_CONFIG
from ..status import read_status
from ..transport import Client, resolve_rfcomm_channel


def display(status: JsonObject) -> str:
    """Format printer status as a spaced, aligned summary."""
    seconds = status["auto_off_seconds"]
    if seconds == 0:
        auto_off = "Never"
    elif seconds > 0:
        auto_off = (
            f"{seconds // 60} minutes" if seconds % 60 == 0 else f"{seconds} seconds"
        )
    else:
        auto_off = f"Unknown ({seconds})"
    ribbon = status["ribbon_count"]
    capacity = status.get("ribbon_capacity")
    remaining = "Unavailable (cartridge may be absent)"
    if ribbon is not None:
        remaining = f"{ribbon}/{capacity}" if capacity else str(ribbon)
    sheets = status["sticker_sheets_printed"]
    identity = [
        ("Printer", status["name"]),
        ("Address", status["address"]),
        ("Model", status["model"]),
        ("Serial", status["serial"]),
        ("Firmware", status["firmware"]),
    ]
    current = [
        ("State", status["state"]),
        ("Auto-off", auto_off),
        ("Prints left", remaining),
        ("Sheets printed", sheets if sheets is not None else "Unavailable"),
        ("Alerts (raw)", status["alerts_raw"]),
    ]
    sections = [
        "\n".join(f"{label + ':':<18}{value}" for label, value in rows)
        for rows in (identity, current)
    ]
    return "\n" + "\n\n".join(sections) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Connect to the selected printer and display its status."""
    parser = ArgumentParser(prog="crycut status", description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print structured status")
    parser.add_argument("--timeout", type=float, default=10, help="Timeout in seconds")
    parser.add_argument(
        "--channel", type=int, help="Override advertised RFCOMM channel"
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Timeout must be finite and positive")
    if not hasattr(socket, "AF_BLUETOOTH"):
        parser.error("Run with /usr/bin/python3; this Python lacks Bluetooth support")
    connecting = False
    address = ""
    name = "StickerPix"
    try:
        config = load_printer(PRINTER_CONFIG)
        address, name = config.address, config.name
        connecting = True
        channel = resolve_rfcomm_channel(address, args.channel, args.timeout)
        with socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
        ) as connection:
            connection.settimeout(args.timeout)
            connection.connect((address, channel))
            client = Client(connection, args.timeout)
            client.handshake()
            status = read_status(client, address, name)
            status["ribbon_capacity"] = config.ribbon_capacity
            status["rfcomm_channel"] = channel
        print(json.dumps(status, indent=2) if args.json else display(status))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        message = str(error)
        if args.json:
            print(json.dumps({"error": message}))
        elif connecting and isinstance(error, OSError):
            rows = [
                ("Printer", name),
                ("Address", address),
                ("Connection", "Unavailable"),
            ]
            summary = "\n".join(f"{label + ':':<18}{value}" for label, value in rows)
            print(
                f"\n{summary}\n\n"
                "Check that the printer is on and the notebook's Bluetooth is enabled.",
                file=sys.stderr,
            )
            print(file=sys.stderr)
        else:
            print(f"\nUnable to read printer status: {message}\n", file=sys.stderr)
        return 1
