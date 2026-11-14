"""Project names must select the same artwork regardless of working directory."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from crycut import cli, projects
from crycut.commands import print as printer


def test_create_export_print_with_a_working_directory_name_collision(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    monkeypatch.setattr(printer, "PRINTER_CONFIG", tmp_path / "printer.json")
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "config"
    local.mkdir()
    marker = local / "keep.txt"
    marker.write_text("unrelated configuration")
    source = tmp_path / "artwork.svg"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="96mm" height="170mm" '
        'viewBox="0 0 96 170"><rect x="10" y="10" width="10" height="10"/></svg>'
    )
    assert cli.main(["new", "config", "--from", str(source)]) == 0
    assert "crycut export config" in capsys.readouterr().out
    assert cli.main(["export", "config"]) == 0
    capsys.readouterr()
    project = tmp_path / "projects/config"
    assert (project / "sheet-print.jpg").is_file()
    assert list(local.iterdir()) == [marker]
    assert marker.read_text() == "unrelated configuration"

    connect = Mock(side_effect=AssertionError("offline inspection must not connect"))
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", connect)
    assert cli.main(["print", "config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["project"] == str(project)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "config").write_text("an unrelated file")
    monkeypatch.chdir(elsewhere)
    assert cli.main(["print", "config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["project"] == str(project)
    connect.assert_not_called()


@pytest.mark.parametrize(
    "value", ["./config", "config/", "nested/config", "./missing", ".", ".."]
)
def test_explicit_relative_paths_are_not_project_names(tmp_path, monkeypatch, value):
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "projects/config").mkdir(parents=True)
    assert projects.resolve(value) == Path(value)


def test_absolute_paths_and_local_artwork_remain_usable(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    artwork = tmp_path / "artwork.svg"
    artwork.write_text("artwork")
    assert projects.resolve("artwork.svg") == Path("artwork.svg")
    assert projects.resolve(tmp_path / "missing.svg") == tmp_path / "missing.svg"
    assert projects.resolve("new-sheet") == tmp_path / "projects/new-sheet"


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../outside",
        "nested/sheet",
        "back\\slash",
        "-option",
        "two words",
        "line\nbreak",
        "name;command",
        "$(command)",
        ".hidden",
    ],
)
def test_invalid_project_names_fail_before_creating_files(tmp_path, monkeypatch, name):
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="project name"):
        projects.create(name)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["my-sheet", "Sheet_2", "123"])
def test_simple_project_names_are_accepted(tmp_path, monkeypatch, name):
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    source = tmp_path / "artwork.svg"
    source.write_text("artwork")
    project = projects.create(name, source=source)
    assert project == tmp_path / "projects" / name
    assert (project / "design.svg").read_bytes() == source.read_bytes()
