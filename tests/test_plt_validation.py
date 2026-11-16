"""Reject unsafe cut outlines before emitting or accepting cutter commands."""

import json

import pytest

from crycut.paths import PLACEMENT_PROFILE, ROOT
from crycut.plt import make_paths_plt, read_paths


@pytest.fixture
def profile():
    return json.loads(PLACEMENT_PROFILE.read_text())


def test_fixture_paths_count_excludes_final_parking_move(profile):
    data = (ROOT / "tests/fixtures/print-cut/sheet-cut.plt").read_bytes()
    assert len(read_paths(data, profile)) == 28


@pytest.mark.parametrize(
    "body,message",
    [
        ("U631,2060 D231,2060 D231,3866", "closed, non-collapsed"),
        ("U1,1 D2,1 D2,2 D1,1", "beyond the design canvas"),
        ("U631,2060 D231,2060 D631,2060", "closed, non-collapsed"),
        ("U631,2060 Z231,2060 D631,2060", "Invalid PLT command format"),
    ],
    ids=["open", "out-of-bounds", "collapsed", "unsupported-command"],
)
def test_invalid_wire_paths_are_rejected(body, message, profile):
    data = (profile["plt_header"] + " " + body + profile["plt_trailer"]).encode()
    with pytest.raises(ValueError, match=message):
        read_paths(data, profile)


@pytest.mark.parametrize(
    "path",
    [[], [(0, 0), (1, 1), (0, 0)], [(0, 0), (1, 0), (1, 1), (0, 1)]],
)
def test_open_or_short_outlines_cannot_be_encoded(path, profile):
    with pytest.raises(ValueError, match="must be closed"):
        make_paths_plt([path], profile)


@pytest.mark.parametrize(
    "point",
    [(-1, 0), (97, 0), (0, 171), (float("nan"), 0), (0, float("inf"))],
)
def test_out_of_canvas_or_nonfinite_coordinates_cannot_be_encoded(point, profile):
    with pytest.raises(ValueError, match="outside the canvas"):
        make_paths_plt([[(0, 0), point, (1, 1), (0, 0)]], profile)


def test_collinear_outline_is_rejected_after_coordinate_mapping(profile):
    with pytest.raises(ValueError, match="zero area"):
        make_paths_plt([[(0, 0), (10, 0), (20, 0), (0, 0)]], profile)


def test_negative_calibrated_coordinates_are_rejected(profile):
    profile["cutter_X_from_y"] = {"offset": -100, "scale": 1}
    with pytest.raises(ValueError, match="Negative cutter coordinate"):
        make_paths_plt([[(0, 0), (10, 0), (10, 10), (0, 0)]], profile)


def test_collinear_wire_outline_is_rejected(profile):
    data = (
        profile["plt_header"]
        + " U1000,1000 D1100,1000 D1200,1000 D1000,1000"
        + profile["plt_trailer"]
    ).encode()
    with pytest.raises(ValueError, match="zero-area"):
        read_paths(data, profile)


@pytest.mark.parametrize("damage", ["header", "trailer"])
def test_wrong_wire_header_or_trailer_is_rejected(damage, profile):
    data = (
        profile["plt_header"]
        + " U1000,1000 D1100,1000 D1100,1100 D1000,1000"
        + profile["plt_trailer"]
    ).encode()
    data = b"BAD" + data if damage == "header" else data + b"BAD"
    with pytest.raises(ValueError, match="header or ending"):
        read_paths(data, profile)
