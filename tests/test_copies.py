"""Multiple sheets share one upload and use printer-reported copy progress."""

import io
import json
from unittest.mock import Mock, PropertyMock, patch

import pytest

from crycut import jobs
from crycut.commands import print as printer
from crycut.progress import ProgressDisplay, message
from crycut.sheets import PrintSheet
from terminal_helpers import visible

pytestmark = pytest.mark.usefixtures("manual_live_refresh")


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "three"])
def test_invalid_quantity_never_connects(value):
    with patch.object(printer.socket, "socket") as connect:
        with pytest.raises(SystemExit) as error:
            printer.main(["tech-icons", "--send", "--copies", value])
    assert error.value.code == 2
    connect.assert_not_called()


def test_monitor_cannot_set_quantity():
    with pytest.raises(SystemExit):
        printer._parse_args(["--monitor", "42", "--copies", "3"])


def test_multiple_copy_preview_is_offline(capsys):
    with patch.object(printer.socket, "socket") as connect:
        assert printer.main(["tech-icons", "--copies", "3", "--json"]) == 0
        preview = json.loads(capsys.readouterr().out)
        assert preview["copies"] == 3
        assert preview["cut_outlines"] == 32
        assert printer.main(["tech-icons", "--copies", "3"]) == 0
    connect.assert_not_called()
    assert "per sheet" in capsys.readouterr().out


@pytest.mark.parametrize("max_wait, expected_wait", [(None, 2700), (60, 60)])
def test_three_copies_submit_one_job_and_one_upload(max_wait, expected_wait):
    client = Mock(key=b"0123456789ABCDEF")
    client.request.side_effect = [{"result": [20]}, {"result": {"job-id": 42}}]
    sheet = PrintSheet(b"image", b"cut", 1, "example")
    with (
        patch.object(
            PrintSheet,
            "timing",
            new_callable=PropertyMock,
            return_value={"total_seconds": 120},
        ),
        patch.object(jobs, "monitor", return_value={"job-state": 9}) as monitor,
        patch.object(jobs.time, "sleep"),
    ):
        jobs.submit(client, sheet, Mock(), copies=3, max_wait=max_wait)
    assert client.request.call_count == 2
    method, entries = client.request.call_args.args
    assert method == "combo-job"
    assert [entry["method"] for entry in entries] == ["print-job", "cut-job"]
    assert [entry["params"]["copies"] for entry in entries] == [3, 3]
    client.send.assert_called_once()
    assert monitor.call_args.args[1] == 42
    assert monitor.call_args.kwargs == {
        "interval": 2,
        "max_wait": expected_wait,
        "estimate_seconds": 120,
        "copies": 3,
    }


def test_copies_flow_from_cli_into_submission_and_log(tmp_path, capsys):
    client = Mock(events=[])
    log = tmp_path / "print.jsonl"
    with (
        patch.object(printer.socket, "socket"),
        patch.object(printer, "resolve_rfcomm_channel", return_value=2),
        patch.object(printer, "Client", return_value=client),
        patch.object(
            printer, "PRINTER_CONFIG", printer.ROOT / "config/printer.example.json"
        ),
        patch.object(printer, "submit", return_value={"job-state": 9}) as submit,
    ):
        assert (
            printer.main(["tech-icons", "--copies", "3", "--send", "--log", str(log)])
            == 0
        )
    assert submit.call_args.kwargs["copies"] == 3
    assert json.loads(log.read_text().splitlines()[0])["copies"] == 3
    assert "per sheet" in capsys.readouterr().out


def test_counter_and_estimate_follow_printer_through_three_copies(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(jobs.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        jobs.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    # A blade alert need not change job-state. Clearing it alone must not
    # advance the copy or reset the estimate. Repeated/stale page values
    # must not reset the current sheet's clock either.
    samples = iter(
        [
            (0, 3016, ":0:0"),
            (1, 3015, ":0:0"),
            (1, 3030, ":0:0"),
            (1, 3031, ":8401:1"),
            (1, 3031, ":8401:1"),
            (1, 3031, ":0:0"),
            (2, 3015, ":0:0"),
            (2, 3018, ":0:0"),
            (1, 3018, ":0:0"),
            (0, 3018, ":0:0"),
            (3, 3015, ":0:0"),
            (3, 3030, ":0:0"),
        ]
    )
    client = Mock(timeout=10, notifications=[])
    status = None

    def request(method, params):
        nonlocal status
        if method == "get-job-info":
            assert params == {"job-id": 42}
            sample = next(samples, None)
            if sample is None:
                return {"result": [{"job-id": 42, "job-state": 9}]}
            page, stage, alerts = sample
            status = [40, stage, alerts]
            return {
                "result": [
                    {
                        "job-id": 42,
                        "job-state": 3,
                        "copies": 3,
                        "printing-page-number": page,
                        "cutting-progress": 4,
                        "cut-contours": 32,
                    }
                ]
            }
        assert method == "get-prop"  # No resume or additional submission.
        return {"result": status}

    client.request.side_effect = request
    events = []
    result = jobs.monitor(
        client,
        42,
        lambda event, **data: events.append((event, data)),
        interval=10,
        estimate_seconds=120,
    )
    progress = [data for event, data in events if event == "progress"]
    assert [row["copy_number"] for row in progress] == [
        0,
        1,
        1,
        1,
        1,
        1,
        2,
        2,
        2,
        2,
        3,
        3,
    ]
    assert all(row["copies"] == 3 for row in progress)
    assert all(row["estimated_sheet_seconds"] == 120 for row in progress)
    assert [row["estimated_remaining_seconds"] for row in progress] == [
        None,
        120,
        110,
        None,
        None,
        100,
        120,
        110,
        100,
        90,
        120,
        110,
    ]
    assert progress[1]["stage"] == "Preparing"
    assert (
        message("progress", progress[3], cut_total=32).strip()
        == "Status:           Paper near blade"
    )
    assert "4/32 cuts" in message("progress", progress[-1], cut_total=32)
    assert "cuts" not in message("progress", progress[6], cut_total=32)
    assert result["job-state"] == 9
    assert events[-1][0] == "job"


def test_terminal_resets_clock_and_redraws_both_rows_on_next_copy():
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    stream = Terminal()
    display = ProgressDisplay(stream, cut_total=32)
    event = dict(
        job_state=3,
        stage="Preparing",
        copies=3,
        copy_number=1,
        estimated_remaining_seconds=120,
    )
    try:
        with patch("crycut.progress.time.monotonic", return_value=100) as clock:
            display.show("progress", event)
            assert "Copies:           1/3\n  Status:" in visible(stream.getvalue())
            clock.return_value = 200
            display.show("progress", {**event, "copy_number": 2})
            assert "\033[1A" in stream.getvalue()
            assert visible(stream.getvalue()).endswith("~2:00 remaining")
            clock.return_value = 201
            display._tick()
            assert visible(stream.getvalue()).endswith("~1:59 remaining")
            display.show(
                "progress",
                {
                    **event,
                    "copy_number": 2,
                    "paper_near_blade": True,
                    "estimated_remaining_seconds": None,
                },
            )
            before = stream.getvalue()
            clock.return_value = 300
            display._tick()
            assert stream.getvalue() == before
            assert "Status:           [!] Paper near blade" in visible(before)
            assert "Time remaining unknown" in visible(before)
    finally:
        display.finish()


def test_redirected_copy_counter_only_prints_when_it_changes():
    stream = io.StringIO()
    display = ProgressDisplay(stream)
    event = dict(
        job_state=3,
        stage="Preparing",
        copies=3,
        copy_number=1,
        estimated_remaining_seconds=120,
    )
    display.show("progress", event)
    display.show("progress", event)
    display.show("progress", {**event, "copy_number": 2})
    display.finish()
    assert stream.getvalue().count("Copies:") == 2
    assert "\033" not in stream.getvalue()
