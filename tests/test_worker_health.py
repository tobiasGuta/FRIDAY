"""Offline worker health regression coverage; no calendar credentials or Qt."""

import sqlite3

from friday.schedule import ScheduleStore, read_worker_health


def test_health_read_is_non_creating(tmp_path):
    path = tmp_path / "never-created.sqlite3"
    assert read_worker_health(path).calendar_state == "stopped"
    assert not path.exists()


def test_health_tracks_only_live_worker_and_latest_outcome(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite3")
    assert not store.worker_health(now=1000).running
    assert store.acquire_owner("first", now=1000)
    store.start_worker_health("first", calendar_enabled=True, now=1000)
    initial = store.worker_health(now=1000)
    assert initial.running and initial.calendar_enabled
    assert initial.calendar_state == "waiting"
    assert initial.last_success is None

    store.calendar_attempt("first", now=1001)
    assert store.worker_health(now=1001).calendar_state == "syncing"
    store.calendar_outcome("first", successful=True, now=1002)
    ok = store.worker_health(now=1002)
    assert ok.calendar_state == "ok" and ok.last_success == 1002

    store.calendar_attempt("first", now=1003)
    store.calendar_outcome("first", successful=False, now=1004)
    failed = store.worker_health(now=1004)
    assert failed.calendar_state == "error"
    assert failed.last_attempt == 1003 and failed.last_success == 1002
    assert not store.worker_health(now=1031).running


def test_stale_worker_cannot_update_new_owner_health(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.sqlite3")
    assert store.acquire_owner("old", now=1000)
    store.start_worker_health("old", calendar_enabled=True, now=1000)
    store.release_owner("old")
    assert store.acquire_owner("new", now=1002)
    assert store.worker_health(now=1002).calendar_state == "unknown"
    store.start_worker_health("new", calendar_enabled=False, now=1002)
    store.calendar_attempt("old", now=1003)
    store.calendar_outcome("old", successful=True, now=1004)
    state = store.worker_health(now=1004)
    assert state.running and not state.calendar_enabled
    assert state.calendar_state == "local-only"
    assert state.last_success is None


def test_old_schema_worker_is_reported_unknown_without_migration(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE scheduler_owner (id INTEGER PRIMARY KEY, token TEXT, expires_at REAL)"
        )
        db.execute("INSERT INTO scheduler_owner VALUES (1, 'old', 1030)")
    health = read_worker_health(path, now=1000)
    assert health.running and health.calendar_state == "unknown"
    with sqlite3.connect(path) as db:
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'worker_health'"
        ).fetchone()
    assert table is None
