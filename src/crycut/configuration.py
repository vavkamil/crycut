"""Load and validate the saved printer settings before opening a connection."""

import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PrinterConfig:
    """Settings for the printer selected for this command."""

    address: str
    name: str
    ribbon_capacity: int | None


def load_printer(path: Path) -> PrinterConfig:
    """Load the saved printer and validate its configuration values."""
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise ValueError("No printer configured. Run uv run crycut setup.") from error
    if not isinstance(config, dict):
        raise ValueError("Printer configuration must be a JSON object")
    address = config.get("address")
    if not isinstance(address, str) or not re.fullmatch(
        r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address
    ):
        raise ValueError("Invalid printer address in configuration")
    name = config.get("name", "StickerPix")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Configured printer name must be a nonempty string")
    capacity = config.get("ribbon_capacity")
    if capacity is not None and (type(capacity) is not int or capacity <= 0):
        raise ValueError("Configured ribbon_capacity must be a positive integer")
    return PrinterConfig(address, name, capacity)


def save_printer(path: Path, config: PrinterConfig) -> None:
    """Install complete printer settings atomically, retaining the old file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, prefix=".printer-", delete=False
        ) as stream:
            staging = Path(stream.name)
            stream.write(json.dumps(asdict(config), indent=2) + "\n")
        staging.replace(path)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
