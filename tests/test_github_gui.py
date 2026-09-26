"""Headless public GitHub UI: explicit click/voice opt-in, no real network."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from friday.tools.projects import ProjectCatalog
from friday.ui import desktop
from friday.ui.desktop import DesktopWindow


def test_public_status_is_never_fetched_on_window_construction(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "demo"
    folder.mkdir()
    catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)
    try:
        assert app is not None
        assert window._github_thread is None
        assert window.github_items.count() == 0
        assert not window.github_voice_option.isChecked()
        assert not window.github_voice_option.isEnabled()
        assert window._worker is None
        assert window._scheduler is None
        assert window.github_check_button.isEnabled()
    finally:
        window._tray = None
        window.close()


def test_explicit_public_status_button_renders_data_and_partial_error(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "demo"
    folder.mkdir()
    project = catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)

    class FakeService:
        def read(self, project_id):
            assert project_id == project.id
            return {
                "status": "partial", "project": "Demo",
                "repository": "example-user/demo",
                "checked_local": "2026-09-25T20:00:00-04:00",
                "pulls": {
                    "state": "error", "error": "github_rate_limited_or_forbidden",
                    "items": [],
                },
                "workflows": {"state": "ok", "more": False, "items": [
                    {"name": "tests", "branch": "feature", "status": "in_progress",
                     "conclusion": None, "event": "pull_request"},
                    {"name": "tests", "branch": "main", "status": "completed",
                     "conclusion": "success", "event": "push"},
                ]},
            }

    try:
        assert app is not None
        window._github_service = FakeService()
        window.project_list.setCurrentRow(0)
        window.github_check_button.click()
        thread = window._github_thread
        assert thread is not None
        assert thread.wait(3000)
        for _ in range(12):
            app.processEvents()
        output = "\n".join(
            window.github_items.item(i).text()
            for i in range(window.github_items.count())
        )
        assert "API limited" in output
        assert "in_progress / not finished" in output
        assert "main · completed / success" in output
        assert "not PR-specific checks" in output
        assert "example-user/demo" in window.github_notice.text()
        assert window._worker is None
        assert window._scheduler is None
        assert window._github_thread is None
        assert window.github_check_button.isEnabled()
    finally:
        thread = window._github_thread
        if thread is not None:
            thread.wait(5000)
            app.processEvents()
        window._tray = None
        window.close()


def test_github_voice_requires_existing_project_voice_permission(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = DesktopWindow()

    class FakeSignal:
        def connect(self, _fn):
            pass

    class FakeVoice:
        args = None

        def __init__(self, **kwargs):
            self.args = kwargs
            self.message = FakeSignal()
            self.finished = FakeSignal()

        def start(self):
            pass

    monkeypatch.setattr(desktop, "DesktopThread", FakeVoice)
    try:
        assert app is not None
        assert not window.github_voice_option.isEnabled()
        window.project_voice_option.setChecked(True)
        assert window.github_voice_option.isEnabled()
        window.github_voice_option.setChecked(True)
        window.project_voice_option.setChecked(False)
        assert not window.github_voice_option.isEnabled()
        assert not window.github_voice_option.isChecked()
        window.project_voice_option.setChecked(True)
        window.github_voice_option.setChecked(True)
        window._connect_or_disconnect()
        assert window._worker.args["github"] is True
        assert window._worker.args["projects"] is True
        assert window._worker is not None
        assert window.github_items.count() == 0
        assert window._github_thread is None
        assert not window.mic_button.isEnabled()
    finally:
        window._worker = None
        window._tray = None
        window.close()
