"""
Export a transparent SVG/PNG sheet with automatic cut outlines.

Uses the StickerPix placement profile to generate the print image and cut paths.
--border sets the outward offset in mm (default 0). Interior holes are not cut.
"""

import json
import os
import shlex
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from ..arguments import ArgumentParser
from ..exporter import export
from ..paths import PLACEMENT_PROFILE
from ..projects import command_argument, resolve, source_path
from ..ui import busy, command_hints, console, fields, heading, notice


def main(argv: list[str] | None = None) -> int:
    """Export a selected project with saved or overridden settings."""
    parser = ArgumentParser(prog="crycut export", description=__doc__)
    parser.add_argument(
        "design", help="Project name, project directory, or SVG/PNG path"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional new project directory; otherwise update beside the design",
    )
    parser.add_argument(
        "--background",
        "--background-color",
        metavar="#RRGGBB",
        help="Sheet background (saved setting, or white for a new project)",
    )
    parser.add_argument(
        "--border",
        type=float,
        metavar="MM",
        help="Outward border (saved setting, or 0 for a new project)",
    )
    parser.add_argument(
        "--border-color",
        metavar="#RRGGBB",
        help="Printed margin color (saved setting, or white)",
    )
    args = parser.parse_args(argv)
    try:
        profile = json.loads(PLACEMENT_PROFILE.read_text())
        source = resolve(args.design)
        if source.is_dir():
            source = source_path(source)
        output = args.output or source.parent
        saved = {}
        if (source.parent / "export.json").exists():
            saved = json.loads((source.parent / "export.json").read_text())
            if not isinstance(saved, dict):
                raise ValueError("Invalid saved export settings")
        background = (
            args.background
            if args.background is not None
            else saved.get("background", "#ffffff")
        )
        border = args.border if args.border is not None else saved.get("border_mm", 0)
        border_color = (
            args.border_color
            if args.border_color is not None
            else saved.get("border_color", "#ffffff")
        )
        with busy("Rendering artwork and tracing cut outlines…"):
            count = export(
                source,
                output,
                profile,
                background,
                border=border,
                border_color=border_color,
                replace=args.output is None,
            )
    except (
        ValueError,
        OSError,
        RuntimeError,
        ET.ParseError,
        subprocess.TimeoutExpired,
    ) as error:
        parser.exit(1, f"Export failed: {error}\n")
    out = console()
    heading("Export", out=out)
    notice(f"Exported {count} cut outlines and a 1200 x 2100 JPEG", "success", out=out)
    out.print()
    out.print(
        fields(
            [
                ("Project", os.path.relpath(output.resolve())),
                ("Preview", os.path.relpath((output / "preview.png").resolve())),
            ]
        )
    )
    command_hints(
        [("Inspect", f"uv run crycut print {shlex.quote(command_argument(output))}")],
        out=out,
    )
    out.print()
    return 0
