"""Durable, local, one-time schedules. No Gemini, network, or audio dependency.

SQLite is the source of truth; APScheduler only drives the independent worker.
A notification is acknowledged after output, so a crash can cause a retry:
delivery is at-least-once, not exactly-once.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

MAX_TEXT = 200
MAX_TIMER_SECONDS = 7 * 24 * 60 * 60
OWNER_LEASE_SECONDS = 30
CLAIM_LEASE_SECONDS = 90
MAX_ATTEMPTS = 3


def default_database_path() -> Path:
    """Keep user reminders outside the repository and its tracked files."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        root = os.environ.get("XDG_DATA_HOME")
        base = Path(root) if root else Path.home() / ".local" / "share"
    return base / "FRIDAY" / "schedules.sqlite3"


def parse_due(value: str, *, now: float | None = None) -> datetime:
    """Require an explicit UTC offset to avoid ambiguous local/DST times."""
    try:
        due = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Use ISO 8601 with UTC offset, e.g. 2026-09-25T19:00:00-04:00") from exc
    if due.tzinfo is None or due.utcoffset() is None:
        raise ValueError("Reminder time must include a UTC offset, e.g. -04:00")
    instant = due.astimezone(UTC)
    current = time.time() if now is None else now
    if not 1 <= instant.timestamp() - current <= 366 * 24 * 60 * 60:
        raise ValueError("Reminder time must be 1 second to 366 days in the future")
    return instant


@dataclass(frozen=True, slots=True)
class Schedule:
    id: str
    kind: str
    text: str
    due_at: float
    status: str
    attempts: int
    revision: int = 0

    @property
    def due_utc(self) -> str:
        return datetime.fromtimestamp(self.due_at, UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    """Observed process lease and calendar outcome; no credentials or event contents."""

    running: bool
    calendar_enabled: bool
    calendar_state: str
    last_attempt: float | None = None
    last_success: float | None = None


def read_pending_reminder_preview(
    *, path: Path | None = None, limit: int = 3, now: float | None = None
) -> tuple[int, tuple[tuple[str, float], ...]]:
    """Bounded, read-only Home preview. Never creates or migrates the database.

    Returns the number of future pending reminders and up to `limit` title/time
    pairs. A missing database is an empty local store, not an error.
    """
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("Preview limit must be 1–5")
    database = default_database_path() if path is None else Path(path)
    if not database.is_file():
        return 0, ()
    current = time.time() if now is None else now
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as db:
        where = "kind = 'reminder' AND status = 'pending' AND due_at > ?"
        count = db.execute(
            f"SELECT COUNT(*) FROM schedules WHERE {where}", (current,)
        ).fetchone()[0]
        rows = db.execute(
            f"SELECT text, due_at FROM schedules WHERE {where} "
            "ORDER BY due_at, id LIMIT ?",
            (current, limit),
        ).fetchall()
    return count, tuple((str(text), float(due_at)) for text, due_at in rows)


def read_today_reminder_preview(
    *, path: Path | None = None, limit: int = 5, now: float | None = None
) -> tuple[int, tuple[tuple[str, float], ...]]:
    """Read future pending reminders due on the computer's local calendar day.

    Uses SQLite read-only URI; never initializes, migrates or writes the store.
    The following local midnight is determined by the OS timezone rules, not
    a fixed 24-hour increment (which is incorrect across DST transitions).
    """
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("Today preview limit must be 1–5")
    database = default_database_path() if path is None else Path(path)
    if not database.is_file():
        return 0, ()
    current = time.time() if now is None else now
    day = datetime.fromtimestamp(current).date()
    next_midnight = datetime.combine(day + timedelta(days=1), datetime.min.time())
    end = next_midnight.timestamp()
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as db:
        where = (
            "kind = 'reminder' AND status = 'pending' "
            "AND due_at > ? AND due_at < ?"
        )
        count = db.execute(
            f"SELECT COUNT(*) FROM schedules WHERE {where}", (current, end)
        ).fetchone()[0]
        rows = db.execute(
            f"SELECT text, due_at FROM schedules WHERE {where} "
            "ORDER BY due_at, id LIMIT ?",
            (current, end, limit),
        ).fetchall()
    return count, tuple((str(text), float(due_at)) for text, due_at in rows)


class ScheduleStore:
    """One SQLite file can be shared by CLI writers and one background worker."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_database_path() if path is None else Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('timer', 'reminder')),
                    text TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'delivering', 'delivered',
                                         'cancelled', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0,
                    claim_token TEXT,
                    claim_until REAL,
                    delivered_at REAL
                )
            """)
            # Existing v0.4.1 databases need a non-destructive migration.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(schedules)")}
            if "revision" not in columns:
                db.execute(
                    "ALTER TABLE schedules ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            db.execute("""
                CREATE INDEX IF NOT EXISTS schedules_due_idx
                ON schedules(status, due_at, available_at)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS scheduler_owner (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    token TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            db.execute("""
                INSERT OR IGNORE INTO scheduler_owner(id, token, expires_at)
                VALUES (1, '', 0)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS worker_health (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    token TEXT NOT NULL,
                    calendar_enabled INTEGER NOT NULL,
                    calendar_state TEXT NOT NULL,
                    last_attempt REAL,
                    last_success REAL
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            yield db

    @staticmethod
    def _record(row: sqlite3.Row) -> Schedule:
        return Schedule(
            id=row["id"], kind=row["kind"], text=row["text"],
            due_at=row["due_at"], status=row["status"], attempts=row["attempts"],
            revision=row["revision"] if "revision" in row.keys() else 0,
        )

    def create(
        self, kind: str, text: str, due_at: float, *, now: float | None = None
    ) -> Schedule:
        if kind not in {"timer", "reminder"}:
            raise ValueError("Unknown schedule kind")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
            raise ValueError("Schedule text must contain 1–200 characters")
        current = time.time() if now is None else now
        if not 1 <= due_at - current <= 366 * 24 * 60 * 60:
            raise ValueError("Schedule must be 1 second to 366 days in the future")
        if kind == "timer" and due_at - current > MAX_TIMER_SECONDS:
            raise ValueError("Timer duration cannot exceed 7 days")
        item = Schedule(uuid4().hex, kind, text.strip(), due_at, "pending", 0)
        with self._connect() as db, db:
            db.execute(
                """INSERT INTO schedules(id, kind, text, due_at, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (item.id, item.kind, item.text, item.due_at, current),
            )
        return item

    def timer(self, seconds: int, text: str, *, now: float | None = None) -> Schedule:
        if type(seconds) is not int or not 1 <= seconds <= MAX_TIMER_SECONDS:
            raise ValueError("Timer must be 1 to 604800 seconds")
        current = time.time() if now is None else now
        return self.create("timer", text, current + seconds, now=current)

    def reminder(self, when: str, text: str, *, now: float | None = None) -> Schedule:
        current = time.time() if now is None else now
        due = parse_due(when, now=current)
        return self.create("reminder", text, due.timestamp(), now=current)

    def list_items(self, *, include_history: bool = False) -> list[Schedule]:
        where = "" if include_history else "WHERE status IN ('pending', 'delivering')"
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT id, kind, text, due_at, status, attempts FROM schedules
                    {where} ORDER BY due_at, id LIMIT 100"""
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_pending_reminders(
        self, *, limit: int = 26, now: float | None = None
    ) -> list[Schedule]:
        """Bounded future reminders only; timers cannot hide relevant entries."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Reminder listing limit must be 1–100")
        current = time.time() if now is None else now
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, kind, text, due_at, status, attempts, revision
                   FROM schedules WHERE kind = 'reminder' AND status = 'pending'
                   AND due_at > ? ORDER BY due_at, id LIMIT ?""",
                (current, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_pending_reminder(self, item_id: str, *, now: float | None = None) -> Schedule | None:
        """Return an exact active reminder, never a timer or expired item."""
        current = time.time() if now is None else now
        with self._connect() as db:
            row = db.execute(
                """SELECT id, kind, text, due_at, status, attempts, revision
                   FROM schedules WHERE id = ? AND kind = 'reminder'
                   AND status = 'pending' AND due_at > ?""",
                (item_id, current),
            ).fetchone()
        return self._record(row) if row else None

    def edit_reminder(
        self, original: Schedule, *, text: str, when: str, now: float | None = None
    ) -> Schedule | None:
        """Compare-and-swap: an outdated draft cannot overwrite a newer edit."""
        current = time.time() if now is None else now
        due = parse_due(when, now=current)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
            raise ValueError("Schedule text must contain 1–200 characters")
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise ValueError("Reminder text must not contain control characters")
        with self._connect() as db, db:
            changed = db.execute(
                """UPDATE schedules SET text = ?, due_at = ?, revision = revision + 1
                   WHERE id = ? AND kind = 'reminder' AND status = 'pending'
                   AND revision = ? AND due_at > ?""",
                (text.strip(), due.timestamp(), original.id, original.revision, current),
            ).rowcount
        return self.get_pending_reminder(original.id, now=current) if changed else None

    def cancel_reminder_if_unchanged(
        self, original: Schedule, *, now: float | None = None
    ) -> bool:
        """Cancel only the approved version, not a reminder edited meanwhile."""
        current = time.time() if now is None else now
        with self._connect() as db, db:
            changed = db.execute(
                """UPDATE schedules SET status = 'cancelled', revision = revision + 1
                   WHERE id = ? AND kind = 'reminder' AND status = 'pending'
                   AND revision = ? AND due_at > ?""",
                (original.id, original.revision, current),
            ).rowcount
        return changed == 1

    def cancel(self, item_id: str) -> bool:
        with self._connect() as db, db:
            changed = db.execute(
                """UPDATE schedules SET status = 'cancelled'
                   WHERE id = ? AND status = 'pending'""",
                (item_id,),
            ).rowcount
        return changed == 1

    def acquire_owner(self, token: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            changed = db.execute(
                """UPDATE scheduler_owner SET token = ?, expires_at = ?
                   WHERE id = 1 AND expires_at <= ?""",
                (token, current + OWNER_LEASE_SECONDS, current),
            ).rowcount
        return changed == 1

    def renew_owner(self, token: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            changed = db.execute(
                """UPDATE scheduler_owner SET expires_at = ?
                   WHERE id = 1 AND token = ? AND expires_at > ?""",
                (current + OWNER_LEASE_SECONDS, token, current),
            ).rowcount
        return changed == 1

    def release_owner(self, token: str) -> None:
        with self._connect() as db, db:
            db.execute(
                """UPDATE scheduler_owner SET token = '', expires_at = 0
                   WHERE id = 1 AND token = ?""",
                (token,),
            )

    def start_worker_health(
        self, token: str, *, calendar_enabled: bool, now: float | None = None
    ) -> None:
        """Associate health with the current owner, not a previous worker."""
        current = time.time() if now is None else now
        state = "waiting" if calendar_enabled else "local-only"
        with self._connect() as db, db:
            db.execute(
                """INSERT INTO worker_health(
                       id, token, calendar_enabled, calendar_state, last_attempt, last_success
                   )
                   SELECT 1, token, ?, ?, NULL, NULL FROM scheduler_owner
                   WHERE id = 1 AND token = ? AND expires_at > ?
                   ON CONFLICT(id) DO UPDATE SET
                       token = excluded.token,
                       calendar_enabled = excluded.calendar_enabled,
                       calendar_state = excluded.calendar_state,
                       last_attempt = NULL, last_success = NULL""",
                (int(calendar_enabled), state, token, current),
            )

    def calendar_attempt(self, token: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            db.execute(
                """UPDATE worker_health SET calendar_state = 'syncing', last_attempt = ?
                   WHERE id = 1 AND token = ? AND calendar_enabled = 1
                   AND EXISTS (SELECT 1 FROM scheduler_owner
                               WHERE id = 1 AND token = ? AND expires_at > ?)""",
                (current, token, token, current),
            )

    def calendar_outcome(
        self, token: str, *, successful: bool, now: float | None = None
    ) -> None:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            db.execute(
                """UPDATE worker_health
                   SET calendar_state = ?,
                       last_success = CASE WHEN ? THEN ? ELSE last_success END
                   WHERE id = 1 AND token = ? AND calendar_enabled = 1
                   AND EXISTS (SELECT 1 FROM scheduler_owner
                               WHERE id = 1 AND token = ? AND expires_at > ?)""",
                ("ok" if successful else "error", int(successful), current,
                 token, token, current),
            )

    def worker_health(self, *, now: float | None = None) -> WorkerHealth:
        return read_worker_health(self.path, now=now)

    def claim_due(self, *, now: float | None = None, limit: int = 10) -> list[tuple[Schedule, str]]:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            # Serialize worker claims with writes/cancellation on the same SQLite file.
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT id, kind, text, due_at, status, attempts FROM schedules
                   WHERE due_at <= ? AND (
                     (status = 'pending' AND available_at <= ?)
                     OR (status = 'delivering' AND claim_until <= ?)
                   ) ORDER BY due_at, id LIMIT ?""",
                (current, current, current, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                token = uuid4().hex
                db.execute(
                    """UPDATE schedules SET status = 'delivering', attempts = attempts + 1,
                       claim_token = ?, claim_until = ?
                       WHERE id = ?""",
                    (token, current + CLAIM_LEASE_SECONDS, row["id"]),
                )
                claimed.append((self._record(row), token))
        return claimed

    def delivered(self, item_id: str, token: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            changed = db.execute(
                """UPDATE schedules SET status = 'delivered', delivered_at = ?,
                   claim_token = NULL, claim_until = NULL
                   WHERE id = ? AND status = 'delivering' AND claim_token = ?""",
                (current, item_id, token),
            ).rowcount
        return changed == 1

    def failed_attempt(self, item_id: str, token: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        with self._connect() as db, db:
            db.execute(
                """UPDATE schedules
                   SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                       available_at = ?, claim_token = NULL, claim_until = NULL
                   WHERE id = ? AND status = 'delivering' AND claim_token = ?""",
                (MAX_ATTEMPTS, current + 15, item_id, token),
            )


def read_worker_health(
    path: Path | None = None, *, now: float | None = None
) -> WorkerHealth:
    """Read-only observation: opening desktop never creates or migrates the database."""
    database = default_database_path() if path is None else Path(path)
    if not database.is_file():
        return WorkerHealth(False, False, "stopped")
    current = time.time() if now is None else now
    uri = "file:" + quote(database.resolve().as_posix(), safe="/:") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as db:
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                """SELECT owner.token AS owner_token, owner.expires_at,
                          health.token AS health_token, health.calendar_enabled,
                          health.calendar_state, health.last_attempt, health.last_success
                   FROM scheduler_owner AS owner
                   LEFT JOIN worker_health AS health ON health.id = 1
                   WHERE owner.id = 1"""
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table: worker_health" not in str(exc):
                raise
            # A worker from an older version may hold the lease, but cannot report sync health.
            owner = db.execute(
                "SELECT token, expires_at FROM scheduler_owner WHERE id = 1"
            ).fetchone()
            return WorkerHealth(
                bool(owner and owner["expires_at"] > current), False,
                "unknown" if owner and owner["expires_at"] > current else "stopped",
            )
    if row is None or row["expires_at"] <= current:
        return WorkerHealth(False, False, "stopped")
    if row["owner_token"] != row["health_token"]:
        return WorkerHealth(True, False, "unknown")
    return WorkerHealth(
        True, bool(row["calendar_enabled"]), row["calendar_state"],
        row["last_attempt"], row["last_success"],
    )


def dispatch_due(
    store: ScheduleStore, notify: Callable[[Schedule], None], *, now: float | None = None
) -> int:
    """Claim, emit, then acknowledge. Crashes after emit may replay an alert."""
    completed = 0
    for item, token in store.claim_due(now=now):
        try:
            notify(item)
        except Exception:
            store.failed_attempt(item.id, token, now=now)
        else:
            if store.delivered(item.id, token, now=now):
                completed += 1
    return completed


def console_notify(item: Schedule) -> None:
    print(f"FRIDAY ALERT [{item.kind}] {item.text} (id={item.id})", flush=True)


def work_once(
    store: ScheduleStore, notify: Callable[[Schedule], None] = console_notify,
    *, now: float | None = None,
) -> int:
    """A no-network diagnostic; the one-worker lease still applies."""
    token = uuid4().hex
    if not store.acquire_owner(token, now=now):
        raise RuntimeError("Another FRIDAY scheduler worker is already active")
    try:
        return dispatch_due(store, notify, now=now)
    finally:
        store.release_owner(token)


def run_worker(
    store: ScheduleStore, *,
    calendar_sync: Callable[[], tuple[int, int]] | None = None,
    notify: Callable[[Schedule], None] = console_notify,
    stop_event: threading.Event | None = None,
    foreground: bool = True,
) -> None:
    """APScheduler drives one leased worker; opt-in GUI workers can stop cooperatively.

    GUI notifications use a separate callback. The default CLI retains console alerts.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError as exc:
        raise RuntimeError(
            "Install scheduling support: python -m pip install -e '.[schedule]'"
        ) from exc

    token = uuid4().hex
    if not store.acquire_owner(token):
        raise RuntimeError("Another FRIDAY scheduler worker is already active")
    try:
        store.start_worker_health(token, calendar_enabled=calendar_sync is not None)
    except Exception:
        store.release_owner(token)
        raise
    lost = threading.Event()
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    scheduler = BackgroundScheduler(timezone="UTC")

    def tick() -> None:
        if stop_event is not None and stop_event.is_set():
            return
        if not store.renew_owner(token):
            lost.set()
            return
        dispatch_due(store, notify)

    scheduler.add_job(
        tick, "interval", seconds=1, id="friday-dispatch",
        coalesce=True, max_instances=1, misfire_grace_time=30,
    )

    def calendar_tick() -> None:
        if calendar_sync is None or (stop_event is not None and stop_event.is_set()):
            return
        store.calendar_attempt(token)
        try:
            published, removed = calendar_sync()
        except Exception:
            store.calendar_outcome(token, successful=False)
            logging.getLogger(__name__).warning(
                "FRIDAY calendar sync failed; local reminders are unaffected. Retry later."
            )
        else:
            store.calendar_outcome(token, successful=True)
            if foreground and (published or removed):
                print(
                    f"FRIDAY calendar sync: {published} published, {removed} removed.",
                    flush=True,
                )

    if calendar_sync is not None:
        scheduler.add_job(
            calendar_tick, "interval", seconds=60, id="friday-calendar-sync",
            coalesce=True, max_instances=1, misfire_grace_time=30,
        )
    try:
        if foreground:
            print("FRIDAY scheduler running; leave this terminal open. Ctrl+C to stop.")
        tick()
        scheduler.start()
        if calendar_sync is not None:
            calendar_tick()
        while not lost.wait(0.25):
            if stop_event is not None and stop_event.is_set():
                break
        if lost.is_set():
            raise RuntimeError("FRIDAY scheduler lost its worker lease")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=True)
        store.release_owner(token)
