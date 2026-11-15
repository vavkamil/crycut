"""Read the saved StickerPix printer's status over classic Bluetooth RFCOMM."""

import json
import math
import socket
import sys
from collections.abc import Generator
from contextlib import contextmanager

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
    remaining = "Unavailable"
    if ribbon is not None:
        remaining = f"{ribbon}/{capacity}" if capacity else str(ribbon)
    sheets = status["sticker_sheets_printed"]
    model = status["model"]
    if revision := status["hardware_revision"]:
        model = f"{model} ({revision})"
    bluetooth = status["bluetooth_firmware"]
    bluetooth = bluetooth.removeprefix("+GVER:").strip() if bluetooth else "Unavailable"
    identity = [
        ("Printer", status["name"]),
        ("Address", status["address"]),
        ("Model", model),
        ("Firmware", status["firmware"]),
        ("Bluetooth FW", bluetooth),
    ]
    current = [
        ("State", status["state"]),
        ("Alerts", "; ".join(status["alerts"]) or "None"),
        ("Auto-off", auto_off),
        ("Prints left", remaining),
        ("Sheets printed", sheets if sheets is not None else "Unavailable"),
    ]
    sections = [
        "\n".join(f"{label + ':':<18}{value}" for label, value in rows)
        for rows in (identity, current)
    ]
    sections = ["Status", *sections, f"{'Details:':<18}uv run crycut status --json"]
    return "\n" + "\n\n".join(sections) + "\n"


def structured_status(status: JsonObject) -> JsonObject:
    """Group detailed status while retaining raw codes and usage counters."""
    return {
        "printer": {
            "name": status["name"],
            "model": status["model"],
            "hardware_revision": status["hardware_revision"],
            "sku": status["sku"],
            "serials": {
                "machine": status["serial"],
                "pcba": status["serial_pcba"],
                "alps": status["serial_alps"],
            },
        },
        "firmware": {
            "main": status["firmware"],
            "bluetooth": status["bluetooth_firmware"],
        },
        "connection": {
            "address": status["address"],
            "rfcomm_channel": status.get("rfcomm_channel"),
        },
        "state": {
            "name": status["state"],
            "code": status["state_code"],
            "substate_code": status["substate_code"],
            "alerts": status["alerts"],
            "alerts_raw": status["alerts_raw"],
            "auto_off_seconds": status["auto_off_seconds"],
        },
        "cartridge": {
            "prints_left": status["ribbon_count"],
            "prints_left_raw": status["ribbon_count_raw"],
            "capacity": status.get("ribbon_capacity"),
        },
        "usage": {
            "sticker_sheets_printed": status["sticker_sheets_printed"],
            "photo_sheets_printed": status["photo_sheets_printed"],
            "counters": status["usage_counters"],
        },
    }


@contextmanager
def reading_status() -> Generator[None]:
    """Show a temporary message only when both output streams are terminals."""
    stream = sys.stderr
    interactive = sys.stdout.isatty() and stream.isatty()
    if interactive:
        stream.write("\nReading printer status…\n\n")
        stream.flush()
    try:
        yield
    finally:
        if interactive:
            stream.write("\r\033[2K" + "\033[1A\r\033[2K" * 3)
            stream.flush()


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
        with reading_status():
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
        print(
            json.dumps(structured_status(status), indent=2)
            if args.json
            else display(status)
        )
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
