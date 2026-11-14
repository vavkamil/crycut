"""Render print progress and tick the local countdown independently of polling."""

import math
import shutil
import threading
import time
from typing import TextIO

from .jobs import JOB_STATES
from .models import JsonObject


def duration(seconds: float) -> str:
    """Format a nonnegative duration as minutes and seconds."""
    minutes, seconds = divmod(max(0, round(seconds)), 60)
    return f"{minutes}:{seconds:02d}"


def message(event: str, details: JsonObject, *, cut_total: int | None = None) -> str:
    """Format one printer event for terminal output."""
    if event == "connecting":
        return f"{'Status:':<15}Connecting…"
    if event == "connected":
        return f"{'Status:':<15}Connected"
    if event == "upload":
        fraction = min(1, max(0, details["sent"] / details["total"]))
        filled = int(fraction * 16)
        bar = "=" * filled + "-" * (16 - filled)
        return f"{'Upload:':<15}[{bar}] {int(fraction * 100):3d}%"
    if event == "progress":
        remaining = details["estimated_remaining_seconds"]
        if remaining is None:
            eta = "time remaining unknown"
        elif remaining == 0:
            eta = "taking longer than estimated"
        else:
            eta = f"~{duration(remaining)} remaining"
        contours = details.get("cut_contours")
        progress = details.get("cutting_progress")
        cut = ""
        if progress is not None and (
            contours or progress or details.get("stage") == "Cutting"
        ):
            if cut_total is not None and progress <= cut_total:
                cut = f" | {progress}/{cut_total} cuts"
            else:
                cut = f" | {progress} {'cut' if progress == 1 else 'cuts'}"
                if cut_total is not None:
                    cut += f" (expected {cut_total})"
        state = JOB_STATES.get(details["job_state"], str(details["job_state"]))
        alerts = details.get("alerts_raw")
        alert_text = f" | alerts {alerts}" if alerts and alerts != ":0:0" else ""
        stage = details["stage"]
        if details["job_state"] not in (2, 3):
            stage = f"{state}: {stage}"
        return f"{'Status:':<15}{stage}{alert_text}{cut} | {eta}"
    if event == "job":
        state = JOB_STATES.get(details.get("job-state"), "Unknown")
        return f"{'Result:':<15}{state} (job {details.get('job-id')})"
    if event == "accepted":
        return f"{'Job:':<15}{details['job_id']}"
    if event == "submitting":
        return f"{'Status:':<15}Submitting sheet…"
    if event == "uploaded":
        return f"{'Status:':<15}Upload complete; waiting for printer…"
    return f"{event}: {details}"


class ProgressDisplay:
    """Refresh progress on a terminal; preserve ordinary lines when redirected."""

    def __init__(self, stream: TextIO, *, cut_total: int | None = None) -> None:
        """Prepare a display with an optional known outline count."""
        self.stream = stream
        self.interactive = stream.isatty()
        self.active = False
        self.cut_total = cut_total
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress: JsonObject | None = None
        self._deadline: float | None = None
        self._last_text: str | None = None

    def show(self, event: str, details: JsonObject) -> None:
        """Show an event and start local countdown updates when appropriate."""
        with self._lock:
            if self.interactive and event == "progress":
                self._progress = dict(details)
                remaining = details["estimated_remaining_seconds"]
                if remaining is None:
                    self._deadline = None
                elif self._deadline is None:
                    # Keep one local deadline across polls so network latency
                    # and rounded estimates cannot restart or jump the clock.
                    self._deadline = time.monotonic() + remaining
                self._redraw_progress()
                if self._deadline is not None and self._thread is None:
                    self._thread = threading.Thread(target=self._run_clock, daemon=True)
                    self._thread.start()
            else:
                self._progress = None
                self._deadline = None
                self._write(event, message(event, details, cut_total=self.cut_total))

    def _run_clock(self) -> None:
        while not self._stop.wait(1):
            self._tick()

    def _tick(self) -> None:
        with self._lock:
            if self._progress is not None:
                self._redraw_progress()

    def _redraw_progress(self) -> None:
        if self._progress is None:
            return
        details = dict(self._progress)
        if self._deadline is not None:
            details["estimated_remaining_seconds"] = max(
                0, math.ceil(self._deadline - time.monotonic())
            )
        self._write("progress", message("progress", details, cut_total=self.cut_total))

    def _write(self, event: str, text: str) -> None:
        if self.interactive:
            if event in (
                "connecting",
                "connected",
                "submitting",
                "upload",
                "uploaded",
                "progress",
            ):
                # Keep the line from wrapping, so the next update can replace it.
                width = max(1, shutil.get_terminal_size().columns - 1)
                if len(text) > width:
                    text = text[: max(0, width - 3)] + "." * min(3, width)
                if text == self._last_text:
                    return
                self._last_text = text
                self.stream.write("\r\033[2K" + text)
                self.stream.flush()
                self.active = True
                return
            if self.active:
                self.stream.write("\r\033[2K")
                self.active = False
            self._last_text = None
        print(text, file=self.stream, flush=True)

    def finish(self) -> None:
        """Stop the countdown thread and release the active terminal line."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self._progress = None
            self._deadline = None
            if self.active:
                print(file=self.stream, flush=True)
                self.active = False
