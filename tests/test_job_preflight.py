"""Complete local job preparation before submission; reuse it through upload."""

from unittest.mock import Mock, PropertyMock, patch

import pytest

from crycut import jobs
from crycut.paths import ROOT
from crycut.sheets import PrintSheet


def test_submission_without_handshake_never_queries_or_uploads():
    client = Mock(key=None)
    sheet = Mock()
    with pytest.raises(ValueError, match="Handshake required"):
        jobs.submit(client, sheet, Mock())
    client.request.assert_not_called()
    client.send.assert_not_called()
    sheet.parameters.assert_not_called()


@pytest.mark.parametrize("state", [[40], ["40"], [], None])
def test_busy_or_unknown_printer_state_never_submits(state):
    client = Mock(key=b"0123456789ABCDEF")
    client.request.return_value = {"result": state}
    sheet = PrintSheet.load(ROOT / "projects/wordmarks")
    emitted = Mock()
    with pytest.raises(ValueError, match="not idle"):
        jobs.submit(client, sheet, emitted)
    client.request.assert_called_once_with("get-prop", ["printer-state"])
    client.send.assert_not_called()
    emitted.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        {},
        {"job-id": True},
        {"job-id": 0},
        {"job-id": -1},
        {"job-id": 2**32},
        {"job-id": "42"},
    ],
)
def test_invalid_assigned_id_never_starts_upload_or_monitoring(result, monkeypatch):
    client = Mock(key=b"0123456789ABCDEF")
    client.request.side_effect = [{"result": [20]}, {"result": result}]
    sheet = PrintSheet.load(ROOT / "projects/wordmarks")
    emitted = Mock()
    monitor = Mock()
    monkeypatch.setattr(jobs, "monitor", monitor)
    with pytest.raises(ValueError, match="No valid job ID"):
        jobs.submit(client, sheet, emitted)
    assert client.request.call_count == 2
    client.send.assert_not_called()
    monitor.assert_not_called()
    emitted.assert_called_once_with("submitting", copies=1)


def test_timing_failure_happens_before_job_submission():
    client = Mock(key=b"0123456789ABCDEF")
    sheet = PrintSheet(b"image", b"cut", 1, "example")
    with patch.object(PrintSheet, "timing", new_callable=PropertyMock) as timing:
        timing.side_effect = OSError("cannot read timing configuration")
        with pytest.raises(OSError, match="timing"):
            jobs.submit(client, sheet, Mock())
    client.request.assert_not_called()
    client.send.assert_not_called()


def test_upload_reuses_preflight_estimate_without_reading_configuration_again():
    client = Mock(key=b"0123456789ABCDEF")
    client.request.side_effect = [
        {"result": ["20"]},
        {"result": {"job-id": 42}},
    ]
    sheet = PrintSheet(b"image", b"cut", 1, "example")
    with (
        patch.object(PrintSheet, "timing", new_callable=PropertyMock) as timing,
        patch.object(jobs, "monitor", return_value={"job-state": 9}) as monitor,
        patch.object(jobs.time, "sleep"),
    ):
        timing.side_effect = [{"total_seconds": 120.0}, OSError("config changed")]
        assert jobs.submit(client, sheet, Mock()) == {"job-state": 9}
        assert timing.call_count == 1
    client.send.assert_called_once()
    assert monitor.call_args.kwargs["estimate_seconds"] == 120.0
