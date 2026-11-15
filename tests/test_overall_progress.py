"""Overall progress advances within sheets and waits for printer completion."""

import io
import re
from unittest.mock import patch

import pytest

from terminal_helpers import visible
from crycut.progress import ProgressDisplay

pytestmark = pytest.mark.usefixtures("manual_live_refresh")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def progress(copy_number=1, copies=3, remaining=120, **fields):
    return dict(
        job_state=3,
        stage="Preparing",
        copy_number=copy_number,
        copies=copies,
        estimated_sheet_seconds=120,
        estimated_remaining_seconds=remaining,
        **fields,
    )


def percent(stream):
    return int(re.findall(r"Progress:[^\n]*?([0-9]+)%", visible(stream.getvalue()))[-1])


@pytest.mark.parametrize("copies", [1, 2, 3])
def test_local_ticks_fill_each_sheet_share_without_waiting_for_another_poll(copies):
    stream = Terminal()
    display = ProgressDisplay(stream)
    try:
        with patch("crycut.progress.time.monotonic", return_value=0) as clock:
            display.show("accepted", {"job_id": 42, "copies": copies})
            display.show("uploaded", {"estimated_execution_seconds": 120})
            assert percent(stream) == 0
            for copy in range(1, copies + 1):
                start = (copy - 1) * 120
                clock.return_value = start
                display.show("progress", progress(copy, copies))
                values = [percent(stream)]
                for elapsed in range(1, 121):
                    clock.return_value = start + elapsed
                    display._tick()
                    values.append(percent(stream))
                assert values == sorted(values)
                assert len(set(values)) >= 30
                assert values[60] == int(100 * (copy - 0.5) / copies)
                assert values[-1] == min(99, int(100 * copy / copies))
            assert "100%" not in stream.getvalue()
            display.show("job", {"job-id": 42, "job-state": 9})
            assert percent(stream) == 100
            assert "Completed 🎉" in stream.getvalue()
            before = stream.getvalue()
            clock.return_value += 1000
            display._tick()
            assert stream.getvalue() == before
    finally:
        display.finish()


@pytest.mark.parametrize("pause", [{"paper_near_blade": True}, {"job_state": 5}])
def test_pause_freezes_bar_and_clearing_it_resumes_without_reset(pause):
    stream = Terminal()
    display = ProgressDisplay(stream)
    try:
        with patch("crycut.progress.time.monotonic", return_value=0) as clock:
            display.show("progress", progress())
            clock.return_value = 60
            display._tick()
            assert percent(stream) == 16
            display.show("progress", {**progress(remaining=None), **pause})
            before = stream.getvalue()
            clock.return_value = 600
            display._tick()
            assert stream.getvalue() == before
            display.show("progress", progress(remaining=60))
            assert percent(stream) == 16
            clock.return_value = 630
            display._tick()
            assert percent(stream) == 25
    finally:
        display.finish()


@pytest.mark.parametrize("terminal_state", [7, 8, None])
def test_overrun_stays_below_completion_on_failure_cancellation_or_timeout(
    terminal_state,
):
    stream = Terminal()
    display = ProgressDisplay(stream)
    try:
        with patch("crycut.progress.time.monotonic", return_value=0) as clock:
            display.show("progress", progress(copies=1))
            clock.return_value = 500
            display._tick()
            assert percent(stream) == 99
            assert "taking longer than estimated" in stream.getvalue()
            if terminal_state is not None:
                display.show("job", {"job-id": 42, "job-state": terminal_state})
            else:
                display.finish()
            assert percent(stream) == 99
            assert "100%" not in stream.getvalue()
    finally:
        display.finish()


def test_poll_corrections_never_move_overall_progress_backward():
    stream = io.StringIO()
    display = ProgressDisplay(stream)
    display.show("progress", progress(remaining=30))
    assert percent(stream) == 25
    display.show("progress", progress(remaining=60))
    assert percent(stream) == 25
    display.show("progress", progress(copy_number=2))
    assert percent(stream) == 33
    display.show("progress", progress(copy_number=2, remaining=60))
    assert percent(stream) == 50
    assert display._thread is None
    assert "\033" not in stream.getvalue()
    display.finish()


def test_reconnected_monitor_without_estimate_does_not_invent_progress():
    stream = Terminal()
    display = ProgressDisplay(stream)
    display.show(
        "progress", {**progress(remaining=None), "estimated_sheet_seconds": None}
    )
    display.show("job", {"job-id": 42, "job-state": 9})
    display.finish()
    assert "Progress:" not in stream.getvalue()


def test_live_display_keeps_progress_below_status_and_clears_all_rows():
    stream = Terminal()
    display = ProgressDisplay(stream)
    display.console.width = 80
    try:
        display.show("progress", progress())
        text = visible(stream.getvalue())
        assert (
            "Copies:           1/3\n\n  Status:           Preparing | ~2:00 remaining"
            in text
        )
        assert "Time:" not in text
        assert text.index("Status:") < text.index("Progress:")
        assert "remaining\n\n  Progress:" in text
        assert "% estimated" not in text
        stream.seek(0)
        stream.truncate()
        display.show("progress", progress(copy_number=2))
        assert stream.getvalue().count("\033[1A") == 4
        assert "Copies:           2/3" in visible(stream.getvalue())
    finally:
        display.finish()
