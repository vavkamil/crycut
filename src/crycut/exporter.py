"""Generate and install a project's print image, cut paths, and preview."""

import hashlib
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

from .geometry import TRACE_PX_PER_MM, trace_alpha
from .models import PlacementProfile
from .placement import pixel_path
from .plt import make_paths_plt
from .rendering import load_design, print_svg, render_raster, render_svg, tag


def export(
    source: Path,
    output: Path,
    profile: PlacementProfile,
    background: str,
    *,
    border: float = 0,
    border_color: str = "#ffffff",
    replace: bool = False,
) -> int:
    """Render and validate a complete export before replacing project files."""
    source_bytes = source.read_bytes()
    for label, color in (("Background", background), ("Border color", border_color)):
        if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
            raise ValueError(f"{label} must be #RRGGBB")
        ImageColor.getrgb(color)  # Validate before passing the value to SVG.
    if type(border) not in (int, float) or not math.isfinite(border) or border < 0:
        raise ValueError("Border must be a finite, nonnegative number of millimeters")
    raster = source.suffix.lower() == ".png"
    if source.suffix.lower() not in (".svg", ".png"):
        raise ValueError("Use SVG or transparent PNG input")
    if output.exists() and not replace:
        raise ValueError(f"Output already exists: {output}; choose a new directory")
    generated_names = (
        "sheet-print.jpg",
        "sheet-cut.plt",
        "sheet-print.svg",
        "preview.png",
        "export.json",
    )
    for name in generated_names:
        destination = output / name
        if destination.exists() and destination.samefile(source):
            raise ValueError(
                f"Generated {name} would overwrite the source; import it with crycut new NAME --from PATH or choose a separate --output directory"
            )
    design_name = "design" + source.suffix.lower()
    for name in ("design.svg", "design.png"):
        existing = output / name
        if existing.exists() and (name != design_name or not existing.samefile(source)):
            raise ValueError(
                f"Project already contains {name}; export that source or choose a new --output directory"
            )
    art = None if raster else load_design(source)
    rgba: Image.Image | None = None
    # Inkscape can silently substitute missing-image glyphs.
    # Raster assets need a separately verified renderer.
    if art is not None and any(el.tag == tag("image") for el in art.iter()):
        raise ValueError(
            "SVGs with embedded/linked images are unsupported; use a transparent PNG export instead"
        )
    with tempfile.TemporaryDirectory(prefix="crycut-export-") as temporary:
        tmp = Path(temporary)
        size = (96 * TRACE_PX_PER_MM, 170 * TRACE_PX_PER_MM)
        if raster:
            with Image.open(source) as original:
                if abs(original.width / original.height / (96 / 170) - 1) > 0.005:
                    raise ValueError(
                        "PNG must represent the full 96:170 canvas; keep its transparent margins"
                    )
                rgba = original.convert("RGBA")
            alpha = rgba.getchannel("A").resize(size, Image.Resampling.LANCZOS)
        else:
            # SVG input must be self-contained. Temporary copies change the base
            # path; external references are neither bundled nor comprehensively
            # rejected and may silently disappear. See docs/SHEET-WORKFLOW.md
            # (SVG scope and known limitations); use PNG for dependent artwork.
            trace_svg = tmp / "artwork.svg"
            ET.ElementTree(art).write(trace_svg, encoding="utf-8", xml_declaration=True)
            render_svg(trace_svg, tmp / "trace.png", *size)
            with Image.open(tmp / "trace.png") as trace:
                alpha = trace.convert("RGBA").getchannel("A")
        paths = trace_alpha(alpha, border)
        plt = make_paths_plt(paths, profile)
        borders = paths if border else []
        render_path = tmp / "sheet-print.svg"
        if raster:
            assert rgba is not None
            image = render_raster(rgba, profile, background, borders, border_color)
        else:
            assert art is not None
            ET.ElementTree(
                print_svg(art, profile, background, borders, border_color)
            ).write(render_path, encoding="utf-8", xml_declaration=True)
            render_svg(render_path, tmp / "render.png", 1200, 2100)
            with Image.open(tmp / "render.png") as png:
                image = png.convert("RGB")
        if image.size != (1200, 2100):
            raise ValueError("Renderer produced unexpected image dimensions")
        image.save(tmp / "sheet-print.jpg", quality=95, subsampling=0)
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay)
        for path in paths:
            draw.line(pixel_path(path, profile), fill="#ff0088", width=2)
        overlay.save(tmp / "preview.png")
        (tmp / "sheet-cut.plt").write_bytes(plt)
        report = {
            "version": 1,
            "source": design_name,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "profile": profile["name"],
            "border_mm": border,
            "border_color": border_color,
            "background": background,
            "files": {
                name: hashlib.sha256((tmp / name).read_bytes()).hexdigest()
                for name in ("sheet-print.jpg", "sheet-cut.plt")
            },
        }
        (tmp / "export.json").write_text(json.dumps(report, indent=2) + "\n")
        if source.read_bytes() != source_bytes:
            raise ValueError(
                "Design changed during export; retry when editing is finished"
            )
        (tmp / design_name).write_bytes(source_bytes)
        output.mkdir(parents=True, exist_ok=replace)
        # Render everything before replacing generated files. Install the
        # manifest last; partial/interrupted updates fail the file hash checks.
        for name in (design_name, *generated_names):
            if (tmp / name).exists():
                destination = output / name
                if name == design_name and destination.exists():
                    if not destination.samefile(source):
                        raise ValueError(f"Project already contains {name}")
                    if destination.read_bytes() != source_bytes:
                        raise ValueError(
                            "Design changed during export; retry when editing is finished"
                        )
                    continue  # Don't touch the source open in the user's editor.
                staging_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=output, prefix=".export-", delete=False
                    ) as staged:
                        staging_path = Path(staged.name)
                        staged.write((tmp / name).read_bytes())
                    staging_path.replace(destination)
                finally:
                    if staging_path is not None:
                        staging_path.unlink(missing_ok=True)
            elif name == "sheet-print.svg":
                (output / name).unlink(missing_ok=True)
    return len(paths)
