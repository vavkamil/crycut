"""Validate project exports and prepare their print-and-cut payloads."""

import hashlib
import io
import json
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from PIL import Image

from .models import JsonObject, TimingEstimate
from .paths import PLACEMENT_PROFILE
from .plt import read_paths
from .projects import source_path
from .protocol import aes, frame
from .timing import estimate

CHUNK_BYTES = 892
MAX_COPIES = 36


class StaleDesignError(ValueError):
    """The editable source has changed since the saved print export."""


@dataclass(frozen=True)
class PrintSheet:
    """The validated files and cut count for a project's current export."""

    image: bytes
    cut: bytes
    cut_count: int
    name: str

    @classmethod
    def load(cls, directory: str | Path) -> Self:
        """Check a complete exported pair before any Bluetooth connection."""
        directory = Path(directory)
        manifest = json.loads((directory / "export.json").read_text())
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("Invalid export manifest")
        source = source_path(directory)
        if source.name != manifest.get("source") or hashlib.sha256(
            source.read_bytes()
        ).hexdigest() != manifest.get("source_sha256"):
            raise StaleDesignError(
                "Design changed; run crycut export for this project before printing"
            )
        expected = json.loads(PLACEMENT_PROFILE.read_text())
        if manifest.get("profile") != expected["name"]:
            raise ValueError("Unsupported export profile")
        image = (directory / "sheet-print.jpg").read_bytes()
        cut = (directory / "sheet-cut.plt").read_bytes()
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise ValueError("Manifest has no file hashes")
        for filename, content in (("sheet-print.jpg", image), ("sheet-cut.plt", cut)):
            if hashlib.sha256(content).hexdigest() != files.get(filename):
                raise ValueError(
                    f"{filename} differs from the exported manifest; export a fresh bundle"
                )
        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.format != "JPEG" or decoded.size != (1200, 2100):
                raise ValueError("Expected a 1200 x 2100 JPEG")
            decoded.load()
        paths = read_paths(cut, expected)
        if len(image) + len(cut) > CHUNK_BYTES * 65535:
            raise ValueError("Job exceeds protocol fragment limit")
        return cls(image, cut, len(paths), directory.name)

    @property
    def data(self) -> bytes:
        """Return cutter data followed by image data in upload order."""
        return self.cut + self.image

    @property
    def fragment_count(self) -> int:
        """Return the upload size without constructing the combined payload."""
        return (len(self.cut) + len(self.image) + CHUNK_BYTES - 1) // CHUNK_BYTES

    @property
    def estimate_seconds(self) -> float:
        """Return the estimated print-and-cut duration after upload."""
        return self.timing["total_seconds"]

    @property
    def timing(self) -> TimingEstimate:
        """Estimate duration from the selected cut geometry."""
        return estimate(self.cut)

    def parameters(self, copies: int = 1) -> list[JsonObject]:
        """Build one combo job for the requested number of identical sheets."""
        if type(copies) is not int or not 1 <= copies <= MAX_COPIES:
            raise ValueError(f"Copies must be an integer from 1 to {MAX_COPIES}")
        result = []
        stem = "sheet_" + hashlib.sha256(self.data).hexdigest()[:12]
        for method, extension, format_code, content in [
            ("print-job", "jpg", 9, self.image),
            ("cut-job", "plt", 18, self.cut),
        ]:
            name = f"{stem}.{extension}"
            result.append(
                {
                    "id": 0,
                    "method": method,
                    "params": {
                        "copies": copies,
                        "document-format": format_code,
                        "document-name": name,
                        "filePath": name,
                        "file-size": len(content),
                        "job-type": 600,
                        "media-size": 5013,
                        "media-type": 2030,
                    },
                    "spec-type": 0,
                }
            )
        return result

    def upload_frames(self, key: bytes, job_id: int) -> Iterator[bytes]:
        """Yield encrypted file fragments associated with the assigned job ID."""
        if type(job_id) is not int or not 0 < job_id <= 0xFFFFFFFF:
            raise ValueError("Invalid printer-assigned job ID")
        data = self.data
        count = self.fragment_count
        if count > 65535:
            raise ValueError("File exceeds protocol fragment limit")
        for offset in range(0, len(data), CHUNK_BYTES):
            index = offset // CHUNK_BYTES + 1
            body = struct.pack("<I", job_id) + data[offset : offset + CHUNK_BYTES]
            yield frame(
                6,
                aes(key, body),
                channel=2,
                sequence=index,
                encrypted=True,
                fragments=count,
                fragment=index,
                file_transfer=True,
                payload_type=2,
            )

    def preview(self, copies: int = 1) -> JsonObject:
        """Describe this sheet without contacting the printer."""
        timing = self.timing
        return {
            "copies": copies,
            "image": "1200 x 2100 JPEG",
            "image_bytes": len(self.image),
            "cut_bytes": len(self.cut),
            "transfer_bytes": len(self.cut) + len(self.image),
            "fragments": self.fragment_count,
            "job_type": 600,
            "media_size": 5013,
            "media_type": 2030,
            "estimated_execution_seconds": timing["total_seconds"],
            "name": self.name,
            "cut_outlines": self.cut_count,
            "timing_estimate": timing,
        }


def list_exports(directory: Path) -> list[JsonObject]:
    """Describe each project as ready, draft, stale, or invalid without Bluetooth."""
    rows: list[JsonObject] = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir():
            continue
        if not (path / "export.json").exists():
            try:
                source_path(path)
                rows.append({"name": path.name, "status": "draft", "valid": False})
            except ValueError as error:
                rows.append(
                    {
                        "name": path.name,
                        "status": "invalid",
                        "valid": False,
                        "error": str(error),
                    }
                )
            continue
        try:
            sheet = PrintSheet.load(path)
            rows.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "cuts": sheet.cut_count,
                    "bytes": len(sheet.cut) + len(sheet.image),
                    "status": "exported",
                    "valid": True,
                }
            )
        except StaleDesignError as error:
            rows.append(
                {
                    "name": path.name,
                    "status": "needs export",
                    "valid": False,
                    "error": str(error),
                }
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            rows.append(
                {
                    "name": path.name,
                    "status": "invalid",
                    "valid": False,
                    "error": str(error),
                }
            )
    return rows
