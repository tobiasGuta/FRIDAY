"""Opt-in, one-way Google Calendar publishing for FRIDAY reminder records.

No Google imports, credentials, or network access until the user explicitly
connects or enables sync. The local SQLite schedule remains authoritative.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from friday.schedule import Schedule, ScheduleStore, default_database_path

SCOPES = ("https://www.googleapis.com/auth/calendar.app.created",)
CALENDAR_NAME = "FRIDAY"


class CalendarSyncError(RuntimeError):
    """Safe, non-secret diagnostic for calendar setup and synchronization."""


def token_path() -> Path:
    return default_database_path().parent / "google-token.json"


def client_secrets_path() -> Path:
    return default_database_path().parent / "google-client.json"


def _save_token(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".google-token-",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(content)
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def connect_google(
    *, secrets: Path | None = None, destination: Path | None = None
) -> None:
    """Explicit browser OAuth only; never print credentials or bearer tokens."""
    file = client_secrets_path() if secrets is None else Path(secrets)
    output = token_path() if destination is None else Path(destination)
    if not file.is_file():
        raise CalendarSyncError(
            "Google OAuth client JSON missing; download a Desktop app credential "
            "and pass --client-secrets PATH to calendar connect."
        )
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise CalendarSyncError(
            "Install calendar support: python -m pip install -e '.[calendar]'"
        ) from exc
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(file), list(SCOPES))
        credentials = flow.run_local_server(port=0, prompt="consent")
        if not credentials.valid or not credentials.has_scopes(SCOPES):
            raise CalendarSyncError("Google did not grant the required calendar permission.")
        _save_token(output, credentials.to_json())
    except CalendarSyncError:
        raise
    except Exception as exc:
        raise CalendarSyncError(
            "Google sign-in failed; check the OAuth Desktop client, test-user access, "
            "and your browser. No token was saved."
        ) from exc


def google_service(*, source: Path | None = None) -> Any:
    """Load an existing authorization. Sync never triggers an OAuth browser."""
    path = token_path() if source is None else Path(source)
    if not path.is_file():
        raise CalendarSyncError("Google Calendar not connected. Run schedule calendar connect.")
    try:
        import httplib2
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_httplib2 import AuthorizedHttp
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise CalendarSyncError(
            "Install calendar support: python -m pip install -e '.[calendar]'"
        ) from exc
    try:
        credentials = Credentials.from_authorized_user_file(str(path), list(SCOPES))
        if not credentials.has_scopes(SCOPES):
            raise CalendarSyncError(
                "The existing Google grant lacks FRIDAY's calendar permission; reconnect."
            )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _save_token(path, credentials.to_json())
        if not credentials.valid:
            raise CalendarSyncError("Google authorization expired. Reconnect FRIDAY.")
        return build(
            "calendar", "v3",
            http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=15)),
            cache_discovery=False,
        )
    except CalendarSyncError:
        raise
    except Exception as exc:
        raise CalendarSyncError(
            "Google authorization could not be loaded or refreshed. Reconnect FRIDAY."
        ) from exc


def _status(exc: Exception) -> int | None:
    response = getattr(exc, "resp", None)
    code = getattr(response, "status", None)
    return code if type(code) is int else None


def event_id(schedule_id: str) -> str:
    """Google IDs allow base32hex; ASCII hex is a supported subset."""
    return "fr" + hashlib.sha256(schedule_id.encode("ascii")).hexdigest()[:40]


def event_body(item: Schedule) -> dict[str, Any]:
    if item.kind != "reminder":
        raise ValueError("Timers do not belong in the calendar")
    start = datetime.fromtimestamp(item.due_at, UTC)
    end = start + timedelta(minutes=15)
    return {
        "id": event_id(item.id),
        "summary": item.text,
        "description": (
            "Created by FRIDAY. One-way sync: edits in Google Calendar "
            "do not change the local reminder."
        ),
        "start": {"dateTime": start.isoformat(timespec="seconds")},
        "end": {"dateTime": end.isoformat(timespec="seconds")},
        "transparency": "transparent",
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 0}],
        },
        "extendedProperties": {"private": {"fridayScheduleId": item.id}},
    }


class CalendarStore:
    """Add sync metadata to the existing database without changing reminder rows."""

    def __init__(self, schedules: ScheduleStore) -> None:
        self.schedules = schedules
        self.path = schedules.path
        with self._connect() as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS friday_calendar (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    calendar_id TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS friday_calendar_links (
                    schedule_id TEXT PRIMARY KEY REFERENCES schedules(id),
                    event_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK(state IN ('linked', 'deleted')),
                    synced_at REAL NOT NULL,
                    synced_revision INTEGER NOT NULL DEFAULT 0
                )
            """)
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(friday_calendar_links)")
            }
            if "synced_revision" not in columns:
                db.execute(
                    "ALTER TABLE friday_calendar_links "
                    "ADD COLUMN synced_revision INTEGER NOT NULL DEFAULT 0"
                )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def calendar_id(self) -> str | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT calendar_id FROM friday_calendar WHERE id = 1").fetchone()
            return row["calendar_id"] if row else None

    def bind(self, calendar_id: str) -> None:
        if not isinstance(calendar_id, str) or not calendar_id.strip():
            raise CalendarSyncError("Google returned no calendar identifier.")
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT calendar_id FROM friday_calendar WHERE id = 1"
            ).fetchone()
            if row and row["calendar_id"] != calendar_id:
                raise CalendarSyncError(
                    "FRIDAY is already bound to a different calendar; no changes were made."
                )
            db.execute(
                "INSERT OR IGNORE INTO friday_calendar (id, calendar_id) VALUES (1, ?)",
                (calendar_id,),
            )

    def pending(self, *, now: float | None = None) -> list[Schedule]:
        current = time.time() if now is None else now
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT s.id, s.kind, s.text, s.due_at, s.status, s.attempts,
                          s.revision
                   FROM schedules AS s
                   LEFT JOIN friday_calendar_links AS l ON l.schedule_id = s.id
                   WHERE s.kind = 'reminder' AND s.status = 'pending'
                     AND s.due_at > ? AND l.schedule_id IS NULL
                   ORDER BY s.due_at, s.id LIMIT 100""",
                (current,),
            ).fetchall()
        return [ScheduleStore._record(row) for row in rows]

    def changed_links(self, *, now: float | None = None) -> list[Schedule]:
        """Pending edits to existing events; include only local revision changes."""
        current = time.time() if now is None else now
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT s.id, s.kind, s.text, s.due_at, s.status, s.attempts,
                          s.revision
                   FROM schedules AS s
                   JOIN friday_calendar_links AS l ON l.schedule_id = s.id
                   WHERE s.kind = 'reminder' AND s.status = 'pending'
                     AND s.due_at > ? AND l.state = 'linked'
                     AND s.revision > l.synced_revision
                   ORDER BY s.due_at, s.id LIMIT 100""",
                (current,),
            ).fetchall()
        return [ScheduleStore._record(row) for row in rows]

    def mark_updated(self, item: Schedule) -> None:
        """Acknowledge only the version actually sent to Google."""
        with closing(self._connect()) as db, db:
            db.execute(
                """UPDATE friday_calendar_links
                   SET synced_revision = ?, synced_at = ?
                   WHERE schedule_id = ? AND event_id = ? AND state = 'linked'
                     AND synced_revision < ?""",
                (item.revision, time.time(), item.id, event_id(item.id), item.revision),
            )

    def cancelled_links(self) -> list[tuple[str, str]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT l.schedule_id, l.event_id
                   FROM friday_calendar_links AS l
                   JOIN schedules AS s ON s.id = l.schedule_id
                   WHERE s.status = 'cancelled' AND l.state = 'linked'
                   ORDER BY s.due_at, s.id LIMIT 100"""
            ).fetchall()
        return [(row["schedule_id"], row["event_id"]) for row in rows]

    def mark_linked(self, item: Schedule) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                """INSERT OR IGNORE INTO friday_calendar_links
                   (schedule_id, event_id, state, synced_at, synced_revision)
                   VALUES (?, ?, 'linked', ?, ?)""",
                (item.id, event_id(item.id), time.time(), item.revision),
            )
            row = db.execute(
                "SELECT event_id, state FROM friday_calendar_links WHERE schedule_id = ?",
                (item.id,),
            ).fetchone()
            if row["event_id"] != event_id(item.id) or row["state"] != "linked":
                raise CalendarSyncError("Local calendar link conflicts with an existing record.")

    def mark_deleted(self, item_id: str, remote_event_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                """UPDATE friday_calendar_links SET state = 'deleted', synced_at = ?
                   WHERE schedule_id = ? AND event_id = ? AND state = 'linked'""",
                (time.time(), item_id, remote_event_id),
            )

    def counts(self) -> tuple[int, int]:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT COUNT(*) AS total,
                   SUM(CASE WHEN state = 'linked' THEN 1 ELSE 0 END) AS active
                   FROM friday_calendar_links"""
            ).fetchone()
        return row["total"], row["active"] or 0


def initialize_calendar(store: CalendarStore, service: Any) -> tuple[str, bool]:
    """Create FRIDAY's calendar only on an explicit init, never during sync."""
    existing = store.calendar_id()
    if existing:
        try:
            result = service.calendars().get(calendarId=existing).execute()
        except Exception as exc:
            raise CalendarSyncError(
                "Saved FRIDAY calendar is unavailable; sync will not create a replacement."
            ) from exc
        if not isinstance(result, dict) or result.get("id") != existing:
            raise CalendarSyncError("Google returned an unexpected calendar identifier.")
        return existing, False
    try:
        result = service.calendars().insert(body={
            "summary": CALENDAR_NAME,
            "description": "One-way reminders synchronized by FRIDAY.",
        }).execute()
        identifier = result.get("id") if isinstance(result, dict) else None
        store.bind(identifier)
    except CalendarSyncError:
        raise
    except Exception as exc:
        raise CalendarSyncError(
            "Could not create or record FRIDAY calendar. Check Google Calendar before "
            "retrying init: a partial request may have created a calendar."
        ) from exc
    return identifier, True


def sync_calendar(store: CalendarStore, service: Any) -> tuple[int, int]:
    """Publish pending future reminders and remove cancelled linked events."""
    calendar_id = store.calendar_id()
    if not calendar_id:
        raise CalendarSyncError("Initialize the dedicated calendar before syncing.")
    removed = 0
    created = 0
    cancelled = store.cancelled_links()
    pending = store.pending()
    changed = store.changed_links()
    if not cancelled and not pending and not changed:
        return created, removed
    try:
        info = service.calendars().get(calendarId=calendar_id).execute()
    except Exception as exc:
        raise CalendarSyncError("Saved FRIDAY calendar could not be reached.") from exc
    if not isinstance(info, dict) or info.get("id") != calendar_id:
        raise CalendarSyncError("Google returned an unexpected calendar identifier.")

    for schedule_id, remote_id in cancelled:
        try:
            service.events().delete(
                calendarId=calendar_id, eventId=remote_id, sendUpdates="none"
            ).execute()
        except Exception as exc:
            if _status(exc) != 404:
                raise CalendarSyncError(
                    "Google Calendar cancellation failed. Local reminder remains cancelled."
                ) from exc
        store.mark_deleted(schedule_id, remote_id)
        removed += 1

    for item in changed:
        body = event_body(item)
        try:
            existing = service.events().get(
                calendarId=calendar_id, eventId=body["id"]
            ).execute()
        except Exception as exc:
            raise CalendarSyncError(
                "Existing Google Calendar event could not be verified before an edit."
            ) from exc
        properties = existing.get("extendedProperties") if isinstance(existing, dict) else None
        private = properties.get("private") if isinstance(properties, dict) else None
        if (
            not isinstance(existing, dict) or existing.get("id") != body["id"]
            or not isinstance(private, dict) or private.get("fridayScheduleId") != item.id
        ):
            raise CalendarSyncError("The linked Google event does not belong to this reminder.")
        try:
            result = service.events().update(
                calendarId=calendar_id, eventId=body["id"], body=body,
                sendUpdates="none",
            ).execute()
        except Exception as exc:
            raise CalendarSyncError(
                "Google Calendar edit failed; the updated reminder remains in SQLite."
            ) from exc
        updated_properties = result.get("extendedProperties") if isinstance(result, dict) else None
        updated_private = (
            updated_properties.get("private") if isinstance(updated_properties, dict) else None
        )
        if (
            not isinstance(result, dict) or result.get("id") != body["id"]
            or not isinstance(updated_private, dict)
            or updated_private.get("fridayScheduleId") != item.id
        ):
            raise CalendarSyncError("Google returned an unverified updated event.")
        store.mark_updated(item)
        created += 1

    for item in pending:
        body = event_body(item)
        conflict = False
        try:
            result = service.events().insert(
                calendarId=calendar_id, body=body, sendUpdates="none"
            ).execute()
        except Exception as exc:
            if _status(exc) != 409:
                raise CalendarSyncError(
                    "Google Calendar publishing failed. The reminder remains in SQLite."
                ) from exc
            conflict = True
            try:
                result = service.events().get(
                    calendarId=calendar_id, eventId=body["id"]
                ).execute()
            except Exception as get_exc:
                raise CalendarSyncError(
                    "An existing event could not be verified after an insertion conflict."
                ) from get_exc
        if not isinstance(result, dict) or result.get("id") != body["id"]:
            raise CalendarSyncError("Google returned an unexpected event identifier.")
        properties = result.get("extendedProperties")
        private = properties.get("private") if isinstance(properties, dict) else None
        if not isinstance(private, dict) or private.get("fridayScheduleId") != item.id:
            raise CalendarSyncError(
                "An existing event did not match FRIDAY's reminder; link was not saved."
            )
        if conflict:
            # An earlier insert may have succeeded before local link persistence.
            # An intervening local edit must win, not be acknowledged as synced.
            try:
                result = service.events().update(
                    calendarId=calendar_id, eventId=body["id"],
                    body=body, sendUpdates="none",
                ).execute()
            except Exception as exc:
                raise CalendarSyncError(
                    "Could not reconcile an existing FRIDAY event; retry sync later."
                ) from exc
            properties = result.get("extendedProperties") if isinstance(result, dict) else None
            private = properties.get("private") if isinstance(properties, dict) else None
            if (
                not isinstance(result, dict) or result.get("id") != body["id"]
                or not isinstance(private, dict) or private.get("fridayScheduleId") != item.id
            ):
                raise CalendarSyncError("Existing event update could not be verified.")
        store.mark_linked(item)
        created += 1
    return created, removed
