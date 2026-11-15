"""Local configuration for tests; no contributor's printer settings are required."""

import pytest

from crycut.configuration import PrinterConfig, save_printer


@pytest.fixture
def printer_config(tmp_path):
    path = tmp_path / "printer.json"
    save_printer(path, PrinterConfig("AA:BB:CC:DD:EE:FF", "Test StickerPix", 36))
    return path


@pytest.fixture
def manual_live_refresh(monkeypatch):
    """Make terminal assertions deterministic; countdown ticks still run."""
    from rich.live import Live

    def live(*args, **kwargs):
        kwargs["auto_refresh"] = False
        return Live(*args, **kwargs)

    monkeypatch.setattr("crycut.progress.Live", live)
