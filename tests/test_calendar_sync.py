"""Offline Google Calendar tests; fake API only, no browser or user credentials."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from friday.calendar_sync import (
    CalendarStore,
    CalendarSyncError,
    _save_token,
    client_secrets_path,
    event_body,
    event_id,
    initialize_calendar,
    sync_calendar,
    token_path,
)
from friday.schedule import ScheduleStore
from friday.ui.cli import main


class FakeHTTPError(Exception):
    def __init__(self, code):
        super().__init__("fake Google API error")
        self.resp = SimpleNamespace(status=code)


class FakeCalendarAPI:
    def __init__(self):
        self.calendar = None
        self.calendars_created = 0
        self.events_by_id = {}
        self.insert_calls = 0
        self.delete_calls = 0
        self.insert_error = None
        self.mismatched_conflict = False
        self._result = None
        self._error = None

    def calendars(self):
        return self

    def events(self):
        return self

    def insert(self, *, body, calendarId=None, sendUpdates=None):
        if calendarId is None:
            self.calendars_created += 1
            self.calendar = {"id": "friday-owned-calendar", "summary": body["summary"]}
            self._result = self.calendar
            return self
        assert calendarId == self.calendar["id"]
        assert sendUpdates == "none"
        self.insert_calls += 1
        if self.insert_error:
            self._error = FakeHTTPError(self.insert_error)
            return self
        if body["id"] in self.events_by_id:
            self._error = FakeHTTPError(409)
        else:
            self.events_by_id[body["id"]] = deepcopy(body)
            self._result = self.events_by_id[body["id"]]
        return self

    def get(self, *, calendarId, eventId=None):
        assert calendarId == self.calendar["id"]
        if eventId is None:
            self._result = self.calendar
        elif eventId not in self.events_by_id:
            self._error = FakeHTTPError(404)
        else:
            result = deepcopy(self.events_by_id[eventId])
            if self.mismatched_conflict:
                result["extendedProperties"]["private"]["fridayScheduleId"] = "not-friday"
            self._result = result
        return self

    def delete(self, *, calendarId, eventId, sendUpdates):
        assert calendarId == self.calendar["id"]
        assert sendUpdates == "none"
        self.delete_calls += 1
        if eventId not in self.events_by_id:
            self._error = FakeHTTPError(404)
        else:
            del self.events_by_id[eventId]
            self._result = {}
        return self

    def execute(self):
        if self._error is not None:
            error = self._error
            self._error = None
            self._result = None
            raise error
        result = self._result
        self._result = None
        return result


def _ready(tmp_path):
    schedule = ScheduleStore(tmp_path / "friday.sqlite3")
    calendar = CalendarStore(schedule)
    api = FakeCalendarAPI()
    identifier, created = initialize_calendar(calendar, api)
    assert created and identifier == "friday-owned-calendar"
    return schedule, calendar, api


def _future():
    return (datetime.now(UTC) + timedelta(days=2)).isoformat()


def test_initialize_is_explicit_and_idempotent(tmp_path):
    store = CalendarStore(ScheduleStore(tmp_path / "friday.sqlite3"))
    api = FakeCalendarAPI()
    assert store.calendar_id() is None
    with pytest.raises(CalendarSyncError):
        sync_calendar(store, api)
    assert api.calendars_created == 0
    assert initialize_calendar(store, api) == ("friday-owned-calendar", True)
    assert initialize_calendar(store, api) == ("friday-owned-calendar", False)
    assert api.calendars_created == 1
    assert CalendarStore(store.schedules).calendar_id() == "friday-owned-calendar"


def test_future_reminder_published_once_and_timer_excluded(tmp_path):
    schedules, links, api = _ready(tmp_path)
    reminder = schedules.reminder(_future(), "Study cybersecurity")
    schedules.timer(120, "Tea")
    assert sync_calendar(links, api) == (1, 0)
    assert sync_calendar(links, api) == (0, 0)
    assert api.insert_calls == 1
    body = api.events_by_id[event_id(reminder.id)]
    assert body["summary"] == "Study cybersecurity"
    assert body["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 0}],
    }
    assert body["extendedProperties"]["private"]["fridayScheduleId"] == reminder.id
    assert body["transparency"] == "transparent"
    assert links.counts() == (1, 1)
    assert schedules.list_items()[0].status == "pending"


def test_cancelled_link_is_removed_and_original_reminder_stays_cancelled(tmp_path):
    schedules, links, api = _ready(tmp_path)
    item = schedules.reminder(_future(), "Cancel this")
    assert sync_calendar(links, api) == (1, 0)
    assert schedules.cancel(item.id)
    assert sync_calendar(links, api) == (0, 1)
    assert sync_calendar(links, api) == (0, 0)
    assert api.delete_calls == 1
    assert api.events_by_id == {}
    assert links.counts() == (1, 0)
    assert schedules.list_items(include_history=True)[0].status == "cancelled"


def test_uncertain_insert_outcome_retries_by_deterministic_id(tmp_path, monkeypatch):
    schedules, links, api = _ready(tmp_path)
    reminder = schedules.reminder(_future(), "No duplicates")
    real_save = links.mark_linked
    calls = []

    def fail_once(item):
        calls.append(item.id)
        if len(calls) == 1:
            raise OSError("simulated local database write failure")
        real_save(item)

    monkeypatch.setattr(links, "mark_linked", fail_once)
    with pytest.raises(OSError):
        sync_calendar(links, api)
    assert len(api.events_by_id) == 1
    assert sync_calendar(links, api) == (1, 0)
    assert len(api.events_by_id) == 1
    assert api.insert_calls == 2
    assert links.counts() == (1, 1)
    assert event_id(reminder.id) in api.events_by_id


def test_conflicting_event_is_not_claimed_by_friday(tmp_path):
    schedules, links, api = _ready(tmp_path)
    item = schedules.reminder(_future(), "Protect identity")
    api.events_by_id[event_id(item.id)] = event_body(item)
    api.mismatched_conflict = True
    with pytest.raises(CalendarSyncError, match="did not match"):
        sync_calendar(links, api)
    assert links.counts() == (0, 0)
    assert schedules.list_items()[0].status == "pending"


def test_google_failure_keeps_local_reminder_and_does_not_record_link(tmp_path):
    schedules, links, api = _ready(tmp_path)
    schedules.reminder(_future(), "Remain local")
    api.insert_error = 503
    with pytest.raises(CalendarSyncError, match="publishing failed"):
        sync_calendar(links, api)
    assert links.counts() == (0, 0)
    assert schedules.list_items()[0].status == "pending"


def test_deleted_remotely_then_cancelled_locally_is_reconciled(tmp_path):
    schedules, links, api = _ready(tmp_path)
    item = schedules.reminder(_future(), "Gone remotely")
    assert sync_calendar(links, api) == (1, 0)
    api.events_by_id.clear()
    assert schedules.cancel(item.id)
    assert sync_calendar(links, api) == (0, 1)
    assert links.counts() == (1, 0)


def test_token_path_and_status_do_not_reveal_credentials(tmp_path, capsys):
    assert token_path().name == "google-token.json"
    assert client_secrets_path().name == "google-client.json"
    token = tmp_path / "token.json"
    _save_token(token, '{"refresh_token":"sensitive-test-value"}')
    assert "sensitive-test-value" in token.read_text()
    db = tmp_path / "schedules.sqlite3"
    assert main(["schedule", "--db", str(db), "calendar", "status"]) == 0
    display = capsys.readouterr().out
    assert "calendar links" in display
    assert "sensitive-test-value" not in display
    assert not (tmp_path / "token.json").samefile(db)
