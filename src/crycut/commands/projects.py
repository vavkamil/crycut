"""Create projects and list their export status."""

import json
from pathlib import Path

from ..arguments import ArgumentParser
from ..paths import ROOT
from ..projects import create, source_path
from ..sheets import list_exports


def list_projects(argv: list[str]) -> int:
    """Validate local projects and display their export status."""
    parser = ArgumentParser(
        prog="crycut projects",
        description="List projects, cut counts, and export status",
    )
    parser.add_argument("--directory", type=Path, default=ROOT / "projects")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        parser.error(f"No projects directory: {args.directory}")
    rows = list_exports(args.directory)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("\nProjects\n\nNo projects yet.\n\nCreate one: uv run crycut new NAME\n")
    else:
        labels = {
            "exported": "Ready",
            "draft": "Draft",
            "needs export": "Needs export",
            "invalid": "Invalid",
        }
        name_width = max(12, *(len(row["name"]) for row in rows))
        print("\nProjects\n")
        print(f"{'Name':<{name_width}}  {'Cuts':>4}    Status")
        for row in rows:
            cuts = str(row["cuts"]) if row["valid"] else "—"
            print(f"{row['name']:<{name_width}}  {cuts:>4}    {labels[row['status']]}")
        errors = [row for row in rows if row["status"] == "invalid"]
        if errors:
            print()
            for row in errors:
                print(f"{row['name']}: {row['error']}")
        print(f"\n{'Inspect:':<15}uv run crycut print NAME")
        print(f"{'Export:':<15}uv run crycut export NAME\n")
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
    print(
        f"Created {path}; edit {source_path(path).name}, then run uv run crycut export {args.name}"
    )
    return 0
