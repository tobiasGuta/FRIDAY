"""Offline scheduling tests: temporary SQLite, controlled time, no audio or API keys."""

from datetime import UTC, datetime, timedelta

import pytest

from friday.schedule import (
    CLAIM_LEASE_SECONDS,
    OWNER_LEASE_SECONDS,
    ScheduleStore,
    default_database_path,
    parse_due,
    work_once,
)
from friday.ui.cli import build_parser, main


def test_store_reopen_preserves_timer_and_explicit_offset_reminder(tmp_path):
    path = tmp_path / "private" / "schedule.sqlite3"
    store = ScheduleStore(path)
    now = datetime(2026, 9, 24, 15, 0, tzinfo=UTC).timestamp()
    timer = store.timer(45, "Tea", now=now)
    reminder = store.reminder("2026-09-25T19:00:00-04:00", "Study", now=now)
    assert timer.id != reminder.id
    assert timer.due_at == now + 45
    assert reminder.due_utc == "2026-09-25T23:00:00+00:00"
    restored = ScheduleStore(path).list_items()
    assert [item.id for item in restored] == [timer.id, reminder.id]
    assert [item.status for item in restored] == ["pending", "pending"]
    assert path.exists()


def test_once_delivers_after_due_and_cannot_repeat(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.sqlite3")
    created = store.timer(10, "Take a break", now=1000)
    notifications = []
    assert work_once(store, notifications.append, now=1009) == 0
    assert work_once(store, notifications.append, now=1010) == 1
    assert [item.id for item in notifications] == [created.id]
    assert work_once(store, notifications.append, now=1011) == 0
    assert store.list_items() == []
    history = store.list_items(include_history=True)
    assert history[0].status == "delivered"
    assert history[0].attempts == 1


def test_cancel_pending_prevents_delivery_and_keeps_history(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.sqlite3")
    created = store.timer(10, "Do not fire", now=1000)
    assert store.cancel(created.id)
    assert not store.cancel(created.id)
    assert not store.cancel("unknown")
    notices = []
    assert work_once(store, notices.append, now=1015) == 0
    assert notices == []
    assert store.list_items(include_history=True)[0].status == "cancelled"


def test_one_worker_lease_is_atomic_and_expiring(tmp_path):
    path = tmp_path / "schedules.sqlite3"
    first = ScheduleStore(path)
    second = ScheduleStore(path)
    assert first.acquire_owner("one", now=1000)
    assert not second.acquire_owner("two", now=1000)
    assert first.renew_owner("one", now=1001)
    assert not second.acquire_owner("two", now=1001 + OWNER_LEASE_SECONDS - 1)
    assert second.acquire_owner("two", now=1001 + OWNER_LEASE_SECONDS)
    first.release_owner("one")
    assert not first.acquire_owner("one", now=1001 + OWNER_LEASE_SECONDS + 1)
    second.release_owner("two")
    assert first.acquire_owner("one", now=2000)


def test_stale_claim_is_recovered_after_worker_crash(tmp_path):
    path = tmp_path / "schedules.sqlite3"
    first = ScheduleStore(path)
    created = first.timer(5, "Recovered", now=1000)
    claimed = first.claim_due(now=1005)
    assert len(claimed) == 1
    assert claimed[0][0].id == created.id
    second = ScheduleStore(path)
    notices = []
    assert work_once(second, notices.append, now=1005 + CLAIM_LEASE_SECONDS - 1) == 0
    assert work_once(second, notices.append, now=1005 + CLAIM_LEASE_SECONDS) == 1
    assert [item.id for item in notices] == [created.id]
    assert second.list_items(include_history=True)[0].attempts == 2


def test_failed_notifications_retry_with_backoff_then_stop(tmp_path):
    store = ScheduleStore(tmp_path / "schedules.sqlite3")
    store.timer(5, "Fail", now=1000)
    attempts = []

    def fail(_item):
        attempts.append(True)
        raise OSError("simulated notifier failure")

    assert work_once(store, fail, now=1005) == 0
    assert work_once(store, fail, now=1006) == 0
    assert work_once(store, fail, now=1020) == 0
    assert work_once(store, fail, now=1035) == 0
    assert work_once(store, fail, now=1050) == 0
    assert len(attempts) == 3
    item = store.list_items(include_history=True)[0]
    assert item.status == "failed"
    assert item.attempts == 3


def test_time_validation_rejects_naive_past_invalid_and_oversized(tmp_path):
    now = datetime(2026, 9, 24, tzinfo=UTC).timestamp()
    assert parse_due("2026-09-25T19:00:00-04:00", now=now).tzinfo is UTC
    for value in ("tomorrow at seven", "2026-09-25T19:00:00",
                  "2026-09-23T19:00:00+00:00", "2029-09-25T19:00:00Z"):
        with pytest.raises(ValueError):
            parse_due(value, now=now)
    store = ScheduleStore(tmp_path / "schedule.sqlite3")
    for seconds in (0, -1, 604801, 1.5, True):
        with pytest.raises(ValueError):
            store.timer(seconds, "Tea", now=now)
    for text in ("", " ", "x" * 201):
        with pytest.raises(ValueError):
            store.timer(5, text, now=now)
    assert store.list_items() == []


def test_cli_is_local_and_does_not_change_talk_parser(tmp_path, capsys):
    path = tmp_path / "schedules.sqlite3"
    before = ["schedule", "--db", str(path)]
    assert main([*before, "timer", "--seconds", "120", "--text", "Tea"]) == 0
    assert main([*before, "list"]) == 0
    assert "Tea" in capsys.readouterr().out
    assert main([*before, "worker", "--once"]) == 0
    assert "Delivered 0 due schedule(s)." in capsys.readouterr().out
    future = (datetime.now(UTC) + timedelta(minutes=3)).isoformat()
    assert main([*before, "add", "--at", future, "--text", "Study"]) == 0
    assert main([*before, "list", "--all"]) == 0
    assert "Study" in capsys.readouterr().out
    assert build_parser().parse_args(["talk", "--input-device", "1"]).input_device == 1
    assert default_database_path().name == "schedules.sqlite3"


def test_edit_reminder_is_guarded_by_revision_and_keeps_same_id(tmp_path):
    store = ScheduleStore(tmp_path / "edits.sqlite3")
    when = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    original = store.reminder(when, "Study")
    new_when = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    modified = store.edit_reminder(original, text="Study more", when=new_when)
    assert modified is not None
    assert modified.id == original.id and modified.revision == 1
    assert modified.text == "Study more"
    assert store.edit_reminder(original, text="Stale overwrite", when=new_when) is None
    assert not store.cancel_reminder_if_unchanged(original)
    assert store.cancel_reminder_if_unchanged(modified)
    assert store.get_pending_reminder(original.id) is None
    assert store.list_items(include_history=True)[0].status == "cancelled"


def test_edit_rejects_timers_delivered_and_bad_times(tmp_path):
    store = ScheduleStore(tmp_path / "edit-guards.sqlite3")
    timer = store.timer(120, "Tea")
    assert store.get_pending_reminder(timer.id) is None
    with pytest.raises(ValueError):
        store.edit_reminder(timer, text="Tea", when="tomorrow")
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    original = store.reminder(future, "Study")
    for bad in ("2026-01-01T12:00:00+00:00", "tomorrow", "2026-09-27T19:00:00"):
        with pytest.raises(ValueError):
            store.edit_reminder(original, text="Study", when=bad)
    with pytest.raises(ValueError):
        store.edit_reminder(original, text="bad\ntext", when=future)
    assert store.get_pending_reminder(original.id).revision == 0


def test_existing_database_migrates_revision_without_data_loss(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""
            CREATE TABLE schedules (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                due_at REAL NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL DEFAULT 0,
                claim_token TEXT,
                claim_until REAL,
                delivered_at REAL
            )
        """)
        db.execute(
            "INSERT INTO schedules(id,kind,text,due_at,created_at) VALUES (?,?,?,?,?)",
            ("old-id", "reminder", "Preserved", datetime.now(UTC).timestamp() + 86400, 1),
        )
    store = ScheduleStore(path)
    item = store.get_pending_reminder("old-id")
    assert item is not None and item.revision == 0 and item.text == "Preserved"
    assert ScheduleStore(path).get_pending_reminder("old-id").text == "Preserved"


def test_cli_edit_preserves_id_and_rejects_missing_values(tmp_path, capsys):
    path = tmp_path / "cli-edit.sqlite3"
    store = ScheduleStore(path)
    first = store.reminder((datetime.now(UTC) + timedelta(days=2)).isoformat(), "Study")
    root = ["schedule", "--db", str(path)]
    assert main([*root, "edit", first.id, "--text", "Study security"]) == 0
    assert "Updated reminder" in capsys.readouterr().out
    assert store.get_pending_reminder(first.id).text == "Study security"
    assert main([*root, "edit", first.id]) == 1
    assert store.get_pending_reminder(first.id).revision == 1
