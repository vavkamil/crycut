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


def optional_property(client: Client, name: str, request_id: int) -> Any:
    """Allow unsupported or unavailable extra properties without losing status."""
    try:
        return values(client.get_properties([name], request_id), 1)[0]
    except (OSError, ValueError):
        return None


def completed_count(counters: Any, key: str) -> int | None:
    """Missing sparse counters mean zero; invalid or unavailable counts do not."""
    count = counters.get(key, 0) if isinstance(counters, dict) else None
    return count if type(count) is int and count >= 0 else None


def ribbon_count(reply: JsonObject) -> int:
    """Read the remaining-print counter; negative values mean unavailable."""
    entry = values(reply, 1)[0]
    ribbon = entry.get("ribbon-info") if isinstance(entry, dict) else None
    if not isinstance(ribbon, list) or len(ribbon) != 6:
        raise ValueError("Unexpected ribbon-info response")
    count = ribbon[2]
    if type(count) not in (int, str):
        raise ValueError("Unexpected ribbon count")
    return int(count)


def read_status(client: Client, address: str, name: str) -> JsonObject:
    """Read printer status, allowing unavailable extra identity and usage data."""
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
    count = ribbon_count(client.get_properties(["ribbon-info"], 3))
    counters = optional_property(client, "big-data", 4)
    device = optional_property(client, "device-info", 5)
    if not isinstance(device, dict):
        device = {}
    bluetooth = optional_property(client, "bt-fw_ver", 6)
    extra = {
        "hardware_revision": device.get("hw_ver"),
        "sku": device.get("sku"),
        "serial_pcba": device.get("sn_pcba"),
        "serial_alps": device.get("sn_alps"),
        "bluetooth_firmware": (
            bluetooth.get("bt-fw_ver") if isinstance(bluetooth, dict) else None
        ),
    }
    return {
        **{
            key: value if isinstance(value, str) and value.strip() else None
            for key, value in extra.items()
        },
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
        "sticker_sheets_printed": completed_count(counters, "finished_5013"),
        "photo_sheets_printed": completed_count(counters, "finished_5012"),
        "usage_counters": counters if isinstance(counters, dict) else None,
    }
