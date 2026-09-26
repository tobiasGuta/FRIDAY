"""Offline Git status regression: fixed commands, no files or Git mutations leaked."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from friday.tools.project_status import ProjectGitStatus, ProjectStatusError
from friday.tools.projects import ProjectCatalog


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, timeout=10,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git executable required for local status integration test")
    folder = tmp_path / "An Odd ; Project"
    folder.mkdir()
    git(folder, "init", "-b", "main")
    git(folder, "config", "user.name", "FRIDAY test")
    git(folder, "config", "user.email", "friday-test@example.invalid")
    (folder / "first.txt").write_text("first\n", encoding="utf-8")
    (folder / "second.txt").write_text("second\n", encoding="utf-8")
    git(folder, "add", "first.txt", "second.txt")
    git(folder, "commit", "-m", "Initial baseline")
    catalog = ProjectCatalog(tmp_path / "config" / "projects.json")
    project = catalog.add_project(folder)
    return folder, catalog, project


def test_clean_and_dirty_repository_status_is_bounded_and_nonmutating(repo):
    folder, catalog, project = repo
    service = ProjectGitStatus(catalog)
    before = git(folder, "rev-parse", "HEAD")
    clean = service.read(project.id)
    assert clean["status"] == "ok"
    assert clean["branch"] == "main"
    assert clean["clean"]
    assert clean["staged"] == clean["modified"] == clean["untracked"] == 0
    assert clean["last_subject"] == "Initial baseline"
    assert clean["last_commit"]
    (folder / "first.txt").write_text("staged\n", encoding="utf-8")
    git(folder, "add", "first.txt")
    (folder / "second.txt").write_text("unstaged\n", encoding="utf-8")
    untracked = folder / "new dir"
    untracked.mkdir()
    (untracked / "secret.txt").write_text("not for model\n", encoding="utf-8")
    dirty = service.read(project.id)
    assert (dirty["staged"], dirty["modified"], dirty["untracked"]) == (1, 1, 1)
    assert dirty["clean"] is False
    assert "secret.txt" not in str(dirty)
    assert str(folder) not in str(dirty)
    assert git(folder, "rev-parse", "HEAD") == before
    assert git(folder, "status", "--porcelain")  # Our read did not stage/commit anything.


def test_nonrepo_and_nested_directory_do_not_read_parent_repository(tmp_path, repo):
    folder, catalog, project = repo
    plain = tmp_path / "not git"
    plain.mkdir()
    manual = catalog.add_project(plain)
    service = ProjectGitStatus(catalog)
    with pytest.raises(ProjectStatusError, match="not_repository"):
        service.read(manual.id)
    nested = folder / "nested"
    nested.mkdir()
    child = catalog.add_project(nested)
    with pytest.raises(ProjectStatusError, match="not_repository_root"):
        service.read(child.id)


def test_missing_project_and_absent_git_are_fail_closed(repo):
    folder, catalog, project = repo
    service = ProjectGitStatus(catalog, git_executable=str(folder / "missing-git.exe"))
    with pytest.raises(ProjectStatusError, match="git_unavailable"):
        service.read(project.id)
    catalog.remove_project(project.id)
    with pytest.raises(ProjectStatusError, match="unknown_project"):
        service.read(project.id)


def test_invalid_or_oversized_porcelain_is_rejected_without_filenames(repo):
    folder, catalog, project = repo

    def fake_run(argv, **kwargs):
        if "status" in argv:
            data = b"?? private-file.txt\0" * 5000
        elif "rev-parse" in argv:
            data = (str(folder) + "\n").encode()
        else:
            data = b"main\n"
        return subprocess.CompletedProcess(argv, 0, data)

    service = ProjectGitStatus(catalog, git_executable="git", runner=fake_run)
    with pytest.raises(ProjectStatusError, match="git_output_too_large"):
        service.read(project.id)
    assert not (folder / "private-file.txt").exists()


def test_unborn_repository_does_not_invent_a_last_commit(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git executable required")
    folder = tmp_path / "Unborn"
    folder.mkdir()
    git(folder, "init", "-b", "main")
    catalog = ProjectCatalog(tmp_path / "projects.json")
    entry = catalog.add_project(folder)
    summary = ProjectGitStatus(catalog).read(entry.id)
    assert summary["branch"] == "main"
    assert summary["last_commit"] is None
    assert summary["last_subject"] is None
