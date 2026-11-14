"""Complete local job preparation before submission; reuse it through upload."""

from unittest.mock import Mock, PropertyMock, patch

import pytest

from crycut import jobs
from crycut.sheets import PrintSheet


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
