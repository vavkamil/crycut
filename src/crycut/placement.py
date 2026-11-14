"""Map design coordinates into image pixels and cutter positions."""

from collections.abc import Sequence

from .models import Contour, PlacementProfile, Point, Transform


def mapped(value: float, transform: Transform) -> float:
    """Apply a calibrated linear coordinate transform."""
    return transform["scale"] * value + transform["offset"]


def pixel_path(path: Sequence[Point], profile: PlacementProfile) -> Contour:
    """Map a design contour into printer image pixels."""
    return [
        (mapped(x, profile["pixel_x_from_x"]), mapped(y, profile["pixel_y_from_y"]))
        for x, y in path
    ]
