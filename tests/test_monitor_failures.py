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
def test_terminal_notification_during_status_query_wins(clock, failure, state):
    connection = client()
    completed = {"job-id": 30, "job-state": state}

    def request(method, params):
        if method == "get-job-info":
            return {"result": [{"job-id": 30, "job-state": 3}]}
        connection.notifications.append(
            {"method": "event.combo-job-finish", "params": completed}
        )
        if failure:
            raise failure("status reply missing")
        return {"result": [40, 3030, ":0:0"]}

    connection.request.side_effect = request
    emitted = Mock()
    assert jobs.monitor(connection, 30, emitted) == completed
    emitted.assert_called_once_with("job", **completed)
    assert clock[0] == 0  # No extra sleep or poll after the completion event.
    assert connection.request.call_count == 2


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
