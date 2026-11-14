"""Regression for the 180-degree cut-layout error revealed by physical job 30."""

import json
import tempfile
import unittest
from pathlib import Path

from crycut.exporter import export
from crycut.plt import make_paths_plt, read_paths
from crycut.sheets import PrintSheet

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "tests/fixtures/orientation-v1"
OLD = json.loads((ARCHIVE / "profile.json").read_text())
NEW = json.loads((ROOT / "config/profiles/stickerpix-v2.json").read_text())


class OrientationTests(unittest.TestCase):
    def test_tech_export_reproduces_saved_print_and_cut_files(self):
        # This previously differed between system NumPy and the same version's
        # PyPI wheel because BLAS rounding changed simplifier tie-breaking.
        project = ROOT / "projects/tech-icons"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "export"
            self.assertEqual(
                export(project / "design.png", output, NEW, "#ffffff", border=1.25),
                32,
            )
            for name in ("sheet-print.jpg", "sheet-cut.plt", "preview.png"):
                self.assertEqual(
                    (output / name).read_bytes(), (project / name).read_bytes(), name
                )

    def test_asymmetric_top_left_triangle_maps_to_correct_cutter_quadrant(self):
        triangle = [(10, 6), (17, 6), (11, 14), (10, 6)]
        paths = read_paths(make_paths_plt([triangle], NEW), NEW)
        points = list(dict.fromkeys(paths[0]))
        self.assertTrue(all(x > 6000 and y < 1000 for x, y in points))
        self.assertGreater(
            points[1][1], points[0][1]
        )  # source x right -> cutter Y increases
        self.assertLess(
            points[2][0], points[0][0]
        )  # source y down -> cutter X decreases

    def test_tech_geometry_rotates_both_axes_but_jpeg_is_unchanged(self):
        before = ARCHIVE / "tech-icons"
        after = ROOT / "projects/tech-icons"
        self.assertEqual(
            (before / "sheet-print.jpg").read_bytes(),
            (after / "sheet-print.jpg").read_bytes(),
        )
        old_paths = read_paths((before / "sheet-cut.plt").read_bytes(), OLD)
        new_paths = read_paths((after / "sheet-cut.plt").read_bytes(), NEW)
        self.assertEqual(len(new_paths), 32)
        self.assertEqual([len(p) for p in old_paths], [len(p) for p in new_paths])
        twice_x_center = 2 * (
            OLD["cutter_X_from_y"]["offset"] + 85 * OLD["cutter_X_from_y"]["scale"]
        )
        twice_y_center = 2 * (
            OLD["cutter_Y_from_x"]["offset"] + 48 * OLD["cutter_Y_from_x"]["scale"]
        )
        for old_path, new_path in zip(old_paths, new_paths):
            for (x, y), (xx, yy) in zip(old_path, new_path):
                self.assertLessEqual(abs(x + xx - twice_x_center), 1)
                self.assertLessEqual(abs(y + yy - twice_y_center), 1)
        for transform in ("pixel_x_from_x", "pixel_y_from_y"):
            for field in ("scale", "offset"):
                self.assertEqual(NEW[transform][field], OLD[transform][field])
        self.assertEqual(NEW["plt_trailer"], OLD["plt_trailer"])

    def test_previous_bad_orientation_cannot_be_sent_as_current_export(self):
        with self.assertRaisesRegex(ValueError, "profile"):
            PrintSheet.load(ARCHIVE / "tech-icons")
