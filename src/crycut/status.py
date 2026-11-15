"""Read printer identity, state, cartridge properties, and sticker-sheet usage."""

import re
from typing import Any

from .models import JsonObject
from .transport import Client

STATES = {
    10: "Initializing",
    20: "Idle",
    30: "Sleeping",
    40: "Working",
    50: "Shutting down",
    60: "Error",
}

ALERTS = {
    7001: "Cover open",
    7101: "Paper empty",
    7102: "Paper mismatch",
    7103: "Paper loading failed",
    7104: "Paper jam",
    7105: "Incorrect paper length",
    7106: "Paper ejection failed",
    7107: "Paper error",
    7108: "Paper tray missing",
    7110: "Paper detection problem",
    7111: "Incorrect paper size",
    7112: "Paper removal problem",
    7113: "Paper detection problem",
    7201: "Ribbon exhausted",
    7203: "Cartridge missing",
    7204: "Ribbon marker detection problem",
    7205: "Ribbon marker detection problem",
    7208: "Ribbon marker detection problem",
    7301: "Hardware error",
    7309: "Temperature too low",
    7310: "Temperature too high",
    8401: "Paper near blade",
    8402: "Paper jam during cutter initialization",
    8403: "Paper detected mid-path during cutter initialization",
    8404: "Cutter carriage failed to return home",
    8405: "Paper handover failed",
    8406: "Cutter paper pickup failed",
    8407: "Cutter paper pickup failed",
    8408: "Cutter carriage failed to find home",
    8409: "Paper-feed motor stalled",
    8410: "Cutter carriage motor stalled",
    8411: "Cutter paper ejection failed",
    8412: "Cutter sensor blocked",
    8413: "Cutter motor stalled",
}


def describe_alerts(raw: Any) -> list[str]:
    """Decode warning and error fields, preserving unknown or malformed alerts."""
    match = (
        re.fullmatch(r":([0-9]+(?:,[0-9]+)*):([0-9]+)", raw)
        if isinstance(raw, str)
        else None
    )
    if match is None:
        return [f"Unknown alert format ({raw!r})"]
    messages = []
    for kind, field in zip(("warning", "error"), match.groups(), strict=True):
        for token in field.split(","):
            code = int(token)
            if code:
                messages.append(ALERTS.get(code, f"Unknown {kind} ({code})"))
    return list(dict.fromkeys(messages))


def values(reply: JsonObject, count: int) -> list[Any]:
    """Require the expected number of returned property values."""
    result = reply.get("result")
    if not isinstance(result, list) or len(result) != count:
        raise ValueError(f"Unexpected property response: {reply!r}")
    return result


def read_status(client: Client, address: str, name: str) -> JsonObject:
    """Read printer status, allowing unavailable optional usage counters."""
    state, substate, alerts = values(
        client.get_properties(
            ["printer-state", "printer-sub-state", "printer-state-alerts"], 1
        ),
        3,
    )
    firmware, auto_off, model, serial = values(
        client.get_properties(
            ["firmware-revision", "auto-off-interval", "model", "serial-number"], 2
        ),
        4,
    )
    ribbon = values(client.get_properties(["ribbon-info"], 3), 1)[0]["ribbon-info"]
    if not isinstance(ribbon, list) or len(ribbon) != 6:
        raise ValueError("Unexpected ribbon-info response")
    count = int(ribbon[2])
    try:
        counters = values(client.get_properties(["big-data"], 4), 1)[0]
    except (OSError, ValueError):
        counters = None
    # The sparse counter response can omit a count that is still zero.
    sheets = counters.get("finished_5013", 0) if isinstance(counters, dict) else None
    if type(sheets) is not int or sheets < 0:
        sheets = None
    return {
        "name": name,
        "address": address,
        "model": model,
        "serial": serial,
        "firmware": firmware,
        "state": STATES.get(int(state), "Unknown"),
        "state_code": int(state),
        "substate_code": int(substate),
        "alerts_raw": alerts,
        "alerts": describe_alerts(alerts),
        "auto_off_seconds": int(auto_off["auto-off-interval"]),
        "ribbon_count": count if count >= 0 else None,
        "ribbon_count_raw": count,
        "sticker_sheets_printed": sheets,
    }
