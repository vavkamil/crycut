"""BlueZ discovery must handle cached names, updates, and adapter failures."""

import subprocess
from unittest.mock import Mock

import pytest

from crycut import discovery


def test_device_updates_are_deduplicated_and_unrelated_devices_ignored():
    devices = discovery.parse_devices("""
Device AA:BB:CC:DD:EE:FF StickerPix old name
Device 00:11:22:33:44:55 Headphones
\x1b[0;92m[CHG]\x1b[0m Device aa:bb:cc:dd:ee:ff Name: StickerPix Print+Cut-EEFF
[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -45
[NEW] Device 11:22:33:44:55:66 StickerPix second
[DEL] Device 11:22:33:44:55:66 StickerPix second
[NEW] Device 22:33:44:55:66:77 22-33-44-55-66-77
[CHG] Device 22:33:44:55:66:77 Alias: StickerPix third
""")
    assert [(d.address, d.name) for d in devices] == [
        ("AA:BB:CC:DD:EE:FF", "StickerPix Print+Cut-EEFF"),
        ("22:33:44:55:66:77", "StickerPix third"),
    ]


def response(output, code=0):
    return subprocess.CompletedProcess([], code, stdout=output, stderr="")


def test_discovery_uses_bounded_classic_scan_and_known_devices(monkeypatch):
    run = Mock(
        side_effect=[
            response("Controller 00:11:22:33:44:55 notebook\n Powered: yes\n"),
            response("Device AA:BB:CC:DD:EE:FF StickerPix paired"),
            response(
                "Discovery started\n[NEW] Device 11:22:33:44:55:66 StickerPix new"
            ),
        ]
    )
    monkeypatch.setattr(discovery.subprocess, "run", run)
    assert len(discovery.discover_printers(8)) == 2
    assert run.call_args.args[0] == ["bluetoothctl", "--timeout", "8", "scan", "bredr"]
    assert run.call_args.kwargs["timeout"] == 13
    assert run.call_args.kwargs["env"]["LC_ALL"] == "C"


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ("No default controller available", "No Bluetooth adapter"),
        ("Waiting to connect to bluetoothd...", "No Bluetooth adapter"),
        ("org.bluez.Error.NotReady", "Bluetooth is off"),
        ("Controller 00:11:22:33:44:55 notebook\n Powered: no\n", "Bluetooth is off"),
        ("Failed to connect to bluetoothd", "Bluetooth discovery failed"),
    ],
)
def test_adapter_errors_fail_before_scanning(monkeypatch, result, message):
    run = Mock(return_value=response(result))
    monkeypatch.setattr(discovery.subprocess, "run", run)
    with pytest.raises(ConnectionError, match=message):
        discovery.discover_printers()
    run.assert_called_once()


@pytest.mark.parametrize("timeout", [0, 61, -1, 1.5, True, None])
def test_invalid_timeout_does_not_start_discovery(monkeypatch, timeout):
    run = Mock(side_effect=AssertionError("invalid timeout reached BlueZ"))
    monkeypatch.setattr(discovery.subprocess, "run", run)
    with pytest.raises(ValueError, match="Discovery timeout must be 1-60 seconds"):
        discovery.discover_printers(timeout)
    run.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CompletedProcess([], 1, stdout="", stderr="scan failed"),
        response("Invalid command: scan bredr"),
    ],
)
def test_failed_scan_does_not_return_only_cached_printers(monkeypatch, failure):
    run = Mock(
        side_effect=[
            response("Controller 00:11:22:33:44:55 notebook\n Powered: yes\n"),
            response("Device AA:BB:CC:DD:EE:FF StickerPix paired"),
            failure,
        ]
    )
    monkeypatch.setattr(discovery.subprocess, "run", run)
    with pytest.raises(ConnectionError, match="Bluetooth discovery failed"):
        discovery.discover_printers()
    assert run.call_count == 3


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileNotFoundError(), "Install BlueZ"),
        (subprocess.TimeoutExpired("bluetoothctl", 5), "timed out"),
    ],
)
def test_missing_tool_and_timeout_have_actionable_errors(monkeypatch, error, expected):
    monkeypatch.setattr(discovery.subprocess, "run", Mock(side_effect=error))
    with pytest.raises((ValueError, ConnectionError), match=expected):
        discovery.discover_printers()
