"""Offline GitHub status tests: no token, network, arbitrary URL, or Git write."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from friday.config import Settings
from friday.providers.gemini_live import GITHUB_INSTRUCTION, GeminiLiveProvider
from friday.tools.github_status import (
    GitHubReadError,
    PublicGitHubStatus,
    parse_public_origin,
    registered_public_repo,
)
from friday.tools.github_voice import GITHUB_STATUS_TOOL, register_public_github_status
from friday.tools.projects import ProjectCatalog
from friday.tools.registry import ToolRegistry


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True, text=True, check=True, timeout=10,
    )
    return proc.stdout.strip()


@pytest.fixture
def registered(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git executable required for fixture")
    root = tmp_path / "New Public Project"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "remote", "add", "origin", "git@github.com:example-user/demo-tool.git")
    catalog = ProjectCatalog(tmp_path / "catalog.json")
    entry = catalog.add_project(root, "Demo")
    return root, catalog, entry


def _fixtures(url: str):
    if url.endswith("/pulls?state=open&sort=updated&direction=desc&per_page=11"):
        return [
            {"number": 13, "title": "Fix HTTP QUERY", "draft": False,
             "updated_at": "2026-09-25T20:00:00Z"},
            {"number": 12, "title": "Research spike", "draft": True,
             "updated_at": "2026-09-25T15:00:00Z"},
        ]
    if url.endswith("/actions/runs?per_page=6"):
        return {"total_count": 2, "workflow_runs": [
            {"id": 123, "name": "test", "status": "in_progress",
             "conclusion": None, "head_branch": "feature/QUERY",
             "event": "pull_request", "created_at": "2026-09-25T20:00:00Z"},
            {"id": 122, "name": "test", "status": "completed",
             "conclusion": "success", "head_branch": "main",
             "event": "push", "created_at": "2026-09-25T15:00:00Z"},
        ]}
    pytest.fail("unexpected URL")


@pytest.mark.parametrize("origin", [
    "https://github.com/example-user/demo-tool",
    "https://github.com/example-user/demo-tool.git",
    "git@github.com:example-user/demo-tool.git",
    "ssh://git@github.com/example-user/demo-tool.git",
])
def test_github_origin_is_strict_and_has_no_network(origin):
    assert parse_public_origin(origin) == "example-user/demo-tool"


@pytest.mark.parametrize("origin", [
    "https://github.com.evil.test/owner/repo",
    "https://token@github.com/owner/repo",
    "https://github.com/owner/repo?token=private",
    "https://other.example/owner/repo",
    "git@other.example:owner/repo",
    "https://github.com/owner/repo/extra",
    "file:///private/path",
    "https://github.com/owner/../repo",
    "https://github.com/owner/repo#fragment",
    "",
])
def test_unsafe_origin_is_rejected_without_echo(origin):
    with pytest.raises(GitHubReadError, match="unsupported_origin") as exc:
        parse_public_origin(origin)
    assert origin not in str(exc.value) or not origin


def test_registered_repository_and_bounded_public_reads(registered):
    root, catalog, project = registered
    assert registered_public_repo(catalog, project.id) == ("Demo", "example-user/demo-tool")
    queried = []

    def getter(url):
        queried.append(url)
        return _fixtures(url)

    service = PublicGitHubStatus(catalog, getter=getter)
    snapshot = service.read(project.id)
    assert snapshot["status"] == "ok"
    assert snapshot["repository"] == "example-user/demo-tool"
    assert snapshot["pulls"]["items"][0]["number"] == 13
    assert snapshot["pulls"]["items"][1]["draft"] is True
    assert snapshot["workflows"]["items"][0]["status"] == "in_progress"
    assert snapshot["workflows"]["items"][0]["conclusion"] is None
    assert snapshot["workflows"]["items"][1]["branch"] == "main"
    assert len(queried) == 2
    assert all(url.startswith("https://api.github.com/repos/example-user/demo-tool/")
               for url in queried)
    assert all("token" not in url for url in queried)
    assert str(root) not in str(snapshot)


def test_unsupported_origin_missing_origin_nested_and_revoked(registered, tmp_path):
    root, catalog, project = registered
    _git(root, "remote", "set-url", "origin", "https://token@github.com/owner/repo")
    calls = []
    service = PublicGitHubStatus(
        catalog, getter=lambda url: calls.append(url),
    )
    with pytest.raises(GitHubReadError, match="unsupported_origin"):
        service.read(project.id)
    assert calls == []
    _git(root, "remote", "remove", "origin")
    with pytest.raises(GitHubReadError, match="unsupported_origin"):
        service.read(project.id)
    nested = root / "nested"
    nested.mkdir()
    child = catalog.add_project(nested)
    with pytest.raises(GitHubReadError, match="not_repository_root"):
        service.read(child.id)
    catalog.remove_project(project.id)
    with pytest.raises(GitHubReadError, match="unknown_project"):
        service.read(project.id)
    assert calls == []


def test_partial_lookup_does_not_claim_no_prs_or_workflow_success(registered):
    root, catalog, project = registered

    def getter(url):
        if "/pulls?" in url:
            raise GitHubReadError("github_rate_limited_or_forbidden")
        return _fixtures(url)

    result = PublicGitHubStatus(catalog, getter=getter).read(project.id)
    assert result["status"] == "partial"
    assert result["pulls"] == {
        "state": "error", "error": "github_rate_limited_or_forbidden", "items": [],
    }
    assert result["workflows"]["items"][0]["conclusion"] is None
    assert result["workflows"]["items"][1]["branch"] == "main"


def test_all_failure_is_error_and_never_auto_retries(registered):
    root, catalog, project = registered
    calls = []

    def getter(url):
        calls.append(url)
        raise GitHubReadError("github_public_repo_unavailable")

    result = PublicGitHubStatus(catalog, getter=getter).read(project.id)
    assert result["status"] == "error"
    assert len(calls) == 2
    assert result["pulls"]["state"] == "error"
    assert result["workflows"]["state"] == "error"


def test_preview_bounded_and_names_are_untrusted_data(registered):
    root, catalog, project = registered
    title = "<img src='file:///secret'>"
    def getter(url):
        if "/pulls?" in url:
            return [
                {"number": n + 1, "title": title, "draft": False}
                for n in range(11)
            ]
        return {"total_count": 100, "workflow_runs": [
            {"id": n + 1, "name": "test", "status": "queued", "conclusion": None}
            for n in range(6)
        ]}

    result = PublicGitHubStatus(catalog, getter=getter).read(project.id)
    assert len(result["pulls"]["items"]) == 10
    assert result["pulls"]["more"]
    assert len(result["workflows"]["items"]) == 5
    assert result["workflows"]["more"]
    assert result["pulls"]["items"][0]["title"] == title


def test_voice_public_status_requires_opt_in_and_rejects_extra_arguments(registered):
    root, catalog, project = registered
    calls = []
    service = PublicGitHubStatus(catalog, getter=lambda url: (
        calls.append(url) or _fixtures(url)
    ))
    registry = ToolRegistry()
    register_public_github_status(registry, catalog, service)
    assert GITHUB_STATUS_TOOL in {x["name"] for x in registry.declarations()}
    result = registry.execute(GITHUB_STATUS_TOOL, {"project": project.id})
    assert result["repository"] == "example-user/demo-tool"
    assert len(calls) == 2
    assert registry.execute(GITHUB_STATUS_TOOL, {
        "project": project.id, "url": "https://evil.test",
    })["error"] == "invalid_arguments"
    assert registry.execute(GITHUB_STATUS_TOOL, {
        "project": "not registered",
    })["error"] == "unknown_project"
    assert len(calls) == 2
    settings = Settings(_env_file=None)
    default = GeminiLiveProvider(settings)
    enabled = GeminiLiveProvider(
        settings, github_status=service, github_catalog=catalog,
    )
    assert GITHUB_STATUS_TOOL not in {
        x["name"] for x in default._tool_registry.declarations()
    }
    assert GITHUB_STATUS_TOOL in {
        x["name"] for x in enabled._tool_registry.declarations()
    }
    assert "not PR-specific checks" in GITHUB_INSTRUCTION
    assert "no private repositories" in GITHUB_INSTRUCTION
