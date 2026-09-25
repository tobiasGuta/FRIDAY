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
            lambda project_id, application: calls.append((project_id, application)),
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
