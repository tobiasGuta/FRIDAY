"""Offline dynamic project catalog and launch-boundary regression tests."""

from pathlib import Path

import pytest

from friday.tools.project_launcher import ProjectLauncher
from friday.tools.projects import ProjectCatalog, ProjectError


def test_catalog_starts_empty_and_manual_entry_persists(tmp_path):
    store = ProjectCatalog(tmp_path / "state" / "projects.json")
    folder = tmp_path / "Outside Tools" / "New Project"
    folder.mkdir(parents=True)
    assert store.projects() == ()
    project = store.add_project(folder, "My Project")
    assert project.name == "My Project"
    assert store.path.exists()
    reloaded = ProjectCatalog(store.path)
    assert reloaded.match("my project").id == project.id
    assert reloaded.roots() == ()
    reloaded.remove_project(project.id)
    assert reloaded.projects() == ()


def test_explicit_one_level_discovery_refresh_hide_and_unhide(tmp_path):
    store = ProjectCatalog(tmp_path / "projects.json")
    root = tmp_path / "Tools"
    root.mkdir()
    first = root / "Reconductor"
    first.mkdir()
    nested = first / "nested"
    nested.mkdir()
    hidden = root / ".secrets"
    hidden.mkdir()
    assert store.projects() == ()  # Never scan even a familiar root implicitly.
    store.add_root(root)
    assert [p.name for p in store.projects()] == ["Reconductor"]
    second = root / "Future Project"
    second.mkdir()
    assert {p.name for p in store.projects()} == {"Reconductor", "Future Project"}
    discovered = store.match("Future Project")
    assert discovered.source == "discovered"
    store.remove_project(discovered.id)
    assert {p.name for p in store.projects()} == {"Reconductor"}
    # Manual registration restores a hidden discovery without source modifications.
    assert store.add_project(second).id == discovered.id
    assert store.match("future project").source == "manual"
    store.remove_root(root)
    assert [p.name for p in store.projects()] == ["Future Project"]


def test_duplicate_display_names_are_not_guessed(tmp_path):
    store = ProjectCatalog(tmp_path / "projects.json")
    for label in ("a", "b"):
        directory = tmp_path / label
        directory.mkdir()
        store.add_project(directory, "Sample")
    with pytest.raises(ProjectError, match="Several projects"):
        store.match("sample")
    assert len({p.id for p in store.projects()}) == 2


def test_missing_paths_corrupt_config_and_bad_roots_fail_closed(tmp_path):
    store = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "vanishing"
    folder.mkdir()
    registered = store.add_project(folder)
    folder.rmdir()
    with pytest.raises(ProjectError, match="no longer registered"):
        store.find(registered.id)
    store.path.write_text('{"version":2,"roots":[]}', encoding="utf-8")
    with pytest.raises(ProjectError, match="configuration"):
        store.projects()
    store.path.write_text("not json", encoding="utf-8")
    with pytest.raises(ProjectError, match="configuration"):
        store.projects()


def test_symlink_outside_authorized_root_is_not_discovered(tmp_path):
    store = ProjectCatalog(tmp_path / "projects.json")
    root = tmp_path / "Tools"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    link = root / "external-link"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This system cannot create directory symlinks")
    store.add_root(root)
    assert store.projects() == ()


def test_launcher_uses_fixed_argument_list_and_revalidates_registration(tmp_path):
    store = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "Project ; echo unwanted"
    folder.mkdir()
    project = store.add_project(folder)
    exe = tmp_path / "Code.exe"
    exe.touch()
    calls = []
    launcher = ProjectLauncher(
        store, platform_name="win32", resolver=lambda app: exe,
        spawn=lambda argv, **kwargs: calls.append((argv, kwargs)),
    )
    result = launcher.launch(project.id, "vscode")
    assert result["status"] == "launched"
    assert calls[0][0] == [str(exe), "--new-window", str(folder.resolve())]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["cwd"] == str(folder.resolve())
    launcher.launch(project.id, "terminal")
    assert calls[1][0] == [
        str(exe), "-w", "0", "new-tab", "-d", str(folder.resolve())
    ]
    with pytest.raises(ProjectError, match="Unsupported"):
        launcher.launch(project.id, "powershell -c something")
    store.remove_project(project.id)
    with pytest.raises(ProjectError, match="no longer registered"):
        launcher.launch(project.id, "vscode")
    assert len(calls) == 2


def test_launcher_rejects_non_windows_and_unavailable_executable(tmp_path):
    store = ProjectCatalog(tmp_path / "projects.json")
    folder = tmp_path / "project"
    folder.mkdir()
    project = store.add_project(folder)
    launcher = ProjectLauncher(store, platform_name="linux")
    with pytest.raises(ProjectError, match="Windows only"):
        launcher.launch(project.id, "vscode")
    broken = ProjectLauncher(
        store, platform_name="win32", resolver=lambda app: Path("missing.exe"),
        spawn=lambda *args, **kwargs: pytest.fail("must not spawn"),
    )
    with pytest.raises(ProjectError, match="unavailable"):
        broken.launch(project.id, "vscode")


def test_catalog_file_uses_nonrepo_location_by_default(monkeypatch, tmp_path):
    from friday.tools import projects as module

    if module.os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert module.default_project_path() == tmp_path / "FRIDAY" / "projects.json"
    assert not module.default_project_path().exists()
