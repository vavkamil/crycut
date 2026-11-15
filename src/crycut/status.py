"""Read printer identity, state, cartridge properties, and sticker-sheet usage."""

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
        "auto_off_seconds": int(auto_off["auto-off-interval"]),
        "ribbon_count": count if count >= 0 else None,
        "ribbon_count_raw": count,
        "sticker_sheets_printed": sheets,
    }
