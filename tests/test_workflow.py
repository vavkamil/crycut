"""Offline tests for selecting generated jobs, sending, and time estimates."""

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from crycut import cli
from crycut.commands import print as printer
from crycut.plt import read_paths
from crycut.protocol import aes
from crycut.sheets import PrintSheet
from crycut.timing import estimate

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_active_bundles_validate_and_have_estimates(self):
        for name, count in (("wordmarks", 28), ("tech-icons", 32)):
            sheet = PrintSheet.load(ROOT / "projects" / name)
            self.assertEqual(sheet.cut_count, count)
            self.assertEqual(sheet.timing["contours"], count)
            self.assertGreater(sheet.estimate_seconds, sheet.timing["print_seconds"])
            self.assertEqual(
                sheet.parameters()[0]["params"]["file-size"], len(sheet.image)
            )

    def test_print_without_send_never_connects(self):
        with (
            patch(
                "crycut.commands.print.socket.socket",
                side_effect=AssertionError("Bluetooth accessed"),
            ),
            patch(
                "crycut.commands.print.resolve_rfcomm_channel",
                side_effect=AssertionError("SDP accessed"),
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(printer.main(["wordmarks"]), 0)
        self.assertIn("Print preview", output.getvalue())

    def test_send_requires_selected_job(self):
        with (
            patch(
                "crycut.commands.print.socket.socket",
                side_effect=AssertionError("Bluetooth accessed"),
            ),
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as stopped:
                printer.main(["--send"])
        self.assertEqual(stopped.exception.code, 2)

    def test_tampered_jpeg_and_mismatched_geometry_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            shutil.copytree(ROOT / "projects/wordmarks", job)
            image = (job / "sheet-print.jpg").read_bytes()
            (job / "sheet-print.jpg").write_bytes(image + b"changed")
            with self.assertRaisesRegex(ValueError, "manifest"):
                PrintSheet.load(job)
            (job / "sheet-print.jpg").write_bytes(image)
            # Even with an updated hash, malformed PLT is still rejected.
            cut = (job / "sheet-cut.plt").read_bytes() + b" D1,1"
            (job / "sheet-cut.plt").write_bytes(cut)
            manifest = json.loads((job / "export.json").read_text())
            manifest["files"]["sheet-cut.plt"] = hashlib.sha256(cut).hexdigest()
            (job / "export.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "PLT"):
                PrintSheet.load(job)

    def test_bad_bundle_rejected_before_bluetooth(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "crycut.commands.print.socket.socket",
                side_effect=AssertionError("Bluetooth accessed"),
            ),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(printer.main([tmp, "--send"]), 1)

    def test_generated_upload_reassembles_selected_pair(self):
        sheet = PrintSheet.load(ROOT / "projects/tech-icons")
        key = b"0123456789ABCDEF"
        frames = list(sheet.upload_frames(key, 321))
        bodies = [aes(key, packet[20:-2], decrypt=True) for packet in frames]
        self.assertTrue(
            all(int.from_bytes(body[:4], "little") == 321 for body in bodies)
        )
        combined = b"".join(body[4:] for body in bodies)
        self.assertEqual(combined[: len(sheet.data)], sheet.data)
        self.assertFalse(any(combined[len(sheet.data) :]))

    def test_send_selection_and_logging_with_fake_connection(self):
        client = MagicMock()
        client.events = []
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("crycut.commands.print.socket.socket") as socket_mock,
            patch("crycut.commands.print.resolve_rfcomm_channel", return_value=2),
            patch("crycut.commands.print.Client", return_value=client),
            patch(
                "crycut.commands.print.PRINTER_CONFIG",
                ROOT / "config/printer.example.json",
            ),
            patch(
                "crycut.commands.print.submit", return_value={"job-state": 9}
            ) as submit,
            redirect_stdout(io.StringIO()),
        ):
            log = Path(tmp) / "events.jsonl"
            result = printer.main(
                [
                    "tech-icons",
                    "--send",
                    "--log",
                    str(log),
                ]
            )
            self.assertEqual(result, 0)
            sheet = submit.call_args.args[1]
            self.assertIsInstance(sheet, PrintSheet)
            self.assertEqual(sheet.cut_count, 32)
            client.handshake.assert_called_once()
            socket_mock.return_value.__enter__.return_value.connect.assert_called_once()
            selected = json.loads(log.read_text().splitlines()[0])
            self.assertEqual(selected["name"], "tech-icons")
            self.assertTrue(log.with_suffix(".protocol.json").is_file())

    def test_jobs_list_alias(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["jobs", "--json"]), 0)
        self.assertTrue(
            {"wordmarks", "tech-icons"}.issubset(
                {r["name"] for r in json.loads(output.getvalue())}
            )
        )


class TimingTests(unittest.TestCase):
    def test_reference_reproduces_measured_baseline(self):
        result = estimate(
            (ROOT / "tests/fixtures/print-cut/sheet-cut.plt").read_bytes()
        )
        self.assertAlmostEqual(result["print_seconds"], 113.3)
        self.assertAlmostEqual(result["cut_seconds"], 110.5)
        self.assertEqual(result["contours"], 28)

    def test_more_cut_distance_and_more_contours_cost_more(self):
        small = estimate(b"U0,0 D10,0 D10,10 D0,10 D0,0")
        large = estimate(b"U0,0 D20,0 D20,20 D0,20 D0,0")
        split = estimate(b"U0,0 D5,0 D5,5 D0,5 D0,0 U20,20 D25,20 D25,25 D20,25 D20,20")
        self.assertGreater(large["cut_seconds"], small["cut_seconds"])
        self.assertGreater(split["cut_seconds"], small["cut_seconds"])
        self.assertEqual(large["print_seconds"], small["print_seconds"])


class PltValidationTests(unittest.TestCase):
    def test_fixture_paths_count_excludes_final_parking_move(self):
        profile = json.loads((ROOT / "config/profiles/stickerpix-v2.json").read_text())
        paths = read_paths(
            (ROOT / "tests/fixtures/print-cut/sheet-cut.plt").read_bytes(), profile
        )
        self.assertEqual(len(paths), 28)

    def test_open_out_of_bounds_and_unsupported_paths_rejected(self):
        profile = json.loads((ROOT / "config/profiles/stickerpix-v2.json").read_text())
        bodies = [
            "U631,2060 D231,2060 D231,3866",  # open
            "U1,1 D2,1 D2,2 D1,1",  # outside calibrated canvas
            "U631,2060 D231,2060 D631,2060",  # collapsed line
            "U631,2060 Z231,2060 D631,2060",  # unsupported command
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ValueError):
                read_paths(
                    (
                        profile["plt_header"] + " " + body + profile["plt_trailer"]
                    ).encode(),
                    profile,
                )

    def test_minimal_manifest_has_no_duplicate_geometry(self):
        for name in ("wordmarks", "tech-icons"):
            path = ROOT / "projects" / name / "export.json"
            data = json.loads(path.read_text())
            self.assertLess(path.stat().st_size, 1024)
            self.assertIsInstance(data["profile"], str)
            self.assertNotIn("paths_mm", data)
