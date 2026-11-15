"""Terminal rendering stays independent of printer polling and raw events."""

import io
import threading
import unittest
from unittest.mock import patch

import pytest

from terminal_helpers import visible
from crycut.progress import ProgressDisplay, message
from crycut.ui import console

pytestmark = pytest.mark.usefixtures("manual_live_refresh")


class Terminal(io.StringIO):
    def isatty(self):
        return True


def progress(remaining=5, **fields):
    return dict(
        job_state=3,
        stage="Cutting",
        estimated_remaining_seconds=remaining,
        cutting_progress=1,
        cut_contours=7,
        **fields,
    )


class ProgressTests(unittest.TestCase):
    def test_alerts_refresh_while_waiting_for_sheet_removal_and_next_copy(self):
        display = ProgressDisplay(Terminal(), cut_total=32)
        self.addCleanup(display.finish)

        def snapshot():
            stream = io.StringIO()
            console(stream).print(display._live.renderable)
            return visible(stream.getvalue())

        event = {
            **progress(None),
            "stage": "Retracting blade",
            "copies": 3,
            "copy_number": 1,
            "estimated_sheet_seconds": 120,
            "cutting_progress": 32,
            "paper_near_blade": True,
            "alerts_raw": ":8401:0",
        }
        with patch("crycut.progress.time.monotonic", return_value=100) as clock:
            display.show(
                "progress",
                {
                    **event,
                    "paper_near_blade": False,
                    "alerts_raw": ":0:0",
                    "estimated_remaining_seconds": 5,
                },
            )
            progress_row = next(
                i
                for i, line in enumerate(snapshot().splitlines())
                if "Progress:" in line
            )
            display.show("progress", event)
            self.assertIn("Paper near blade", snapshot())
            self.assertIn("Copies:           1/3", snapshot())
            self.assertNotIn("32/32 cuts", snapshot())
            self.assertIn("Progress:", snapshot().splitlines()[progress_row])
            self.assertNotIn("Alert:", snapshot())
            self.assertEqual(snapshot().count("Paper near blade"), 1)
            self.assertIsNone(display._deadline)

            # The main status remains identical as another warning appears
            # and clears; the separate alert row must still update.
            event["alerts_raw"] = ":8401:7001"
            display.show("progress", event)
            self.assertIn("Cover open", snapshot())
            self.assertEqual(snapshot().count("Paper near blade"), 1)
            self.assertGreater(
                snapshot().index("Alert:"), snapshot().index("Progress:")
            )
            self.assertIn("Progress:", snapshot().splitlines()[progress_row])
            event["alerts_raw"] = ":8401:0"
            display.show("progress", event)
            self.assertNotIn("Cover open", snapshot())
            self.assertNotIn("Alert:", snapshot())
            self.assertIn("Paper near blade", snapshot())

            before = snapshot()
            clock.return_value = 200
            display._tick()
            self.assertEqual(snapshot(), before)
            self.assertIsNone(display._deadline)

            # Removing the sheet clears the alert without advancing the copy.
            event.update(
                paper_near_blade=False, alerts_raw=":0:0", estimated_remaining_seconds=5
            )
            display.show("progress", event)
            self.assertNotIn("Paper near blade", snapshot())
            self.assertNotIn("Alert:", snapshot())
            self.assertIn("Progress:", snapshot().splitlines()[progress_row])
            self.assertIn("Copies:           1/3", snapshot())

            # Only the printer's next-copy event restarts the sheet estimate.
            event.update(
                copy_number=2, stage="Preparing", estimated_remaining_seconds=120
            )
            display.show("progress", event)
            self.assertIn("Copies:           2/3", snapshot())
            self.assertIn("Preparing", snapshot())
            self.assertIn("~2:00 remaining", snapshot())
            clock.return_value = 201
            display._tick()
            self.assertIn("~1:59 remaining", snapshot())

    def test_cut_details_are_hidden_during_sheet_removal_and_return_on_resume(self):
        stream = Terminal()
        display = ProgressDisplay(stream, cut_total=32)
        self.addCleanup(display.finish)
        event = progress(None, paper_near_blade=True)
        display.show("progress", event)
        stream.seek(0)
        stream.truncate()
        event["cutting_progress"] = 2
        display.show("progress", event)
        self.assertNotIn("2/32 cuts", visible(stream.getvalue()))
        event["paper_near_blade"] = False
        display.show("progress", event)
        self.assertIn("2/32 cuts", visible(stream.getvalue()))

    def test_clock_refreshes_without_another_printer_event(self):
        updated = threading.Event()
        main_thread = threading.get_ident()

        class ObservedTerminal(Terminal):
            def write(self, text):
                result = super().write(text)
                if threading.get_ident() != main_thread and "Status:" in text:
                    updated.set()
                return result

        stream = ObservedTerminal()
        display = ProgressDisplay(stream, cut_total=32)
        try:
            display.show("progress", progress(30))
            self.assertTrue(updated.wait(5), "No local countdown update arrived")
        finally:
            display.finish()
        self.assertIn("1/32 cuts", visible(stream.getvalue()))
        self.assertNotIn("Completed", stream.getvalue())
        self.assertFalse(display._thread.is_alive())

    def test_local_ticks_keep_deadline_across_polls_and_preserve_event(self):
        stream = Terminal()
        display = ProgressDisplay(stream, cut_total=32)
        self.addCleanup(display.finish)
        event = progress()
        with patch("crycut.progress.time.monotonic", return_value=100) as clock:
            display.show("progress", event)
            clock.return_value = 101
            display._tick()
            self.assertIn("~0:04 remaining", visible(stream.getvalue()))
            clock.return_value = 102
            display.show("progress", progress(4))  # Rounded/delayed poll.
            self.assertTrue(visible(stream.getvalue()).endswith("~0:03 remaining"))
            clock.return_value = 103
            display._tick()
            self.assertTrue(visible(stream.getvalue()).endswith("~0:02 remaining"))
            self.assertEqual(event["estimated_remaining_seconds"], 5)
            self.assertEqual(event["cut_contours"], 7)
            clock.return_value = 110
            display._tick()
            self.assertTrue(
                visible(stream.getvalue()).endswith("taking longer than estimated")
            )
            self.assertNotIn("Completed", stream.getvalue())

    def test_pause_completion_and_cleanup_stop_countdown(self):
        stream = Terminal()
        display = ProgressDisplay(stream, cut_total=32)
        self.addCleanup(display.finish)
        with patch("crycut.progress.time.monotonic", return_value=100) as clock:
            display.show("progress", progress())
            paused = {**progress(None), "job_state": 5}
            display.show("progress", paused)
            before = stream.getvalue()
            clock.return_value = 200
            display._tick()
            self.assertEqual(stream.getvalue(), before)
            self.assertIn("Interrupted", before)
            display.show("progress", progress(10))
            clock.return_value = 201
            display._tick()
            self.assertTrue(visible(stream.getvalue()).endswith("~0:09 remaining"))
            display.show("job", {"job-id": 30, "job-state": 9})
            before = stream.getvalue()
            display._tick()
            self.assertEqual(stream.getvalue(), before)
        display.finish()
        self.assertFalse(display._thread.is_alive())
        self.assertTrue(visible(stream.getvalue()).endswith("Completed 🎉"))
        self.assertIn("\x1b[?25h", stream.getvalue())
        self.assertFalse(display._live.is_started)

    def test_redirected_output_does_not_animate(self):
        stream = io.StringIO()
        display = ProgressDisplay(stream, cut_total=32)
        display.show("progress", progress())
        before = stream.getvalue()
        display._tick()
        display.finish()
        self.assertIsNone(display._thread)
        self.assertEqual(stream.getvalue(), before)
        self.assertNotIn("\x1b", before)
        self.assertIn("1/32 cuts", before)

    def test_captured_cut_counts_use_fixed_plt_total(self):
        # Observed (completed, reported total) pairs from jobs 30 and 29.
        # Keep the regression independent of deletable runtime logs.
        captures = [
            (32, [(0, 4), (0, 5), (1, 6), (2, 7), (3, 7), (31, 32), (32, 32)]),
            (28, [(0, 7), (1, 7), (1, 8), (2, 8), (2, 9), (27, 28), (28, 28)]),
        ]
        for total, samples in captures:
            for completed, reported_total in samples:
                event = {
                    **progress(),
                    "cutting_progress": completed,
                    "cut_contours": reported_total,
                }
                self.assertIn(
                    f"{completed}/{total} cuts",
                    message("progress", event, cut_total=total),
                )

    def test_unknown_total_or_unexpected_count_is_not_misrepresented(self):
        self.assertIn(" | 1 cut |", message("progress", progress()))
        event = {**progress(), "cutting_progress": 33}
        text = message("progress", event, cut_total=32)
        self.assertIn("33 cuts (expected 32)", text)
        self.assertNotIn("32/32", text)
