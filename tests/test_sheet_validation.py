"""Invalid export bundles must be rejected before preparing printer uploads."""

import hashlib
import io
import json
import shutil

import pytest
from PIL import Image

from crycut.paths import ROOT
from crycut.sheets import CHUNK_BYTES, PrintSheet, StaleDesignError, list_exports


@pytest.fixture
def exported_project(tmp_path):
    project = tmp_path / "sheet"
    shutil.copytree(ROOT / "projects/wordmarks", project)
    return project


@pytest.mark.parametrize("name", ["wordmarks", "tech-icons"])
def test_bundled_manifest_has_no_duplicate_geometry(name):
    path = ROOT / "projects" / name / "export.json"
    manifest = json.loads(path.read_text())
    assert path.stat().st_size < 1024
    assert isinstance(manifest["profile"], str)
    assert "paths_mm" not in manifest


@pytest.mark.parametrize("filename", ["sheet-print.jpg", "sheet-cut.plt"])
def test_modified_export_file_fails_checksum_validation(exported_project, filename):
    path = exported_project / filename
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(
        ValueError, match=f"{filename} differs from the exported manifest"
    ):
        PrintSheet.load(exported_project)


def test_matching_hash_does_not_allow_malformed_cut_commands(exported_project):
    path = exported_project / "sheet-cut.plt"
    content = path.read_bytes() + b" D1,1"
    path.write_bytes(content)
    manifest_path = exported_project / "export.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][path.name] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsupported PLT header or ending"):
        PrintSheet.load(exported_project)


@pytest.mark.parametrize("manifest", [[], None, {}, {"version": 2}])
def test_invalid_manifest_is_rejected(exported_project, manifest):
    (exported_project / "export.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Invalid export manifest"):
        PrintSheet.load(exported_project)


@pytest.mark.parametrize(
    ("field", "value", "error", "message"),
    [
        ("profile", "unknown-profile", ValueError, "Unsupported export profile"),
        ("files", None, ValueError, "no file hashes"),
        ("files", {}, ValueError, "differs from the exported manifest"),
        ("source", "other.svg", StaleDesignError, "Design changed"),
    ],
)
def test_invalid_manifest_fields_are_rejected(
    exported_project, field, value, error, message
):
    manifest_path = exported_project / "export.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(error, match=message):
        PrintSheet.load(exported_project)


@pytest.mark.parametrize("format,size", [("PNG", (1200, 2100)), ("JPEG", (1200, 2099))])
def test_matching_hash_does_not_allow_wrong_image_format_or_dimensions(
    exported_project, format, size
):
    image = io.BytesIO()
    Image.new("RGB", size).save(image, format=format)
    content = image.getvalue()
    (exported_project / "sheet-print.jpg").write_bytes(content)
    manifest_path = exported_project / "export.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["sheet-print.jpg"] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="1200 x 2100 JPEG"):
        PrintSheet.load(exported_project)


@pytest.mark.parametrize("copies", [0, -1, True, 1.5, "2"])
def test_invalid_copies_cannot_be_encoded_in_job_parameters(copies):
    sheet = PrintSheet(b"image", b"cut", 1, "sheet")
    with pytest.raises(ValueError, match="positive integer"):
        sheet.parameters(copies)


@pytest.mark.parametrize("job_id", [0, -1, 2**32, True, "42"])
def test_invalid_assigned_id_cannot_be_encoded_in_upload(job_id):
    sheet = PrintSheet(b"image", b"cut", 1, "sheet")
    with pytest.raises(ValueError, match="printer-assigned job ID"):
        next(sheet.upload_frames(b"0123456789ABCDEF", job_id))


def test_upload_rejects_more_fragments_than_wire_header_can_represent():
    sheet = PrintSheet(b"x" * (CHUNK_BYTES * 65535 + 1), b"", 1, "too-large")
    assert sheet.fragment_count == 65536
    with pytest.raises(ValueError, match="protocol fragment limit"):
        next(sheet.upload_frames(b"0123456789ABCDEF", 42))


def test_listing_isolates_invalid_projects_and_ignores_regular_files(tmp_path):
    (tmp_path / "notes.txt").write_text("not a project")
    empty = tmp_path / "empty"
    empty.mkdir()
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "export.json").write_text("{broken")
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "design.svg").write_text("artwork")
    rows = list_exports(tmp_path)
    assert [(row["name"], row["status"]) for row in rows] == [
        ("corrupt", "invalid"),
        ("draft", "draft"),
        ("empty", "invalid"),
    ]
    assert all(row["valid"] is False for row in rows)
    assert "exactly one design" in rows[2]["error"]
    assert rows[0]["error"]
