"""Offline playback uses the real display without contacting or writing to a printer."""

import io
import re
from unittest.mock import Mock

import pytest

from crycut import simulation
from crycut.commands import print as printer
from crycut.paths import ROOT
from crycut.progress import ProgressDisplay
from crycut.sheets import PrintSheet
from terminal_helpers import visible


class FastClock(simulation.PlaybackClock):
    def __init__(self, speed):
        super().__init__(speed)
        self.elapsed = 0.0
        self.waits = []

    def __call__(self):
        return self.elapsed

    def sleep(self, seconds):
        assert seconds > 0
        self.waits.append(seconds / self.speed)
        self.elapsed += seconds


@pytest.mark.parametrize("speed", [1, 10, 0.5])
def test_clock_scales_both_countdown_and_sleep(speed, monkeypatch):
    now = 100.0

    def sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(simulation.time, "monotonic", lambda: now)
    monkeypatch.setattr(simulation.time, "sleep", sleep)
    clock = simulation.PlaybackClock(speed)
    clock.sleep(20)
    assert now == pytest.approx(100 + 20 / speed)
    assert clock() == pytest.approx(20)


@pytest.mark.parametrize("speed", [1, 10])
def test_simulation_covers_stages_cuts_and_copy_removal(speed):
    sheet = PrintSheet.load(ROOT / "projects/wordmarks")
    clock = FastClock(speed)
    events = []
    simulation.simulate(
        sheet,
        lambda event, **details: events.append((event, details)),
        clock=clock,
        copies=2,
    )
    assert sum(clock.waits) == pytest.approx(
        (8 + 2 * sheet.estimate_seconds + 30) / speed
    )
    assert sum(event == "uploaded" for event, _ in events) == 1
    assert events[-1] == ("job", {"job-id": 1, "job-state": 9})
    progress = [details for event, details in events if event == "progress"]
    for copy in (1, 2):
        active = [
            row
            for row in progress
            if row["copy_number"] == copy and not row["paper_near_blade"]
        ]
        stages = list(dict.fromkeys(row["stage"] for row in active))
        assert stages == [
            "Decoding",
            "Preparing",
            "Printing yellow",
            "Returning home",
            "Printing magenta",
            "Printing cyan",
            "Protective layer",
            "Feeding for cutting",
            "Finding origin",
            "Cutting",
            "Retracting blade",
        ]
        remaining = [row["estimated_remaining_seconds"] for row in active]
        assert remaining[0] == sheet.estimate_seconds
        assert remaining == sorted(remaining, reverse=True)
        cuts = [row["cutting_progress"] for row in active if row["stage"] == "Cutting"]
        assert cuts == sorted(cuts)
        assert cuts[0] == 0 and cuts[-1] == sheet.cut_count
    pauses = [i for i, row in enumerate(progress) if row["paper_near_blade"]]
    assert len(pauses) == 1
    pause = progress[pauses[0]]
    assert pause["estimated_remaining_seconds"] is None
    assert pause["alerts_raw"] == ":8401:0"
    next_copy = progress[pauses[0] + 1]
    assert next_copy["copy_number"] == 2 and not next_copy["paper_near_blade"]
    assert next_copy["alerts_raw"] == ":0:0"


@pytest.mark.usefixtures("manual_live_refresh")
@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("interrupt", [False, True])
def test_cli_simulation_is_offline_and_cleans_up(
    interactive, interrupt, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    forbidden = Mock(side_effect=AssertionError("simulation must remain offline"))
    for name in (
        "load_printer",
        "resolve_rfcomm_channel",
        "Client",
        "submit",
        "monitor",
        "EventLog",
    ):
        monkeypatch.setattr(printer, name, forbidden)
    monkeypatch.setattr(printer.socket, "socket", forbidden)
    monkeypatch.setattr(printer, "ROOT", tmp_path)
    clock = FastClock(10)
    if interrupt:
        original_sleep = clock.sleep

        def sleep(seconds):
            if clock.elapsed > 100:
                raise KeyboardInterrupt
            original_sleep(seconds)

        monkeypatch.setattr(clock, "sleep", sleep)
    monkeypatch.setattr(printer, "PlaybackClock", lambda _: clock)
    output, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(output, "isatty", lambda: interactive)
    monkeypatch.setattr(printer.sys, "stdout", output)
    monkeypatch.setattr(printer.sys, "stderr", errors)
    display = ProgressDisplay(output, clock=clock)
    monkeypatch.setattr(printer, "ProgressDisplay", lambda *a, **kw: display)
    result = printer.main(["wordmarks", "--simulate", "--copies", "2"])
    assert result == (130 if interrupt else 0)
    text = visible(output.getvalue())
    assert "Print & cut (simulation, 10x)" in text
    assert "Printing yellow" in text
    if interrupt:
        assert "Simulation interrupted" in errors.getvalue()
        assert "may still be running" not in errors.getvalue()
        assert "crycut monitor" not in errors.getvalue()
    else:
        assert "Paper near blade" in text
        assert "2/2" in text
        assert "Completed" in text
        assert "Completed (job" not in text
        if interactive:
            assert "Completed 🎉" in text
        percentages = [int(p) for p in re.findall(r"(\d+)%", text)]
        assert percentages[-1] == 100
        assert not errors.getvalue()
    assert not display.active and not display._live.is_started
    assert display._thread is None or not display._thread.is_alive()
    if interactive:
        assert "\x1b[?25h" in output.getvalue()
    forbidden.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "options",
    [
        ["--simulate", "--send"],
        ["--simulate", "--monitor", "1"],
        ["--simulate", "--json"],
        ["--simulate", "--log", "demo.jsonl"],
        ["--simulate", "--channel", "2"],
        ["--simulate", "--max-wait", "30"],
        ["--speed", "1"],
        ["--simulate", "--speed", "0"],
        ["--simulate", "--speed", "-1"],
        ["--simulate", "--speed", "nan"],
        ["--simulate", "--speed", "inf"],
    ],
)
def test_invalid_simulation_options_fail_before_loading_a_sheet(options, monkeypatch):
    load = Mock(side_effect=AssertionError("must validate arguments first"))
    monkeypatch.setattr(printer.PrintSheet, "load", load)
    with pytest.raises(SystemExit) as error:
        printer.main(["wordmarks", *options])
    assert error.value.code == 2
    load.assert_not_called()


@pytest.mark.usefixtures("manual_live_refresh")
def test_live_countdown_uses_playback_time_between_events():
    clock = FastClock(10)
    stream = io.StringIO()
    stream.isatty = lambda: True
    display = ProgressDisplay(stream, clock=clock)
    try:
        display.show(
            "progress",
            {
                "job_state": 3,
                "stage": "Printing cyan",
                "copies": 1,
                "copy_number": 1,
                "estimated_sheet_seconds": 120,
                "estimated_remaining_seconds": 120,
            },
        )
        clock.sleep(60)
        display._tick()
        text = visible(stream.getvalue())
        assert "~1:00 remaining" in text
        assert "50%" in text
        assert "% estimated" not in text
    finally:
        display.finish()
