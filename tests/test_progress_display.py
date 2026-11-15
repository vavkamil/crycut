"""Terminal rendering stays independent of printer polling and raw events."""

import io
import threading
import unittest
from unittest.mock import patch

import pytest

from terminal_helpers import visible
from crycut.progress import ProgressDisplay, message

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
        self.assertTrue(visible(stream.getvalue()).endswith("Completed (job 30)"))
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
