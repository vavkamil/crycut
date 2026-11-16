"""Reject invalid wire replies without retrying printer requests."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from crycut import transport
from crycut.protocol import aes, frame

KEY = b"0123456789ABCDEF"


def reply(value, **kwargs):
    options = dict(channel=1, fragments=1, fragment=1)
    options.update(kwargs)
    return frame(7, json.dumps(value).encode(), **options)


def test_service_lookup_uses_requested_printer_and_timeout(monkeypatch):
    lookup = Mock(
        return_value=Mock(
            stdout='Service RecHandle: 1\n"Serial Port" (0x1101)\n'
            'Protocol Descriptor List:\n"RFCOMM" (0x0003)\n Channel: 7\n'
        )
    )
    monkeypatch.setattr(transport.subprocess, "run", lookup)
    assert transport.resolve_rfcomm_channel("AA:BB:CC:DD:EE:FF", timeout=3) == 7
    lookup.assert_called_once_with(
        ["sdptool", "browse", "AA:BB:CC:DD:EE:FF"],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )


@pytest.mark.parametrize(
    ("failure", "expected", "message"),
    [
        (FileNotFoundError(), ValueError, "sdptool is required"),
        (subprocess.TimeoutExpired("sdptool", 3), ConnectionError, "lookup failed"),
        (subprocess.CalledProcessError(1, "sdptool"), ConnectionError, "lookup failed"),
    ],
)
def test_service_lookup_errors_are_actionable(failure, expected, message, monkeypatch):
    lookup = Mock(side_effect=failure)
    monkeypatch.setattr(transport.subprocess, "run", lookup)
    with pytest.raises(expected, match=message):
        transport.resolve_rfcomm_channel("AA:BB:CC:DD:EE:FF")
    lookup.assert_called_once()


@pytest.mark.parametrize("channel", [0, 31, True, "2"])
def test_invalid_channel_never_runs_lookup(channel, monkeypatch):
    lookup = Mock()
    monkeypatch.setattr(transport.subprocess, "run", lookup)
    with pytest.raises(ValueError, match="channel must be 1-30"):
        transport.resolve_rfcomm_channel("AA:BB:CC:DD:EE:FF", channel)
    lookup.assert_not_called()


@pytest.mark.parametrize(
    "descriptor", ["", '"RFCOMM"\n Channel: 0', '"RFCOMM"\n Channel: 31']
)
def test_serial_service_without_valid_channel_is_rejected(descriptor):
    with pytest.raises(ValueError, match="Expected one advertised Serial Port channel"):
        transport.spp_channel_from_sdp(
            'Service RecHandle: 1\n"Serial Port" (0x1101)\n'
            f"Protocol Descriptor List:\n{descriptor}"
        )


@pytest.mark.parametrize("damage", ["boundary", "header", "ending"])
def test_invalid_frame_structure_is_rejected(damage):
    packet = bytearray(frame(7, b"{}"))
    if damage == "boundary":
        packet[0] = 0
    elif damage == "header":
        packet[1] = 0x65
        packet[-2] = sum(packet[1:-2]) & 255
    else:
        packet[-1] = 0
    connection = Mock()
    connection.recv.return_value = bytes(packet)
    with pytest.raises(ValueError, match="boundary|header|delimiter"):
        transport.Client(connection).receive()
    connection.recv.assert_called_once()


def test_incomplete_frame_obeys_absolute_deadline(monkeypatch):
    monkeypatch.setattr(transport.time, "monotonic", Mock(side_effect=[0, 2]))
    connection = Mock()
    connection.recv.return_value = b"\x7e"
    client = transport.Client(connection)
    with pytest.raises(TimeoutError, match="complete frame"):
        client.receive(deadline=1)
    connection.recv.assert_called_once()
    connection.settimeout.assert_called_once_with(1)
    assert client.buffer == b"\x7e"


def test_wrong_greeting_clears_key_before_any_confirmation():
    connection = Mock()
    connection.recv.return_value = frame(0x13, b"ok")
    client = transport.Client(connection)
    client.key = KEY
    with pytest.raises(ValueError, match="DH-info"):
        client.handshake()
    assert client.key is None
    connection.sendall.assert_called_once_with(frame(0x10, b"hello"))


@pytest.mark.parametrize(
    ("packet", "message"),
    [
        (reply({"id": 1}, channel=3), "command/channel"),
        (frame(6, b"{}", channel=1), "command/channel"),
        (reply({"id": 1}, fragments=2), "Multi-frame"),
        (reply({"id": 1}, fragment=0), "Multi-frame"),
        (reply([]), "JSON object"),
        (reply({"id": 1}, channel=2), "non-event reply"),
        (frame(7, b"not-json", channel=1, fragments=1, fragment=1), "Expecting value"),
    ],
)
def test_invalid_json_replies_fail_after_one_send(packet, message):
    connection = Mock()
    connection.recv.return_value = packet
    client = transport.Client(connection)
    client.key = KEY
    with pytest.raises(ValueError, match=message):
        client.request("combo-job", [])
    connection.sendall.assert_called_once()
    connection.recv.assert_called_once()


def test_unknown_encryption_is_rejected_before_decoding():
    packet = bytearray(reply({"id": 1}))
    packet[19] |= 4  # Encryption mode 1, rather than supported mode 5.
    packet[-2] = sum(packet[1:-2]) & 255
    connection = Mock()
    connection.recv.return_value = bytes(packet)
    client = transport.Client(connection)
    client.key = KEY
    with pytest.raises(ValueError, match="Unknown response encryption"):
        client.request("get-prop", [])
    assert not any(event["direction"] == "decoded" for event in client.events)
    connection.sendall.assert_called_once()


def test_old_reply_is_ignored_and_explicit_ids_advance_next_request():
    connection = Mock()
    connection.recv.side_effect = [
        reply({"id": 2, "result": ["old"]}) + reply({"id": 8, "result": [20]}),
        reply({"id": 9, "result": [40]}),
    ]
    client = transport.Client(connection)
    client.key = KEY
    assert client.get_properties(["printer-state"], 8) == {"id": 8, "result": [20]}
    assert client.request("get-prop", []) == {"id": 9, "result": [40]}
    requests = [
        json.loads(aes(KEY, call.args[0][20:-2], decrypt=True).rstrip(b"\0"))
        for call in connection.sendall.call_args_list
    ]
    assert [request["id"] for request in requests] == [8, 9]
    assert requests[0]["params"] == ["printer-state"]
    assert client.notifications == []
