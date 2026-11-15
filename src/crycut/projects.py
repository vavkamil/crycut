"""One editable source and its latest export in each project folder."""

import os
import re
from pathlib import Path

from .paths import ROOT


def command_argument(directory: Path) -> str:
    """Select a project by name when possible, otherwise by an explicit path."""
    directory = directory.resolve()
    if directory.parent == (
        ROOT / "projects"
    ).resolve() and not directory.name.startswith("-"):
        return directory.name
    path = os.path.relpath(directory)
    return f"./{path}" if "/" not in path or path.startswith("-") else path


def resolve(value: str | Path) -> Path:
    """Prefer named projects for bare names; preserve explicit filesystem paths."""
    path = Path(value)
    if (
        isinstance(value, Path)
        or path.is_absolute()
        or "/" in value
        or value in (".", "..")
    ):
        return path
    project = ROOT / "projects" / path
    if project.is_dir():
        return project
    return path if path.exists() else project


def source_path(directory: Path) -> Path:
    """Find the single editable SVG or PNG in a project."""
    sources = [
        directory / name
        for name in ("design.svg", "design.png")
        if (directory / name).is_file()
    ]
    if len(sources) != 1:
        raise ValueError("A project must contain exactly one design.svg or design.png")
    return sources[0]


def create(name: str, source: str | Path | None = None, png: bool = False) -> Path:
    """Create a project from a supplied design or a blank template."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError(
            "Use a project name starting with a letter or number, containing only ASCII letters, numbers, hyphens, and underscores"
        )
    if source is None:
        source = (
            ROOT
            / "templates"
            / ("sticker-sheet-1152x2040.png" if png else "sticker-sheet-96x170mm.svg")
        )
    source = Path(source)
    if source.suffix.lower() not in (".svg", ".png"):
        raise ValueError("Use SVG or PNG artwork")
    content = source.read_bytes()
    destination = ROOT / "projects" / name
    destination.mkdir(parents=True)  # An existing project is never replaced by new.
    (destination / ("design" + source.suffix.lower())).write_bytes(content)
    return destination
