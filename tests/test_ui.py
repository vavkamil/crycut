"""Terminal themes, literal values, narrow layouts, and redirected output."""

import io
import re
import shlex
from socket import gethostname
from urllib.parse import unquote, urlsplit

import pytest
from rich.cells import cell_len

from crycut.commands import print as printer
from crycut.commands import projects
from crycut.progress import ProgressDisplay
from crycut.ui import command_hints, console, field_value, fields, heading, notice
from terminal_helpers import visible


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("no_color", [False, True])
def test_command_help_uses_shared_theme(terminal, no_color, monkeypatch):
    from crycut.arguments import ArgumentParser

    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "80")
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    parser = ArgumentParser(prog="crycut status", description="Read printer status.")
    parser.add_argument("--timeout", help="Timeout in seconds")
    stream = Terminal() if terminal else io.StringIO()
    parser.print_help(file=stream)
    output = stream.getvalue()
    assert "\x1b" not in parser.format_help()
    assert "--timeout TIMEOUT" in visible(output)
    assert "Timeout in seconds" in visible(output)
    if terminal and not no_color:
        assert "\x1b[36mcrycut status\x1b[0m" in output
        assert "\x1b[33m--timeout\x1b[0m" in output
        assert "\x1b[1;35mTIMEOUT\x1b[0m" in output
        assert re.search(r"\x1b\[32m\s*Timeout in seconds", output)
    elif terminal:
        for sequence in re.findall(r"\x1b\[([0-9;]*)m", output):
            assert set(sequence.split(";")) <= {"", "0", "1", "2"}
    else:
        assert "\x1b" not in output


@pytest.mark.parametrize("label", ["Project", "Preview", "Log"])
def test_path_links_keep_relative_labels_and_encode_local_targets(
    label, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERM", "xterm-256color")
    path = "projects/stickers #1 [draft]/žluť.png"
    stream = Terminal()
    out = console(stream)
    out.width = 100
    out.print(fields([(label, path)]))
    output = stream.getvalue()
    links = re.findall(r"\x1b\]8;[^;]*;([^\x1b]+)\x1b\\", output)
    assert links
    for link in links:
        uri = urlsplit(link)
        assert uri.scheme == "file"
        assert uri.netloc == gethostname()
        assert unquote(uri.path) == str(tmp_path / path)
        assert uri.fragment == uri.query == ""
        assert "%20" in link and "%23" in link and "%C5%BE" in link
    assert field_value(label, path).plain == path
    assert path in visible(output)
    assert str(tmp_path) not in visible(output)


def test_redirected_paths_contain_no_hyperlink_escape_sequences(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = io.StringIO()
    out = console(stream)
    out.print(fields([("Preview", "projects/wordmarks/preview.png")]))
    output = stream.getvalue()
    assert "projects/wordmarks/preview.png" in output
    assert "\x1b" not in output
    assert "file://" not in output


def test_terminal_colors_are_accents_and_values_keep_default_foreground(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = Terminal()
    out = console(stream)
    heading("Status", out=out)
    assert "\x1b[1;36m" in stream.getvalue()
    stream.seek(0)
    stream.truncate()
    out.print(fields([("Printer", "StickerPix"), ("Address", "AA:BB:CC:DD:EE:FF")]))
    # Labels and the printer name are bold, but ordinary values keep the
    # terminal foreground; neither a foreground nor background is imposed.
    assert set(re.findall(r"\x1b\[([0-9;]*)m", stream.getvalue())) <= {"0", "1"}


def test_numeric_fields_and_commands_have_selective_accents(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = Terminal()
    out = console(stream)
    out.print(
        fields(
            [
                ("Cuts", "28 outlines"),
                ("Estimate", "~3:44 per sheet"),
                ("Firmware", "1.0.31_0033"),
                ("Print", "uv run crycut print wordmarks --send"),
            ]
        )
    )
    output = stream.getvalue()
    assert "\x1b[1;35m28\x1b[0m outlines" in output
    assert "\x1b[1;35m3:44\x1b[0m per sheet" in output
    assert "\x1b[36muv run crycut\x1b[0m" in output
    assert "\x1b[33m--send\x1b[0m" in output
    assert "\x1b[1;35m1.0.31" not in output


@pytest.mark.parametrize("copies", [1, 3])
@pytest.mark.parametrize("channel", [None, 2])
@pytest.mark.parametrize("width", [40, 80])
def test_preview_hints_are_aligned_commands_and_preserve_requested_copies(
    copies, channel, width, monkeypatch, capsys
):
    monkeypatch.setenv("COLUMNS", str(width))
    args = ["wordmarks", "--copies", str(copies)]
    if channel is not None:
        args.extend(["--channel", str(channel)])
    assert printer.main(args) == 0
    output = capsys.readouterr().out
    command = "uv run crycut print wordmarks --send"
    if channel is not None:
        command += f" --channel {channel}"
    if copies != 1:
        command += f" --copies {copies}"
    assert f"Print:            {command}" in output
    assert "[i]" not in output
    if copies == 1:
        assert f"Print 3 copies:   {command} --copies 3" in output
    for line in output.splitlines():
        if line.lstrip().startswith(("Print:", "Print 3 copies:")):
            suggested = printer._parse_args(shlex.split(line.split(":", 1)[1])[4:])
            assert suggested.channel == channel
            assert suggested.copies == (
                3 if line.lstrip().startswith("Print 3 copies:") else copies
            )


@pytest.mark.parametrize("width", [40, 80])
@pytest.mark.parametrize("terminal", [False, True])
def test_long_command_hints_have_no_inserted_breaks_or_truncation(width, terminal):
    stream = Terminal() if terminal else io.StringIO()
    out = console(stream)
    out.width = width
    arguments = [
        "uv",
        "run",
        "crycut",
        "print",
        "/tmp/sheets with spaces/" + "wordmark-" * 12 + "artist's sheet",
        "--send",
        "--channel",
        "2",
    ]
    command_hints([("Print", shlex.join(arguments))], out=out)
    output = visible(stream.getvalue())
    assert "\n" not in output
    assert output.startswith("  Print:            ")
    assert shlex.split(output.split(":", 1)[1]) == arguments


def test_no_color_keeps_markers_without_color_codes(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    stream = Terminal()
    out = console(stream)
    heading("Status", out=out)
    notice("Cover open", "warning", out=out)
    notice("Disconnected", "error", out=out)
    assert "[!] Cover open" in visible(stream.getvalue())
    assert "[x] Disconnected" in visible(stream.getvalue())
    # Bold is allowed under NO_COLOR; foreground/background colors are not.
    for sequence in re.findall(r"\x1b\[([0-9;]*)m", stream.getvalue()):
        assert set(sequence.split(";")) <= {"", "0", "1", "2"}


def test_redirected_messages_stay_plain_and_commands_are_not_split(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = io.StringIO()
    out = console(stream)
    out.width = 40
    command = "uv run crycut monitor 42 --channel 2"
    notice("Job 42 may still be running. Check it with " + command, "warning", out=out)
    assert command in stream.getvalue()
    assert "\x1b" not in stream.getvalue()


@pytest.mark.parametrize("width", [40, 80])
def test_fields_wrap_long_literal_paths_and_align_values(width):
    stream = io.StringIO()
    out = console(stream)
    out.width = width
    path = "projects/[bold]my-stickers[/bold]/" + "long-name-" * 8 + "preview.png"
    out.print(fields([("Sheet", ":smile:"), ("Preview", path)]))
    lines = stream.getvalue().splitlines()
    assert lines[0].index(":smile:") == lines[1].index("projects/") == 20
    assert all(line.startswith("  ") for line in lines if line.strip())
    assert all(cell_len(line) <= width for line in lines)
    assert path in "".join(line.strip() for line in lines)


def test_project_names_are_literal_and_status_is_readable_without_color(
    monkeypatch, capsys, tmp_path
):
    name = "[red]my-project[/red]:smile:"
    monkeypatch.setattr(
        projects,
        "list_exports",
        lambda _: [
            {"name": name, "cuts": 28, "valid": True, "status": "exported"},
        ],
    )
    assert projects.list_projects(["--directory", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert name in output
    assert "Ready" in output
    assert "\x1b" not in output


@pytest.mark.usefixtures("manual_live_refresh")
def test_live_summary_keeps_copies_under_estimate_and_cutting_on_status_line():
    display = ProgressDisplay(Terminal(), cut_total=28)
    display.console.width = 80
    display.set_summary(
        [("Sheet", "wordmarks"), ("Estimate", "~3:44 per sheet")],
        copies=3,
        log_path="logs/print.jsonl",
    )
    event = {
        "job_state": 3,
        "stage": "Printing cyan",
        "copies": 3,
        "copy_number": 1,
        "estimated_sheet_seconds": 224,
        "estimated_remaining_seconds": 101,
    }

    def snapshot():
        stream = io.StringIO()
        out = console(stream)
        out.width = 80
        out.print(display._live.renderable)
        return visible(stream.getvalue())

    try:
        display.show("progress", event)
        before = snapshot()
        assert (
            "Estimate:         ~3:44 per sheet\n  Copies:           1/3\n\n" in before
        )
        display.show("progress", {**event, "stage": "Cutting", "cutting_progress": 2})
        cutting = snapshot()
        assert "Status:           Cutting 2/28 cuts | ~1:41 remaining\n\n" in cutting
        assert "Cuts:" not in cutting and "Time:" not in cutting
        assert len(before.splitlines()) == len(cutting.splitlines())
        display.show("progress", {**event, "copy_number": 2})
        assert (
            "Estimate:         ~3:44 per sheet\n  Copies:           2/3" in snapshot()
        )
    finally:
        display.finish()


@pytest.mark.usefixtures("manual_live_refresh")
@pytest.mark.parametrize("width", [40, 80])
def test_live_layout_preserves_counts_and_estimates_on_narrow_terminals(width):
    stream = Terminal()
    display = ProgressDisplay(stream, cut_total=28)
    display.console.width = width
    try:
        display.show(
            "progress",
            {
                "job_state": 3,
                "stage": "Cutting",
                "copies": 3,
                "copy_number": 2,
                "estimated_sheet_seconds": 224,
                "estimated_remaining_seconds": 42,
                "cutting_progress": 14,
            },
        )
        text = visible(stream.getvalue())
        assert "2/3" in text
        assert "14/28 cuts" in text
        assert "~0:42 remaining" in text
        assert "% estimated" not in text
        assert "100%" not in text
        assert all(line.startswith("  ") for line in text.splitlines() if line.strip())
        assert all(cell_len(line) <= width for line in text.splitlines())
    finally:
        display.finish()
    assert not display._live.is_started
    assert "\x1b[?25h" in stream.getvalue()


@pytest.mark.usefixtures("manual_live_refresh")
def test_live_unexpected_cut_count_is_not_clamped_to_success():
    stream = Terminal()
    display = ProgressDisplay(stream, cut_total=28)
    try:
        display.show(
            "progress",
            {
                "job_state": 3,
                "stage": "Cutting",
                "estimated_remaining_seconds": None,
                "cutting_progress": 29,
            },
        )
        text = visible(stream.getvalue())
        assert "29 cuts (expected 28)" in text
        assert "28/28" not in text
        assert "Progress:" not in text
    finally:
        display.finish()
