"""Explicit, bounded read-only academic calendar capability for Gemini Live."""

from __future__ import annotations

from typing import Any

from friday.brightspace_calendar import AcademicStore, BrightspaceError, display_time
from friday.tools.registry import NoArguments, ToolRegistry, ToolSpec

ACADEMIC_TOOL_NAME = "get_academic_calendar"


def register_academic_calendar(
    registry: ToolRegistry, store: AcademicStore | None = None
) -> None:
    database = store or AcademicStore()

    def read(_arguments: NoArguments) -> dict[str, Any]:
        try:
            result = database.upcoming(days=7, limit=10)
        except (BrightspaceError, OSError, ValueError):
            return {"status": "error", "error": "academic_cache_unavailable"}
        if result.last_success is None:
            return {"status": "error", "error": "academic_calendar_not_synced"}
        return {
            "status": "ok",
            "source": "CUNY Brightspace iCalendar (local read-only cache)",
            "last_success": result.last_success,
            "coverage": "Only items published to the calendar feed; not a complete assignment list.",
            "recurrence_note": "Recurring series are not expanded into every occurrence.",
            "items": [
                {
                    "title": item.title,
                    "calendar_time": display_time(item),
                    "kind": item.kind,
                    "explicit_due": item.explicit_due,
                    "recurring_series": item.recurring,
                }
                for item in result.items
            ],
        }

    registry.register(
        ToolSpec(
            name=ACADEMIC_TOOL_NAME,
            description=(
                "Read up to 10 upcoming items in the next 7 days from the locally synced "
                "CUNY Brightspace calendar. Does not fetch the internet, submit work, "
                "or access grades. Only task DUE is an explicit deadline; VEVENT DTSTART "
                "is a scheduled event, not proof of a submission deadline. "
                "Empty results do not prove there are no assignments."
            ),
            arguments=NoArguments,
            handler=read,
            notice="Read up to 10 locally cached Brightspace calendar items.",
        )
    )
