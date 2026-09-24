"""Read-only access to the operating system's configured local clock.

No geolocation, timezone conversion, network access, shell execution, or persistence.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

CLOCK_TOOL_NAME = "get_local_time"
CLOCK_FUNCTION_DECLARATION = {
    "name": CLOCK_TOOL_NAME,
    "description": (
        "Read the current date and time from this computer's operating system clock "
        "in its configured local timezone. Call this for current-time/date questions "
        "instead of guessing or using model knowledge. This does not reveal the "
        "computer's geographic location or give the time in other timezones. "
        "The function takes no arguments."
    ),
}


def read_local_clock(*, now: Callable[[], datetime] | None = None) -> dict[str, str]:
    """Capture a single local instant and return simple JSON-safe values.

    ``now`` is injectable for deterministic tests. In production Python gets the
    system clock and current local timezone/DST offset at the instant of the call.
    """
    current = (now or (lambda: datetime.now().astimezone()))()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Local clock must be timezone-aware")
    offset = current.strftime("%z")
    utc_offset = f"{offset[:3]}:{offset[3:]}" if offset else ""
    return {
        "local_datetime": current.isoformat(timespec="seconds"),
        "date": current.date().isoformat(),
        "time_12h": current.strftime("%I:%M:%S %p").lstrip("0"),
        "time_24h": current.strftime("%H:%M:%S"),
        "weekday": current.strftime("%A"),
        "timezone_name": current.tzname() or "Unknown",
        "utc_offset": utc_offset,
        "source": "computer_local_clock",
    }


def execute_local_tool(
    name: str | None,
    arguments: Any,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Strict allowlist: an unexpected model tool request can never run OS actions."""
    if name != CLOCK_TOOL_NAME:
        return {"status": "error", "error": "unknown_tool"}
    if arguments is not None and (not isinstance(arguments, Mapping) or arguments):
        return {"status": "error", "error": "invalid_arguments"}
    return {"status": "ok", **read_local_clock(now=now)}
