"""CLI intent, help aliases, and diagnostics must not submit accidental jobs."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from crycut import cli
from crycut.commands import print as printer


@pytest.mark.usefixtures("manual_live_refresh")
@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("failure", ["lookup", "connect", "handshake"])
def test_connection_failure_leaves_a_static_unavailable_result(
    failure, interactive, tmp_path, monkeypatch, printer_config
):
    import io
    from unittest.mock import MagicMock

    from crycut.progress import ProgressDisplay
    from crycut.ui import console
    from terminal_helpers import visible

    output, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(output, "isatty", lambda: interactive)
    monkeypatch.setattr(printer.sys, "stdout", output)
    monkeypatch.setattr(printer.sys, "stderr", errors)
    display = ProgressDisplay(output)
    monkeypatch.setattr(printer, "ProgressDisplay", lambda _: display)
    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    lookup = Mock(return_value=2)
    connection = MagicMock()
    client = Mock(events=[])
    if failure == "lookup":
        lookup.side_effect = ValueError("sdptool is required for service lookup")
    elif failure == "connect":
        connection.__enter__.return_value.connect.side_effect = OSError("unreachable")
    else:
        client.handshake.side_effect = ValueError("Handshake rejected")
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", lookup)
    monkeypatch.setattr(printer.socket, "socket", Mock(return_value=connection))
    monkeypatch.setattr(printer, "Client", Mock(return_value=client))
    submit = Mock(side_effect=AssertionError("must not submit"))
    monkeypatch.setattr(printer, "submit", submit)
    assert (
        printer.main(["wordmarks", "--send", "--log", str(tmp_path / "run.jsonl")]) == 1
    )
    assert "Status:           Unavailable" in visible(output.getvalue())
    assert "[x]" in errors.getvalue()
    assert not display.active
    assert not display._live.is_started
    if interactive:
        final = io.StringIO()
        console(final).print(display._live.renderable)
        assert "Status:           Unavailable" in visible(final.getvalue())
        assert "Connecting" not in final.getvalue()
        assert "\x1b[?25h" in output.getvalue()
    submit.assert_not_called()


@pytest.mark.parametrize("channel", [None, 7])
def test_interrupted_print_recovery_reconnects_without_resubmitting(
    channel, tmp_path, monkeypatch, capsys, printer_config
):
    import shlex
    from unittest.mock import MagicMock

    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(printer, "ROOT", tmp_path)
    connection = MagicMock()
    monkeypatch.setattr(printer.socket, "socket", Mock(return_value=connection))
    lookup = Mock(return_value=channel or 2)
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", lookup)
    monkeypatch.setattr(printer, "Client", Mock(return_value=Mock(events=[])))

    def interrupt(client, sheet, emit, **kwargs):
        emit("accepted", job_id=42)
        raise KeyboardInterrupt

    submit = Mock(side_effect=interrupt)
    monitor = Mock(return_value={"job-state": 9})
    monkeypatch.setattr(printer, "submit", submit)
    monkeypatch.setattr(printer, "monitor", monitor)
    args = ["print", "tech-icons", "--send"]
    if channel is not None:
        args.extend(["--channel", str(channel)])
    assert cli.main(args) == 130
    error = capsys.readouterr().err
    recovery = error.split("Check it with ", 1)[1].strip().removesuffix(".")
    assert cli.main(shlex.split(recovery)[3:]) == 0
    assert lookup.call_args.args == ("AA:BB:CC:DD:EE:FF", channel, 10)
    assert connection.__enter__.return_value.connect.call_args.args == (
        ("AA:BB:CC:DD:EE:FF", channel or 2),
    )
    submit.assert_called_once()
    monitor.assert_called_once()
    assert monitor.call_args.args[1] == 42


@pytest.mark.parametrize("option", ["--s", "--se", "--sen"])
def test_send_requires_full_option(option, monkeypatch, capsys, tmp_path):
    connect = Mock(side_effect=AssertionError("Bluetooth must not be accessed"))
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", connect)
    with pytest.raises(SystemExit) as error:
        cli.main(["print", "tech-icons", option, "--log", str(tmp_path / "run.jsonl")])
    assert error.value.code == 2
    connect.assert_not_called()
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("args", [[], ["help"], ["-h"], ["--help"], ["-help"]])
def test_main_help_is_consistent(args, capsys):
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert "StickerPix — print and cut from Linux" in output.out
    assert "Usage: uv run crycut" in output.out
    assert not output.err


def test_help_for_print_never_submits(monkeypatch, capsys):
    send = Mock(side_effect=AssertionError("must not submit"))
    monkeypatch.setattr(printer, "submit", send)
    with pytest.raises(SystemExit) as error:
        cli.main(["help", "print", "tech-icons", "--send"])
    assert error.value.code == 0
    send.assert_not_called()
    assert "--send" in capsys.readouterr().out


def test_transcript_write_failure_preserves_confirmed_success(
    tmp_path, monkeypatch, capsys, printer_config
):
    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(printer.socket, "socket", Mock(return_value=connection))
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", Mock(return_value=2))
    monkeypatch.setattr(printer, "Client", Mock(return_value=Mock(events=[])))
    submit = Mock(return_value={"job-state": 9})
    monkeypatch.setattr(printer, "submit", submit)
    original = Path.write_text

    def write(path, *args, **kwargs):
        if path.name.endswith(".protocol.json"):
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    assert (
        printer.main(
            [
                "tech-icons",
                "--send",
                "--log",
                str(tmp_path / "run.jsonl"),
            ]
        )
        == 0
    )
    submit.assert_called_once()
    assert "protocol" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("failure", ["accepted", "job", "flush", "close"])
def test_event_log_failure_does_not_hide_completed_job(
    failure, tmp_path, monkeypatch, capsys, printer_config
):
    import io
    import json

    class FailingLog(io.StringIO):
        fail_flush = False

        def write(self, text):
            event = json.loads(text)["event"]
            if event == failure:
                raise OSError("disk full")
            self.fail_flush = event == "job" and failure == "flush"
            return super().write(text)

        def flush(self):
            if self.fail_flush:
                raise OSError("disk full")
            return super().flush()

        def close(self):
            super().close()
            if failure == "close":
                raise OSError("disk full")

    stream = FailingLog()
    log_path = tmp_path / "run.jsonl"
    original = Path.open

    def open_log(path, *args, **kwargs):
        return stream if path == log_path else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_log)
    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(printer.socket, "socket", Mock(return_value=connection))
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", Mock(return_value=2))
    monkeypatch.setattr(printer, "Client", Mock(return_value=Mock(events=[])))

    def complete(client, sheet, emit, **kwargs):
        emit("accepted", job_id=42)
        emit("job", **{"job-id": 42, "job-state": 9})
        return {"job-id": 42, "job-state": 9}

    submit = Mock(side_effect=complete)
    monkeypatch.setattr(printer, "submit", submit)
    assert (
        printer.main(
            [
                "tech-icons",
                "--send",
                "--log",
                str(log_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "Completed" in output.out
    assert "event log" in output.err.lower()
    assert "may still be running" not in output.err
    assert stream.closed
    submit.assert_called_once()
    assert log_path.with_suffix(".protocol.json").is_file()


@pytest.mark.parametrize("command", ["status", "print"])
@pytest.mark.parametrize(
    "config", [None, [], {"address": "AA:BB:CC:DD:EE:FF", "name": []}]
)
def test_invalid_printer_config_reports_error_before_connecting(
    command, config, tmp_path, monkeypatch, capsys
):
    import json

    from crycut.commands import status

    config_path = tmp_path / "printer.json"
    config_path.write_text(json.dumps(config))
    adapter = status if command == "status" else printer
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(adapter, "resolve_rfcomm_channel", connect)
    monkeypatch.setattr(adapter, "PRINTER_CONFIG", config_path)
    args = []
    if command == "print":
        args = ["tech-icons", "--send", *args, "--log", str(tmp_path / "run.jsonl")]
    assert adapter.main(args) == 1
    connect.assert_not_called()
    assert "config" in capsys.readouterr().err.lower()


def test_offline_preview_works_before_setup(tmp_path, monkeypatch, capsys):
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", connect)
    monkeypatch.setattr(printer, "PRINTER_CONFIG", tmp_path / "missing.json")
    args = ["tech-icons"]
    assert printer.main(args) == 0
    assert "Print preview" in capsys.readouterr().out
    assert printer.main([*args, "--json"]) == 0
    import json

    assert json.loads(capsys.readouterr().out)["address"] is None
    connect.assert_not_called()


def test_status_without_configuration_suggests_setup(tmp_path, monkeypatch, capsys):
    from crycut.commands import status

    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(status, "resolve_rfcomm_channel", connect)
    monkeypatch.setattr(status, "PRINTER_CONFIG", tmp_path / "missing.json")
    assert status.main([]) == 1
    assert "crycut setup" in capsys.readouterr().err
    connect.assert_not_called()
