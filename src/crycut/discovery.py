"""Find StickerPix candidates through BlueZ without changing pairing or power."""

import os
import re
import subprocess
from dataclasses import dataclass

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEVICE = re.compile(r"(?:^|\] )Device ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}) (.+)$")


@dataclass(frozen=True, slots=True)
class DiscoveredPrinter:
    """An advertised name and address; setup verifies the printer before saving."""

    address: str
    name: str


def parse_devices(output: str) -> list[DiscoveredPrinter]:
    """Read device listings and discovery updates, ignoring other Bluetooth events."""
    devices: dict[str, str] = {}
    for line in ANSI.sub("", output).splitlines():
        match = DEVICE.search(line.strip())
        if match is None:
            continue
        address, name = match.groups()
        address = address.upper()
        if "[DEL]" in line:
            devices.pop(address, None)
            continue
        if "[CHG]" in line:
            if not name.startswith(("Name: ", "Alias: ")):
                continue
            name = name.split(": ", 1)[1]
        name = "".join(char for char in name if char.isprintable()).strip()
        devices[address] = name
    return sorted(
        (
            DiscoveredPrinter(address, name)
            for address, name in devices.items()
            if "stickerpix" in name.casefold()
        ),
        key=lambda device: (device.name.casefold(), device.address),
    )


def _bluetoothctl(arguments: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl", *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ | {"LC_ALL": "C"},
        )
    except FileNotFoundError as error:
        raise ValueError(
            "Install BlueZ with bluetoothctl to discover printers."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ConnectionError(
            "Bluetooth discovery timed out. Check the Bluetooth service."
        ) from error
    output = ANSI.sub("", result.stdout + "\n" + result.stderr)
    if "No default controller available" in output:
        raise ConnectionError("No Bluetooth adapter available.")
    if "org.bluez.Error.NotReady" in output:
        raise ConnectionError("Bluetooth is off. Turn it on and run setup again.")
    if result.returncode or "Failed to" in output or "Invalid command" in output:
        raise ConnectionError(
            "Bluetooth discovery failed. Check the adapter and Bluetooth service."
        )
    return output


def discover_printers(timeout: int = 12) -> list[DiscoveredPrinter]:
    """Combine known devices and a bounded classic Bluetooth discovery session."""
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("Discovery timeout must be 1-60 seconds")
    controller = _bluetoothctl(["show"], 5)
    if not re.search(r"^\s*Controller ", controller, re.MULTILINE):
        raise ConnectionError("No Bluetooth adapter available.")
    if not re.search(r"^\s*Powered: yes\s*$", controller, re.MULTILINE):
        raise ConnectionError("Bluetooth is off. Turn it on and run setup again.")
    known = _bluetoothctl(["devices"], 5)
    scan = _bluetoothctl(["--timeout", str(timeout), "scan", "bredr"], timeout + 5)
    return parse_devices(known + "\n" + scan)
