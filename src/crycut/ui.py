"""Shared terminal presentation with literal text and terminal-owned defaults."""

import sys
import textwrap
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from socket import gethostname
from typing import TextIO
from urllib.parse import urlsplit

from rich import box
from rich.console import Console, RenderableType
from rich.live import Live
from rich.padding import Padding
from rich.spinner import Spinner
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

LABEL_WIDTH = 16
LEFT_MARGIN = 2
THEME = Theme(
    {
        "heading": "bold cyan",
        "active": "bold cyan",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "muted": "dim",
        "label": "bold",
        "number": "bold magenta",
        "path": "cyan",
        "command": "cyan",
        "option": "yellow",
        "argument": "bold magenta",
        "description": "green",
    }
)


def console(stream: TextIO | None = None) -> Console:
    """Use the current stream and respect its color and terminal capabilities."""
    stream = sys.stdout if stream is None else stream
    return Console(
        file=stream,
        force_terminal=None if stream.isatty() else False,
        theme=THEME,
        markup=False,
        highlight=False,
        emoji=False,
    )


def heading(title: str, *, out: Console | None = None) -> None:
    """Start a section with a compact ASCII heading."""
    out = out or console()
    out.print()
    out.print(indented(Text(f"[ {title} ]", style="heading")))
    out.print()


def indented(renderable: RenderableType) -> Padding:
    """Reserve a shared left margin while fitting content to the terminal."""
    return Padding.indent(renderable, LEFT_MARGIN)


def indent_text(text: str) -> str:
    """Indent logical lines without wrapping copyable commands."""
    return textwrap.indent(text, " " * LEFT_MARGIN)


def fields(rows: Iterable[tuple[str | Text, object]]) -> Padding:
    """Build aligned label/value rows that wrap within the terminal width."""
    table = Table.grid(padding=(0, 2), pad_edge=False)
    table.add_column(width=LABEL_WIDTH, no_wrap=True)
    table.add_column(overflow="fold")
    for label, value in rows:
        label_text = (
            label.copy() if isinstance(label, Text) else Text(label, style="label")
        )
        label_text.append(":")
        table.add_row(
            label_text,
            value
            if isinstance(value, (Text, Table, Spinner))
            else field_value(label.plain if isinstance(label, Text) else label, value),
        )
    return indented(table)


def field_value(label: str, value: object) -> Text:
    """Style meaningful values without treating names or firmware as numbers."""
    text = Text(str(value))
    if text.plain.startswith("uv run crycut "):
        text.stylize("command", 0, len("uv run crycut"))
        text.highlight_regex(r"(?<!\S)--[a-z][a-z-]*", "option")
        text.highlight_regex(r"\b(?:NAME|JOB_ID|COMMAND)\b", "argument")
    elif label in {"Project", "Preview", "Edit", "Saved", "Log"}:
        text.stylize("path")
        if label in {"Project", "Preview", "Log"}:
            uri = urlsplit(Path(str(value)).resolve().as_uri())
            text.stylize(Style(link=uri._replace(netloc=gethostname()).geturl()))
    elif label in {"Printer", "Sheet", "Model"}:
        text.stylize("bold")
    if label in {
        "Image",
        "Cuts",
        "Copies",
        "Estimate",
        "Auto-off",
        "Prints left",
        "Sheets printed",
        "Time",
        "Progress",
        "Job",
    }:
        text.highlight_regex(r"(?<!\w)\d+(?:[.:/]\d+)*(?!\w)", "number")
    return text


def command_hints(rows: Iterable[tuple[str, str]], *, out: Console) -> None:
    """Leave command wrapping to the terminal so copies retain one logical line."""
    for label, command in rows:
        line = Text.assemble(
            (f"{' ' * LEFT_MARGIN}{label + ':':<{LABEL_WIDTH + 2}}", "label"),
            field_value(label, command),
        )
        out.print(line, soft_wrap=True)


def listing(*columns: str, right: tuple[str, ...] = ()) -> Table:
    """Build a lightweight table with readable headings and numeric alignment."""
    table = Table(
        box=box.SIMPLE_HEAD,
        show_edge=False,
        padding=(0, 2),
        pad_edge=False,
        header_style="heading",
        border_style="muted",
    )
    for column in columns:
        table.add_column(
            column, justify="right" if column in right else "left", overflow="fold"
        )
    return table


def notice(message: str, kind: str = "info", *, out: Console | None = None) -> None:
    """Show a meaningful marker without coloring ordinary message text."""
    marker, style = {
        "info": ("i", "active"),
        "success": ("+", "success"),
        "warning": ("!", "warning"),
        "error": ("x", "error"),
    }[kind]
    text = Text(" " * LEFT_MARGIN)
    text.append(f"[{marker}] ", style=style)
    text.append(message.replace("\n", "\n" + " " * LEFT_MARGIN))
    out = out or console()
    out.print(text, soft_wrap=True)


@contextmanager
def busy(message: str, *, leading_blank: bool = True) -> Generator[None]:
    """Animate temporary waits on stderr only when both streams are terminals."""
    out = console(sys.stderr)
    if sys.stdout.isatty() and out.is_terminal and not out.is_dumb_terminal:
        with Live(
            Padding(
                Spinner("line", text=Text(message), style="active"),
                (int(leading_blank), 0, 1, LEFT_MARGIN),
            ),
            console=out,
            transient=True,
            refresh_per_second=8,
        ):
            yield
    else:
        yield
