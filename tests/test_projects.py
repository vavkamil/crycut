"""Project creation, stale-export detection, and re-export without losing artwork."""

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from crycut import cli, exporter, projects
from crycut.commands import export as export_command
from crycut.sheets import PrintSheet, StaleDesignError

ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "config/profiles/stickerpix-v2.json").read_text())


class ProjectTests(unittest.TestCase):
    def test_new_project_and_draft_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT / "templates", root / "templates")
            with patch.object(projects, "ROOT", root):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["new", "new-sheet"]), 0)
                project = root / "projects/new-sheet"
                self.assertTrue((project / "design.svg").is_file())
                with self.assertRaises(FileExistsError):
                    projects.create("new-sheet")
            with redirect_stdout(io.StringIO()) as output:
                cli.main(["projects", "--directory", str(root / "projects"), "--json"])
            self.assertEqual(json.loads(output.getvalue())[0]["status"], "draft")

    def test_changed_design_cannot_print_old_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "sheet"
            shutil.copytree(ROOT / "projects/wordmarks", project)
            source = project / "design.svg"
            source.write_text(
                source.read_text().replace("ACCESS DENIED", "EDITED LABEL")
            )
            with self.assertRaises(StaleDesignError):
                PrintSheet.load(project)
            with redirect_stdout(io.StringIO()) as output:
                cli.main(["projects", "--directory", tmp, "--json"])
            self.assertEqual(json.loads(output.getvalue())[0]["status"], "needs export")

    def test_reexport_keeps_saved_settings_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "sheet"
            shutil.copytree(ROOT / "projects/wordmarks", project)
            source = project / "design.svg"
            original = source.read_bytes()
            modified_time = source.stat().st_mtime_ns
            with redirect_stdout(io.StringIO()):
                export_command.main([str(project)])
            manifest = json.loads((project / "export.json").read_text())
            self.assertEqual(manifest["background"], "#000000")
            self.assertEqual(manifest["border_mm"], 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(source.stat().st_mtime_ns, modified_time)
            self.assertEqual(
                manifest["source_sha256"], hashlib.sha256(original).hexdigest()
            )
            self.assertEqual(PrintSheet.load(project).cut_count, 28)

    def test_failed_render_preserves_previous_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "sheet"
            shutil.copytree(ROOT / "projects/wordmarks", project)
            before = {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()}
            with patch.object(
                exporter, "render_svg", side_effect=RuntimeError("renderer failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "renderer failed"):
                    exporter.export(
                        project / "design.svg",
                        project,
                        PROFILE,
                        "#ffffff",
                        replace=True,
                    )
            self.assertEqual(
                before,
                {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()},
            )
