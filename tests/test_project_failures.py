"""Project commands handle empty collections and preserve artwork on errors."""

import json
import shutil

import pytest

from crycut import cli, projects
from crycut.sheets import list_exports


def test_missing_projects_directory_reports_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["projects", "--directory", str(tmp_path / "missing")])
    assert stopped.value.code == 2
    assert "No projects directory" in capsys.readouterr().err


def test_empty_projects_directory_offers_creation(tmp_path, capsys):
    assert cli.main(["projects", "--directory", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "No projects yet" in output
    assert "uv run crycut new NAME" in output
    assert cli.main(["projects", "--directory", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_invalid_project_does_not_hide_valid_draft(tmp_path, capsys):
    (tmp_path / "broken").mkdir()
    (tmp_path / "draft").mkdir()
    (tmp_path / "draft/design.svg").write_text("artwork")
    assert cli.main(["projects", "--directory", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Invalid" in output and "Draft" in output
    assert (
        "broken: A project must contain exactly one design.svg or design.png" in output
    )


def test_new_existing_project_reports_error_without_overwriting(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "incoming.svg"
    source.write_text("replacement")
    project = tmp_path / "projects/existing"
    project.mkdir(parents=True)
    original = project / "design.svg"
    original.write_text("original artwork")
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["new", "existing", "--from", str(source)])
    assert stopped.value.code == 1
    assert "Cannot create project" in capsys.readouterr().err
    assert original.read_text() == "original artwork"
    assert list(project.iterdir()) == [original]


def test_unsupported_artwork_type_does_not_create_project(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "artwork.jpg"
    source.write_bytes(b"unsupported artwork")
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["new", "unsupported", "--from", str(source)])
    assert stopped.value.code == 1
    assert "Use SVG or PNG artwork" in capsys.readouterr().err
    assert not (tmp_path / "projects").exists()


def test_png_template_creates_a_single_editable_source(tmp_path, monkeypatch, capsys):
    shutil.copytree(projects.ROOT / "templates", tmp_path / "templates")
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    assert cli.main(["new", "png-sheet", "--png"]) == 0
    capsys.readouterr()
    project = tmp_path / "projects/png-sheet"
    assert list(project.iterdir()) == [project / "design.png"]
    assert (project / "design.png").read_bytes() == (
        tmp_path / "templates/sticker-sheet-1152x2040.png"
    ).read_bytes()
    assert list_exports(project.parent)[0]["status"] == "draft"
