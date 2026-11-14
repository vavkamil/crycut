"""Consistent help and argument errors for the CLI commands."""

import argparse
import sys
from typing import Any, NoReturn


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
        self._print_message(
            "\n" + self.format_help() + "\n", sys.stdout if file is None else file
        )
