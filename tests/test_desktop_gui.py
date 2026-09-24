"""Headless Qt smoke tests: constructing UI never opens a paid/audio session."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from friday.schedule import WorkerHealth
from friday.ui.desktop import DesktopWindow, _calendar_label


def test_desktop_starts_offline_with_microphone_disabled():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow(input_device=1)
    try:
        assert app is not None
        assert window.windowTitle().startswith("FRIDAY")
        assert window.status.text() == "Disconnected"
        assert not window.mic_button.isEnabled()
        assert window.connect_button.isEnabled()
        assert window.reminder_option.isChecked()
        assert not window.web_option.isChecked()
        assert window._worker is None
    finally:
        window.close()


def test_approval_controls_reflect_app_owned_draft_and_result():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window._on_event("status", "Ready")
        assert window.mic_button.isEnabled()
        window._on_event("draft", {
            "action": "edit", "text": "Study security",
            "at": "2026-09-27T20:00:00-04:00",
            "before_text": "Study",
            "before_at": "2026-09-27T19:00:00-04:00",
        })
        assert window.approval_panel.isVisibleTo(window)
        assert window.approve_button.isEnabled()
        assert "Study security" in window.draft_description.text()
        window._on_event("result", {
            "status": "updated", "text": "Study security",
            "at": "2026-09-27T20:00:00-04:00",
        })
        assert "Reminder updated" in window.transcript.toPlainText()
        window._on_event("draft", None)
        assert not window.approval_panel.isVisibleTo(window)
        assert not window.approve_button.isEnabled()
    finally:
        window.close()


def test_transcript_displays_untrusted_text_as_plain_text():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        sample = '<img src="file:///private/path">'
        window._on_event("transcript", {"speaker": "user", "text": sample})
        assert sample in window.transcript.toPlainText()
        assert window._worker is None
    finally:
        window.close()


def test_desktop_health_poll_does_not_create_user_database(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        assert window.calendar_status.text() == "Calendar: worker not running"
        assert not (tmp_path / "FRIDAY" / "schedules.sqlite3").exists()
    finally:
        window._tray = None
        window.close()


def test_calendar_status_differentiates_local_only_failure_and_success():
    assert _calendar_label(WorkerHealth(False, False, "stopped")) == (
        "Calendar: worker not running"
    )
    assert "local-only" in _calendar_label(WorkerHealth(True, False, "local-only"))
    assert "sync mode unknown" in _calendar_label(WorkerHealth(True, False, "unknown"))
    assert "waiting" in _calendar_label(WorkerHealth(True, True, "waiting"))
    assert "syncing" in _calendar_label(WorkerHealth(True, True, "syncing"))
    assert "last success" in _calendar_label(
        WorkerHealth(True, True, "ok", last_success=1000)
    )
    assert "failed" in _calendar_label(
        WorkerHealth(True, True, "error", last_success=1000)
    )


def test_tray_close_hides_without_disconnecting():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()

    class FakeTray:
        def __init__(self):
            self.visible = True

        def isVisible(self):
            return self.visible

        def hide(self):
            self.visible = False

        def setToolTip(self, _value):
            return None

    fake_tray = FakeTray()
    try:
        assert app is not None
        window._tray = fake_tray
        window.show()
        assert window.isVisible()
        window.close()
        assert not window.isVisible()
        assert window._worker is None
        assert window._state == "Disconnected"
        window._open_window()
        assert window.isVisible()
    finally:
        window._tray = None
        window.close()
