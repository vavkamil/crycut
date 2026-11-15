"""Compare visible content independently of Rich's ANSI styling and padding."""

from rich.text import Text


def visible(text):
    return "\n".join(line.rstrip() for line in Text.from_ansi(text).plain.splitlines())
