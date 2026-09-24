"""Read-only Brightspace iCalendar ingestion. No model SDK, Qt, or credentials here.

All calendar text is untrusted. No URL, response body, or exception is logged.
VEVENT start times are calendar events, NOT verified assignment due dates.
VTODO DUE is the only explicit deadline recognized in this first slice.
Recurring series are flagged but not expanded; do not claim a complete schedule.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from friday.schedule import default_database_path

MAX_FEED_BYTES = 2_000_000
MAX_ITEMS = 3000
MAX_TITLE = 180


class BrightspaceError(RuntimeError):
    """Sanitized, user-presentable failure; never contains credentials or source text."""


@dataclass(frozen=True, slots=True)
class AcademicItem:
    uid: str
    title: str
    kind: str
    when: str
    all_day: bool
    explicit_due: bool
    recurring: bool


@dataclass(frozen=True, slots=True)
class AcademicSnapshot:
    items: tuple[AcademicItem, ...]
    last_success: str | None


def default_academic_path() -> Path:
    return default_database_path().with_name("brightspace.sqlite3")


def _value(component: Any, name: str) -> date | datetime | None:
    field = component.get(name)
    if field is None:
        return None
    try:
        parsed = component.decoded(name)
    except (TypeError, ValueError, KeyError) as exc:
        raise BrightspaceError("Calendar has an invalid date or time.") from exc
    if not isinstance(parsed, (date, datetime)):
        raise BrightspaceError("Calendar has an unsupported date or time.")
    return parsed


def _iso(value: date | datetime) -> str:
    return value.isoformat()


def parse_calendar(payload: bytes) -> tuple[AcademicItem, ...]:
    """Validate a complete iCalendar snapshot before any database mutation."""
    if not payload or len(payload) > MAX_FEED_BYTES:
        raise BrightspaceError("Calendar response is empty or exceeds the size limit.")
    if not payload.lstrip(b"\\xef\\xbb\\xbf \\t\\r\\n").startswith(b"BEGIN:VCALENDAR"):
        raise BrightspaceError("Calendar response is not an iCalendar document.")
    try:
        from icalendar import Calendar

        calendar = Calendar.from_ical(payload)
    except Exception as exc:
        raise BrightspaceError("Unable to parse the Brightspace calendar.") from exc
    if getattr(calendar, "name", None) != "VCALENDAR":
        raise BrightspaceError("Calendar document has an invalid root.")
    records: dict[str, AcademicItem] = {}
    try:
        for component in calendar.walk():
            kind = getattr(component, "name", "")
            if kind not in {"VEVENT", "VTODO"}:
                continue
            if str(component.get("STATUS", "")).upper() == "CANCELLED":
                continue
            uid = str(component.get("UID", "")).strip()
            if not uid or len(uid) > 512:
                raise BrightspaceError("Calendar contains an invalid item identifier.")
            recurrence_id = _value(component, "RECURRENCE-ID")
            identity = uid if recurrence_id is None else f"{uid}::{_iso(recurrence_id)}"
            title = " ".join(str(component.get("SUMMARY", "Untitled event")).split())
            title = title[:MAX_TITLE] or "Untitled event"
            due = _value(component, "DUE") if kind == "VTODO" else None
            when = due or _value(component, "DTSTART")
            if when is None:
                # Undated tasks cannot be safely included in a dated agenda.
                continue
            item = AcademicItem(
                uid=identity,
                title=title,
                kind="task" if kind == "VTODO" else "event",
                when=_iso(when),
                all_day=not isinstance(when, datetime),
                explicit_due=due is not None,
                recurring=component.get("RRULE") is not None,
            )
            old = records.get(identity)
            if old is not None and old != item:
                raise BrightspaceError("Calendar contains conflicting duplicate item IDs.")
            records[identity] = item
            if len(records) > MAX_ITEMS:
                raise BrightspaceError("Calendar exceeds the supported item limit.")
    except BrightspaceError:
        raise
    except Exception as exc:
        raise BrightspaceError("Calendar contains an unsupported event.") from exc
    if not records:
        # Protect previously valid cache against an accidentally empty or malformed feed.
        raise BrightspaceError("Calendar has no dated events; previous data was preserved.")
    return tuple(records.values())


def _when_local(item: AcademicItem) -> datetime:
    value = datetime.fromisoformat(item.when)
    if item.all_day:
        # Date-only events remain local all-day dates, never UTC midnight.
        value = datetime.combine(value.date(), datetime.min.time())
    if value.tzinfo is None:
        value = value.astimezone()  # iCalendar floating time: device's local zone.
    return value.astimezone()


def _upcoming(items: tuple[AcademicItem, ...], *, now: datetime, days: int,
              limit: int) -> tuple[AcademicItem, ...]:
    if not 1 <= days <= 31 or not 1 <= limit <= 30:
        raise ValueError("Invalid academic calendar range")
    local_now = now.astimezone()
    horizon = local_now + timedelta(days=days)
    eligible = []
    for item in items:
        when = _when_local(item)
        if item.all_day:
            if local_now.date() <= when.date() <= horizon.date():
                eligible.append((when, item))
        elif local_now <= when <= horizon:
            eligible.append((when, item))
    eligible.sort(key=lambda x: (x[0], x[1].title, x[1].uid))
    return tuple(item for _, item in eligible[:limit])


def display_time(item: AcademicItem) -> str:
    value = _when_local(item)
    if item.all_day:
        return value.strftime("%a, %b %d (all day)")
    return value.strftime("%a, %b %d · %I:%M %p %Z").strip()


class AcademicStore:
    """Separate local SQLite snapshot; never writes to reminder or Google tables."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_academic_path() if path is None else Path(path)

    def replace(self, items: tuple[AcademicItem, ...]) -> None:
        if not items or len(items) > MAX_ITEMS:
            raise BrightspaceError("Refusing an empty or oversized calendar update.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS academic_items ("
                    "uid TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL, "
                    "when_iso TEXT NOT NULL, all_day INTEGER NOT NULL, "
                    "explicit_due INTEGER NOT NULL, recurring INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS academic_meta ("
                    "id INTEGER PRIMARY KEY CHECK(id=1), last_success TEXT NOT NULL)"
                )
                db.execute("DELETE FROM academic_items")
                db.executemany(
                    "INSERT INTO academic_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (x.uid, x.title, x.kind, x.when, int(x.all_day),
                         int(x.explicit_due), int(x.recurring))
                        for x in items
                    ],
                )
                db.execute(
                    "INSERT INTO academic_meta (id, last_success) VALUES (1, ?) "
                    "ON CONFLICT(id) DO UPDATE SET last_success=excluded.last_success",
                    (datetime.now(UTC).isoformat(),),
                )
        except sqlite3.Error as exc:
            raise BrightspaceError("Unable to update the local academic calendar.") from exc

    def snapshot(self) -> AcademicSnapshot:
        if not self.path.is_file():
            return AcademicSnapshot((), None)
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                rows = db.execute(
                    "SELECT uid, title, kind, when_iso, all_day, explicit_due, recurring "
                    "FROM academic_items"
                ).fetchall()
                meta = db.execute(
                    "SELECT last_success FROM academic_meta WHERE id=1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise BrightspaceError("Unable to read the local academic calendar.") from exc
        return AcademicSnapshot(
            tuple(AcademicItem(uid, title, kind, when, bool(all_day), bool(due), bool(rec))
                  for uid, title, kind, when, all_day, due, rec in rows),
            meta[0] if meta is not None else None,
        )

    def upcoming(self, *, days: int = 7, limit: int = 10,
                 now: datetime | None = None) -> AcademicSnapshot:
        result = self.snapshot()
        return AcademicSnapshot(
            _upcoming(result.items, now=now or datetime.now().astimezone(),
                      days=days, limit=limit),
            result.last_success,
        )
