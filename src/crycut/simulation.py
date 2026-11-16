"""Generate display events for an offline print demonstration."""

import math
import time

from .jobs import STAGES
from .models import Emit
from .sheets import PrintSheet

SIMULATED_PRINTS_LEFT = 35

# Approximate stage proportions from the November 14 reference prints.
# Scale to the selected sheet's timing estimate, rather than replaying a log.
PRINT_STAGES = (
    (3016, 10),  # Decoding
    (3015, 10),  # Preparing
    (3018, 15),  # Yellow
    (3025, 2),  # Returning home
    (3019, 17),  # Magenta
    (3025, 2),
    (3020, 17),  # Cyan
    (3021, 12),  # Protective layer
    (3028, 13),  # Feeding for cutting
    (3029, 8),  # Finding origin
)


class PlaybackClock:
    """Advance simulated time and sleeps at the same playback speed."""

    def __init__(self, speed: float) -> None:
        """Start a monotonic clock at a positive, finite speed multiplier."""
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("Simulation speed must be finite and positive")
        self.speed = speed
        self._started = time.monotonic()

    def __call__(self) -> float:
        """Return elapsed simulated seconds."""
        return (time.monotonic() - self._started) * self.speed

    def sleep(self, seconds: float) -> None:
        """Wait for simulated seconds, allowing Ctrl+C to interrupt normally."""
        time.sleep(seconds / self.speed)


def simulate(
    sheet: PrintSheet,
    emit: Emit,
    *,
    clock: PlaybackClock,
    copies: int = 1,
    interval: float = 2,
) -> None:
    """Model a successful job, including automatic sheet removal between copies."""
    timing = sheet.timing
    total = timing["total_seconds"]
    emit("connecting")
    clock.sleep(1)
    emit("connected")
    if copies > SIMULATED_PRINTS_LEFT:
        emit("cartridge_warning", prints_left=SIMULATED_PRINTS_LEFT)
    emit("submitting", copies=copies)
    clock.sleep(1)
    emit("accepted", job_id=1, copies=copies)
    for step in range(1, 11):
        emit("upload", job_id=1, sent=step, total=10)
        clock.sleep(0.5)
    emit("uploaded", job_id=1, estimated_execution_seconds=total)
    clock.sleep(1)

    print_weight = sum(weight for _, weight in PRINT_STAGES)
    retract_seconds = min(3, timing["cut_seconds"] * 0.05)
    stages = [
        (stage, timing["print_seconds"] * weight / print_weight)
        for stage, weight in PRINT_STAGES
    ] + [
        (3030, timing["cut_seconds"] - retract_seconds),
        (3031, retract_seconds),
    ]
    for copy in range(1, copies + 1):
        started = clock()
        for stage, seconds in stages:
            end = clock() + seconds
            while True:
                now = clock()
                cuts = 0
                if stage == 3030:
                    fraction = min(1, max(0, 1 - (end - now) / seconds))
                    cuts = int(sheet.cut_count * fraction)
                elif stage == 3031:
                    cuts = sheet.cut_count
                emit(
                    "progress",
                    job_id=1,
                    job_state=3,
                    stage=STAGES[stage],
                    substate=stage,
                    copies=copies,
                    copy_number=copy,
                    cutting_progress=cuts,
                    cut_contours=sheet.cut_count,
                    alerts_raw=":0:0",
                    paper_near_blade=False,
                    estimated_sheet_seconds=total,
                    estimated_remaining_seconds=max(0, total - (now - started)),
                )
                if now >= end:
                    break
                clock.sleep(min(interval, end - now))
        if copy < copies:
            emit(
                "progress",
                job_id=1,
                job_state=3,
                stage=STAGES[3031],
                substate=3031,
                copies=copies,
                copy_number=copy,
                cutting_progress=sheet.cut_count,
                cut_contours=sheet.cut_count,
                alerts_raw=":8401:0",
                paper_near_blade=True,
                estimated_sheet_seconds=total,
                estimated_remaining_seconds=None,
            )
            clock.sleep(30)
    emit("job", **{"job-id": 1, "job-state": 9})
