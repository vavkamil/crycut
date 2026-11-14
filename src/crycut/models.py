"""Shared geometry and configuration types; wire replies remain JSON objects."""

from collections.abc import Callable
from typing import Any, TypedDict

JsonObject = dict[str, Any]
Point = tuple[float, float]
Contour = list[Point]
Emit = Callable[..., None]


class Transform(TypedDict):
    """Linear mapping between design, cutter, or image coordinates."""

    scale: float
    offset: float


class PlacementProfile(TypedDict):
    """Calibrated transforms and framing for one supported sheet profile."""

    name: str
    plt_header: str
    plt_trailer: str
    cutter_X_from_y: Transform
    cutter_Y_from_x: Transform
    pixel_x_from_x: Transform
    pixel_y_from_y: Transform


class TimingModel(TypedDict):
    """Measured reference times and provisional cutting assumptions."""

    name: str
    reference_print_seconds: list[float]
    reference_cut_seconds: list[float]
    reference_cut_length_units: float
    reference_contours: int
    assumed_contour_overhead_seconds: float
    cut_uncertainty_fraction: float


class TimingEstimate(TypedDict):
    """Estimated duration and geometry measurements for a selected sheet."""

    print_seconds: float
    cut_seconds: float
    total_seconds: float
    rough_range_seconds: list[int]
    cut_length_units: float
    contours: int
    basis: str
    model: str
