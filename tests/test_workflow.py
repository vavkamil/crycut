"""Offline tests for selecting generated jobs, sending, and time estimates."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crycut import cli
from crycut.commands import print as printer
from crycut.protocol import aes
from crycut.sheets import PrintSheet
from crycut.timing import estimate

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("copies", [1, 3])
def test_selected_sheet_and_copies_reach_submission_and_logs(
    copies, tmp_path, monkeypatch, capsys, printer_config
):
    events = [{"direction": "tx", "hex": "7e"}]
    client = MagicMock(events=events)
    connection = MagicMock()
    monkeypatch.setattr(printer, "PRINTER_CONFIG", printer_config)
    monkeypatch.setattr(printer.socket, "socket", lambda *args: connection)
    monkeypatch.setattr(printer, "resolve_rfcomm_channel", lambda *args: 2)
    monkeypatch.setattr(printer, "Client", lambda *args: client)
    log = tmp_path / "events.jsonl"
    args = ["tech-icons", "--send", "--log", str(log)]
    if copies != 1:
        args.extend(["--copies", str(copies)])
    with patch.object(printer, "submit", return_value={"job-state": 9}) as submit:
        assert printer.main(args) == 0
    submit.assert_called_once()
    sheet = submit.call_args.args[1]
    assert isinstance(sheet, PrintSheet)
    assert sheet.name == "tech-icons" and sheet.cut_count == 32
    assert submit.call_args.kwargs["copies"] == copies
    client.handshake.assert_called_once()
    connection.__enter__.return_value.connect.assert_called_once_with(
        ("AA:BB:CC:DD:EE:FF", 2)
    )
    selected = json.loads(log.read_text().splitlines()[0])
    assert selected["event"] == "selected_sheet"
    assert selected["name"] == "tech-icons" and selected["copies"] == copies
    assert json.loads(log.with_suffix(".protocol.json").read_text()) == events
    assert "per sheet" in capsys.readouterr().out


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

    @pytest.mark.usefixtures("offline_printer")
    def test_print_without_send_never_connects(self):
        with redirect_stdout(io.StringIO()) as output:
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

    def test_bad_bundle_rejected_before_bluetooth(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "crycut.commands.print.socket.socket",
                side_effect=AssertionError("Bluetooth accessed"),
            ),
            patch(
                "crycut.commands.print.resolve_rfcomm_channel",
                side_effect=AssertionError("SDP accessed"),
            ),
            patch(
                "crycut.commands.print.load_printer",
                side_effect=AssertionError("Configuration accessed before validation"),
            ),
            redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(printer.main([tmp, "--send"]), 1)
        self.assertIn("export.json", errors.getvalue())

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
