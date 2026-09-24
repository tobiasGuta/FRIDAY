"""Offline Brightspace fixtures: never require a personal feed or API credentials."""

from datetime import UTC, datetime

import httpx
import pytest

from friday.brightspace_calendar import (
    AcademicStore,
    BrightspaceError,
    display_time,
    parse_calendar,
    source_labeled_due,
)
from friday.brightspace_feed import (
    ACCOUNT,
    SERVICE,
    fetch_feed,
    forget_feed,
    load_feed,
    save_feed,
    sync_feed,
    validate_feed_url,
)

URL = "https://brightspace.cuny.edu/d2l/le/calendar/feed/private?token=do-not-print"
SAMPLE = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//FRIDAY tests//EN
BEGIN:VEVENT
UID:class-1
DTSTART:20261005T140000Z
DTEND:20261005T150000Z
SUMMARY:Cybersecurity class
END:VEVENT
BEGIN:VTODO
UID:task-1
DTSTART;VALUE=DATE:20261007
DUE;VALUE=DATE:20261008
SUMMARY:Submit the report
END:VTODO
BEGIN:VEVENT
UID:holiday-1
DTSTART;VALUE=DATE:20261009
SUMMARY:All-day college event
END:VEVENT
END:VCALENDAR
"""


class FakeVault:
    def __init__(self):
        self.data = {}

    def set_password(self, service, account, value):
        self.data[(service, account)] = value

    def get_password(self, service, account):
        return self.data.get((service, account))

    def delete_password(self, service, account):
        self.data.pop((service, account), None)


def test_parser_keeps_tasks_and_events_distinct_and_all_day_dates():
    items = parse_calendar(SAMPLE)
    by_id = {x.uid: x for x in items}
    assert len(items) == 3
    assert not by_id["class-1"].explicit_due
    assert by_id["class-1"].kind == "event"
    assert by_id["task-1"].kind == "task"
    assert by_id["task-1"].explicit_due
    assert by_id["task-1"].when == "2026-10-08"
    assert by_id["task-1"].all_day
    assert "all day" in display_time(by_id["task-1"])
    assert not by_id["holiday-1"].explicit_due


def test_parser_timezone_recurring_and_cancelled_events():
    payload = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:series
DTSTART;TZID=America/New_York:20261102T090000
DTEND;TZID=America/New_York:20261102T100000
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Weekly class
END:VEVENT
BEGIN:VEVENT
UID:gone
STATUS:CANCELLED
DTSTART:20261103T120000Z
SUMMARY:Cancelled
END:VEVENT
END:VCALENDAR
"""
    items = parse_calendar(payload)
    assert len(items) == 1
    assert items[0].recurring
    assert items[0].when.endswith("-05:00")


def test_parser_rejects_html_empty_and_conflicting_duplicates():
    with pytest.raises(BrightspaceError, match="not an iCalendar"):
        parse_calendar(b"<html>Login</html>")
    with pytest.raises(BrightspaceError, match="no dated events"):
        parse_calendar(b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")
    malformed = SAMPLE.replace(b"UID:class-1", b"UID:task-1")
    with pytest.raises(BrightspaceError, match="conflicting duplicate"):
        parse_calendar(malformed)


def test_snapshot_atomic_dedup_and_failed_update_preserves_previous(tmp_path):
    store = AcademicStore(tmp_path / "academic.sqlite3")
    assert not store.path.exists()
    assert store.snapshot().items == ()
    items = parse_calendar(SAMPLE)
    store.replace(items)
    previous = store.snapshot()
    assert previous.last_success is not None
    assert len(previous.items) == 3
    store.replace(items)
    assert len(store.snapshot().items) == 3
    with pytest.raises(BrightspaceError):
        store.replace(())
    assert store.snapshot().items == previous.items
    upcoming = store.upcoming(now=datetime(2026, 10, 5, 10, tzinfo=UTC))
    assert {x.uid for x in upcoming.items} == {"class-1", "task-1", "holiday-1"}


def test_url_validation_is_locked_to_cuny_https_without_credentials():
    assert validate_feed_url(URL) == URL
    for value in [
        "http://brightspace.cuny.edu/private",
        "https://brightspace.cuny.edu.evil.test/feed",
        "https://evil.test@brightspace.cuny.edu/feed",
        "https://brightspace.cuny.edu:8080/feed",
        "https://brightspace.cuny.edu/feed#secret",
        "file:///private/feed.ics",
        "https://127.0.0.1/feed",
    ]:
        with pytest.raises(BrightspaceError):
            validate_feed_url(value)


def test_keyring_operations_never_require_network_or_expose_url(tmp_path):
    vault = FakeVault()
    save_feed(URL, vault=vault)
    assert vault.data[(SERVICE, ACCOUNT)] == URL
    assert load_feed(vault=vault) == URL
    store = AcademicStore(tmp_path / "academic.sqlite3")
    store.replace(parse_calendar(SAMPLE))
    forget_feed(vault=vault, store=store)
    assert not store.path.exists()
    with pytest.raises(BrightspaceError, match="No Brightspace"):
        load_feed(vault=vault)


def test_read_only_http_sync_and_cache_preservation(tmp_path):
    vault = FakeVault()
    save_feed(URL, vault=vault)
    store = AcademicStore(tmp_path / "academic.sqlite3")
    requests = []

    def ok(request):
        requests.append(request)
        return httpx.Response(200, content=SAMPLE, headers={"Content-Type": "text/calendar"})

    with httpx.Client(transport=httpx.MockTransport(ok)) as client:
        snapshot = sync_feed(store=store, vault=vault, client=client)
    assert len(snapshot.items) == 3
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.host == "brightspace.cuny.edu"

    def redirect(_request):
        return httpx.Response(302, headers={"Location": "https://evil.test/collect"})

    with httpx.Client(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(BrightspaceError, match="redirected"):
            sync_feed(store=store, vault=vault, client=client)
    assert store.snapshot().items == snapshot.items

    def invalid(_request):
        return httpx.Response(200, content=b"<html>untrusted sign-in page</html>")

    with httpx.Client(transport=httpx.MockTransport(invalid)) as client:
        with pytest.raises(BrightspaceError) as error:
            sync_feed(store=store, vault=vault, client=client)
    assert URL not in str(error.value)
    assert "untrusted sign-in page" not in str(error.value)
    assert store.snapshot().items == snapshot.items


def test_http_rejects_large_decompressed_body_and_never_redirects():
    from friday.brightspace_calendar import MAX_FEED_BYTES

    def oversized(_request):
        return httpx.Response(200, content=b"X" * (MAX_FEED_BYTES + 1))

    with httpx.Client(transport=httpx.MockTransport(oversized)) as client:
        with pytest.raises(BrightspaceError, match="size limit"):
            fetch_feed(URL, client=client)


def test_replacing_feed_erases_previous_course_cache(tmp_path):
    vault = FakeVault()
    store = AcademicStore(tmp_path / "academic.sqlite3")
    first = URL
    second = "https://brightspace.cuny.edu/d2l/le/calendar/feed/other?token=synthetic"
    save_feed(first, vault=vault, store=store)
    store.replace(parse_calendar(SAMPLE))
    assert store.path.exists()
    save_feed(second, vault=vault, store=store)
    assert load_feed(vault=vault) == second
    assert not store.path.exists()
    assert store.snapshot().items == ()


def test_due_named_brightspace_event_is_source_labeled_not_verified_due(tmp_path):
    sample = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:worksheet
DTSTART:20260924T235900Z
SUMMARY:Worksheet I - Due
END:VEVENT
BEGIN:VEVENT
UID:unrelated
DTSTART:20260925T140000Z
SUMMARY:Due diligence discussion
END:VEVENT
BEGIN:VTODO
UID:explicit-task
DUE;VALUE=DATE:20260926
SUMMARY:Lab submission
END:VTODO
END:VCALENDAR
"""
    items = {item.uid: item for item in parse_calendar(sample)}
    assert source_labeled_due(items["worksheet"])
    assert not items["worksheet"].explicit_due
    assert not source_labeled_due(items["unrelated"])
    assert items["explicit-task"].explicit_due
    assert not source_labeled_due(items["explicit-task"])
    store = AcademicStore(tmp_path / "academic.sqlite3")
    store.replace(tuple(items.values()))
    persisted = {item.uid: item for item in store.snapshot().items}
    assert source_labeled_due(persisted["worksheet"])
    assert not persisted["worksheet"].explicit_due
