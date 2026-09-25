"""Headless Qt smoke tests: constructing UI never opens a paid/audio session."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from friday.schedule import WorkerHealth
from friday.ui import desktop
from friday.ui.desktop import DesktopWindow, _calendar_label
from friday.ui.desktop_scheduler import AlertRequest


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


def test_scheduler_controls_do_not_autostart_and_respect_external_owner(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        assert window._scheduler is None
        assert not window.scheduler_button.isEnabled()  # Headless: no tray notification.
        monkeypatch.setattr(window, "_tray_notifications_available", lambda: True)
        monkeypatch.setattr(
            desktop, "read_worker_health",
            lambda: WorkerHealth(True, True, "ok", last_success=1000),
        )
        window._refresh_worker_status()
        assert window.scheduler_button.text() == "Worker running externally"
        assert not window.scheduler_button.isEnabled()
        assert not window.scheduler_sync_option.isEnabled()
        window._start_or_stop_scheduler()
        assert window._scheduler is None
    finally:
        window.close()


def test_scheduler_controls_start_and_stop_only_owned_thread(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class TestSignal:
        def __init__(self):
            self.listeners = []

        def connect(self, listener):
            self.listeners.append(listener)

    class FakeThread:
        def __init__(self, *, calendar_enabled):
            self.calendar_enabled = calendar_enabled
            self.alert = TestSignal()
            self.notice = TestSignal()
            self.finished = TestSignal()
            self.started = False
            self.stopped = False
            self.deleted = False

        def start(self):
            self.started = True

        def request_stop(self):
            self.stopped = True

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(desktop, "SchedulerThread", FakeThread)
    monkeypatch.setattr(
        desktop, "read_worker_health",
        lambda: WorkerHealth(False, False, "stopped"),
    )
    window = DesktopWindow()
    try:
        assert app is not None
        monkeypatch.setattr(window, "_tray_notifications_available", lambda: True)
        window._refresh_worker_status()
        assert window.scheduler_button.isEnabled()
        window.scheduler_sync_option.setChecked(True)
        window._start_or_stop_scheduler()
        thread = window._scheduler
        assert thread.started and thread.calendar_enabled
        assert window.scheduler_button.text() == "Stop scheduler"
        assert not window.scheduler_sync_option.isEnabled()
        window._start_or_stop_scheduler()
        assert thread.stopped and window._scheduler_stop_requested
        assert not window.scheduler_button.isEnabled()
        window._scheduler_finished()
        assert thread.deleted
        assert window._scheduler is None
    finally:
        window.close()


def test_unavailable_tray_does_not_silently_acknowledge_alert():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        alert = AlertRequest("reminder", "Study")
        window._show_scheduler_alert(alert)
        assert alert.done.is_set()
        assert not alert.attempted
    finally:
        window.close()


def test_hidden_desktop_keeps_owned_scheduler_alive(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class FakeTray:
        def __init__(self):
            self.visible = True

        def isVisible(self):
            return self.visible

        def hide(self):
            self.visible = False

        def setToolTip(self, _value):
            pass

        def showMessage(self, *_args):
            pass

    class FakeScheduler:
        def __init__(self):
            self.stopped = False

        def request_stop(self):
            self.stopped = True

    window = DesktopWindow()
    try:
        assert app is not None
        window._tray = FakeTray()
        monkeypatch.setattr(window, "_tray_notifications_available", lambda: True)
        fake = FakeScheduler()
        window._scheduler = fake
        window.show()
        window.close()
        assert not window.isVisible()
        assert not fake.stopped
        alert = AlertRequest("reminder", "Study")
        window._show_scheduler_alert(alert)
        assert alert.attempted and alert.done.is_set()
        window._open_window()
        assert window.isVisible()
    finally:
        window._scheduler = None
        window._tray = None
        window.close()


def test_recovery_waits_for_thread_exit_and_requires_click(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window._on_event("recovery", "connection")
        window._on_event("status", "Disconnected")
        window._worker_finished()
        assert window.status.text() == "Connection lost"
        assert window.connect_button.text() == "Reconnect"
        assert window.connect_button.isEnabled()
        assert not window.mic_button.isEnabled()
        assert window._worker is None  # No automatic new paid session.
        window._last_recovery = "audio"
        window._worker_finished()
        assert window.status.text() == "Audio unavailable"
        assert window.connect_button.text() == "Reconnect"
        window._last_recovery = "expired"
        window._worker_finished()
        assert window.status.text() == "Session expired"
        assert window.connect_button.text() == "Reconnect"
    finally:
        window.close()


def test_recovery_clear_on_explicit_new_connection(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()

    class FakeSignal:
        def connect(self, _listener):
            pass

    class FakeThread:
        def __init__(self, **_kwargs):
            self.message = FakeSignal()
            self.finished = FakeSignal()
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(desktop, "DesktopThread", FakeThread)
    try:
        assert app is not None
        window._last_recovery = "connection"
        window._set_state("Connection lost")
        window._connect_or_disconnect()
        assert window._worker is not None and window._worker.started
        assert window._last_recovery is None
        assert "Previous Live context is not restored" in window.transcript.toPlainText()
    finally:
        window._worker = None
        window.close()


def test_voice_recovery_does_not_stop_separate_desktop_scheduler():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()

    class OwnedScheduler:
        def __init__(self):
            self.stopped = False

        def request_stop(self):
            self.stopped = True

    scheduler = OwnedScheduler()
    try:
        assert app is not None
        window._scheduler = scheduler
        window._on_event("recovery", "connection")
        window._worker_finished()
        assert window.status.text() == "Connection lost"
        assert window._scheduler is scheduler
        assert not scheduler.stopped
    finally:
        window._scheduler = None
        window.close()


def test_brightspace_is_offline_and_requires_explicit_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        assert window._academic_sync is None
        assert window.academic_feed_input.echoMode() == desktop.QLineEdit.EchoMode.Password
        assert not window.academic_voice_option.isChecked()
        assert not window.academic_auto_option.isChecked()
        assert not window.academic_voice_option.isEnabled()
        assert not (tmp_path / "FRIDAY" / "brightspace.sqlite3").exists()
    finally:
        window.close()


def test_brightspace_secret_is_cleared_from_ui_without_logging(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    saved = []
    monkeypatch.setattr(desktop, "save_feed", lambda value: saved.append(value))
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    secret = "https://brightspace.cuny.edu/d2l/le/feed/private?token=hidden-in-test"
    try:
        assert app is not None
        window.academic_feed_input.setText(secret)
        window._save_academic_feed()
        assert saved == [secret]
        assert window.academic_feed_input.text() == ""
        assert secret not in window.academic_status.text()
        assert secret not in window.transcript.toPlainText()
        assert "Sync now" in window.academic_status.text()
    finally:
        window.close()


def test_brightspace_worker_lifecycle_keeps_voice_and_scheduler_independent(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class TestSignal:
        def __init__(self):
            self.listeners = []

        def connect(self, listener):
            self.listeners.append(listener)

    class FakeAcademicThread:
        def __init__(self):
            self.completed = TestSignal()
            self.failed = TestSignal()
            self.finished = TestSignal()
            self.started = False
            self.deleted = False

        def start(self):
            self.started = True

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(desktop, "AcademicSyncThread", FakeAcademicThread)
    window = DesktopWindow()
    try:
        assert app is not None
        window._sync_academic()
        worker = window._academic_sync
        assert worker.started
        assert not window.academic_sync_button.isEnabled()
        assert window._worker is None and window._scheduler is None
        window._sync_academic()
        assert window._academic_sync is worker  # No duplicate HTTP request.
        window._academic_failed("Calendar request failed.")
        assert "cached data" in window.academic_status.text()
        window._academic_finished()
        assert worker.deleted and window._academic_sync is None
        assert window.academic_sync_button.isEnabled()
        assert window._worker is None and window._scheduler is None
    finally:
        window.close()


def test_brightspace_due_named_event_is_not_presented_as_explicit_task(
    monkeypatch, tmp_path
):
    from friday.brightspace_calendar import AcademicStore, parse_calendar

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    payload = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:worksheet
DTSTART:20991005T235900Z
SUMMARY:Worksheet I - Due
END:VEVENT
BEGIN:VTODO
UID:task
DUE;VALUE=DATE:20991006
SUMMARY:Actual VTODO task
END:VTODO
END:VCALENDAR
"""
    store = AcademicStore()
    store.replace(parse_calendar(payload))
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        # Use a known, synthetic local-cache snapshot regardless of wall clock.
        monkeypatch.setattr(
            AcademicStore,
            "upcoming",
            lambda self, **_kwargs: self.snapshot(),
        )
        window._display_academic_cached()
        labels = [
            window.academic_list.item(i).text()
            for i in range(window.academic_list.count())
        ]
        assert any("Brightspace-labeled due (event): Worksheet I - Due" in x for x in labels)
        assert any("Due (task): Actual VTODO task" in x for x in labels)
    finally:
        window.close()


def test_hybrid_shell_navigation_exposes_six_real_pages_without_starting_workers():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        assert window.pages.count() == 6
        assert tuple(window.nav_buttons) == (
            "Home", "Voice", "Academic", "Reminders", "Calendar", "Settings"
        )
        assert window.page_title.text() == "Home"
        assert window.nav_buttons["Home"].isChecked()
        for index, page in enumerate(window.nav_buttons):
            window._navigate(page)
            assert window.pages.currentIndex() == index
            assert window.page_title.text() == page
            assert window.nav_buttons[page].isChecked()
            assert sum(button.isChecked() for button in window.nav_buttons.values()) == 1
        assert window._worker is None
        assert window._scheduler is None
        assert window._academic_sync is None
        assert window.academic_feed_input.echoMode() == desktop.QLineEdit.EchoMode.Password
        assert window.transcript.isReadOnly()
        assert window.reminder_list is not None
        assert window.scheduler_sync_option.isChecked() is False
    finally:
        window.close()


def test_dashboard_only_mirrors_session_state_and_plaintext(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window._on_event("status", "Ready")
        assert window.home_voice_state.text() == "Voice · Ready"
        dangerous = '<img src="file:///not-an-asset">'
        window._on_event("transcript", {"speaker": "user", "text": dangerous})
        assert dangerous in window.home_recent.text()
        assert window.home_recent.textFormat() == desktop.Qt.TextFormat.PlainText
        assert window.transcript.toPlainText().endswith(dangerous)
        window._on_event("reminders", [{
            "text": "Review notes", "at": "2026-10-01T15:00:00-04:00"
        }])
        assert "1 pending" in window.home_reminder_status.text()
        assert window.reminder_list.count() == 1
        monkeypatch.setattr(
            desktop, "read_worker_health",
            lambda: WorkerHealth(False, False, "stopped"),
        )
        window._refresh_worker_status()
        assert window.calendar_status.text() in window.home_scheduler_status.text()
        assert "worker not running" in window.top_scheduler_status.text()
    finally:
        window.close()


def test_approval_draft_opens_voice_page_instead_of_creating_second_approval():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window._navigate("Home")
        window._on_event("status", "Ready")
        draft = {"action": "create", "text": "Study", "at": "2026-10-01T15:00:00-04:00"}
        window._on_event("draft", draft)
        assert window.page_title.text() == "Voice"
        assert window.approval_panel.isVisibleTo(window)
        assert window.approve_button.isEnabled()
        assert window.reject_button.isEnabled()
        window._on_event("draft", None)
        assert not window.approval_panel.isVisibleTo(window)
        assert window._worker is None  # UI test never opens Gemini.
    finally:
        window.close()


def test_home_academic_summary_is_from_local_visible_records_only(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window._display_academic_cached()
        assert window.home_academic_status.text() == window.academic_status.text()
        assert window.top_academic_status.text() == "Brightspace: not synced"
        assert not (tmp_path / "FRIDAY" / "brightspace.sqlite3").exists()
    finally:
        window.close()


def test_home_polish_mirrors_voice_state_and_resizes_without_new_workers():
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window.show()
        window._on_event("status", "Listening")
        assert window.home_orb._state == "Listening"
        assert window.home_voice_title.text() == "Listening…"
        window.resize(1000, 760)
        app.processEvents()
        assert window.home_columns.direction() == desktop.QBoxLayout.Direction.TopToBottom
        assert window.top_academic_status.isHidden()
        window.resize(1330, 850)
        app.processEvents()
        assert window.home_columns.direction() == desktop.QBoxLayout.Direction.LeftToRight
        assert not window.top_academic_status.isHidden()
        assert window._worker is None
        assert window._scheduler is None
        assert window._academic_sync is None
    finally:
        window.close()


def test_home_academic_cards_preserve_source_due_distinctions(monkeypatch, tmp_path):
    from friday.brightspace_calendar import AcademicSnapshot, AcademicStore, parse_calendar

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    sample = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:label
DTSTART:20261005T235900Z
SUMMARY:Worksheet I - Due
END:VEVENT
BEGIN:VTODO
UID:explicit
DUE;VALUE=DATE:20261006
SUMMARY:Submit lab
END:VTODO
BEGIN:VEVENT
UID:lecture
DTSTART:20261007T140000Z
SUMMARY:Class meeting
END:VEVENT
END:VCALENDAR
"""
    snapshot = AcademicSnapshot(
        parse_calendar(sample),
        "2026-09-24T23:00:00+00:00",
    )
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        monkeypatch.setattr(AcademicStore, "upcoming", lambda self, **_kwargs: snapshot)
        window._display_academic_cached()
        assert window.home_academic_rows.count() == 3
        labels = [
            window.home_academic_rows.itemAt(i).widget().findChildren(desktop.QLabel)
            for i in range(3)
        ]
        assert any("Brightspace-labeled due (event)" in x.text() for x in labels[0])
        assert any("Due (task)" in x.text() for x in labels[1])
        assert any("Scheduled" in x.text() for x in labels[2])
        assert not (tmp_path / "FRIDAY" / "brightspace.sqlite3").exists()
    finally:
        window.close()


def test_home_reminders_read_only_local_preview_and_quick_sync(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    import time

    from friday.schedule import ScheduleStore, default_database_path

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    now = time.time()
    store = ScheduleStore(default_database_path())
    due = datetime.fromtimestamp(now + 3600, UTC).isoformat()
    store.reminder(due, "Read chapter", now=now)
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()
    try:
        assert app is not None
        window._refresh_home_reminders()
        assert "1 pending" in window.home_reminder_status.text()
        assert window.home_reminder_rows.count() == 1
        assert "Read chapter" in [
            label.text()
            for label in window.home_reminder_rows.itemAt(0).widget()
            .findChildren(desktop.QLabel)
        ]
        calls = []
        monkeypatch.setattr(window, "_sync_academic", lambda: calls.append("sync"))
        window._navigate("Home")
        window.home_sync_button.click()
        assert calls == ["sync"]
        assert window.page_title.text() == "Academic"
        assert window._worker is None
    finally:
        window.close()
