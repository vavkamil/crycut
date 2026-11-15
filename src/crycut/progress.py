"""Render print progress and tick the local countdown independently of polling."""

import math
import threading
import time
from copy import deepcopy
from typing import TextIO

from rich.console import Group
from rich.emoji import Emoji
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .jobs import JOB_STATES
from .models import JsonObject
from .status import describe_alerts
from .ui import LABEL_WIDTH, console, field_value, fields, indent_text


def duration(seconds: float) -> str:
    """Format a nonnegative duration as minutes and seconds."""
    minutes, seconds = divmod(max(0, round(seconds)), 60)
    return f"{minutes}:{seconds:02d}"


def progress_bar(label: str, fraction: float) -> str:
    """Render a bounded fraction using the same bar for upload and printing."""
    fraction = min(1, max(0, fraction))
    filled = int(fraction * 16)
    bar = "=" * filled + "-" * (16 - filled)
    return f"{label + ':':<{LABEL_WIDTH + 2}}[{bar}] {int(fraction * 100):3d}%"


def message(event: str, details: JsonObject, *, cut_total: int | None = None) -> str:
    """Format one printer event for terminal output."""
    if event == "connecting":
        return f"{'Status:':<{LABEL_WIDTH + 2}}Connecting…"
    if event == "connected":
        return f"{'Status:':<{LABEL_WIDTH + 2}}Connected"
    if event == "connection_failed":
        return f"{'Status:':<{LABEL_WIDTH + 2}}Unavailable"
    if event == "upload":
        return progress_bar("Upload", details["sent"] / details["total"])
    if event == "progress":
        if details.get("paper_near_blade"):
            return f"{'Status:':<{LABEL_WIDTH + 2}}Paper near blade"
        remaining = details["estimated_remaining_seconds"]
        if remaining is None:
            eta = "Time unknown"
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
        return f"{'Status:':<{LABEL_WIDTH + 2}}{stage}{alert_text}{cut} | {eta}"
    if event == "job":
        state = JOB_STATES.get(details.get("job-state"), "Unknown")
        if details.get("job-state") == 9:
            return f"{'Status:':<{LABEL_WIDTH + 2}}Completed"
        return f"{'Status:':<{LABEL_WIDTH + 2}}{state} (job {details.get('job-id')})"
    if event == "accepted":
        return f"{'Job:':<{LABEL_WIDTH + 2}}{details['job_id']}"
    if event == "submitting":
        return f"{'Status:':<{LABEL_WIDTH + 2}}Submitting sheet…"
    if event == "uploaded":
        return f"{'Status:':<{LABEL_WIDTH + 2}}Upload complete; waiting for printer…"
    return f"{event}: {details}"


class ProgressDisplay:
    """Refresh progress on a terminal; preserve ordinary lines when redirected."""

    def __init__(self, stream: TextIO, *, cut_total: int | None = None) -> None:
        """Prepare a display with an optional known outline count."""
        self.stream = stream
        self.console = console(stream)
        self.interactive = (
            self.console.is_terminal and not self.console.is_dumb_terminal
        )
        self._live = Live(
            console=self.console,
            auto_refresh=True,
            refresh_per_second=4,
            vertical_overflow="visible",
        )
        self._details: JsonObject = {}
        self.active = False
        self.cut_total = cut_total
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress: JsonObject | None = None
        self._deadline: float | None = None
        self._last_frame: (
            tuple[str, int | None, int, int | None, float | None, JsonObject] | None
        ) = None
        self._copies: int | None = None
        self._copy_number = 0
        self._sheet_estimate: float | None = None
        self._overall_fraction: float | None = None
        self._summary: list[tuple[str, object]] = []
        self._log_path: str | None = None

    def set_summary(
        self,
        rows: list[tuple[str, object]],
        *,
        copies: int | None = None,
        log_path: str | None = None,
    ) -> None:
        """Keep the live copy counter with the sheet details above the status."""
        self._summary = list(rows)
        self._copies = copies
        self._log_path = log_path
        if not self.interactive:
            summary = list(rows)
            if copies is not None:
                summary.append(("Copies", copies))
            self.console.print(fields(summary))
            self.console.print()
            if log_path is not None:
                self.console.print(fields([("Log", log_path)]))
                self.console.print()

    def show(self, event: str, details: JsonObject) -> None:
        """Show an event and start local countdown updates when appropriate."""
        with self._lock:
            self._details = dict(details)
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
        self._details = details
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

    def _bar(self, fraction: float, *, style: str = "active") -> Table:
        fraction = min(1, max(0, fraction))
        bar = Table.grid(padding=(0, 1))
        bar.add_column(ratio=1)
        bar.add_column(no_wrap=True)
        bar.add_row(
            ProgressBar(
                total=100,
                completed=fraction * 100,
                width=32,
                style="progress.track",
                complete_style=style,
                finished_style="success",
            ),
            field_value(
                "Progress",
                f"{int(fraction * 100):3d}%",
            ),
        )
        return bar

    def _render(self, event: str, details: JsonObject) -> Group:
        rows: list[tuple[str, object]] = []
        alert_rows: list[tuple[str, object]] = []
        progress_style = "active"
        summary = list(self._summary)
        if self._copies is not None:
            copy = (
                f"{self._copy_number}/{self._copies}"
                if self._copy_number
                else f"{self._copies} queued"
            )
            summary.append(("Copies", copy))
        if event == "connection_failed":
            rows.append(("Status", Text("Unavailable", style="error")))
        elif event == "upload":
            rows.append(("Upload", self._bar(details["sent"] / details["total"])))
        elif event == "progress":
            stage = details["stage"]
            state = details["job_state"]
            warning = state not in (2, 3) or details.get("paper_near_blade")
            progress_style = (
                "warning"
                if warning
                else {
                    "Printing yellow": "stage.yellow",
                    "Printing magenta": "stage.magenta",
                    "Printing cyan": "stage.cyan",
                }.get(stage, "stage")
            )
            if details.get("paper_near_blade"):
                stage = "Paper near blade"
            elif state not in (2, 3):
                stage = f"{JOB_STATES.get(state, state)}: {stage}"
            status = Text()
            status.append(
                ("[!] " if warning else "") + stage,
                style=progress_style,
            )
            alerts = details.get("alerts_raw")
            if alerts and alerts != ":0:0":
                additional_alerts = [
                    alert for alert in describe_alerts(alerts) if alert != stage
                ]
                if additional_alerts:
                    alert_rows.append(
                        (
                            "Alert",
                            Text("; ".join(additional_alerts), style="warning"),
                        )
                    )
            count = details.get("cutting_progress")
            if (
                not details.get("paper_near_blade")
                and count is not None
                and details["stage"] in ("Cutting", "Retracting blade")
            ):
                if self.cut_total is not None and count <= self.cut_total:
                    cut = f"{count}/{self.cut_total} cuts"
                else:
                    cut = f"{count} {'cut' if count == 1 else 'cuts'}"
                    if self.cut_total is not None:
                        cut += f" (expected {self.cut_total})"
                status.append(" ", style="default")
                status.append(field_value("Cuts", cut))
            remaining = details["estimated_remaining_seconds"]
            if remaining is None:
                eta = "Time unknown"
            elif remaining == 0:
                eta = "taking longer than estimated"
            else:
                eta = f"~{duration(remaining)} remaining"
            status.append(" | ", style="default")
            status.append(field_value("Time", eta))
            rows.insert(0, ("Status", status))
        elif event == "job":
            state = details.get("job-state")
            style = "success" if state == 9 else "error" if state == 7 else "warning"
            progress_style = style
            marker = "+" if state == 9 else "x" if state == 7 else "!"
            result = Text(
                Emoji.replace("[+] Completed :tada:")
                if state == 9
                else f"[{marker}] {JOB_STATES.get(state, 'Unknown')} (job {details.get('job-id')})",
                style=style,
            )
            if state == 9:
                result.stylize("bold")
            rows.append(("Status", result))
        elif event == "accepted":
            rows.append(("Job", details["job_id"]))
        else:
            label = {
                "connecting": "Connecting…",
                "connected": "Connected",
                "submitting": "Submitting sheet…",
                "uploaded": "Waiting for printer…",
            }.get(event, event)
            rows.append(("Status", Spinner("line", text=Text(label), style="active")))
        layout = Group()
        if summary:
            layout.renderables.extend([fields(summary), Text("")])
        if self._log_path is not None:
            layout.renderables.extend([fields([("Log", self._log_path)]), Text("")])
        layout.renderables.append(fields(rows))
        if self._overall_fraction is not None and event in (
            "uploaded",
            "progress",
            "job",
        ):
            layout.renderables.extend(
                [
                    Text(""),
                    fields(
                        [
                            (
                                "Progress",
                                self._bar(
                                    self._overall_fraction,
                                    style=progress_style,
                                ),
                            )
                        ]
                    ),
                ]
            )
        if alert_rows:
            layout.renderables.extend([Text(""), fields(alert_rows)])
        return layout

    def _write(self, event: str, text: str) -> None:
        if not self.interactive:
            self.console.print(Text(indent_text(text)), soft_wrap=True)
            return
        # The live layout also shows fields omitted by the plain log message,
        # notably additional alerts while paper is waiting near the blade.
        frame = (
            event,
            self._copies,
            self._copy_number,
            self.cut_total,
            self._overall_fraction,
            self._details,
        )
        if frame == self._last_frame:
            return
        self._last_frame = deepcopy(frame)
        renderable = self._render(event, self._details)
        if not self.active:
            self._live.update(renderable)
            self._live.start(refresh=True)
            self.active = True
        else:
            self._live.update(renderable, refresh=True)
        if event in ("job", "connection_failed"):
            self._live.stop()
            self.active = False

    def _copy_text(self) -> str:
        return f"{'Copies:':<{LABEL_WIDTH + 2}}{self._copy_number}/{self._copies}"

    def finish(self) -> None:
        """Stop the countdown thread and release the active terminal line."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self._progress = None
            self._deadline = None
            if self.active:
                self._live.stop()
                self.active = False
