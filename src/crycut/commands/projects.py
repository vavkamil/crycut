"""Create projects and list their export status."""

import json
import os
from pathlib import Path

from rich.text import Text

from ..arguments import ArgumentParser
from ..paths import ROOT
from ..projects import create, source_path
from ..sheets import list_exports
from ..ui import (
    command_hints,
    console,
    field_value,
    fields,
    heading,
    indented,
    listing,
    notice,
)


def list_projects(argv: list[str]) -> int:
    """Validate local projects and display their export status."""
    parser = ArgumentParser(
        prog="crycut projects",
        description="List projects, cut counts, and export status",
    )
    parser.add_argument("--directory", type=Path, default=ROOT / "projects")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    out = console()
    if not args.directory.is_dir():
        parser.error(f"No projects directory: {args.directory}")
    rows = list_exports(args.directory)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        heading("Projects", out=out)
        notice("No projects yet.", out=out)
        out.print()
        command_hints(
            [
                ("New (svg)", "uv run crycut new NAME"),
                ("New (png)", "uv run crycut new NAME --png"),
            ],
            out=out,
        )
        out.print()
    else:
        labels = {
            "exported": "Ready",
            "draft": "Draft",
            "needs export": "Needs export",
            "invalid": "Invalid",
        }
        styles = {
            "exported": "success",
            "draft": "muted",
            "needs export": "warning",
            "invalid": "error",
        }
        heading("Projects", out=out)
        table = listing("Name", "Cuts", "Status", right=("Cuts",))
        for row in rows:
            cuts = str(row["cuts"]) if row["valid"] else "-"
            table.add_row(
                Text(row["name"]),
                field_value("Cuts", cuts),
                Text(labels[row["status"]], style=styles[row["status"]]),
            )
        out.print(indented(table))
        errors = [row for row in rows if row["status"] == "invalid"]
        if errors:
            out.print()
            for row in errors:
                notice(f"{row['name']}: {row['error']}", "error", out=out)
        out.print()
        command_hints(
            [
                ("New (svg)", "uv run crycut new NAME"),
                ("New (png)", "uv run crycut new NAME --png"),
                ("Inspect", "uv run crycut print NAME"),
                ("Export", "uv run crycut export NAME"),
            ],
            out=out,
        )
        out.print()
    return 0


def new(argv: list[str]) -> int:
    """Create a project from the requested artwork or template."""
    new_parser = ArgumentParser(
        prog="crycut new",
        description="Create a project from a blank template or existing artwork",
    )
    new_parser.add_argument("name")
    origin = new_parser.add_mutually_exclusive_group()
    origin.add_argument(
        "--png",
        action="store_true",
        help="Use the transparent PNG template instead of SVG",
    )
    origin.add_argument(
        "--from", dest="source", type=Path, help="Copy existing SVG or PNG artwork"
    )
    args = new_parser.parse_args(argv)
    try:
        path = create(args.name, args.source, args.png)
    except (OSError, ValueError) as error:
        new_parser.exit(1, f"Cannot create project: {error}\n")
    out = console()
    heading("New project", out=out)
    notice(f"Created {os.path.relpath(path)}", "success", out=out)
    out.print()
    out.print(
        fields(
            [
                ("Project", os.path.relpath(path)),
                ("Edit", os.path.relpath(source_path(path))),
            ]
        )
    )
    command_hints([("Export", f"uv run crycut export {args.name}")], out=out)
    out.print()
    return 0
