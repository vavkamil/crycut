"""Local configuration for tests; no contributor's printer settings are required."""

import pytest

from crycut.configuration import PrinterConfig, save_printer


@pytest.fixture
def printer_config(tmp_path):
    path = tmp_path / "printer.json"
    save_printer(path, PrinterConfig("AA:BB:CC:DD:EE:FF", "Test StickerPix", 36))
    return path


@pytest.fixture
def offline_printer(monkeypatch, printer_config):
    """Use temporary settings and reject any connection during offline previews."""
    from crycut.commands import print as printer

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline preview attempted Bluetooth access")

    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", forbidden)
    monkeypatch.setattr(printer.socket, "socket", forbidden)


@pytest.fixture
def manual_live_refresh(monkeypatch):
    """Make terminal assertions deterministic; countdown ticks still run."""
    from rich.live import Live

    def live(*args, **kwargs):
        kwargs["auto_refresh"] = False
        return Live(*args, **kwargs)

    monkeypatch.setattr("crycut.progress.Live", live)
