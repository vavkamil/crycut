"""
Bluetooth service lookup and framed requests over one RFCOMM connection.

Only single-frame JSON replies are supported.
"""

import json
import re
import socket
import struct
import subprocess
import time
from typing import Any

from .models import JsonObject
from .protocol import aes, derive_exchange, frame


def spp_channel_from_sdp(text: str) -> int:
    """Extract the unique Serial Port service channel from SDP output."""
    channels = set()
    for record in re.split(r"(?=Service RecHandle:)", text):
        classes = record.split("Protocol Descriptor List:", 1)[0]
        if not re.search(r"\(0x1101\)", classes):
            continue
        match = re.search(r'"RFCOMM"[^\n]*\n\s*Channel:\s*(\d+)', record)
        if match and 1 <= int(match[1]) <= 30:
            channels.add(int(match[1]))
    if len(channels) != 1:
        raise ValueError(
            f"Expected one advertised Serial Port channel, found {sorted(channels)}; use --channel explicitly"
        )
    return channels.pop()


def resolve_rfcomm_channel(
    address: str, override: int | None = None, timeout: float = 10
) -> int:
    """Query only this printer's service record, without scanning nearby devices."""
    if override is not None:
        if type(override) is not int or not 1 <= override <= 30:
            raise ValueError("RFCOMM channel must be 1-30")
        return override
    try:
        result = subprocess.run(
            ["sdptool", "browse", address],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except FileNotFoundError as error:
        raise ValueError(
            "sdptool is required for service lookup; alternatively use --channel"
        ) from error
    except subprocess.SubprocessError as error:
        raise ConnectionError(f"Bluetooth service lookup failed: {error}") from error
    return spp_channel_from_sdp(result.stdout)


class Client:
    """Frame requests and retain replies and notifications for one connection."""

    def __init__(self, connection: socket.socket, timeout: float = 10) -> None:
        """Wrap an established socket with framing and event collection."""
        self.connection = connection
        self.timeout = timeout
        self.buffer = bytearray()
        self.events: list[JsonObject] = []
        self.key: bytes | None = None
        self.request_id = 0
        self.notifications: list[JsonObject] = []

    def send(self, packet: bytes) -> None:
        """Log and send a complete frame once."""
        self.events.append({"direction": "tx", "hex": packet.hex()})
        self.connection.settimeout(self.timeout)
        self.connection.sendall(packet)

    def receive(self, deadline: float | None = None) -> bytes:
        """Receive one checked frame before the deadline, retaining excess bytes."""
        deadline = time.monotonic() + self.timeout if deadline is None else deadline
        while True:
            if self.buffer and self.buffer[0] != 0x7E:
                raise ValueError("Unexpected frame boundary")
            if len(self.buffer) >= 20:
                length = (int.from_bytes(self.buffer[18:20], "little") & 1023) + 22
                if len(self.buffer) >= length:
                    packet = bytes(self.buffer[:length])
                    del self.buffer[:length]
                    self.events.append({"direction": "rx", "hex": packet.hex()})
                    if packet[-1] != 0x7E or packet[-2] != sum(packet[1:-2]) & 255:
                        raise ValueError("Invalid frame delimiter or checksum")
                    if packet[1:3] != b"\x64\0":
                        raise ValueError("Unexpected protocol header")
                    return packet
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for a complete frame")
            self.connection.settimeout(remaining)
            part = self.connection.recv(4096)
            if not part:
                raise ConnectionError("Printer closed the connection")
            self.buffer.extend(part)

    def handshake(self) -> JsonObject:
        """Establish the session key and require printer confirmation."""
        self.key = None
        self.send(frame(0x10, b"hello"))
        info = self.receive()
        if info[3:5] != b"\xff\x11":
            raise ValueError("Expected DH-info response")
        key, confirmation = derive_exchange(info[20:-2])
        self.send(frame(0x12, confirmation, sequence=1))
        reply = self.receive()
        if reply[3:5] != b"\xff\x13" or reply[20:-2].rstrip(b"\0") != b"ok":
            raise ValueError("Printer rejected handshake")
        self.key = key
        return {
            "generator": 2,
            "modulus": info[24:40].decode(),
            "printer_public": info[40:56].decode(),
            "accepted": True,
        }

    def get_properties(self, properties: list[str], request_id: int) -> JsonObject:
        """Request named printer properties."""
        return self.request("get-prop", properties, request_id=request_id)

    def request(
        self, method: str, params: Any, request_id: int | None = None
    ) -> JsonObject:
        """
        Send once, match its response ID, and retain unsolicited notifications.

        Never retry automatically: a timed-out job request may have been accepted.
        """
        if self.key is None:
            raise ValueError("Handshake required")
        if request_id is None:
            request_id = self.request_id + 1
        self.request_id = max(self.request_id, request_id)
        request = {"id": request_id, "method": method, "params": params, "spec-type": 1}
        payload = json.dumps(request, separators=(",", ":")).encode()
        # Sending and receiving share one budget, including Bluetooth backpressure.
        deadline = time.monotonic() + self.timeout
        self.send(
            frame(
                6,
                aes(self.key, payload),
                channel=1,
                sequence=1,
                encrypted=True,
                fragments=1,
                fragment=1,
            )
        )
        while time.monotonic() < deadline:
            reply = self.receive(deadline)
            if reply[3] not in (1, 2) or reply[4] != 7:
                raise ValueError("Unexpected JSON response command/channel")
            fragments, fragment = struct.unpack_from("<HH", reply, 14)
            if (fragments, fragment) != (1, 1):
                raise ValueError("Multi-frame JSON replies are not implemented")
            flags = int.from_bytes(reply[18:20], "little")
            body = reply[20:-2]
            if ((flags >> 10) & 7) == 5:
                body = aes(self.key, body, decrypt=True)
            elif (flags >> 10) & 7:
                raise ValueError("Unknown response encryption")
            # Keep plaintext for inspection even if this is not expected JSON.
            self.events.append({"direction": "decoded", "hex": body.hex()})
            response = json.loads(body.rstrip(b"\0"))
            if not isinstance(response, dict):
                raise ValueError("Expected a JSON object")
            if "method" in response:
                self.notifications.append(response)
                if response["method"] == "event.rpt_err":
                    raise ValueError(
                        f"Printer reported event.rpt_err: {response.get('params')}"
                    )
                continue
            if reply[3] != 1:
                raise ValueError("Unexpected non-event reply on file-transfer channel")
            if response.get("id") == request_id:
                if "error" in response:
                    raise ValueError(f"Printer rejected {method}: {response['error']}")
                return response
            # Ignore unsolicited notifications or replies to older queries.
        raise TimeoutError("No matching JSON reply")
