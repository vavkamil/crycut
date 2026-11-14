"""Derive closed sticker outlines from a design's alpha, in canvas millimeters."""

import math

import contourpy
import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import ndimage

from .models import Contour

TRACE_PX_PER_MM = 24
SIMPLIFY_MM = 0.02


def rectangle_path(x: float, y: float, w: float, h: float) -> Contour:
    """Return a closed rectangular contour in design coordinates."""
    return [(x + w, y + h), (x + w, y), (x, y), (x, y + h), (x + w, y + h)]


def simplify_open(points: NDArray[np.float64], tolerance: float) -> NDArray[np.float64]:
    """Simplify a polyline with Ramer-Douglas-Peucker, preserving endpoints."""
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, stop = stack.pop()
        if stop <= start + 1:
            continue
        line = points[stop] - points[start]
        offsets = points[start + 1 : stop] - points[start]
        # Explicit 2D arithmetic avoids BLAS-dependent rounding in equal-distance
        # ties, keeping exported vertices stable across system and uv wheels.
        length2 = line[0] * line[0] + line[1] * line[1]
        if length2:
            projection = offsets[:, 0] * line[0] + offsets[:, 1] * line[1]
            fraction = np.clip(projection / length2, 0, 1)
            distances = np.linalg.norm(offsets - fraction[:, None] * line, axis=1)
        else:
            distances = np.linalg.norm(offsets, axis=1)
        index = int(np.argmax(distances))
        if distances[index] > tolerance:
            middle = start + 1 + index
            keep.add(middle)
            stack.extend([(start, middle), (middle, stop)])
    return points[sorted(keep)]


def trace_alpha(
    alpha: Image.Image | NDArray[np.uint8],
    border_mm: float = 0,
    pixels_per_mm: int = TRACE_PX_PER_MM,
) -> list[Contour]:
    """
    Trace opaque components, expand by border_mm, and omit interior hole cuts.

    Pixel centers are at (index+0.5)/pixels_per_mm. The alpha threshold is 128.
    No specks/components are silently discarded. Expansion can merge neighbors.
    """
    if not math.isfinite(border_mm) or border_mm < 0:
        raise ValueError("Border must be a finite, nonnegative number of millimeters")
    alpha = np.asarray(alpha)
    expected = (round(170 * pixels_per_mm), round(96 * pixels_per_mm))
    if alpha.ndim != 2 or alpha.shape != expected:
        raise ValueError(f"Expected alpha raster {expected[1]} x {expected[0]}")
    mask = alpha >= 128
    if not mask.any():
        raise ValueError("No visible artwork found on the transparent canvas")
    if mask.all():
        raise ValueError(
            "The whole canvas is opaque; keep the background transparent for automatic cuts"
        )
    # Internal holes are not separate cuts. This also prevents an isolated
    # element inside an enclosing sticker from receiving a redundant cut.
    mask = ndimage.binary_fill_holes(mask)
    if border_mm:
        rows, columns = np.nonzero(mask)
        bounds = (
            columns.min() / pixels_per_mm,
            rows.min() / pixels_per_mm,
            (columns.max() + 1) / pixels_per_mm,
            (rows.max() + 1) / pixels_per_mm,
        )
        if (
            bounds[0] < border_mm
            or bounds[1] < border_mm
            or bounds[2] + border_mm > 96
            or bounds[3] + border_mm > 170
        ):
            raise ValueError("Requested border extends beyond the 96 x 170 mm canvas")
        del rows, columns
        distance = ndimage.distance_transform_edt(~mask)
        mask |= distance <= border_mm * pixels_per_mm
        del distance
        mask = ndimage.binary_fill_holes(mask)
    labels, _count = ndimage.label(mask)  # four-connected components
    paths = []
    for label, region in enumerate(ndimage.find_objects(labels), 1):
        if region is None:
            continue
        ys, xs = region
        component = labels[region] == label
        if component.all():
            # Preserve exact straight rectangle edges instead of introducing
            # the half-pixel corner chamfers of marching squares.
            path = rectangle_path(
                xs.start / pixels_per_mm,
                ys.start / pixels_per_mm,
                (xs.stop - xs.start) / pixels_per_mm,
                (ys.stop - ys.start) / pixels_per_mm,
            )
        else:
            contours = contourpy.contour_generator(
                z=np.pad(component, 1).astype(float)
            ).lines(0.5)
            if len(contours) != 1:
                raise ValueError(
                    "Ambiguous touching contours; separate the shapes or add a border"
                )
            points = (
                contours[0] + np.array([xs.start - 0.5, ys.start - 0.5])
            ) / pixels_per_mm
            if not np.array_equal(points[0], points[-1]):
                raise ValueError("Detected an open cut contour")
            # Split the ring so simplification never starts with a zero-length chord.
            pivot = int(np.argmax(np.linalg.norm(points - points[0], axis=1)))
            points = np.concatenate(
                (
                    simplify_open(points[: pivot + 1], SIMPLIFY_MM)[:-1],
                    simplify_open(points[pivot:], SIMPLIFY_MM),
                )
            )
            path = [(float(x), float(y)) for x, y in points]
        paths.append(path)
    # Stable reading order by bounding box; no travel optimization is assumed.
    paths.sort(key=lambda p: (min(y for x, y in p), min(x for x, y in p)))
    return paths
