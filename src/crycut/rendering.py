"""Read SVG artwork and render printer-sized images through Inkscape or Pillow."""

import copy
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from .models import PlacementProfile, Point
from .placement import pixel_path

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def tag(name: str) -> str:
    """Qualify an SVG element name."""
    return f"{{{SVG}}}{name}"


def load_design(path: Path) -> ET.Element:
    """Read the supported design canvas and discard legacy cut guides."""
    root = ET.parse(path).getroot()
    if (
        root.tag != tag("svg")
        or root.get("width") != "96mm"
        or root.get("height") != "170mm"
        or root.get("viewBox", "").split() != ["0", "0", "96", "170"]
        or "transform" in root.attrib
    ):
        raise ValueError(
            "Use a 96mm x 170mm SVG with viewBox='0 0 96 170' and no root transform"
        )
    # Ignore guide layers in older SVGs; cut outlines always come from alpha.
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == "cut":
                parent.remove(child)
    return root


def print_svg(
    artwork: ET.Element,
    profile: PlacementProfile,
    background: str,
    borders: Sequence[Sequence[Point]] = (),
    border_color: str = "#ffffff",
) -> ET.Element:
    """Compose the printer-sized SVG with artwork and optional border fills."""
    root = ET.Element(
        tag("svg"), {"width": "1200", "height": "2100", "viewBox": "0 0 1200 2100"}
    )
    ET.SubElement(
        root, tag("rect"), {"width": "1200", "height": "2100", "fill": background}
    )
    xp, yp = profile["pixel_x_from_x"], profile["pixel_y_from_y"]
    group = ET.SubElement(
        root,
        tag("g"),
        {
            "transform": f"matrix({xp['scale']} 0 0 {yp['scale']} {xp['offset']} {yp['offset']})"
        },
    )
    for path in borders:
        ET.SubElement(
            group,
            tag("polygon"),
            {"points": " ".join(f"{x},{y}" for x, y in path), "fill": border_color},
        )
    # Preserve root presentation attributes and its viewport clipping.
    # Artwork must use element attributes/inline styles: nesting does not scope
    # <style> selectors, which can also change the generated background/borders.
    # This is a documented input limitation, not enforced CSS isolation; see
    # docs/SHEET-WORKFLOW.md (SVG scope and known limitations).
    nested = copy.deepcopy(artwork)
    # Root offsets are ignored during standalone tracing; keep that placement
    # when nesting, where x/y would otherwise become active.
    nested.attrib.pop("x", None)
    nested.attrib.pop("y", None)
    nested.set("width", "96")
    nested.set("height", "170")
    group.append(nested)
    return root


def render_svg(source: Path, destination: Path, width: int, height: int) -> None:
    """Render an SVG through Inkscape with explicit output dimensions."""
    result = subprocess.run(
        [
            "inkscape",
            str(source),
            "--export-type=png",
            f"--export-filename={destination}",
            f"--export-width={width}",
            f"--export-height={height}",
            "--export-background-opacity=0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Inkscape rendering failed: {result.stderr[-2000:]}")


def render_raster(
    rgba: Image.Image,
    profile: PlacementProfile,
    background: str,
    borders: Sequence[Sequence[Point]] = (),
    border_color: str = "#ffffff",
) -> Image.Image:
    """Place transparent raster artwork and border fills on the printer canvas."""
    image = Image.new("RGB", (1200, 2100), background)
    draw = ImageDraw.Draw(image)
    for path in borders:
        draw.polygon(pixel_path(path, profile), fill=border_color)
    xp, yp = profile["pixel_x_from_x"], profile["pixel_y_from_y"]
    sx, sy = rgba.width / (96 * xp["scale"]), rgba.height / (170 * yp["scale"])
    placed = rgba.transform(
        (1200, 2100),
        Image.Transform.AFFINE,
        (sx, 0, -xp["offset"] * sx, 0, sy, -yp["offset"] * sy),
        Image.Resampling.BICUBIC,
    )
    image.paste(placed, (0, 0), placed)
    return image
