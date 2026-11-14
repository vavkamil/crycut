"""Provisional print-time baseline plus cut-distance/contour-count estimate."""

import json
import math
import re
from statistics import mean

from .models import TimingEstimate, TimingModel
from .paths import TIMING_MODEL


def estimate(cut: bytes, model: TimingModel | None = None) -> TimingEstimate:
    """Estimate printing and cutting times from the selected cut geometry."""
    if model is None:
        model = json.loads(TIMING_MODEL.read_text())
    length = 0.0
    contours = 0
    position = None
    active = False
    for cmd, x, y in re.findall(r"([UD])(-?\d+),(-?\d+)", cut.decode("ascii")):
        point = (int(x), int(y))
        if cmd == "U":
            active = False
        elif position is not None:
            distance = math.dist(position, point)
            length += distance
            if distance and not active:
                contours += 1
                active = True
        position = point
    if not length or not contours:
        raise ValueError("Cannot estimate an empty cut job")
    overhead = model["assumed_contour_overhead_seconds"]
    reference_motion_time = (
        mean(model["reference_cut_seconds"]) - overhead * model["reference_contours"]
    )
    cut_seconds = (
        overhead * contours
        + reference_motion_time * length / model["reference_cut_length_units"]
    )
    print_seconds = mean(model["reference_print_seconds"])
    uncertainty = model["cut_uncertainty_fraction"]
    return {
        "print_seconds": round(print_seconds, 1),
        "cut_seconds": round(cut_seconds, 1),
        "total_seconds": round(print_seconds + cut_seconds, 1),
        "rough_range_seconds": [
            round(
                min(model["reference_print_seconds"]) + cut_seconds * (1 - uncertainty)
            ),
            round(
                max(model["reference_print_seconds"]) + cut_seconds * (1 + uncertainty)
            ),
        ],
        "cut_length_units": round(length, 1),
        "contours": contours,
        "basis": "Measured print baseline plus cut distance and assumed contour overhead; curves may be slower",
        "model": model["name"],
    }
