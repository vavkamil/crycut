"""Consistent help and argument errors for the CLI commands."""

import argparse
import re
import sys
from typing import Any, NoReturn

from rich.text import Text

from .ui import console, heading, indented, notice


class ArgumentParser(argparse.ArgumentParser):
    """Keep help and argument errors consistent across commands."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the parser with the alternate help spelling."""
        kwargs.setdefault("allow_abbrev", False)
        if sys.version_info >= (3, 14):
            kwargs["color"] = False
        super().__init__(*args, **kwargs)
        self.add_argument("-help", action="help", help=argparse.SUPPRESS)

    def error(self, message: str) -> NoReturn:
        """Report an argument error with the relevant help command."""
        self.exit(2, f"\nError: {message}\n\nHelp: uv run {self.prog} --help\n\n")

    def print_help(self, file: Any = None) -> None:
        """Print help with spacing consistent with normal CLI output."""
        out = console(sys.stdout if file is None else file)
        heading(f"{self.prog} help", out=out)
        text = Text()
        usage = True
        for line in self.format_help().splitlines(keepends=True):
            styled = Text(line)
            if not line.strip():
                usage = False
            elif usage:
                start = line.find(self.prog)
                if start >= 0:
                    styled.stylize("command", start, start + len(self.prog))
            elif line.rstrip().endswith(":") and not line.startswith(" "):
                styled.stylize("heading")
            else:
                styled.stylize("description")
            # Argument rows separate syntax from descriptions with two spaces.
            syntax = line if usage else re.split(r"\s{2,}", line.strip(), maxsplit=1)[0]
            if usage or re.match(r"^  \S", line):
                start = line.find(syntax)
                end = start + len(syntax)
                if not usage:
                    styled.stylize(
                        "option" if syntax.startswith("-") else "argument", start, end
                    )
                for match in re.finditer(r"(?<![\w-])--?[a-zA-Z][\w-]*", syntax):
                    styled.stylize("option", start + match.start(), start + match.end())
                for match in re.finditer(r"\b[A-Z][A-Z0-9_]*\b", syntax):
                    styled.stylize(
                        "argument", start + match.start(), start + match.end()
                    )
            text.append(styled)
        out.print(indented(text), end="\n")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        """Report failures consistently while preserving argparse exit codes."""
        if message:
            notice(
                message.strip(), "error" if status else "info", out=console(sys.stderr)
            )
        raise SystemExit(status)
