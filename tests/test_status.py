"""Sticker-sheet usage must stay separate from photo and cartridge counts."""

import json
from unittest.mock import MagicMock, Mock

import pytest

from crycut.commands import status as command
from crycut.status import describe_alerts, read_status


def client_with_counters(reply, alerts=":0:0", ribbon_count=31):
    return Mock(
        get_properties=Mock(
            side_effect=[
                {"result": ["20", "2000", alerts]},
                {
                    "result": [
                        "1.0.31_0033",
                        {"auto-off-interval": 1200},
                        "CSTP401",
                        "test",
                    ]
                },
                {"result": [{"ribbon-info": [0, 0, ribbon_count, 0, 0, 0]}]},
                reply,
            ]
        )
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_status_displays_completed_sticker_sheets(
    as_json, monkeypatch, capsys, printer_config
):
    client = client_with_counters(
        {
            "result": [
                {
                    "printed": 46,
                    "finished": 38,
                    "finished_5012": 18,
                    "finished_5013": 20,
                }
            ]
        }
    )
    monkeypatch.setattr(command, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(command, "resolve_rfcomm_channel", Mock(return_value=1))
    monkeypatch.setattr(command.socket, "socket", Mock(return_value=MagicMock()))
    monkeypatch.setattr(command, "Client", Mock(return_value=client))

    assert command.main(["--json"] if as_json else []) == 0
    output = capsys.readouterr().out
    if as_json:
        status = json.loads(output)
        assert status["sticker_sheets_printed"] == 20
        assert status["ribbon_count"] == 31
        assert status["alerts"] == []
        assert status["alerts_raw"] == ":0:0"
    else:
        lines = output.splitlines()
        index = next(
            i for i, line in enumerate(lines) if line.startswith("Prints left:")
        )
        assert lines[index + 1] == "Sheets printed:   20"
        assert lines[index + 2] == "Alerts:           None"
    client.get_properties.assert_any_call(["big-data"], 4)


@pytest.mark.parametrize("counters", [{}, {"finished_5012": 18}, {"finished_5013": 0}])
def test_zero_or_missing_sticker_counter_displays_zero(counters):
    status = read_status(
        client_with_counters({"result": [counters]}), "address", "printer"
    )
    assert status["sticker_sheets_printed"] == 0
    assert "Sheets printed:   0" in command.display(status)


@pytest.mark.parametrize(
    "reply",
    [
        TimeoutError("no counter reply"),
        ConnectionError("disconnected"),
        ValueError("unsupported property"),
        {"result": []},
        {"result": [None]},
        {"result": [{"finished_5013": None}]},
        {"result": [{"finished_5013": -1}]},
        {"result": [{"finished_5013": True}]},
        {"result": [{"finished_5013": "bad"}]},
    ],
)
def test_unavailable_counter_preserves_other_status(reply):
    status = read_status(client_with_counters(reply), "address", "printer")
    assert status["sticker_sheets_printed"] is None
    assert status["state"] == "Idle"
    assert status["ribbon_count"] == 31
    assert "Sheets printed:   Unavailable" in command.display(status)
    assert json.loads(json.dumps(status))["sticker_sheets_printed"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (":0:0", []),
        (":8401:0", ["Paper near blade"]),
        (":0:7203", ["Cartridge missing"]),
        (":0:7101", ["Paper empty"]),
        (":0:7001", ["Cover open"]),
        (
            ":8401,8123,8124:7203",
            [
                "Paper near blade",
                "Unknown warning (8123)",
                "Unknown warning (8124)",
                "Cartridge missing",
            ],
        ),
        (":0:9999", ["Unknown error (9999)"]),
        (":0:7305", ["Unknown error (7305)"]),
        (":8401:8401", ["Paper near blade"]),
    ],
)
def test_alert_descriptions_preserve_unknown_codes(raw, expected):
    assert describe_alerts(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "0", ":0:", ":0:0:7104", ":8401,,8123:0", ":oops:0", ":0:-7203", 0],
)
def test_unexpected_alert_format_never_means_no_alerts(raw):
    assert describe_alerts(raw) == [f"Unknown alert format ({raw!r})"]


def test_missing_cartridge_keeps_usage_count_and_explains_unavailable_prints():
    status = read_status(
        client_with_counters(
            {"result": [{"finished_5013": 20}]}, alerts=":0:7203", ribbon_count=-1
        ),
        "address",
        "printer",
    )
    output = command.display(status)
    assert "Prints left:      Unavailable" in output
    assert "Sheets printed:   20" in output
    assert "Alerts:           Cartridge missing" in output
    assert "Alerts (raw)" not in output
    assert status["alerts_raw"] == ":0:7203"
    assert status["ribbon_count_raw"] == -1
    assert status["alerts"] == ["Cartridge missing"]


def test_multiple_alerts_display_together():
    status = read_status(
        client_with_counters({"result": [{}]}, alerts=":8401,8123:7104"),
        "address",
        "printer",
    )
    assert (
        "Alerts:           Paper near blade; Unknown warning (8123); Paper jam"
        in command.display(status)
    )
