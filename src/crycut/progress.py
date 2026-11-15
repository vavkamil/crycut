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


def progress_bar(label: str, fraction: float) -> str:
    """Render a bounded fraction using the same bar for upload and printing."""
    fraction = min(1, max(0, fraction))
    filled = int(fraction * 16)
    bar = "=" * filled + "-" * (16 - filled)
    return f"{label + ':':<15}[{bar}] {int(fraction * 100):3d}%"


def message(event: str, details: JsonObject, *, cut_total: int | None = None) -> str:
    """Format one printer event for terminal output."""
    if event == "connecting":
        return f"{'Status:':<15}Connecting…"
    if event == "connected":
        return f"{'Status:':<15}Connected"
    if event == "upload":
        return progress_bar("Upload", details["sent"] / details["total"])
    if event == "progress":
        if details.get("paper_near_blade"):
            return f"{'Status:':<15}Paper near blade"
        remaining = details["estimated_remaining_seconds"]
        if remaining is None:
            eta = "time remaining unknown"
        elif remaining == 0:
            eta = "taking longer than estimated"
        else:
            eta = f"~{duration(remaining)} remaining"
        progress = details.get("cutting_progress")
        cut = ""
        if progress is not None and details.get("stage") in (
            "Cutting",
            "Retracting blade",
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
        self._copies: int | None = None
        self._copy_number = 0
        self._active_lines = 0
        self._sheet_estimate: float | None = None
        self._overall_fraction: float | None = None

    def show(self, event: str, details: JsonObject) -> None:
        """Show an event and start local countdown updates when appropriate."""
        with self._lock:
            copies = details.get("copies", self._copies)
            copy_number = details.get("copy_number", self._copy_number)
            if (copies, copy_number) != (self._copies, self._copy_number):
                self._deadline = None
                self._copies, self._copy_number = copies, copy_number
                if not self.interactive and copies is not None:
                    self._write("copies", self._copy_text())
            estimate = details.get("estimated_sheet_seconds")
            if event == "uploaded":
                estimate = details.get("estimated_execution_seconds")
            if estimate is not None and estimate > 0 and self._copies:
                self._sheet_estimate = estimate
                if self._overall_fraction is None:
                    self._overall_fraction = 0.0
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
                self._write(event, self._message(event, details))

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
        self._write("progress", self._message("progress", details))

    def _message(self, event: str, details: JsonObject) -> str:
        text = message(event, details, cut_total=self.cut_total)
        if self._overall_fraction is None:
            return text
        if event == "progress":
            remaining = details["estimated_remaining_seconds"]
            if remaining is not None and self._copy_number > 0:
                assert self._sheet_estimate is not None and self._copies is not None
                sheet_fraction = min(1, max(0, 1 - remaining / self._sheet_estimate))
                fraction = (self._copy_number - 1 + sheet_fraction) / self._copies
                # Estimates may overrun or be corrected by a poll. Never go
                # backward or claim completion before the printer confirms it.
                self._overall_fraction = max(
                    self._overall_fraction, min(0.99, fraction)
                )
        elif event == "job" and details.get("job-state") == 9:
            self._overall_fraction = 1.0
        if event in ("uploaded", "progress", "job"):
            text += "\n" + progress_bar("Progress", self._overall_fraction)
        return text

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
                lines = text.splitlines()
                if self._copies is not None:
                    lines.insert(0, self._copy_text())
                lines = [
                    line
                    if len(line) <= width
                    else line[: max(0, width - 3)] + "." * min(3, width)
                    for line in lines
                ]
                text = "\n".join(lines)
                if text == self._last_text:
                    return
                self._last_text = text
                self._clear_active()
                self.stream.write(text)
                self.stream.flush()
                self.active = True
                self._active_lines = len(lines)
                return
            if self.active:
                self._clear_active()
            self._last_text = None
        print(text, file=self.stream, flush=True)

    def _copy_text(self) -> str:
        return f"{'Copies:':<15}{self._copy_number}/{self._copies}"

    def _clear_active(self) -> None:
        self.stream.write("\r\033[2K")
        for _ in range(max(0, self._active_lines - 1)):
            self.stream.write("\033[1A\r\033[2K")
        self.active = False
        self._active_lines = 0

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
