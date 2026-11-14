"""Submit one print-and-cut job and monitor printer-reported progress."""

import time

from .models import Emit, JsonObject
from .sheets import PrintSheet
from .transport import Client

JOB_STATES = {
    1: "Waiting",
    2: "Started",
    3: "In progress",
    4: "Temporarily unavailable",
    5: "Interrupted",
    6: "Terminating",
    7: "Failed",
    8: "Cancelled",
    9: "Completed",
}
STAGES = {
    2000: "Idle/starting",
    3016: "Decoding",
    3018: "Printing yellow",
    3019: "Printing magenta",
    3020: "Printing cyan",
    3021: "Protective layer",
    3025: "Returning home",
    3028: "Feeding for cutting",
    3029: "Finding origin",
    3030: "Cutting",
    3031: "Retracting blade",
}


def job_info(client: Client, job_id: int) -> JsonObject:
    """Read status for the requested printer job."""
    reply = client.request("get-job-info", {"job-id": job_id})
    rows = reply.get("result")
    if not isinstance(rows, list):
        raise ValueError(f"Unexpected get-job-info response: {reply}")
    for row in rows:
        if isinstance(row, dict) and row.get("job-id") == job_id:
            return row
    raise ValueError(f"No status returned for job {job_id}")


def finish_notification(client: Client, job_id: int) -> JsonObject | None:
    """Find the newest completion notification for this job."""
    for notification in reversed(client.notifications):
        if notification.get("method") == "event.combo-job-finish":
            params = notification.get("params", {})
            if isinstance(params, dict) and params.get("job-id") == job_id:
                return params
    return None


def monitor(
    client: Client,
    job_id: int,
    emit: Emit,
    *,
    interval: float = 2,
    max_wait: float = 900,
    estimate_seconds: float | None = None,
) -> JsonObject:
    """Poll the job and report progress until a confirmed terminal state."""
    started = time.monotonic()
    deadline = started + max_wait
    request_timeout = client.timeout
    paused_seconds = 0.0
    previous_elapsed = 0.0
    was_paused = False

    def terminal_notification() -> JsonObject | None:
        completed = finish_notification(client, job_id)
        if completed and completed.get("job-state") in (7, 8, 9):
            emit("job", **completed)
            return completed
        return None

    def bound_request() -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Stopped monitoring job {job_id}; completion is unknown"
            )
        client.timeout = min(request_timeout, remaining)

    try:
        while time.monotonic() < deadline:
            if (completed := terminal_notification()) is not None:
                return completed
            try:
                bound_request()
                info = job_info(client, job_id)
                if (completed := terminal_notification()) is not None:
                    return completed
                state = info.get("job-state")
                if state in (7, 8, 9):
                    emit("job", **info)
                    return info
                if type(state) is not int or state not in JOB_STATES:
                    raise ValueError(f"Unexpected job state: {state!r}")
                # Each poll contains two requests; both share the overall budget.
                bound_request()
                status = client.request(
                    "get-prop",
                    ["printer-state", "printer-sub-state", "printer-state-alerts"],
                ).get("result")
            except (TimeoutError, ConnectionError, ValueError):
                # A finish event may arrive during either request, even when its
                # response is invalid, never arrives, or the printer disconnects.
                if (completed := terminal_notification()) is not None:
                    return completed
                raise
            if (completed := terminal_notification()) is not None:
                return completed
            if not isinstance(status, list) or len(status) != 3:
                raise ValueError("Unexpected printer-state response")
            substate = int(status[1])
            elapsed = time.monotonic() - started
            if was_paused:
                paused_seconds += elapsed - previous_elapsed
            previous_elapsed = elapsed
            was_paused = state in (4, 5, 6)
            estimate = (
                None
                if was_paused or estimate_seconds is None
                else max(0, estimate_seconds - (elapsed - paused_seconds))
            )
            emit(
                "progress",
                job_id=job_id,
                job_state=state,
                stage=STAGES.get(substate, f"Stage {substate}"),
                substate=substate,
                alerts_raw=status[2],
                cutting_progress=info.get("cutting-progress"),
                cut_contours=info.get("cut-contours"),
                elapsed_seconds=round(elapsed, 1),
                estimated_remaining_seconds=None
                if estimate is None
                else round(estimate),
            )
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"Stopped monitoring job {job_id}; completion is unknown")
    finally:
        client.timeout = request_timeout


def submit(
    client: Client,
    sheet: PrintSheet,
    emit: Emit,
    *,
    interval: float = 2,
    max_wait: float = 900,
) -> JsonObject:
    """Submit one sheet exactly once, upload its files, and monitor the job."""
    if client.key is None:
        raise ValueError("Handshake required")
    estimate_seconds = sheet.estimate_seconds
    total = sheet.fragment_count
    state = client.request("get-prop", ["printer-state"]).get("result")
    if state != ["20"] and state != [20]:
        raise ValueError(f"Printer is not idle: {state}")
    emit("submitting", copies=1)
    response = client.request("combo-job", sheet.parameters())
    result = response.get("result")
    job_id = result.get("job-id") if isinstance(result, dict) else None
    if type(job_id) is not int or not 0 < job_id <= 0xFFFFFFFF:
        raise ValueError(f"No valid job ID in submission response: {response}")
    emit("accepted", job_id=job_id)
    for index, packet in enumerate(sheet.upload_frames(client.key, job_id), 1):
        client.send(packet)
        if index == 1 or index % 25 == 0 or index == total:
            emit("upload", job_id=job_id, sent=index, total=total)
        # RFCOMM applies link flow control. A small pacing delay avoids a burst
        # far faster than the observed ~5.6-second phone upload.
        time.sleep(0.01)
    emit("uploaded", job_id=job_id, estimated_execution_seconds=estimate_seconds)
    return monitor(
        client,
        job_id,
        emit,
        interval=interval,
        max_wait=max_wait,
        estimate_seconds=estimate_seconds,
    )
