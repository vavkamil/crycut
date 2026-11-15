"""The everyday StickerPix workflow."""

import sys

from rich.style import Style
from rich.text import Text

from .arguments import ArgumentParser
from .ui import command_hints, console, field_value, fields, heading, indented

COMMANDS = {
    "setup": "Find a printer and save its connection settings",
    "status": "Show printer status and prints remaining",
    "new": "Create a project from a blank template or existing artwork",
    "export": "Update a project's JPEG, cut paths, and preview",
    "projects": "List projects, cut counts, and export status",
    "print": "Inspect a project; --send to print, --simulate for a demo",
    "monitor": "Reconnect to an existing job for recovery or debugging",
}


def print_help() -> None:
    """Print the command overview."""
    arguments = {"new": "NAME", "export": "NAME", "print": "NAME", "monitor": "JOB_ID"}
    out = console()
    heading("crycut", out=out)
    out.print(indented("StickerPix — print and cut from Linux"))
    out.print()
    repository = "https://github.com/vavkamil/crycut"
    out.print(indented(Text(repository, style=Style(color="cyan", link=repository))))
    out.print()
    usage = Text.assemble(
        "Usage: ", field_value("Usage", "uv run crycut COMMAND [options]")
    )
    usage.highlight_regex(r"\[options\]", "option")
    out.print(indented(usage))
    heading("Commands", out=out)
    rows = []
    for command, description in COMMANDS.items():
        syntax = Text(command, style="command")
        if command in arguments:
            syntax.append(f" {arguments[command]}", style="argument")
        detail = Text(description, style="description")
        detail.highlight_regex(r"(?<!\S)--[a-z][a-z-]*", "option")
        rows.append((syntax, detail))
    out.print(fields(rows))
    out.print()
    command_hints([("Help", "uv run crycut COMMAND --help")], out=out)
    out.print()


def main(argv: list[str] | None = None) -> int:
    """Dispatch a command without connecting for help or offline operations."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "help":
        argv = [*argv[1:], "--help"]
    if argv and argv[0] == "jobs":  # Compatibility with the previous entry point.
        argv[0] = "projects"
    parser = ArgumentParser(prog="crycut", usage="%(prog)s COMMAND [options]")
    if not argv or argv[0] in ("-h", "--help", "-help"):
        print_help()
        return 0
    command = argv[0]
    if command not in COMMANDS:
        kind = "option" if command.startswith("-") else "command"
        parser.error(f"Unknown {kind}: {command!r}")
    rest = argv[1:]
    if command == "projects":
        from .commands.projects import list_projects

        return list_projects(rest)
    if command == "new":
        from .commands.projects import new

        return new(rest)
    if command == "setup":
        from .commands.setup import main as run
    elif command == "status":
        from .commands.status import main as run
    elif command == "export":
        from .commands.export import main as run
    else:
        from .commands.print import main as run

        if command == "monitor":
            monitor_parser = ArgumentParser(
                prog="crycut monitor", description=COMMANDS["monitor"]
            )
            monitor_parser.add_argument("job_id", type=int)
            monitor_parser.add_argument(
                "--channel",
                type=int,
                help="Override RFCOMM channel for troubleshooting",
            )
            monitor_parser.add_argument("--timeout", type=float, default=10)
            monitor_parser.add_argument("--interval", type=float, default=2)
            monitor_parser.add_argument("--max-wait", type=float, default=900)
            args = monitor_parser.parse_args(rest)
            rest = [
                "--monitor",
                str(args.job_id),
                "--timeout",
                str(args.timeout),
                "--interval",
                str(args.interval),
                "--max-wait",
                str(args.max_wait),
            ]
            if args.channel is not None:
                rest.extend(["--channel", str(args.channel)])
    return run(rest) or 0
