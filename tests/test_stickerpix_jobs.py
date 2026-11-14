"""Compare uploads to protocol fixtures encrypted with a mock test key."""

import json
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from crycut.jobs import monitor, submit
from crycut.protocol import aes, frame
from crycut.sheets import PrintSheet
from crycut.transport import (
    Client,
    resolve_rfcomm_channel,
    spp_channel_from_sdp,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/print-cut"
# Mock AES key for offline protocol fixtures.
FIXTURE_KEY = b"crycut-test-key!"


class FakeSocket:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.sent = []

    def settimeout(self, value):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        result = next(self.replies, None)
        if result is None:
            raise TimeoutError("No reply")
        return result


def json_reply(obj):
    return frame(
        7,
        aes(FIXTURE_KEY, json.dumps(obj).encode()),
        channel=1,
        encrypted=True,
        fragments=1,
        fragment=1,
    )


class JobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fixed reference files for offline upload comparisons.
        cls.sheet = PrintSheet(
            (FIXTURES / "sheet-print.jpg").read_bytes(),
            (FIXTURES / "sheet-cut.plt").read_bytes(),
            28,
            "wordmark-fixture",
        )
        cls.records = [
            json.loads(line)
            for line in (FIXTURES / "upload-frames.jsonl").read_text().splitlines()
        ]

    def test_metadata_matches_fixture_combo_job(self):
        request = next(
            r["json"]
            for r in self.records
            if r.get("json", {}).get("method") == "combo-job"
        )
        actual = self.sheet.parameters()
        # Current filenames identify content; all protocol/media fields must
        # still match the independently prepared request.
        expected = request["params"]
        for generated, reference in zip(actual, expected):
            generated = {**generated, "params": dict(generated["params"])}
            filename = generated["params"]["document-name"]
            self.assertRegex(filename, r"^sheet_[0-9a-f]{12}\.(jpg|plt)$")
            self.assertEqual(generated["params"]["filePath"], filename)
            for key in ("document-name", "filePath"):
                generated["params"][key] = reference["params"][key]
            self.assertEqual(generated, reference)
        self.assertEqual(len(actual), len(expected))

    def test_all_encrypted_fragments_match_fixture(self):
        recorded = [r for r in self.records if r["channel"] == 2]
        generated = list(self.sheet.upload_frames(FIXTURE_KEY, 42))
        self.assertEqual(len(recorded), 713)
        self.assertEqual(len(generated), len(recorded))
        for packet, expected in zip(generated, recorded):
            self.assertEqual(packet.hex(), expected["frame_hex"])
            self.assertEqual(packet[20:-2].hex(), expected["payload_hex"])
            self.assertEqual(int.from_bytes(packet[18:20], "little"), expected["flags"])
            self.assertEqual(
                int.from_bytes(packet[10:14], "little"), expected["sequence"]
            )
            self.assertEqual(
                struct.unpack_from("<HH", packet, 14),
                (expected["fragments"], expected["fragment"]),
            )
            self.assertEqual(packet[-2], sum(packet[1:-2]) & 255)

    def test_new_job_id_and_session_key(self):
        key = b"0123456789ABCDEF"
        packets = list(self.sheet.upload_frames(key, 123))
        plain = [aes(key, p[20:-2], decrypt=True) for p in packets]
        self.assertTrue(all(struct.unpack_from("<I", p)[0] == 123 for p in plain))
        combined = b"".join(p[4:] for p in plain)
        self.assertEqual(combined[: len(self.sheet.data)], self.sheet.data)
        self.assertFalse(any(combined[len(self.sheet.data) :]))

    def test_notification_retained_and_request_id_matched(self):
        notification = {
            "method": "event.combo-job-finish",
            "params": {"job-id": 123, "job-state": 9},
        }
        sock = FakeSocket(
            [
                json_reply(notification)
                + json_reply({"id": 1, "result": {"job-id": 123}})
            ]
        )
        client = Client(sock)
        client.key = FIXTURE_KEY
        self.assertEqual(client.request("combo-job", [])["result"]["job-id"], 123)
        self.assertEqual(client.notifications, [notification])
        self.assertEqual(len(sock.sent), 1)

    def test_submission_timeout_is_not_retried(self):
        sock = FakeSocket([])
        client = Client(sock)
        client.key = FIXTURE_KEY
        with self.assertRaises(TimeoutError):
            client.request("combo-job", [])
        self.assertEqual(len(sock.sent), 1)

    def test_rejected_job_not_uploaded(self):
        sock = FakeSocket(
            [
                json_reply({"id": 1, "result": ["20"]}),
                json_reply({"id": 2, "error": {"code": 5401}}),
            ]
        )
        client = Client(sock)
        client.key = FIXTURE_KEY
        with self.assertRaisesRegex(ValueError, "Printer rejected combo-job"):
            submit(client, self.sheet, lambda *a, **kw: None)
        self.assertEqual(len(sock.sent), 2)
        self.assertTrue(all(p[3] == 1 for p in sock.sent))

    def test_submit_uses_assigned_id_and_monitors(self):
        sock = FakeSocket(
            [
                json_reply({"id": 1, "result": ["20"]}),
                json_reply({"id": 2, "result": {"job-id": 123}}),
            ]
        )
        client = Client(sock)
        client.key = FIXTURE_KEY
        with (
            patch("crycut.jobs.monitor", return_value={"job-state": 9}) as watch,
            patch("crycut.jobs.time.sleep"),
        ):
            result = submit(client, self.sheet, lambda *a, **kw: None)
        self.assertEqual(result["job-state"], 9)
        self.assertEqual(watch.call_args.args[1], 123)
        self.assertEqual(len(sock.sent), 715)
        for packet in sock.sent[2:]:
            self.assertEqual(
                struct.unpack_from("<I", aes(FIXTURE_KEY, packet[20:-2], decrypt=True))[
                    0
                ],
                123,
            )

    def test_countdown_zero_does_not_mark_job_complete(self):
        sock = FakeSocket(
            [
                json_reply({"id": 1, "result": [{"job-id": 123, "job-state": 3}]}),
                json_reply({"id": 2, "result": ["40", "3030", ":0:0"]}),
                json_reply({"id": 3, "result": [{"job-id": 123, "job-state": 9}]}),
            ]
        )
        client = Client(sock)
        client.key = FIXTURE_KEY
        events = []
        with patch("crycut.jobs.time.sleep"):
            result = monitor(
                client,
                123,
                lambda event, **data: events.append((event, data)),
                estimate_seconds=0,
            )
        self.assertEqual(events[0][0], "progress")
        self.assertEqual(events[0][1]["estimated_remaining_seconds"], 0)
        self.assertEqual(events[-1][0], "job")
        self.assertEqual(result["job-state"], 9)
        self.assertEqual(len(sock.sent), 3)

    def test_finish_event_survives_missing_poll_reply(self):
        sock = FakeSocket(
            [
                json_reply(
                    {
                        "method": "event.combo-job-finish",
                        "params": {"job-id": 123, "job-state": 9},
                    }
                )
            ]
        )
        client = Client(sock)
        client.key = FIXTURE_KEY
        self.assertEqual(monitor(client, 123, lambda *a, **kw: None)["job-state"], 9)

    def test_terminal_failure_is_not_success(self):
        sock = FakeSocket(
            [json_reply({"id": 1, "result": [{"job-id": 123, "job-state": 7}]})]
        )
        client = Client(sock)
        client.key = FIXTURE_KEY
        self.assertEqual(monitor(client, 123, lambda *a, **kw: None)["job-state"], 7)

    def test_real_plaintext_file_channel_error_is_reported(self):
        # Actual error packet from the first physical Python attempt (job 27).
        packet = bytes.fromhex(
            "7e64000207030000000016080000010001003f00"
            "7b226d6574686f64223a226576656e742e7270745f657272222c22706172616d73223a"
            "7b22636f6465223a2d383031322c226d657373616765223a22227d7d627e"
        )
        sock = FakeSocket([packet])
        client = Client(sock)
        client.key = FIXTURE_KEY
        with self.assertRaisesRegex(ValueError, "event.rpt_err.*-8012"):
            client.request("get-job-info", {"job-id": 27})
        self.assertEqual(client.notifications[0]["params"]["code"], -8012)
        self.assertEqual(len(sock.sent), 1)


class ServiceSelectionTests(unittest.TestCase):
    SDP = """Service Name: SerialPort
Service RecHandle: 0x1000f
Service Class ID List:
  "Serial Port" (0x1101)
Protocol Descriptor List:
  "L2CAP" (0x0100)
  "RFCOMM" (0x0003)
    Channel: 2
Service Name: Apple Inc.
Service RecHandle: 0x1000d
Service Class ID List:
  UUID 128: 00000000-deca-fade-deca-deafdecacaff
Protocol Descriptor List:
  "L2CAP" (0x0100)
  "RFCOMM" (0x0003)
    Channel: 22
"""

    def test_selects_serial_port_not_apple_service(self):
        self.assertEqual(spp_channel_from_sdp(self.SDP), 2)

    def test_missing_service_does_not_fall_back_to_one(self):
        with self.assertRaises(ValueError):
            spp_channel_from_sdp("Failed to connect to SDP server")

    def test_ambiguous_service_is_rejected(self):
        with self.assertRaises(ValueError):
            spp_channel_from_sdp(
                self.SDP + self.SDP.replace("Channel: 2\n", "Channel: 3\n")
            )

    def test_explicit_override_does_not_run_lookup(self):
        with patch(
            "crycut.transport.subprocess.run",
            side_effect=AssertionError("unexpected lookup"),
        ):
            self.assertEqual(resolve_rfcomm_channel("FC:97:A8:D0:53:78", 2), 2)


if __name__ == "__main__":
    unittest.main()
