"""Cartridge advice is read once before submission and never blocks low counts."""

import io
import json
from unittest.mock import MagicMock, Mock, call

import pytest

from crycut import jobs
from crycut.commands import print as printer
from crycut.progress import ProgressDisplay
from crycut.sheets import PrintSheet
from crycut.ui import console
from terminal_helpers import visible


def ribbon_reply(count):
    return {"result": [{"ribbon-info": [0, 0, count, 0, 0, 0]}]}


@pytest.fixture
def printing(monkeypatch, printer_config, tmp_path):
    client = Mock(key=b"0123456789ABCDEF", events=[])
    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(printer, "Client", Mock(return_value=client))
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", Mock(return_value=2))
    monkeypatch.setattr(printer.socket, "socket", Mock(return_value=MagicMock()))
    sheet = PrintSheet(b"image", b"U0,0 D10,0 D10,10 D0,0", 1, "test-sheet")
    monkeypatch.setattr(printer.PrintSheet, "load", Mock(return_value=sheet))
    monkeypatch.setattr(jobs.time, "sleep", Mock())
    monkeypatch.setattr(
        "builtins.input", Mock(side_effect=AssertionError("must not prompt"))
    )

    def complete(client, job_id, emit, **kwargs):
        result = {"job-id": job_id, "job-state": 9}
        emit("job", **result)
        return result

    monkeypatch.setattr(jobs, "monitor", complete)
    return client, tmp_path / "run.jsonl"


@pytest.mark.parametrize(
    "remaining,copies,warning",
    [
        (0, 1, True),
        (1, 3, True),
        (2, 1, True),
        (3, 1, True),
        (4, 4, False),
        (4, 5, True),
        (36, 1, False),
        (36, 36, False),
    ],
)
def test_cartridge_advice_does_not_change_submission(
    printing, printer_config, capsys, remaining, copies, warning
):
    client, log = printing
    config_before = printer_config.read_bytes()
    client.request.side_effect = [
        {"result": [20]},
        ribbon_reply(remaining),
        {"result": {"job-id": 42}},
    ]

    assert (
        printer.main(
            ["wordmarks", "--send", "--copies", str(copies), "--log", str(log)]
        )
        == 0
    )

    output = capsys.readouterr()
    assert not output.err
    assert "Completed" in output.out
    assert client.request.call_args_list[:2] == [
        call("get-prop", ["printer-state"]),
        call("get-prop", ["ribbon-info"]),
    ]
    assert client.request.call_count == 3
    method, entries = client.request.call_args.args
    assert method == "combo-job"
    assert [entry["params"]["copies"] for entry in entries] == [copies, copies]
    client.send.assert_called_once()
    records = [json.loads(line) for line in log.read_text().splitlines()]
    warnings = [row for row in records if row["event"] == "cartridge_warning"]
    assert len(warnings) == int(warning)
    if warning:
        noun = "print" if remaining == 1 else "prints"
        text = f"[!] Cartridge reports {remaining} {noun} left."
        assert output.out.count(text) == 1
        assert output.out.index("Copies:") < output.out.index(text)
        assert output.out.index(text) < output.out.index("Submitting sheet")
        assert warnings[0]["prints_left"] == remaining
        events = [row["event"] for row in records]
        assert events.index("cartridge_warning") < events.index("submitting")
        assert "requested" not in output.out.lower()
    else:
        assert "Cartridge" not in output.out
    assert printer_config.read_bytes() == config_before


@pytest.mark.parametrize(
    "reply",
    [
        ribbon_reply(-1),
        ribbon_reply(None),
        ribbon_reply(True),
        ribbon_reply(1.5),
        ribbon_reply("invalid"),
        {"result": []},
        {"result": [None]},
        {"result": [{"ribbon-info": [0, 0, 2]}]},
        ValueError("Unsupported property"),
        TimeoutError("no reply"),
        ConnectionError("disconnected"),
    ],
)
def test_unavailable_count_warns_and_allows_submission(printing, capsys, reply):
    client, log = printing
    client.request.side_effect = [{"result": [20]}, reply, {"result": {"job-id": 42}}]

    assert printer.main(["wordmarks", "--send", "--log", str(log)]) == 0

    output = capsys.readouterr()
    assert output.out.count("[!] Cartridge prints left could not be determined.") == 1
    assert "Cartridge reports -" not in output.out
    assert not output.err
    assert client.request.call_count == 3
    assert client.request.call_args.args[0] == "combo-job"
    client.send.assert_called_once()
    records = [json.loads(line) for line in log.read_text().splitlines()]
    warning = next(row for row in records if row["event"] == "cartridge_warning")
    assert warning["prints_left"] is None


def test_interrupting_cartridge_query_does_not_submit(printing, capsys):
    client, log = printing
    client.request.side_effect = [{"result": [20]}, KeyboardInterrupt()]

    assert printer.main(["wordmarks", "--send", "--log", str(log)]) == 130

    assert client.request.call_count == 2
    assert client.request.call_args == call("get-prop", ["ribbon-info"])
    client.send.assert_not_called()
    assert "Submission outcome is unknown" not in capsys.readouterr().err


@pytest.mark.usefixtures("manual_live_refresh")
@pytest.mark.parametrize("interactive", [False, True])
def test_warning_persists_below_summary_through_progress_and_completion(interactive):
    stream = io.StringIO()
    stream.isatty = lambda: interactive
    display = ProgressDisplay(stream)
    display.console.width = 100
    warning = "[!] Cartridge reports 2 prints left."
    try:
        display.set_summary([("Sheet", "wordmarks")], copies=3)
        display.show("connected", {})
        display.show("cartridge_warning", {"prints_left": 2})
        for event, details in [
            ("submitting", {"copies": 3}),
            ("upload", {"sent": 1, "total": 2}),
            (
                "progress",
                {
                    "job_state": 3,
                    "stage": "Printing yellow",
                    "copies": 3,
                    "copy_number": 1,
                    "estimated_remaining_seconds": None,
                },
            ),
            ("job", {"job-id": 42, "job-state": 9}),
        ]:
            display.show(event, details)
            if interactive:
                snapshot = io.StringIO()
                console(snapshot).print(display._live.renderable)
                text = visible(snapshot.getvalue())
                assert text.count(warning) == 1
                assert text.index("Copies:") < text.index(warning)
                assert text.index(warning) < text.index(
                    "Upload:" if event == "upload" else "Status:"
                )
        if not interactive:
            assert stream.getvalue().count(warning) == 1
        assert not display.active
    finally:
        display.finish()
