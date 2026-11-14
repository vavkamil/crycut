"""Encode and validate closed U/D cutter paths."""

import math
import re
from collections.abc import Sequence
from itertools import pairwise

from .models import PlacementProfile, Point
from .placement import mapped


def make_paths_plt(
    paths: Sequence[Sequence[Point]], profile: PlacementProfile
) -> bytes:
    """Encode closed design outlines into calibrated cutter commands."""
    tokens = [profile["plt_header"]]
    for path in paths:
        if len(path) < 4 or path[0] != path[-1]:
            raise ValueError("Cut paths must be closed")
        if any(
            not math.isfinite(x)
            or not math.isfinite(y)
            or not (0 <= x <= 96 and 0 <= y <= 170)
            for x, y in path
        ):
            raise ValueError("Cut path extends outside the canvas")
        points = [
            (
                round(mapped(py, profile["cutter_X_from_y"])),
                round(mapped(px, profile["cutter_Y_from_x"])),
            )
            for px, py in path
        ]
        points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
        if len(set(points)) < 3:
            raise ValueError(
                "Cut outline collapses after rounding to cutter coordinates"
            )
        area2 = sum(x * yy - xx * y for (x, y), (xx, yy) in pairwise(points))
        if not area2:
            raise ValueError("Cut outline collapses to zero area")
        if any(px < 0 or py < 0 for px, py in points):
            raise ValueError("Negative cutter coordinate")
        tokens.append(f"U{points[0][0]},{points[0][1]}")
        tokens.extend(f"D{px},{py}" for px, py in [points[0], *points[1:], points[-1]])
    return (" ".join(tokens) + profile["plt_trailer"]).encode("ascii")


def read_paths(data: bytes, profile: PlacementProfile) -> list[list[tuple[int, int]]]:
    """Validate cutter commands and return their closed outlines."""
    text = data.decode("ascii")
    header, trailer = profile["plt_header"] + " ", profile["plt_trailer"]
    if not text.startswith(header) or not text.endswith(trailer):
        raise ValueError("Unsupported PLT header or ending")
    body = text[len(header) : -len(trailer)]
    if not re.fullmatch(r"U\d+,\d+(?: [UD]\d+,\d+)*", body):
        raise ValueError("Invalid PLT command format")
    paths = []
    for cmd, x, y in re.findall(r"([UD])(\d+),(\d+)", body):
        if cmd == "U":
            paths.append([])
        paths[-1].append((int(x), int(y)))
    xmap, ymap = profile["cutter_X_from_y"], profile["cutter_Y_from_x"]
    xbounds = sorted(round(xmap["offset"] + xmap["scale"] * v) for v in (0, 170))
    ybounds = sorted(round(ymap["offset"] + ymap["scale"] * v) for v in (0, 96))
    for path in paths:
        if len(set(path)) < 3 or path[0] != path[-1]:
            raise ValueError(
                "PLT geometry must contain closed, non-collapsed cut outlines"
            )
        if not sum(x * yy - xx * y for (x, y), (xx, yy) in pairwise(path)):
            raise ValueError("PLT geometry has a zero-area outline")
        if any(
            not (xbounds[0] <= x <= xbounds[1] and ybounds[0] <= y <= ybounds[1])
            for x, y in path
        ):
            raise ValueError("PLT geometry extends beyond the design canvas")
    return paths
