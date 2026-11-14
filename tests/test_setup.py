"""First-run printer selection must verify connectivity before saving settings."""

import json
from unittest.mock import MagicMock, Mock

import pytest

from crycut import cli
from crycut.commands import setup
from crycut.discovery import DiscoveredPrinter

FIRST = DiscoveredPrinter("AA:BB:CC:DD:EE:FF", "StickerPix Print+Cut-EEFF")
SECOND = DiscoveredPrinter("11:22:33:44:55:66", "StickerPix Print+Cut-5566")


@pytest.fixture
def environment(tmp_path, monkeypatch):
    config = tmp_path / "config/printer.json"
    monkeypatch.setattr(setup, "PRINTER_CONFIG", config)
    scan = Mock(return_value=[FIRST])
    monkeypatch.setattr(setup, "discover_printers", scan)
    lookup = Mock(return_value=2)
    monkeypatch.setattr(setup, "resolve_rfcomm_channel", lookup)
    connection = MagicMock()
    monkeypatch.setattr(setup.socket, "socket", Mock(return_value=connection))
    client = Mock()
    monkeypatch.setattr(setup, "Client", Mock(return_value=client))
    monkeypatch.setattr(
        setup,
        "read_status",
        Mock(return_value={"model": "CSTP401", "firmware": "1.0.31_0033"}),
    )
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    return config, scan, lookup, connection, client


def saved(path, address=SECOND.address, capacity=36):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"address": address, "name": "Saved printer", "ribbon_capacity": capacity}
        )
    )
    return path.read_bytes()


def test_single_printer_is_verified_and_saved(environment, capsys):
    config, scan, lookup, connection, client = environment
    assert cli.main(["setup"]) == 0
    assert json.loads(config.read_text()) == {
        "address": FIRST.address,
        "name": FIRST.name,
        "ribbon_capacity": 36,
    }
    scan.assert_called_once_with(12)
    lookup.assert_called_once_with(FIRST.address, timeout=12)
    connection.__enter__.return_value.connect.assert_called_once_with(
        (FIRST.address, 2)
    )
    client.handshake.assert_called_once()
    client.request.assert_not_called()  # No print submission.
    output = capsys.readouterr().out
    assert FIRST.address in output and "Ready" in output and "crycut status" in output


def test_known_address_skips_discovery(environment):
    config, scan, *_ = environment
    assert setup.main(["--mac", FIRST.address.lower()]) == 0
    scan.assert_not_called()
    assert json.loads(config.read_text())["address"] == FIRST.address


def test_select_second_printer_and_reprompt_invalid_selection(environment, monkeypatch):
    config, scan, *_ = environment
    scan.return_value = [FIRST, SECOND]
    replies = iter(["invalid", "0", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))
    assert setup.main([]) == 0
    assert json.loads(config.read_text())["address"] == SECOND.address


def test_multiple_printers_in_pipeline_require_explicit_address(
    environment, monkeypatch, capsys
):
    config, scan, lookup, *_ = environment
    scan.return_value = [FIRST, SECOND]
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    assert setup.main([]) == 1
    assert "--mac" in capsys.readouterr().err
    assert not config.exists()
    lookup.assert_not_called()


def test_no_results_do_not_connect_or_save(environment, capsys):
    config, scan, lookup, *_ = environment
    scan.return_value = []
    assert setup.main([]) == 1
    assert "No StickerPix printers found" in capsys.readouterr().out
    lookup.assert_not_called()
    assert not config.exists()


def test_same_printer_preserves_custom_capacity(environment):
    config, *_ = environment
    saved(config, FIRST.address.lower(), 24)
    assert setup.main([]) == 0
    assert json.loads(config.read_text())["ribbon_capacity"] == 24


def test_declining_replacement_preserves_saved_config(environment, monkeypatch):
    config, _, lookup, *_ = environment
    before = saved(config)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert setup.main([]) == 0
    lookup.assert_not_called()
    assert config.read_bytes() == before


def test_replacement_needs_terminal_confirmation(environment, monkeypatch):
    config, _, lookup, *_ = environment
    before = saved(config)
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    assert setup.main(["--mac", FIRST.address]) == 1
    lookup.assert_not_called()
    assert config.read_bytes() == before


def test_failed_connection_preserves_saved_config(environment, monkeypatch, capsys):
    config, _, lookup, *_ = environment
    before = saved(config)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    lookup.side_effect = ConnectionError("private subprocess diagnostic")
    assert setup.main([]) == 1
    assert config.read_bytes() == before
    output = capsys.readouterr().err
    assert "Cannot connect" in output
    assert "subprocess" not in output


def test_handshake_failure_does_not_create_config(environment):
    config, _, _, _, client = environment
    client.handshake.side_effect = ValueError("Printer rejected handshake")
    assert setup.main([]) == 1
    assert not config.exists()


def test_cancelled_selection_leaves_config_untouched(environment, monkeypatch):
    config, scan, lookup, *_ = environment
    scan.return_value = [FIRST, SECOND]
    monkeypatch.setattr("builtins.input", lambda _: "q")
    assert setup.main([]) == 130
    lookup.assert_not_called()
    assert not config.exists()


@pytest.mark.parametrize(
    "args", [["--help"], ["--timeout", "0"], ["--timeout", "61"], ["--mac", "invalid"]]
)
def test_help_and_invalid_options_do_not_scan(environment, args):
    config, scan, *_ = environment
    with pytest.raises(SystemExit):
        setup.main(args)
    scan.assert_not_called()
    assert not config.exists()
