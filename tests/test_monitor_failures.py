"""Failure cases for monitoring without a printer or real-time sleeps."""

import json
from unittest.mock import Mock

import pytest

from crycut import jobs
from crycut.protocol import frame
from crycut.transport import Client


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(jobs.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        jobs.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    return now


def client():
    return Mock(timeout=10.0, notifications=[])


@pytest.mark.parametrize("failure", [None, TimeoutError, ConnectionError, ValueError])
@pytest.mark.parametrize("state", [7, 8, 9])
@pytest.mark.parametrize("query", ["get-job-info", "get-prop"])
def test_terminal_notification_during_poll_wins(clock, failure, state, query):
    connection = client()
    completed = {"job-id": 30, "job-state": state}

    def request(method, params):
        if method == query:
            connection.notifications.append(
                {"method": "event.combo-job-finish", "params": completed}
            )
            if failure:
                raise failure("poll reply missing")
        if method == "get-job-info":
            return {"result": [{"job-id": 30, "job-state": 3}]}
        return {"result": [40, 3030, ":0:0"]}

    connection.request.side_effect = request
    emitted = Mock()
    assert jobs.monitor(connection, 30, emitted, max_wait=5) == completed
    emitted.assert_called_once_with("job", **completed)
    assert clock[0] == 0  # No extra sleep or poll after the completion event.
    assert [call.args[0] for call in connection.request.call_args_list] == (
        ["get-job-info"] if query == "get-job-info" else ["get-job-info", "get-prop"]
    )
    assert connection.timeout == 10


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ({"id": 1, "result": []}, "No status returned"),
        ({"id": 1, "error": {"code": 123}}, "Printer rejected get-job-info"),
    ],
)
@pytest.mark.parametrize(
    ("method", "params", "expected_state"),
    [
        ("event.combo-job-finish", {"job-id": 30, "job-state": 7}, 7),
        ("event.combo-job-finish", {"job-id": 30, "job-state": 8}, 8),
        ("event.combo-job-finish", {"job-id": 30, "job-state": 9}, 9),
        ("event.combo-job-finish", {"job-id": 31, "job-state": 9}, None),
        ("event.combo-job-finish", {"job-id": 30, "job-state": 3}, None),
        ("event.combo-job-finish", None, None),
        ("event.other", {"job-id": 30, "job-state": 9}, None),
        (None, None, None),
    ],
)
def test_poll_validation_error_requires_matching_terminal_event(
    reply, error, method, params, expected_state
):
    def packet(value):
        return frame(7, json.dumps(value).encode(), channel=1, fragments=1, fragment=1)

    notification = {"method": method, "params": params}
    incoming = packet(notification) if method is not None else b""
    connection = Mock()
    connection.recv.return_value = incoming + packet(reply)
    transport = Client(connection, timeout=10)
    transport.key = b"0123456789ABCDEF"
    emitted = Mock()
    if expected_state is not None:
        result = jobs.monitor(transport, 30, emitted)
        assert result == params
        emitted.assert_called_once_with("job", **params)
    else:
        with pytest.raises(ValueError, match=error):
            jobs.monitor(transport, 30, emitted)
        emitted.assert_not_called()
    assert transport.timeout == 10
    connection.sendall.assert_called_once()
    assert transport.notifications == ([notification] if method is not None else [])


def test_monitor_bounds_each_request_and_restores_timeout(clock):
    connection = client()
    observed = []

    def request(method, params):
        observed.append(connection.timeout)
        if method == "get-job-info":
            clock[0] += 4
            return {"result": [{"job-id": 30, "job-state": 3}]}
        clock[0] += connection.timeout
        raise TimeoutError("no status reply")

    connection.request.side_effect = request
    with pytest.raises(TimeoutError):
        jobs.monitor(connection, 30, Mock(), max_wait=5)
    assert observed == [5, 1]
    assert clock[0] == 5
    assert connection.timeout == 10


@pytest.mark.parametrize("send_seconds", [4, 5])
def test_slow_send_consumes_monitor_request_budget(clock, send_seconds):
    connection = Mock()

    def send(packet):
        clock[0] += send_seconds

    def receive(size):
        clock[0] += connection.settimeout.call_args.args[0]
        raise TimeoutError("no status reply")

    connection.sendall.side_effect = send
    connection.recv.side_effect = receive
    transport = Client(connection, timeout=10)
    transport.key = b"0123456789ABCDEF"
    with pytest.raises(TimeoutError):
        jobs.monitor(transport, 30, Mock(), max_wait=5)
    assert clock[0] == 5
    assert transport.timeout == 10
    connection.sendall.assert_called_once()
    if send_seconds == 4:
        assert connection.settimeout.call_args.args == (1,)
        connection.recv.assert_called_once()
    else:
        connection.recv.assert_not_called()


def test_paused_time_does_not_consume_remaining_estimate(clock):
    connection = client()
    states = iter([3, 5, 5, 3, 9])

    def request(method, params):
        if method == "get-job-info":
            return {"result": [{"job-id": 30, "job-state": next(states)}]}
        return {"result": [40, 3030, ":0:0"]}

    connection.request.side_effect = request
    progress = []
    jobs.monitor(
        connection,
        30,
        lambda event, **data: progress.append((event, data)),
        interval=10,
        estimate_seconds=20,
    )
    estimates = [
        data["estimated_remaining_seconds"]
        for event, data in progress
        if event == "progress"
    ]
    assert estimates == [20, None, None, 10]


def test_unrelated_and_malformed_notifications_do_not_complete_job(clock):
    connection = client()
    connection.notifications = [
        {"method": "event.combo-job-finish", "params": None},
        {"method": "event.combo-job-finish", "params": {"job-id": 31, "job-state": 9}},
    ]
    connection.request.return_value = {"result": [{"job-id": 30, "job-state": 7}]}
    assert jobs.monitor(connection, 30, Mock())["job-state"] == 7


@pytest.mark.parametrize("rows", [None, {}, [None, {"job-id": 31, "job-state": 9}]])
def test_missing_or_malformed_job_status_never_reports_completion(clock, rows):
    connection = client()
    connection.request.return_value = {"result": rows}
    emitted = Mock()
    with pytest.raises(ValueError, match="Unexpected get-job-info|No status returned"):
        jobs.monitor(connection, 30, emitted)
    emitted.assert_not_called()
    connection.request.assert_called_once()
    assert connection.timeout == 10


@pytest.mark.parametrize("state", [None, True, "3", 0, 10])
def test_unknown_job_state_stops_polling_without_progress(clock, state):
    connection = client()
    connection.request.return_value = {"result": [{"job-id": 30, "job-state": state}]}
    emitted = Mock()
    with pytest.raises(ValueError, match="Unexpected job state"):
        jobs.monitor(connection, 30, emitted)
    connection.request.assert_called_once()
    emitted.assert_not_called()
    assert connection.timeout == 10


@pytest.mark.parametrize("status", [None, {}, [40], [40, 3030, ":0:0", "extra"]])
def test_malformed_printer_status_never_emits_progress(clock, status):
    connection = client()
    connection.request.side_effect = [
        {"result": [{"job-id": 30, "job-state": 3}]},
        {"result": status},
    ]
    emitted = Mock()
    with pytest.raises(ValueError, match="Unexpected printer-state response"):
        jobs.monitor(connection, 30, emitted)
    emitted.assert_not_called()
    assert connection.timeout == 10


def test_existing_terminal_notification_avoids_polling(clock):
    connection = client()
    completed = {"job-id": 30, "job-state": 9}
    connection.notifications = [
        {"method": "event.combo-job-finish", "params": completed}
    ]
    emitted = Mock()
    assert jobs.monitor(connection, 30, emitted) == completed
    connection.request.assert_not_called()
    emitted.assert_called_once_with("job", **completed)


def test_job_poll_exhausting_budget_does_not_start_status_request(clock):
    connection = client()

    def request(*args):
        clock[0] = 5
        return {"result": [{"job-id": 30, "job-state": 3}]}

    connection.request.side_effect = request
    emitted = Mock()
    with pytest.raises(TimeoutError, match="completion is unknown"):
        jobs.monitor(connection, 30, emitted, max_wait=5)
    connection.request.assert_called_once()
    emitted.assert_not_called()
    assert connection.timeout == 10


def test_active_job_reaching_monitor_deadline_is_not_completed(clock):
    connection = client()
    connection.request.side_effect = [
        {"result": [{"job-id": 30, "job-state": 3, "copies": 2}]},
        {"result": [40, 3030, ":0:0"]},
    ]
    emitted = Mock()
    with pytest.raises(TimeoutError, match="completion is unknown"):
        jobs.monitor(connection, 30, emitted, interval=10, max_wait=5)
    assert clock[0] == 5
    assert connection.timeout == 10
    assert connection.request.call_count == 2
    emitted.assert_called_once()
    assert emitted.call_args.args == ("progress",)
    assert emitted.call_args.kwargs["copies"] == 2
