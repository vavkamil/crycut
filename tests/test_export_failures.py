"""Exports must not replace another editable design or hide changed artwork."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw

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
    before = snapshot(project)
    edited = before["design.svg"] + b"\n<!-- edited while export is finishing -->\n"
    mkdir = Path.mkdir
    edits = []

    def edit_before_install(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        # This boundary follows rendering and its source comparison, but
        # precedes installation. Avoid depending on the number of source reads.
        if path == project:
            source.write_bytes(edited)
            edits.append(path)
        return result

    monkeypatch.setattr(Path, "mkdir", edit_before_install)
    with pytest.raises(ValueError, match="Design changed"):
        exporter.export(
            source,
            project,
            json.loads(PLACEMENT_PROFILE.read_text()),
            "#ffffff",
            replace=True,
        )
    assert edits == [project]
    assert snapshot(project) == {**before, "design.svg": edited}


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


@pytest.mark.parametrize("settings", [None, [], "invalid"])
def test_non_object_saved_settings_fail_before_rendering(
    project, settings, monkeypatch, capsys
):
    (project / "export.json").write_text(json.dumps(settings))
    before = snapshot(project)
    render = Mock(side_effect=AssertionError("rendered invalid settings"))
    monkeypatch.setattr(exporter, "render_svg", render)

    with pytest.raises(SystemExit) as error:
        export_command.main([str(project)])

    assert error.value.code == 1
    assert "Invalid saved export settings" in capsys.readouterr().err
    assert snapshot(project) == before
    render.assert_not_called()


@pytest.mark.parametrize("kind", ["unsupported", "linked-image", "wrong-png-ratio"])
def test_unsupported_artwork_leaves_output_uncreated(tmp_path, monkeypatch, kind):
    if kind == "unsupported":
        source = tmp_path / "design.jpg"
        Image.new("RGB", (96, 170)).save(source)
        message = "Use SVG or transparent PNG input"
    elif kind == "linked-image":
        source = tmp_path / "design.svg"
        source.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="96mm" height="170mm" viewBox="0 0 96 170">'
            '<image href="missing.png" width="10" height="10"/></svg>'
        )
        message = "embedded/linked images are unsupported"
    else:
        source = tmp_path / "design.png"
        Image.new("RGBA", (100, 100)).save(source)
        message = "PNG must represent the full 96:170 canvas"
    before = source.read_bytes()
    output = tmp_path / "export"
    render = Mock(side_effect=AssertionError("rendered unsupported artwork"))
    monkeypatch.setattr(exporter, "render_svg", render)
    monkeypatch.setattr(exporter, "trace_alpha", render)

    with pytest.raises(ValueError, match=message):
        exporter.export(
            source, output, json.loads(PLACEMENT_PROFILE.read_text()), "#ffffff"
        )

    assert source.read_bytes() == before
    assert not output.exists()
    render.assert_not_called()


@pytest.mark.parametrize(
    "failure,message",
    [
        (
            subprocess.CompletedProcess([], 1, stdout="", stderr="invalid filter"),
            "Inkscape rendering failed: invalid filter",
        ),
        (subprocess.TimeoutExpired("inkscape", 120), "timed out"),
        (FileNotFoundError("inkscape is missing"), "inkscape is missing"),
    ],
)
def test_renderer_failure_preserves_existing_export(
    project, monkeypatch, capsys, failure, message
):
    run = Mock()
    if isinstance(failure, BaseException):
        run.side_effect = failure
    else:
        run.return_value = failure
    monkeypatch.setattr("crycut.rendering.subprocess.run", run)
    before = snapshot(project)

    with pytest.raises(SystemExit) as error:
        export_command.main([str(project)])

    assert error.value.code == 1
    assert message in capsys.readouterr().err
    assert snapshot(project) == before
    run.assert_called_once()


@pytest.mark.parametrize("failure", ["wrong-dimensions", "edit-during-render"])
def test_invalid_render_is_not_installed(project, monkeypatch, failure):
    source = project / "design.svg"
    before = snapshot(project)
    edited = before["design.svg"] + b"\n<!-- edited during rendering -->\n"

    def render(svg, destination, width, height):
        if svg.name == "sheet-print.svg":
            if failure == "wrong-dimensions":
                width -= 1
            else:
                source.write_bytes(edited)
        image = Image.new("RGBA", (width, height))
        ImageDraw.Draw(image).rectangle((240, 240, 479, 479), fill="#ff0000")
        image.save(destination)

    monkeypatch.setattr(exporter, "render_svg", render)
    message = (
        "unexpected image dimensions"
        if failure == "wrong-dimensions"
        else "Design changed during export"
    )
    with pytest.raises(ValueError, match=message):
        exporter.export(
            source,
            project,
            json.loads(PLACEMENT_PROFILE.read_text()),
            "#ffffff",
            replace=True,
        )

    if failure == "edit-during-render":
        before["design.svg"] = edited
    assert snapshot(project) == before
