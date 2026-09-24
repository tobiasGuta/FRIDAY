"""Explicit, independent Brightspace sync worker. No Gemini or microphone access."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from friday.brightspace_calendar import BrightspaceError
from friday.brightspace_feed import sync_feed


class AcademicSyncThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.completed.emit(sync_feed())
        except BrightspaceError as exc:
            # Only controlled, sanitized messages may cross into UI.
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Brightspace synchronization stopped unexpectedly.")
