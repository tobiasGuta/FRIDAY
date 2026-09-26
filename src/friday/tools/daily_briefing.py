"""On-demand local daily briefing; combines existing read-only data only.

No scheduler, network, new provider session, database migration, or reminder
mutation. Every academic item remains a published calendar entry, not a
verified assignment inventory. All titles/text are untrusted display data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from friday.brightspace_calendar import (
    AcademicStore,
    BrightspaceError,
    display_time,
    local_date,
    source_labeled_due,
)
from friday.schedule import (
    default_database_path,
    read_today_reminder_preview,
)
from friday.tools.registry import NoArguments, ToolRegistry, ToolSpec

BRIEFING_TOOL_NAME = "get_daily_briefing"
ACADEMIC_LIMIT = 8
REMINDER_LIMIT = 5


class DailyBriefingService:
    def __init__(
        self,
        *,
        academic_store: AcademicStore | None = None,
        reminder_path: Path | None = None,
        include_academic: bool = True,
        include_reminders: bool = True,
    ) -> None:
        self.academic_store = academic_store or AcademicStore()
        self.reminder_path = (
            default_database_path() if reminder_path is None else Path(reminder_path)
        )
        self.include_academic = include_academic
        self.include_reminders = include_reminders

    def read(self, *, now: datetime | None = None) -> dict[str, Any]:
        local_now = (now or datetime.now().astimezone()).astimezone()
        result: dict[str, Any] = {
            "status": "ok",
            "local_date": local_now.date().isoformat(),
            "generated_local": local_now.isoformat(timespec="seconds"),
            "academic": {"state": "disabled", "items": [], "more": False},
            "reminders": {"state": "disabled", "items": [], "more": False},
            "coverage": (
                "Only published cached calendar items and saved local reminders. "
                "Calendar data may omit assignments. Recurring series are not expanded."
            ),
        }
        if self.include_academic:
            result["academic"] = self._academic(local_now)
        if self.include_reminders:
            result["reminders"] = self._reminders(local_now)
        return result

    def _academic(self, now: datetime) -> dict[str, Any]:
        try:
            snapshot = self.academic_store.snapshot()
            if snapshot.last_success is None:
                return {"state": "not_synced", "items": [], "more": False}
            stamp = datetime.fromisoformat(snapshot.last_success)
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("Sync timestamp must be timezone-aware")
            last = stamp.astimezone()
            age = (now - last).total_seconds()
            freshness = (
                "time_unreliable" if age < -300
                else "older_than_24h" if age > 24 * 3600
                else "recent"
            )
            today = [
                item for item in snapshot.items
                if local_date(item) == now.date().isoformat()
            ]
            today.sort(key=lambda item: (
                datetime.fromisoformat(item.when).astimezone(),
                item.title.casefold(), item.uid,
            ))
            return {
                "state": "cached",
                "freshness": freshness,
                "last_sync_local": last.isoformat(timespec="seconds"),
                "last_sync_display": last.strftime(
                    "%b %d, %Y · %I:%M %p %Z"
                ).strip(),
                "count": len(today),
                "more": len(today) > ACADEMIC_LIMIT,
                "items": [
                    {
                        "title": item.title,
                        "calendar_time": display_time(item),
                        "kind": item.kind,
                        "explicit_due": item.explicit_due,
                        "source_labeled_due": source_labeled_due(item),
                        "recurring_series": item.recurring,
                    }
                    for item in today[:ACADEMIC_LIMIT]
                ],
            }
        except (BrightspaceError, OSError, sqlite3.Error, ValueError, OverflowError):
            return {"state": "unavailable", "items": [], "more": False}

    def _reminders(self, now: datetime) -> dict[str, Any]:
        try:
            count, preview = read_today_reminder_preview(
                path=self.reminder_path,
                limit=REMINDER_LIMIT,
                now=now.timestamp(),
            )
            return {
                "state": "ok" if self.reminder_path.is_file() else "not_set_up",
                "count": count,
                "more": count > REMINDER_LIMIT,
                "items": [
                    {
                        "text": text,
                        "due_local": datetime.fromtimestamp(due_at).astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "due_display": datetime.fromtimestamp(due_at).astimezone().strftime(
                            "%I:%M %p"
                        ),
                    }
                    for text, due_at in preview
                ],
            }
        except (OSError, sqlite3.Error, ValueError, OverflowError):
            return {"state": "unavailable", "items": [], "more": False}


def register_daily_briefing(
    registry: ToolRegistry, service: DailyBriefingService
) -> None:
    registry.register(ToolSpec(
        name=BRIEFING_TOOL_NAME,
        description=(
            "Read today's on-demand summary from explicitly enabled local sources: "
            "saved pending reminders due later today, and already-cached published "
            "Brightspace calendar items. No network, sync, scheduling, grades, "
            "or assignment-list verification. Check per-source state, sync age and "
            "coverage; do not interpret ordinary VEVENTs as due dates."
        ),
        arguments=NoArguments,
        handler=lambda _args: service.read(),
        notice="Read today's local briefing on request",
    ))
