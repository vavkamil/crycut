"""Consistent help and argument errors for the CLI commands."""

import argparse
import sys
from typing import Any, NoReturn

from rich.text import Text

from .ui import console, heading, indented, notice


class ArgumentParser(argparse.ArgumentParser):
    """Keep help and argument errors consistent across commands."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the parser with the alternate help spelling."""
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        self.add_argument("-help", action="help", help=argparse.SUPPRESS)

    def error(self, message: str) -> NoReturn:
        """Report an argument error with the relevant help command."""
        self.exit(2, f"\nError: {message}\n\nHelp: uv run {self.prog} --help\n\n")

    def print_help(self, file: Any = None) -> None:
        """Print help with spacing consistent with normal CLI output."""
        out = console(sys.stdout if file is None else file)
        heading(f"{self.prog} help", out=out)
        text = Text(self.format_help())
        for line in text.plain.splitlines(keepends=True):
            if line.rstrip().endswith(":"):
                start = text.plain.index(line)
                text.stylize("heading", start, start + len(line.rstrip()))
        out.print(indented(text), end="\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        """Report failures consistently while preserving argparse exit codes."""
        if message:
            notice(
                message.strip(), "error" if status else "info", out=console(sys.stderr)
            )
        raise SystemExit(status)
