"""Offline export checks against measured placement and invalid geometry."""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from crycut.geometry import rectangle_path, trace_alpha
from crycut.plt import read_paths

ROOT = Path(__file__).resolve().parents[1]
from crycut.exporter import export
from crycut.placement import pixel_path
from crycut.plt import make_paths_plt
from crycut.rendering import load_design, tag

PROFILE = json.loads((ROOT / "config/profiles/stickerpix-v2.json").read_text())
EXAMPLE = ROOT / "projects/wordmarks/design.svg"
EXPLICIT_EXAMPLE = ROOT / "tests/fixtures/wordmarks-manual.svg"
MEASUREMENTS = json.loads((ROOT / "tests/fixtures/wordmark-placement.json").read_text())
REFERENCE_VERTICES = {
    frozenset(((left, top), (right, top), (right, bottom), (left, bottom)))
    for left, top, right, bottom in MEASUREMENTS["cut_rectangles"]
}


def contour_vertices(data):
    # Rectangle layout is invariant under 180-degree rotation. Compare geometry,
    # not path order/start corner, which change with the corrected orientation.
    return {frozenset(path) for path in read_paths(data, PROFILE)}


class SheetExportTests(unittest.TestCase):
    def test_root_offsets_do_not_shift_print_relative_to_cuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = []
            for name, offsets in (("plain", ""), ("offset", 'x="10" y="20"')):
                source = root / f"{name}.svg"
                source.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" '
                    'width="96mm" height="170mm" viewBox="0 0 96 170" '
                    f'{offsets}><rect x="10" y="10" width="10" height="10" '
                    'fill="#ff0000"/></svg>'
                )
                original = source.read_bytes()
                out = root / name
                self.assertEqual(export(source, out, PROFILE, "#ffffff"), 1)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual((out / "design.svg").read_bytes(), original)
                outputs.append(out)
            for filename in ("sheet-cut.plt", "sheet-print.jpg", "preview.png"):
                with self.subTest(filename=filename):
                    self.assertEqual(
                        (outputs[0] / filename).read_bytes(),
                        (outputs[1] / filename).read_bytes(),
                    )

    def test_generated_wordmark_rectangle_positions_match_measurements(self):
        artwork = ET.parse(EXAMPLE).getroot()
        rectangles = [
            tuple(float(el.get(key)) for key in ("x", "y", "width", "height"))
            for el in artwork.findall(".//" + tag("rect"))
        ]
        self.assertEqual(
            contour_vertices(
                make_paths_plt([rectangle_path(*r) for r in rectangles], PROFILE)
            ),
            REFERENCE_VERTICES,
        )

    def test_cut_guides_removed_from_artwork(self):
        artwork = load_design(EXPLICIT_EXAMPLE)
        self.assertFalse(any(el.get("id") == "cut" for el in artwork.iter()))
        self.assertEqual(len(artwork.findall(".//" + tag("rect"))), 28)

    def test_empty_template_does_not_export_a_cutless_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "No visible artwork"):
                export(
                    ROOT / "templates/sticker-sheet-96x170mm.svg",
                    Path(tmp) / "export",
                    PROFILE,
                    "#ffffff",
                )

    def test_subunit_rectangles_cannot_collapse_to_a_line(self):
        with self.assertRaisesRegex(ValueError, "collapses"):
            make_paths_plt([rectangle_path(2, 2, 0.00001, 10)], PROFILE)

    def test_export_rendering_matches_reference_placement(self):
        def green_mask(image):
            r, g, b = image.convert("RGB").split()
            high_green = g.point(lambda v: 255 if v > 128 else 0)
            difference = ImageChops.subtract(g, ImageChops.lighter(r, b))
            return ImageChops.multiply(
                high_green, difference.point(lambda v: 255 if v > 70 else 0)
            )

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "job"
            self.assertEqual(export(EXAMPLE, out, PROFILE, "#000000"), 28)
            # Default mode traces the clean original; there is no manual cut layer.
            self.assertEqual(
                contour_vertices((out / "sheet-cut.plt").read_bytes()),
                REFERENCE_VERTICES,
            )
            with Image.open(out / "sheet-print.jpg") as generated:
                self.assertEqual(generated.size, (1200, 2100))
                actual = green_mask(generated)
            # Measured bounds remain independent of the current renderer.
            # JPEG/rasterizer antialiasing may differ by at most one pixel.
            for measurement in MEASUREMENTS["green_wordmark_bounds"]:
                bounds = actual.crop(measurement["region"]).getbbox()
                self.assertIsNotNone(bounds)
                self.assertLessEqual(
                    max(abs(v - w) for v, w in zip(bounds, measurement["bounds"])), 1
                )
            with self.assertRaisesRegex(ValueError, "already exists"):
                export(EXAMPLE, out, PROFILE, "#000000")

    def test_png_curved_cut_and_white_border(self):
        from PIL import ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            png = Image.new("RGBA", (384, 680))
            ImageDraw.Draw(png).ellipse((40, 40, 119, 119), fill="#ff0000")
            png.save(tmp / "design.png")
            out = tmp / "out"
            self.assertEqual(
                export(tmp / "design.png", out, PROFILE, "#000000", border=1),
                1,
            )
            path = read_paths((out / "sheet-cut.plt").read_bytes(), PROFILE)[0]
            self.assertGreater(len(path), 12)  # silhouette, not its bounding rectangle
            self.assertEqual(path[0], path[-1])
            with Image.open(out / "sheet-print.jpg") as jpg:
                # At the circle's left center, x=9.5 is in the added white border;
                # x=20 is red artwork and x=5 is the requested black background.
                def at(x, y):
                    px, py = pixel_path([(x, y)], PROFILE)[0]
                    return jpg.getpixel((round(px), round(py)))

                self.assertGreater(min(at(9.5, 20)), 220)
                self.assertGreater(at(20, 20)[0], 220)
                self.assertLess(at(20, 20)[1], 30)
                self.assertLess(max(at(5, 20)), 30)

    def test_border_and_background_colors_for_svg_and_png(self):
        from PIL import ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            png = Image.new("RGBA", (384, 680))
            ImageDraw.Draw(png).ellipse((40, 40, 119, 119), fill="#ff0000")
            png.save(tmp / "design.png")
            (tmp / "design.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'width="96mm" height="170mm" viewBox="0 0 96 170">'
                '<circle cx="20" cy="20" r="10" fill="#ff0000"/></svg>'
            )
            for extension in ("svg", "png"):
                with self.subTest(extension=extension):
                    out = tmp / extension
                    export(
                        tmp / f"design.{extension}",
                        out,
                        PROFILE,
                        "#0000ff",
                        border=1,
                        border_color="#00ff00",
                    )
                    report = json.loads((out / "export.json").read_text())
                    self.assertEqual(report["border_color"], "#00ff00")
                    with Image.open(out / "sheet-print.jpg") as image:
                        for xy, expected in [
                            ((9.5, 20), (0, 255, 0)),
                            ((20, 20), (255, 0, 0)),
                            ((5, 20), (0, 0, 255)),
                        ]:
                            x, y = pixel_path([xy], PROFILE)[0]
                            actual = image.getpixel((round(x), round(y)))
                            self.assertLessEqual(
                                max(abs(a - b) for a, b in zip(actual, expected)), 15
                            )


class AlphaOutlineTests(unittest.TestCase):
    def canvas(self):
        return np.zeros((680, 384), dtype=np.uint8)

    def trace(self, mask, border=0):
        return trace_alpha(mask, border, pixels_per_mm=4)

    def test_disconnected_shapes_and_no_hole_cuts(self):
        mask = self.canvas()
        mask[40:80, 40:80] = 255
        mask[50:70, 50:70] = 0  # hole stays inside the sticker
        mask[40:80, 100:140] = 255
        self.assertEqual(len(self.trace(mask)), 2)

    def test_positive_border_merges_nearby_shapes(self):
        mask = self.canvas()
        mask[40:80, 40:80] = 255
        mask[40:80, 84:124] = 255  # 1 mm gap
        self.assertEqual(len(self.trace(mask)), 2)
        self.assertEqual(len(self.trace(mask, 1)), 1)

    def test_round_border_expands_bounds_by_requested_distance(self):
        mask = self.canvas()
        mask[40:80, 40:80] = 255
        (path,) = self.trace(mask, 1)
        self.assertEqual((min(x for x, y in path), max(x for x, y in path)), (9, 21))
        self.assertEqual((min(y for x, y in path), max(y for x, y in path)), (9, 21))
        self.assertGreater(len(path), 5)

    def test_concavity_is_preserved(self):
        mask = self.canvas()
        mask[40:120, 40:60] = 255
        mask[100:120, 40:120] = 255
        (path,) = self.trace(mask)
        self.assertTrue(any(abs(x - 15) < 0.2 and abs(y - 25) < 0.2 for x, y in path))

    def test_filled_background_and_empty_design_rejected(self):
        for mask in (self.canvas(), self.canvas() + 255):
            with self.assertRaises(ValueError):
                self.trace(mask)

    def test_expansion_outside_page_rejected(self):
        mask = self.canvas()
        mask[40:80, 0:40] = 255
        with self.assertRaisesRegex(ValueError, "beyond"):
            self.trace(mask, 1)

    def test_invalid_border_rejected(self):
        mask = self.canvas()
        mask[40:80, 40:80] = 255
        for border in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.trace(mask, border)


if __name__ == "__main__":
    unittest.main()
