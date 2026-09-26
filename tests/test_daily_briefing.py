"""Daily briefing: local calendar/reminder evidence without sync or model calls."""

from __future__ import annotations

from datetime import datetime, timedelta

from friday.brightspace_calendar import AcademicItem, AcademicSnapshot, AcademicStore
from friday.config import Settings
from friday.providers.gemini_live import BRIEFING_INSTRUCTION, GeminiLiveProvider
from friday.schedule import ScheduleStore, read_today_reminder_preview
from friday.tools.daily_briefing import (
    BRIEFING_TOOL_NAME,
    DailyBriefingService,
    briefing_display_lines,
    register_daily_briefing,
)
from friday.tools.registry import ToolRegistry


class FakeCalendar:
    def __init__(self, snapshot: AcademicSnapshot):
        self.current = snapshot
        self.reads = 0

    def snapshot(self):
        self.reads += 1
        return self.current


def _local_morning() -> datetime:
    return datetime.now().astimezone().replace(
        hour=9, minute=0, second=0, microsecond=0,
    )


def test_combined_briefing_keeps_calendar_categories_and_local_day(tmp_path):
    now = _local_morning()
    today = now.date().isoformat()
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    items = (
        AcademicItem(
            "class", "Algorithms lecture", "event", now.replace(hour=10).isoformat(),
            False, False, False,
        ),
        AcademicItem(
            "labeled", "Worksheet - Due", "event", now.replace(hour=11).isoformat(),
            False, False, False,
        ),
        AcademicItem("task", "Lab report", "task", today, True, True, False),
        AcademicItem("future", "Tomorrow's class", "event", tomorrow, True, False, False),
    )
    calendar = FakeCalendar(AcademicSnapshot(items, now.isoformat()))
    path = tmp_path / "schedules.sqlite3"
    store = ScheduleStore(path)
    store.create("reminder", "Prepare for class", now.timestamp() + 3600,
                 now=now.timestamp())
    store.create("reminder", "Tomorrow reminder", now.timestamp() + 86400,
                 now=now.timestamp())
    store.create("timer", "Not a reminder", now.timestamp() + 1800,
                 now=now.timestamp())
    before = path.read_bytes()
    service = DailyBriefingService(
        academic_store=calendar, reminder_path=path,
    )
    result = service.read(now=now)
    assert result["status"] == "ok"
    assert result["local_date"] == today
    assert result["academic"]["count"] == 3
    assert {x["title"] for x in result["academic"]["items"]} == {
        "Algorithms lecture", "Worksheet - Due", "Lab report",
    }
    assert result["reminders"]["count"] == 1
    assert result["reminders"]["items"][0]["text"] == "Prepare for class"
    assert calendar.reads == 1
    assert path.read_bytes() == before
    lines = briefing_display_lines(result)
    assert any("Due (task): Lab report" in line for line in lines)
    assert any("Brightspace-labeled Due (event; not verified)" in line for line in lines)
    assert any("Scheduled (calendar event): Algorithms lecture" in line for line in lines)
    assert not any("Tomorrow reminder" in line for line in lines)
    assert any("coverage is incomplete" in line for line in lines)


def test_unsynced_unavailable_and_disabled_sources_are_not_assumed_empty(tmp_path):
    missing_academic = tmp_path / "absent-academic.sqlite3"
    missing_reminders = tmp_path / "absent-reminders.sqlite3"
    service = DailyBriefingService(
        academic_store=AcademicStore(missing_academic),
        reminder_path=missing_reminders,
    )
    result = service.read(now=_local_morning())
    assert result["academic"]["state"] == "not_synced"
    assert result["reminders"]["state"] == "not_set_up"
    assert not missing_academic.exists()
    assert not missing_reminders.exists()
    lines = briefing_display_lines(result)
    assert any("no locally synced" in line for line in lines)
    assert not any("no assignments" in line.lower() for line in lines)
    disabled = DailyBriefingService(
        academic_store=FakeCalendar(AcademicSnapshot((), None)),
        reminder_path=missing_reminders, include_academic=False,
        include_reminders=False,
    ).read(now=_local_morning())
    assert disabled["academic"]["state"] == "disabled"
    assert disabled["reminders"]["state"] == "disabled"
    assert not missing_reminders.exists()
    missing_reminders.write_bytes(b"not-a-database")
    error = service.read(now=_local_morning())
    assert error["reminders"]["state"] == "unavailable"


def test_today_query_excludes_expired_next_day_and_timers_without_migration(tmp_path):
    now = _local_morning()
    path = tmp_path / "schedules.sqlite3"
    store = ScheduleStore(path)
    for index in range(6):
        store.create(
            "reminder", f"Today {index}", now.timestamp() + 60 * (index + 1),
            now=now.timestamp(),
        )
    store.create("reminder", "Tomorrow", now.timestamp() + 86400,
                 now=now.timestamp())
    store.create("timer", "Timer", now.timestamp() + 90,
                 now=now.timestamp())
    before = path.read_bytes()
    count, records = read_today_reminder_preview(path=path, now=now.timestamp(), limit=5)
    assert count == 6
    assert len(records) == 5
    assert all(name.startswith("Today") for name, _ in records)
    assert path.read_bytes() == before
    result = DailyBriefingService(
        academic_store=FakeCalendar(AcademicSnapshot((), None)),
        reminder_path=path,
    ).read(now=now)
    assert result["reminders"]["count"] == 6
    assert result["reminders"]["more"] is True
    assert any("showing five" in line for line in briefing_display_lines(result))


def test_cache_age_and_recurrence_are_explicit(tmp_path):
    now = _local_morning()
    snapshot = AcademicSnapshot((
        AcademicItem(
            "weekly", "Recurring seminar", "event", now.isoformat(),
            False, False, True,
        ),
    ), (now - timedelta(days=3)).isoformat())
    result = DailyBriefingService(
        academic_store=FakeCalendar(snapshot),
        reminder_path=tmp_path / "none.sqlite3",
    ).read(now=now)
    assert result["academic"]["freshness"] == "older_than_24h"
    assert result["academic"]["items"][0]["recurring_series"]
    lines = briefing_display_lines(result)
    assert any("older than 24 hours" in line for line in lines)
    assert any("not expanded" in line for line in lines)


def test_voice_tool_requires_explicit_gate_and_rejects_extra_arguments(tmp_path):
    calendar = FakeCalendar(AcademicSnapshot((), None))
    service = DailyBriefingService(
        academic_store=calendar, reminder_path=tmp_path / "none.sqlite3",
        include_academic=False, include_reminders=True,
    )
    registry = ToolRegistry()
    register_daily_briefing(registry, service)
    assert BRIEFING_TOOL_NAME in {tool["name"] for tool in registry.declarations()}
    result = registry.execute(BRIEFING_TOOL_NAME, {})
    assert result["academic"]["state"] == "disabled"
    assert calendar.reads == 0
    assert registry.execute(BRIEFING_TOOL_NAME, {
        "force_sync": True,
    }) == {"status": "error", "error": "invalid_arguments"}
    assert registry.execute("make_assignments", {})["error"] == "unknown_tool"
    settings = Settings(_env_file=None)
    default = GeminiLiveProvider(settings, enable_local_clock=True)
    enabled = GeminiLiveProvider(settings, daily_briefing=service)
    assert BRIEFING_TOOL_NAME not in {
        item["name"] for item in default._tool_registry.declarations()
    }
    assert BRIEFING_TOOL_NAME in {
        item["name"] for item in enabled._tool_registry.declarations()
    }
    assert "calendar feed" in BRIEFING_INSTRUCTION.lower()
    assert "get_daily_briefing" in BRIEFING_INSTRUCTION
