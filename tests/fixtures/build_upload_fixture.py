"""Build fixed upload vectors from crycut's wordmark export, without its encoder.

Run manually from the repository root using the locked test environment.
Never run this from tests or regenerate expectations to conceal a regression.
"""

import json
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "projects/wordmarks"
DESTINATION = HERE / "print-cut"
KEY = b"crycut-test-key!"
JOB_ID = 42
CHUNK_SIZE = 892


def main():
    """Copy project output and independently encode the documented wire format."""
    image = (SOURCE / "sheet-print.jpg").read_bytes()
    cut = (SOURCE / "sheet-cut.plt").read_bytes()
    entries = []
    for method, extension, format_code, content in (
        ("print-job", "jpg", 9, image),
        ("cut-job", "plt", 18, cut),
    ):
        entries.append(
            {
                "id": 0,
                "method": method,
                "params": {
                    "copies": 1,
                    "document-format": format_code,
                    "document-name": f"fixture-sheet.{extension}",
                    "filePath": f"fixture-sheet.{extension}",
                    "file-size": len(content),
                    "job-type": 600,
                    "media-size": 5013,
                    "media-type": 2030,
                },
                "spec-type": 0,
            }
        )
    records = [
        {
            "channel": 1,
            "json": {"id": 1, "method": "combo-job", "params": entries, "spec-type": 1},
        }
    ]
    data = cut + image
    count = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE
    for index in range(count):
        plain = (
            JOB_ID.to_bytes(4, "little")
            + data[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
        )
        plain += bytes((-len(plain)) % 16)
        encryptor = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
        payload = encryptor.update(plain) + encryptor.finalize()
        flags = 0x3400 | len(payload)
        header = bytes.fromhex("7e640002060200000000") + struct.pack(
            "<IHHH", index + 1, count, index + 1, flags
        )
        packet = header + payload
        packet += bytes((sum(packet[1:]) % 256, 0x7E))
        records.append(
            {
                "channel": 2,
                "sequence": index + 1,
                "fragments": count,
                "fragment": index + 1,
                "flags": flags,
                "frame_hex": packet.hex(),
                "payload_hex": payload.hex(),
            }
        )
    DESTINATION.mkdir(exist_ok=True)
    (DESTINATION / "sheet-print.jpg").write_bytes(image)
    (DESTINATION / "sheet-cut.plt").write_bytes(cut)
    (DESTINATION / "upload-frames.jsonl").write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
    )


if __name__ == "__main__":
    main()
