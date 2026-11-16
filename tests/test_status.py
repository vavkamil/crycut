"""Sticker-sheet usage must stay separate from photo and cartridge counts."""

import io
import json
from unittest.mock import MagicMock, Mock

import pytest

from terminal_helpers import visible
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
                {
                    "result": [
                        {
                            "hw_ver": "x02",
                            "sku": "2013410",
                            "sn_pcba": "board-serial",
                            "sn_alps": "component-serial",
                        }
                    ]
                },
                {"result": [{"bt-fw_ver": "+GVER:BR8051A01B_fW_260519_r7997"}]},
            ]
        )
    )


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_never_attempts_connection(timeout, monkeypatch, capsys):
    lookup = Mock()
    monkeypatch.setattr(command, "resolve_rfcomm_channel", lookup)
    with pytest.raises(SystemExit) as stopped:
        command.main(["--timeout", timeout])
    assert stopped.value.code == 2
    assert "Timeout must be finite and positive" in capsys.readouterr().err
    lookup.assert_not_called()


def test_python_without_bluetooth_reports_actionable_error(monkeypatch, capsys):
    monkeypatch.delattr(command.socket, "AF_BLUETOOTH", raising=False)
    lookup = Mock()
    monkeypatch.setattr(command, "resolve_rfcomm_channel", lookup)
    with pytest.raises(SystemExit) as stopped:
        command.main([])
    assert stopped.value.code == 2
    assert "lacks Bluetooth support" in capsys.readouterr().err
    lookup.assert_not_called()


@pytest.mark.parametrize("as_json", [False, True])
def test_unreachable_printer_reports_failure_in_requested_format(
    as_json, monkeypatch, capsys, printer_config
):
    monkeypatch.setattr(command, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(command, "resolve_rfcomm_channel", Mock(return_value=2))
    connection = MagicMock()
    connection.__enter__.return_value.connect.side_effect = OSError(
        "connection refused"
    )
    factory = Mock(return_value=connection)
    monkeypatch.setattr(command.socket, "socket", factory)
    client = Mock()
    monkeypatch.setattr(command, "Client", client)
    assert command.main(["--json"] if as_json else []) == 1
    output = capsys.readouterr()
    if as_json:
        assert json.loads(output.out) == {"error": "connection refused"}
        assert output.err == ""
    else:
        assert "Connection:" in output.err and "Unavailable" in output.err
        assert "Check that the printer is on and Bluetooth is enabled" in output.err
        assert "AA:BB:CC:DD:EE:FF" in output.err
    client.assert_not_called()
    factory.assert_called_once()
    connection.__exit__.assert_called_once()


@pytest.mark.parametrize("ribbon", [None, {}, [0] * 5, [0] * 7])
@pytest.mark.parametrize("as_json", [False, True])
def test_malformed_ribbon_reports_error_without_displaying_status(
    ribbon, as_json, monkeypatch, capsys, printer_config
):
    client = client_with_counters({"result": [{}]})
    replies = list(client.get_properties.side_effect)
    replies[2] = {"result": [{"ribbon-info": ribbon}]}
    client.get_properties.side_effect = replies
    monkeypatch.setattr(command, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(command, "resolve_rfcomm_channel", Mock(return_value=2))
    connection = MagicMock()
    monkeypatch.setattr(command.socket, "socket", Mock(return_value=connection))
    monkeypatch.setattr(command, "Client", Mock(return_value=client))

    assert command.main(["--json"] if as_json else []) == 1

    output = capsys.readouterr()
    if as_json:
        assert json.loads(output.out) == {"error": "Unexpected ribbon-info response"}
        assert output.err == ""
    else:
        assert (
            "Unable to read printer status: Unexpected ribbon-info response"
            in output.err
        )
        assert "Prints left:" not in output.out
        assert "Sheets printed:" not in output.out
    assert client.get_properties.call_count == 3
    client.get_properties.assert_called_with(["ribbon-info"], 3)
    connection.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "seconds,expected", [(0, "Never"), (90, "90 seconds"), (-1, "Unknown (-1)")]
)
def test_status_summary_handles_auto_off_variants_and_missing_identity(
    seconds, expected
):
    status = read_status(client_with_counters({"result": [{}]}), "address", "printer")
    status.update(
        auto_off_seconds=seconds,
        hardware_revision=None,
        bluetooth_firmware=None,
        ribbon_capacity=None,
    )
    output = command.display(status)
    assert f"Auto-off:         {expected}" in output
    assert "Model:            CSTP401\n" in output
    assert "Bluetooth FW:     Unavailable" in output
    assert "Prints left:      31\n" in output


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("interactive", [False, True])
def test_status_displays_completed_sticker_sheets(
    as_json, interactive, monkeypatch, capsys, printer_config
):
    progress = io.StringIO()
    monkeypatch.setattr(progress, "isatty", lambda: interactive)
    monkeypatch.setattr(command.sys, "stderr", progress)
    monkeypatch.setattr(command.sys.stdout, "isatty", lambda: interactive)
    client = client_with_counters(
        {
            "result": [
                {
                    "printed": 46,
                    "finished": 38,
                    "finished_5012": 18,
                    "finished_5013": 20,
                    "cancelled": 1,
                    "poweroned": 16,
                    "new_counter": 7,
                }
            ]
        }
    )
    monkeypatch.setattr(command, "PRINTER_CONFIG", printer_config)

    def resolve(*args):
        assert ("Reading printer status…" in progress.getvalue()) == interactive
        return 1

    monkeypatch.setattr(command, "resolve_rfcomm_channel", resolve)
    monkeypatch.setattr(command.socket, "socket", Mock(return_value=MagicMock()))
    monkeypatch.setattr(command, "Client", Mock(return_value=client))

    assert command.main(["--json"] if as_json else []) == 0
    output = capsys.readouterr().out
    assert "Reading printer status" not in output
    if as_json or not interactive:
        assert "\033" not in output
    if interactive:
        assert "\033[?25h" in progress.getvalue()
        assert "\033[2K" in progress.getvalue()
    else:
        assert progress.getvalue() == ""
    if as_json:
        status = json.loads(output)
        assert set(status) == {
            "printer",
            "firmware",
            "connection",
            "state",
            "cartridge",
            "usage",
        }
        assert status["usage"] == {
            "sticker_sheets_printed": 20,
            "photo_sheets_printed": 18,
            "counters": {
                "printed": 46,
                "finished": 38,
                "finished_5012": 18,
                "finished_5013": 20,
                "cancelled": 1,
                "poweroned": 16,
                "new_counter": 7,
            },
        }
        assert status["cartridge"] == {
            "prints_left": 31,
            "prints_left_raw": 31,
            "capacity": 36,
        }
        assert status["state"]["alerts"] == []
        assert status["state"]["alerts_raw"] == ":0:0"
        assert status["printer"]["serials"] == {
            "machine": "test",
            "pcba": "board-serial",
            "alps": "component-serial",
        }
        assert status["printer"]["sku"] == "2013410"
        assert status["printer"]["hardware_revision"] == "x02"
        assert status["firmware"] == {
            "main": "1.0.31_0033",
            "bluetooth": "+GVER:BR8051A01B_fW_260519_r7997",
        }
        assert status["connection"]["rfcomm_channel"] == 1
    else:
        lines = [line.removeprefix("  ") for line in visible(output).splitlines()]
        index = next(
            i for i, line in enumerate(lines) if line.startswith("Prints left:")
        )
        assert lines[index + 1] == "Sheets printed:   20"
        state_index = lines.index("State:            Idle")
        assert lines[state_index + 1] == "Alerts:           None"
        assert "Model:            CSTP401 (x02)" in lines
        assert "Bluetooth FW:     BR8051A01B_fW_260519_r7997" in lines
        assert "Serial:" not in output
        for identifier in ("test", "board-serial", "component-serial", "2013410"):
            assert identifier not in output
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
    usage = command.structured_status(status)["usage"]
    assert usage["sticker_sheets_printed"] is None
    result = reply.get("result", []) if isinstance(reply, dict) else []
    expected = result[0] if result and isinstance(result[0], dict) else None
    assert usage["counters"] == expected
    if expected is None:
        assert usage["photo_sheets_printed"] is None


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
    structured = command.structured_status(status)
    assert structured["cartridge"]["prints_left"] is None
    assert structured["cartridge"]["prints_left_raw"] == -1
    assert structured["state"]["alerts"] == ["Cartridge missing"]


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


@pytest.mark.parametrize("property_index", [4, 5])
@pytest.mark.parametrize(
    "reply",
    [
        TimeoutError("no reply"),
        {"error": {"code": -6001}},
        {"result": []},
        {"result": [None]},
        {"result": ["unexpected"]},
        {"result": [{}]},
        {"result": [{"hw_ver": [], "bt-fw_ver": 123}]},
    ],
)
def test_unavailable_extra_identity_preserves_status(property_index, reply):
    client = client_with_counters({"result": [{"finished_5013": 20}]})
    responses = list(client.get_properties.side_effect)
    responses[property_index] = reply
    client.get_properties.side_effect = responses
    status = read_status(client, "address", "printer")
    output = command.display(status)
    assert status["state"] == "Idle"
    assert status["sticker_sheets_printed"] == 20
    assert status["serial"] == "test"
    assert "Serial:" not in output
    if property_index == 4:
        assert status["hardware_revision"] is None
        assert status["sku"] is None
        assert status["serial_pcba"] is None
        assert status["serial_alps"] is None
        assert "Model:            CSTP401\n" in output
        assert "Bluetooth FW:     BR8051A01B_fW_260519_r7997" in output
    else:
        assert status["bluetooth_firmware"] is None
        assert "Bluetooth FW:     Unavailable" in output
        assert "Model:            CSTP401 (x02)" in output


@pytest.mark.parametrize("count", [0, 18, -1, True, None, "bad"])
def test_photo_counts_are_separate_and_raw_values_are_preserved(count):
    counters = {"finished_5012": count, "finished_5013": 20}
    status = read_status(
        client_with_counters({"result": [counters]}), "address", "printer"
    )
    usage = command.structured_status(status)["usage"]
    assert usage["photo_sheets_printed"] == (
        count if type(count) is int and count >= 0 else None
    )
    assert usage["sticker_sheets_printed"] == 20
    assert usage["counters"] == counters
    assert "photo" not in command.display(status).lower()


@pytest.mark.parametrize("error", [OSError("connection failed"), KeyboardInterrupt()])
def test_reading_message_is_removed_when_interrupted(monkeypatch, error):
    output, progress = io.StringIO(), io.StringIO()
    monkeypatch.setattr(output, "isatty", lambda: True)
    monkeypatch.setattr(progress, "isatty", lambda: True)
    monkeypatch.setattr(command.sys, "stdout", output)
    monkeypatch.setattr(command.sys, "stderr", progress)
    with pytest.raises(type(error)):
        with command.reading_status():
            lines = visible(progress.getvalue()).split("\n")
            index = next(
                i for i, line in enumerate(lines) if "Reading printer status…" in line
            )
            assert index > 0 and not lines[index - 1].strip()
            assert index + 1 < len(lines) and not lines[index + 1].strip()
            assert "\033[?25l" in progress.getvalue()
            raise error
    assert "\033[?25h" in progress.getvalue()
    assert "\033[2K" in progress.getvalue()
    assert output.getvalue() == ""


@pytest.mark.parametrize("stdout_tty,stderr_tty", [(False, True), (True, False)])
def test_reading_message_is_silent_when_either_stream_is_redirected(
    monkeypatch, stdout_tty, stderr_tty
):
    output, progress = io.StringIO(), io.StringIO()
    monkeypatch.setattr(output, "isatty", lambda: stdout_tty)
    monkeypatch.setattr(progress, "isatty", lambda: stderr_tty)
    monkeypatch.setattr(command.sys, "stdout", output)
    monkeypatch.setattr(command.sys, "stderr", progress)
    with command.reading_status():
        assert progress.getvalue() == ""
    assert progress.getvalue() == output.getvalue() == ""
