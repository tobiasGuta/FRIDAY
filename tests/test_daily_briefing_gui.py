"""Headless on-demand briefing UI, no implicit service/session work."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from friday.brightspace_calendar import AcademicStore
from friday.tools.daily_briefing import DailyBriefingService
from friday.ui import desktop
from friday.ui.desktop import DesktopWindow


def test_offline_briefing_waits_for_click_and_does_not_create_local_stores(
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    academic = tmp_path / "brightspace.sqlite3"
    reminders = tmp_path / "schedules.sqlite3"
    window = DesktopWindow()
    try:
        assert app is not None
        assert window._briefing_thread is None
        assert not window.briefing_voice_option.isChecked()
        assert window._worker is None
        window._briefing_service = DailyBriefingService(
            academic_store=AcademicStore(academic), reminder_path=reminders,
        )
        window._navigate("Home")
        assert window.briefing_refresh_button.isEnabled()
        assert window.briefing_items.count() == 0
        window.briefing_refresh_button.click()
        thread = window._briefing_thread
        assert thread is not None
        assert thread.wait(3500)
        for _ in range(10):
            app.processEvents()
        lines = "\n".join(
            window.briefing_items.item(i).text()
            for i in range(window.briefing_items.count())
        )
        assert "no locally synced" in lines
        assert "no local reminder store yet" in lines
        assert window.briefing_refresh_button.isEnabled()
        assert window._briefing_thread is None
        assert window._worker is None
        assert window._scheduler is None
        assert not academic.exists()
        assert not reminders.exists()
        assert "no calendar sync was performed" in window.briefing_notice.text()
    finally:
        thread = window._briefing_thread
        if thread is not None:
            thread.wait(5000)
            app.processEvents()
        window._tray = None
        window.close()


def test_voice_briefing_opt_in_is_captured_for_next_connection(
    monkeypatch,
):
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()

    class TestSignal:
        def connect(self, _fn):
            pass

    class FakeVoice:
        args = None

        def __init__(self, **kwargs):
            self.args = kwargs
            self.message = TestSignal()
            self.finished = TestSignal()

        def start(self):
            pass

    monkeypatch.setattr(desktop, "DesktopThread", FakeVoice)
    try:
        assert app is not None
        window._navigate("Home")
        assert not window.briefing_voice_option.isChecked()
        window.briefing_voice_option.setChecked(True)
        window._connect_or_disconnect()
        assert window._worker is not None
        assert window._worker.args["briefing"] is True
        assert window._worker.args["briefing_reminders"] is True
        assert window._worker.args["briefing_academic"] is False
        assert window._state == "Connecting"
        assert window.briefing_items.count() == 0
        assert not window.mic_button.isEnabled()
        assert window._scheduler is None
    finally:
        window._worker = None
        window._tray = None
        window.close()
