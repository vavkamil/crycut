"""Preserve print diagnostics without letting disk errors hide printer results."""

import json
import sys
from pathlib import Path

from ..models import JsonObject


class EventLog:
    """Write JSONL events and report an unavailable log once per run."""

    def __init__(self, path: Path) -> None:
        """Create a new log; failure here prevents the command from connecting."""
        self.path = path
        self._stream = path.open("x")
        self._failed = False

    def _report_failure(self, error: OSError) -> None:
        if not self._failed:
            print(f"\nCould not save event log: {error}", file=sys.stderr)
        self._failed = True

    def write(self, record: JsonObject) -> None:
        """Append an event, disabling writes after an I/O failure."""
        if self._failed:
            return
        try:
            self._stream.write(json.dumps(record) + "\n")
            self._stream.flush()
        except OSError as error:
            self._report_failure(error)

    def close(self) -> None:
        """Close the stream without replacing the command's result on failure."""
        try:
            self._stream.close()
        except OSError as error:
            self._report_failure(error)

    def save_protocol(self, events: list[JsonObject]) -> None:
        """Save the connection transcript independently of the event stream."""
        try:
            self.path.with_suffix(".protocol.json").write_text(
                json.dumps(events, indent=2) + "\n"
            )
        except OSError as error:
            print(f"Could not save protocol log: {error}", file=sys.stderr)
