"""Headless Qt smoke tests: constructing UI never opens a paid/audio session."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from friday.ui.desktop import DesktopWindow


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
        sample = '<img src="file:///private/path">'
        window._on_event("transcript", {"speaker": "user", "text": sample})
        assert sample in window.transcript.toPlainText()
        assert window._worker is None
    finally:
        window.close()
