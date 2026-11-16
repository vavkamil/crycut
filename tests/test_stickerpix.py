"""Protocol checks using bytes captured from the real StickerPix."""

import unittest

import pytest

from crycut.protocol import aes, derive_exchange, frame
from crycut.transport import Client

# Actual greeting reply received by the notebook, before completing key exchange.
GREETING = bytes.fromhex(
    "7e6400ff11020000000041000000000000002400"
    "320000004536393639443344343935424533324334323731343535333044454332363243"
    "347e"
)


def test_frame_payload_limit_accepts_boundary_and_rejects_overflow():
    payload = b"x" * 1023
    packet = frame(6, payload)
    assert packet[20:-2] == payload
    assert int.from_bytes(packet[18:20], "little") & 1023 == 1023
    with pytest.raises(ValueError, match="single-frame limit"):
        frame(6, payload + b"x")


@pytest.mark.parametrize("body", [b"", GREETING[20:-3], GREETING[20:-2] + b"0"])
def test_exchange_rejects_wrong_body_length(body):
    with pytest.raises(ValueError, match="36-byte"):
        derive_exchange(body)


@pytest.mark.parametrize(
    ("generator", "modulus", "peer"),
    [(3, 17, 4), (2, 4, 2), (2, 17, 0), (2, 17, 17)],
)
def test_exchange_rejects_invalid_parameters_before_randomness(
    generator, modulus, peer, monkeypatch
):
    from unittest.mock import Mock

    random = Mock()
    monkeypatch.setattr("crycut.protocol.secrets.randbelow", random)
    body = f"{generator:X}".encode().ljust(4, b"\0")
    body += f"{modulus:016X}{peer:016X}".encode()
    with pytest.raises(ValueError, match="DH parameters"):
        derive_exchange(body)
    random.assert_not_called()


class Stream:
    def __init__(self, parts):
        self.parts = iter(parts)

    def recv(self, size):
        return next(self.parts, b"")

    def settimeout(self, timeout):
        pass


class ProtocolTests(unittest.TestCase):
    def test_hello_matches_android_capture(self):
        self.assertEqual(
            frame(0x10, b"hello").hex(),
            "7e6400ff1003000000000000000000000000050068656c6c6f8f7e",
        )

    def test_fragmented_and_coalesced_real_reply(self):
        client = Client(
            Stream([GREETING[:1], GREETING[1:19], GREETING[19:] + GREETING])
        )
        self.assertEqual(client.receive(), GREETING)
        self.assertEqual(client.receive(), GREETING)

    def test_corrupt_reply_is_rejected(self):
        packet = bytearray(GREETING)
        packet[30] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            Client(Stream([packet])).receive()

    def test_truncated_reply_is_rejected(self):
        with self.assertRaises(ConnectionError):
            Client(Stream([GREETING[:-2]])).receive()

    def test_both_peers_derive_same_key_and_confirmation(self):
        p = int("E6969D3D495BE32C", 16)
        printer_secret, client_secret = 1234567, 7654321
        public = pow(2, printer_secret, p)
        body = b"2\0\0\0" + b"E6969D3D495BE32C" + f"{public:016X}".encode()
        key, confirmation = derive_exchange(body, client_secret)
        client_public = int(confirmation[:16], 16)
        printer_shared = pow(client_public, printer_secret, p)
        printer_key = f"{printer_shared:X}".encode().ljust(16, b"\0")
        self.assertEqual(key, printer_key)
        self.assertEqual(aes(printer_key, confirmation[16:], decrypt=True), body[4:20])

    def test_invalid_encrypted_block_is_rejected(self):
        with self.assertRaises(ValueError):
            aes(b"0" * 16, b"short", decrypt=True)


class HandshakeFailureTests(unittest.TestCase):
    def test_confirmed_handshake_enables_encrypted_requests(self):
        import json
        from unittest.mock import Mock, patch

        key, _ = derive_exchange(GREETING[20:-2], secret=126)
        reply = frame(
            7,
            aes(key, json.dumps({"id": 1, "result": [20]}).encode()),
            channel=1,
            encrypted=True,
            fragments=1,
            fragment=1,
        )
        connection = Mock()
        # A single socket read can contain several complete responses.
        connection.recv.side_effect = [GREETING + frame(0x13, b"ok") + reply]
        client = Client(connection)
        with patch("crycut.protocol.secrets.randbelow", return_value=123):
            self.assertTrue(client.handshake()["accepted"])
        self.assertEqual(client.key, key)
        self.assertEqual(client.request("get-prop", ["printer-state"])["result"], [20])
        self.assertEqual(connection.sendall.call_count, 3)

    def test_failed_handshake_never_leaves_a_usable_session_key(self):
        from unittest.mock import Mock

        for response in (frame(0x13, b"no"), TimeoutError("missing confirmation")):
            with self.subTest(response=response):
                connection = Mock()
                connection.recv.side_effect = [GREETING, response]
                client = Client(connection)
                client.key = b"previous-session"  # A retry must discard this too.
                with self.assertRaises((ValueError, TimeoutError)):
                    client.handshake()
                self.assertIsNone(client.key)
                writes = connection.sendall.call_count
                with self.assertRaisesRegex(ValueError, "Handshake required"):
                    client.request("get-prop", ["printer-state"])
                self.assertEqual(connection.sendall.call_count, writes)


if __name__ == "__main__":
    unittest.main()
