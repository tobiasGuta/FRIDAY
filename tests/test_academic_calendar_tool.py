"""Synthetic model-tool boundary: no network, credentials or provider SDK."""

from datetime import UTC, datetime

from friday.brightspace_calendar import AcademicStore, parse_calendar
from friday.tools.academic_calendar import ACADEMIC_TOOL_NAME, register_academic_calendar
from friday.tools.registry import ToolRegistry

CALENDAR = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:lecture
DTSTART:20261005T140000Z
SUMMARY:Professor lecture
END:VEVENT
BEGIN:VTODO
UID:deadline
DUE;VALUE=DATE:20261006
SUMMARY:Lab deadline
END:VTODO
END:VCALENDAR
"""


def test_academic_tool_is_read_only_and_reports_coverage(tmp_path):
    store = AcademicStore(tmp_path / "academic.sqlite3")
    store.replace(parse_calendar(CALENDAR))
    registry = ToolRegistry()
    register_academic_calendar(registry, store)
    assert ACADEMIC_TOOL_NAME in [d["name"] for d in registry.declarations()]
    result = registry.execute(ACADEMIC_TOOL_NAME, {})
    assert result["status"] == "ok"
    assert "not a complete assignment list" in result["coverage"]
    assert result["source"].startswith("CUNY Brightspace")
    assert "feed" not in str(result).lower() or "subscription" not in str(result).lower()
    assert not any("http" in str(item) for item in result["items"])
    assert registry.execute(ACADEMIC_TOOL_NAME, {"url": "https://evil.test"}) == {
        "status": "error", "error": "invalid_arguments",
    }
    assert registry.execute("submit_coursework", {})["error"] == "unknown_tool"


def test_unsynced_academic_tool_does_not_invent_assignments(tmp_path):
    store = AcademicStore(tmp_path / "absent.sqlite3")
    registry = ToolRegistry()
    register_academic_calendar(registry, store)
    assert registry.execute(ACADEMIC_TOOL_NAME, {})["error"] == "academic_calendar_not_synced"
    assert not store.path.exists()


def test_upcoming_items_are_sorted_and_bounded(tmp_path):
    store = AcademicStore(tmp_path / "local.sqlite3")
    store.replace(parse_calendar(CALENDAR))
    result = store.upcoming(
        now=datetime(2026, 10, 5, 10, tzinfo=UTC), days=7, limit=10
    )
    assert [x.uid for x in result.items] == ["lecture", "deadline"]


def test_gemini_academic_tool_is_explicitly_gated():
    from friday.config import Settings
    from friday.providers.gemini_live import GeminiLiveProvider

    settings = Settings(_env_file=None)
    default = GeminiLiveProvider(settings, enable_local_clock=True)
    enabled = GeminiLiveProvider(
        settings, enable_local_clock=True, enable_academic_calendar=True
    )
    assert ACADEMIC_TOOL_NAME not in [
        declaration["name"] for declaration in default._tool_registry.declarations()
    ]
    assert ACADEMIC_TOOL_NAME in [
        declaration["name"] for declaration in enabled._tool_registry.declarations()
    ]


def test_due_named_event_is_exposed_as_source_label_not_explicit_deadline(
    tmp_path, monkeypatch
):
    payload = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:worksheet
DTSTART:20261005T235900Z
SUMMARY:Worksheet I - Due
END:VEVENT
END:VCALENDAR
"""
    store = AcademicStore(tmp_path / "academic.sqlite3")
    store.replace(parse_calendar(payload))
    monkeypatch.setattr(store, "upcoming", lambda **_kwargs: store.snapshot())
    registry = ToolRegistry()
    register_academic_calendar(registry, store)
    result = registry.execute(ACADEMIC_TOOL_NAME, {})
    assert result["status"] == "ok"
    assert result["items"][0]["title"] == "Worksheet I - Due"
    assert result["items"][0]["source_labeled_due"] is True
    assert result["items"][0]["explicit_due"] is False
    assert "http" not in str(result["items"][0])
