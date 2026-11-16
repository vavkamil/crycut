"""Exports must not replace another editable design or hide changed artwork."""

import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

from crycut import exporter
from crycut.commands import export as export_command
from crycut.paths import PLACEMENT_PROFILE, ROOT


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "sheet"
    shutil.copytree(ROOT / "projects/wordmarks", path)
    return path


def snapshot(path):
    return {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}


def test_svg_entities_report_cli_error_before_rendering(project, monkeypatch, capsys):
    (project / "design.svg").write_text(
        '<!DOCTYPE svg [<!ENTITY sample "harmless marker">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="96mm" height="170mm" viewBox="0 0 96 170">'
        "<text>&sample;</text></svg>"
    )
    before = snapshot(project)
    render = Mock(side_effect=AssertionError("rendered before entity rejection"))
    monkeypatch.setattr(exporter, "render_svg", render)

    with pytest.raises(SystemExit) as error:
        export_command.main([str(project)])

    assert error.value.code == 1
    stderr = capsys.readouterr().err
    assert "Export failed:" in stderr
    assert "EntitiesForbidden" in stderr
    assert "Traceback" not in stderr
    assert snapshot(project) == before
    render.assert_not_called()


@pytest.mark.parametrize("name", ["preview.png", "sheet-print.svg"])
def test_generated_filename_cannot_overwrite_source(
    tmp_path, name, monkeypatch, capsys
):
    original = ROOT / (
        "projects/tech-icons/design.png"
        if name.endswith(".png")
        else "projects/wordmarks/design.svg"
    )
    source = tmp_path / name
    shutil.copyfile(original, source)
    before = snapshot(tmp_path)
    render = Mock(side_effect=AssertionError("rendered before collision check"))
    monkeypatch.setattr(exporter, "render_svg", render)
    monkeypatch.setattr(exporter, "trace_alpha", render)
    with pytest.raises(SystemExit) as error:
        export_command.main([str(source)])
    assert error.value.code == 1
    assert "would overwrite the source" in capsys.readouterr().err
    assert snapshot(tmp_path) == before
    render.assert_not_called()


@pytest.mark.parametrize("suffix", [".svg", ".png"])
def test_different_artwork_cannot_replace_project_source(project, suffix, monkeypatch):
    original = ROOT / (
        "projects/wordmarks/design.svg"
        if suffix == ".svg"
        else "projects/tech-icons/design.png"
    )
    incoming = project / ("replacement" + suffix)
    shutil.copyfile(original, incoming)
    before = snapshot(project)
    monkeypatch.setattr(
        exporter,
        "render_svg",
        Mock(side_effect=AssertionError("rendered before validation")),
    )
    monkeypatch.setattr(
        exporter,
        "trace_alpha",
        Mock(side_effect=AssertionError("traced before validation")),
    )
    with pytest.raises(ValueError, match="already contains"):
        exporter.export(
            incoming,
            project,
            json.loads(PLACEMENT_PROFILE.read_text()),
            "#ffffff",
            replace=True,
        )
    assert snapshot(project) == before


def test_edit_after_render_cannot_be_overwritten(project, monkeypatch):
    source = project / "design.svg"
    original = Path.read_bytes
    reads = 0
    edited = original(source) + b"\n<!-- edited while export is finishing -->\n"

    def read(path):
        nonlocal reads
        data = original(path)
        if path == source:
            reads += 1
            if reads == 2:  # Edit just after the post-render source comparison.
                source.write_bytes(edited)
        return data

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="Design changed"):
        exporter.export(
            source,
            project,
            json.loads(PLACEMENT_PROFILE.read_text()),
            "#ffffff",
            replace=True,
        )
    assert original(source) == edited


@pytest.mark.parametrize("settings", [{"border_mm": "wide"}, {"border_color": None}])
def test_invalid_saved_settings_report_cli_error(project, settings, capsys):
    manifest = project / "export.json"
    values = json.loads(manifest.read_text())
    values.update(settings)
    manifest.write_text(json.dumps(values))
    with pytest.raises(SystemExit) as error:
        export_command.main([str(project)])
    assert error.value.code == 1
    assert "Export failed:" in capsys.readouterr().err


def test_missing_profile_reports_cli_error(project, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(export_command, "PLACEMENT_PROFILE", tmp_path / "missing.json")
    with pytest.raises(SystemExit) as error:
        export_command.main([str(project)])
    assert error.value.code == 1
    assert "Export failed:" in capsys.readouterr().err


def test_failed_staged_write_leaves_no_temporary_project_files(project, monkeypatch):
    original = exporter.tempfile.NamedTemporaryFile
    before = snapshot(project)

    def failing_file(*args, **kwargs):
        staged = original(*args, **kwargs)
        if kwargs.get("prefix") == ".export-":
            staged.write = Mock(side_effect=OSError("disk full"))
        return staged

    monkeypatch.setattr(exporter.tempfile, "NamedTemporaryFile", failing_file)
    with pytest.raises(OSError, match="disk full"):
        exporter.export(
            project / "design.svg",
            project,
            json.loads(PLACEMENT_PROFILE.read_text()),
            "#ffffff",
            replace=True,
        )
    assert snapshot(project) == before
