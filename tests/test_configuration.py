"""Saved printer settings must be valid and survive failed writes."""

import json

import pytest

from crycut.configuration import load_printer


def test_saved_printer_identity_and_capacity(tmp_path):
    path = tmp_path / "printer.json"
    path.write_text(
        json.dumps(
            {
                "address": "AA:BB:CC:DD:EE:FF",
                "name": "StickerPix",
                "ribbon_capacity": 36,
            }
        )
    )
    result = load_printer(path)
    assert result.address == "AA:BB:CC:DD:EE:FF"
    assert result.name == "StickerPix"
    assert result.ribbon_capacity == 36


@pytest.mark.parametrize("address", [None, "invalid", 123])
def test_invalid_saved_address_is_rejected(tmp_path, address):
    path = tmp_path / "printer.json"
    path.write_text(json.dumps({"address": address}))
    with pytest.raises(ValueError, match="address"):
        load_printer(path)


@pytest.mark.parametrize("capacity", [True, 0, -1, "36"])
def test_invalid_capacity_is_rejected(tmp_path, capacity):
    path = tmp_path / "printer.json"
    path.write_text(
        json.dumps({"address": "AA:BB:CC:DD:EE:FF", "ribbon_capacity": capacity})
    )
    with pytest.raises(ValueError, match="ribbon_capacity"):
        load_printer(path)


def test_missing_configuration_points_to_setup(tmp_path):
    with pytest.raises(ValueError, match="crycut setup"):
        load_printer(tmp_path / "missing.json")


def test_failed_atomic_save_preserves_existing_configuration(tmp_path, monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    from crycut.configuration import PrinterConfig, save_printer

    path = tmp_path / "printer.json"
    before = '{"address":"AA:BB:CC:DD:EE:FF"}\n'
    path.write_text(before)
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        save_printer(path, PrinterConfig("11:22:33:44:55:66", "New printer", 36))
    assert path.read_text() == before
    assert list(tmp_path.iterdir()) == [path]
