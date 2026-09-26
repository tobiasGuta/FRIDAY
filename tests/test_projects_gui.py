"""Headless Projects page: no Live connection, shell command or implicit launch."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from friday.tools.projects import ProjectCatalog
from friday.ui import desktop
from friday.ui.desktop import DesktopWindow


def test_projects_page_can_discover_add_remove_and_launch_with_confirmation(
    monkeypatch, tmp_path,
):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "state" / "projects.json")
    root = tmp_path / "Tools"
    root.mkdir()
    discovered = root / "FreshProject"
    discovered.mkdir()
    manual = tmp_path / "Outside" / "Other Project"
    manual.mkdir(parents=True)
    window = DesktopWindow(project_catalog=catalog)
    calls = []
    try:
        assert app is not None
        assert window._worker is None
        assert window._scheduler is None
        assert window._state == "Disconnected"
        assert window.project_list.count() == 0
        assert not catalog.path.exists()
        monkeypatch.setattr(
            desktop.QFileDialog, "getExistingDirectory", lambda *a, **kw: str(root)
        )
        window._add_project_root()
        assert window.project_list.count() == 1
        assert window.project_root_list.count() == 1

        monkeypatch.setattr(
            desktop.QFileDialog, "getExistingDirectory", lambda *a, **kw: str(manual)
        )
        monkeypatch.setattr(
            desktop.QInputDialog, "getText", lambda *a, **kw: ("My Other Project", True)
        )
        window._add_project()
        assert window.project_list.count() == 2
        assert catalog.match("My Other Project").source == "manual"

        monkeypatch.setattr(
            desktop.QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.No,
        )
        window.project_list.setCurrentRow(0)
        monkeypatch.setattr(
            window._project_launcher, "launch",
            lambda project_id, application: (
                calls.append((project_id, application)) or
                {"status": "launched", "project": "FreshProject", "application": application}
            ),
        )
        window._launch_selected_project("vscode")
        assert not calls
        monkeypatch.setattr(
            desktop.QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        window._launch_selected_project("vscode")
        window._launch_selected_project("terminal")
        assert [target for _, target in calls] == ["vscode", "terminal"]
        assert window._worker is None
        assert window._scheduler is None

        window._remove_selected_project()
        assert window.project_list.count() == 1
        window.project_root_list.setCurrentRow(0)
        window._remove_project_root()
        assert window.project_root_list.count() == 0
        assert window.project_list.count() <= 1
        assert window.experimental_hologram_action.isChecked() is False
    finally:
        window._tray = None
        window.close()


def test_projects_accessible_from_focus_without_turning_on_microphone(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    window = DesktopWindow(project_catalog=catalog)
    try:
        assert app is not None
        assert window.page_title.text() == "Voice"
        assert not window.mic_button.isEnabled()
        window._navigate("Projects")
        assert window.page_title.text() == "Projects"
        assert window._worker is None
        window._navigate("Voice")
        assert window.page_title.text() == "Voice"
        assert not window.orb._experimental
        assert not window.energy_particles_action.isChecked()
    finally:
        window._tray = None
        window.close()


def test_voice_proposal_is_visible_but_cannot_launch_without_host_click(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "FutureProject"
    folder.mkdir()
    project = catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)

    class FakeWorker:
        def __init__(self):
            self.commands = []

        def request(self, command):
            self.commands.append(command)

    fake = FakeWorker()
    try:
        window.show()
        app.processEvents()
        window._on_event("status", "Ready")
        window._on_event("project_draft", {
            "id": project.id, "name": project.name,
            "path": str(project.path), "application": "terminal",
        })
        assert window.project_approval_panel.isVisibleTo(window)
        assert window.project_approve_button.isEnabled()
        assert "Windows Terminal" in window.project_draft_description.text()
        assert window._worker is None  # No worker or OS launch from a UI event.
        window._worker = fake
        window.project_approve_button.click()
        assert fake.commands == ["project_approve"]
        window._on_event("project_result", {"status": "rejected"})
        window._on_event("project_draft", None)
        assert window._project_draft is None
        assert not window.project_approval_panel.isVisibleTo(window)
    finally:
        window._worker = None
        window._tray = None
        window.close()


def test_local_git_status_button_runs_without_voice_or_application_launch(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "Code"
    folder.mkdir()
    project = catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)

    class FakeStatus:
        def read(self, project_id):
            assert project_id == project.id
            return {
                "status": "ok", "project": project.name, "branch": "main",
                "staged": 1, "modified": 2, "untracked": 1,
                "clean": False, "last_commit": "abcd123",
                "last_subject": "Initial local commit", "last_at": None,
            }

    try:
        assert app is not None
        window._project_status_service = FakeStatus()
        window._navigate("Projects")
        window.project_list.setCurrentRow(0)
        window._check_project_status()
        worker = window._project_status_thread
        assert worker is not None
        assert worker.wait(2500)
        for _ in range(5):
            app.processEvents()
        assert "Branch: main" in window.project_status_detail.text()
        assert "Staged: 1" in window.project_status_detail.text()
        assert "Initial local commit" in window.project_status_detail.text()
        assert window._worker is None
        assert window._scheduler is None
        assert not window.orb._experimental
    finally:
        worker = window._project_status_thread
        if worker is not None:
            worker.wait(5000)
            app.processEvents()
        window._tray = None
        window.close()


def test_git_status_errors_do_not_claim_a_repository_exists(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "not a repo"
    folder.mkdir()
    project = catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)
    try:
        assert app is not None
        window.project_list.setCurrentRow(0)
        window._project_status_id = project.id
        window._display_project_status({
            "status": "error", "error": "git_unavailable_or_not_repository",
        })
        assert "not a Git repository" in window.project_status_detail.text()
        window._display_project_status({
            "status": "error", "error": "not_repository_root",
        })
        assert "repository root" in window.project_status_detail.text()
        assert window._worker is None
    finally:
        window._tray = None
        window.close()


def test_workspace_button_one_confirmation_and_visible_partial_failure(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "Workspace"
    folder.mkdir()
    project = catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)
    calls = []
    decisions = []

    def ask(*args, **kwargs):
        decisions.append(args[2])
        return QMessageBox.StandardButton.No if len(decisions) == 1 else (
            QMessageBox.StandardButton.Yes
        )

    monkeypatch.setattr(desktop.QMessageBox, "question", ask)
    monkeypatch.setattr(
        window._project_launcher, "launch",
        lambda pid, target: (
            calls.append((pid, target)) or {
                "status": "partial", "project": project.name,
                "application": target, "opened": "vscode", "failed": "terminal",
            }
        ),
    )
    try:
        assert app is not None
        window.project_list.setCurrentRow(0)
        window.project_workspace_button.click()
        assert len(decisions) == 1
        assert calls == []
        assert "VS Code and Windows Terminal" in decisions[0]
        window.project_workspace_button.click()
        assert len(decisions) == 2
        assert calls == [(project.id, "workspace")]
        assert "Terminal did not start" in window.projects_notice.text()
        assert window._worker is None
        assert window._scheduler is None
    finally:
        window._tray = None
        window.close()


def test_workspace_voice_card_identifies_both_targets_and_partial_result(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "workspace"
    folder.mkdir()
    project = catalog.add_project(folder)
    window = DesktopWindow(project_catalog=catalog)
    try:
        assert app is not None
        window.show()
        app.processEvents()
        window._on_event("status", "Ready")
        window._on_event("project_draft", {
            "id": project.id, "name": project.name,
            "path": str(project.path), "application": "workspace",
        })
        assert window.project_approval_panel.isVisibleTo(window)
        assert "VS Code + Windows Terminal" in window.project_draft_description.text()
        assert window.project_approve_button.isEnabled()
        assert window._worker is None
        window._on_event("project_result", {
            "status": "partial", "project": project.name,
            "application": "workspace", "opened": "vscode", "failed": "terminal",
        })
        assert "Terminal did not start" in window.transcript.toPlainText()
        window._on_event("project_draft", None)
        assert not window.project_approval_panel.isVisibleTo(window)
    finally:
        window._tray = None
        window.close()
