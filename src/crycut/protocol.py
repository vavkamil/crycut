"""Packet encoding, encryption, and session key derivation."""

import secrets
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes(key: bytes, data: bytes, *, decrypt: bool = False) -> bytes:
    """Encrypt or decrypt using the printer protocol padding and AES mode."""
    if decrypt and len(data) % 16:
        raise ValueError("Encrypted payload is not a multiple of 16 bytes")
    if not decrypt:
        data += b"\0" * (-len(data) % 16)
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    ctx = cipher.decryptor() if decrypt else cipher.encryptor()
    return ctx.update(data) + ctx.finalize()


def frame(
    command: int,
    body: bytes,
    *,
    channel: int = 255,
    sequence: int = 0,
    encrypted: bool = False,
    fragments: int = 0,
    fragment: int = 0,
    file_transfer: bool = False,
    payload_type: int = 3,
) -> bytes:
    """Build a framed protocol packet with flags and checksum."""
    if len(body) > 1023:
        raise ValueError("Payload exceeds single-frame limit")
    flags = len(body) | (5 << 10 if encrypted else 0) | (0x2000 if file_transfer else 0)
    header = bytes([0x7E, 0x64, 0, channel, command, payload_type])
    header += struct.pack("<IIHHH", 0, sequence, fragments, fragment, flags)
    packet = header + body
    return packet + bytes([sum(packet[1:]) & 255, 0x7E])


def derive_exchange(body: bytes, secret: int | None = None) -> tuple[bytes, bytes]:
    """Derive the session key and handshake confirmation from the greeting."""
    if len(body) != 36:
        raise ValueError("Expected 36-byte DH-info body")
    generator = int(body[:4].rstrip(b"\0"), 16)
    modulus = int(body[4:20], 16)
    peer_public = int(body[20:36], 16)
    if generator != 2 or not 4 < modulus < 2**64 or not 0 < peer_public < modulus:
        raise ValueError("Unexpected DH parameters")
    # Generate a fresh private value using the operating system RNG.
    secret = secrets.randbelow(modulus - 4) + 3 if secret is None else secret
    public = pow(generator, secret, modulus)
    shared = pow(peer_public, secret, modulus)
    # The wire format uses uppercase hex ASCII with right NUL padding.
    key = format(shared, "X").encode().ljust(16, b"\0")
    confirmation = format(public, "016X").encode() + aes(key, body[4:20])
    return key, confirmation
