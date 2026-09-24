"""Explicit GUI-owned scheduler. It does not use Gemini or create a second worker.

The Qt tray stays alive while the window is hidden; a full application Quit stops
this worker. The background thread waits for the UI to attempt a notification
before acknowledging a local reminder to SQLite.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from friday.calendar_sync import (
    CalendarStore,
    CalendarSyncError,
    google_service,
    sync_calendar,
)
from friday.schedule import Schedule, ScheduleStore, run_worker


@dataclass(slots=True)
class AlertRequest:
    kind: str
    text: str
    done: threading.Event = field(default_factory=threading.Event)
    attempted: bool = False


class SchedulerThread(QThread):
    """Runs the existing leased scheduler independently of voice and the Qt event loop."""

    alert = Signal(object)
    notice = Signal(str)

    def __init__(self, *, calendar_enabled: bool) -> None:
        super().__init__()
        self.calendar_enabled = calendar_enabled
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _notify(self, item: Schedule) -> None:
        alert = AlertRequest(item.kind, item.text)
        self.alert.emit(alert)
        # If the UI is gone or cannot show a notification, do NOT silently
        # acknowledge the reminder. Existing bounded retry semantics apply.
        if not alert.done.wait(timeout=5) or not alert.attempted:
            raise RuntimeError("FRIDAY desktop notification unavailable")

    def run(self) -> None:
        try:
            store = ScheduleStore()
            callback = None
            if self.calendar_enabled:
                calendar = CalendarStore(store)
                if not calendar.calendar_id():
                    raise CalendarSyncError("Dedicated FRIDAY calendar not initialized.")
                # Reuse saved OAuth; never open a browser automatically.
                service = google_service()

                def callback() -> tuple[int, int]:
                    return sync_calendar(calendar, service)

            if self._stop.is_set():
                return
            run_worker(
                store,
                calendar_sync=callback,
                notify=self._notify,
                stop_event=self._stop,
                foreground=False,
            )
        except CalendarSyncError:
            self.notice.emit(
                "Calendar setup or authorization is unavailable. "
                "Check 'friday schedule calendar status' in PowerShell."
            )
        except RuntimeError as exc:
            if str(exc) == "Another FRIDAY scheduler worker is already active":
                self.notice.emit("Another scheduler already owns the reminder database.")
            else:
                self.notice.emit("The scheduler stopped unexpectedly (RuntimeError).")
        except Exception as exc:
            # Provider/HTTP exceptions can contain user data or credentials.
            self.notice.emit(f"The scheduler stopped unexpectedly ({type(exc).__name__}).")
