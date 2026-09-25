"""Model can draft a project launch, never directly execute it."""

from friday.config import Settings
from friday.providers.gemini_live import GeminiLiveProvider
from friday.tools.project_launcher import ProjectLauncher
from friday.tools.project_voice import (
    LIST_PROJECTS_TOOL,
    PROPOSE_LAUNCH_TOOL,
    ProjectLaunchProposals,
    register_project_tools,
)
from friday.tools.projects import ProjectCatalog
from friday.tools.registry import ToolRegistry


def _ready(tmp_path):
    catalog = ProjectCatalog(tmp_path / "projects.json")
    path = tmp_path / "New Project"
    path.mkdir()
    project = catalog.add_project(path)
    calls = []
    exe = tmp_path / "Code.exe"
    exe.touch()
    launcher = ProjectLauncher(
        catalog, platform_name="win32", resolver=lambda _: exe,
        spawn=lambda argv, **kw: calls.append((argv, kw)),
    )
    proposals = ProjectLaunchProposals(catalog, launcher)
    registry = ToolRegistry()
    register_project_tools(registry, proposals)
    return project, catalog, proposals, registry, calls


def test_read_only_project_listing_does_not_reveal_paths(tmp_path):
    project, catalog, proposals, registry, calls = _ready(tmp_path)
    names = {item["name"] for item in registry.declarations()}
    assert names == {LIST_PROJECTS_TOOL, PROPOSE_LAUNCH_TOOL}
    listing = registry.execute(LIST_PROJECTS_TOOL, {"query": "new"})
    assert listing == {
        "status": "ok", "projects": [{"id": project.id, "name": "New Project"}],
        "more": False,
    }
    assert str(project.path) not in str(listing)
    assert calls == []


def test_model_cannot_approve_or_inject_process_arguments(tmp_path):
    project, catalog, proposals, registry, calls = _ready(tmp_path)
    assert registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "vscode",
    })["state"] == "pending_approval"
    assert proposals.display()["id"] == project.id
    assert calls == []
    assert registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "vscode", "approved": True
    })["error"] == "invalid_arguments"
    assert registry.execute("launch_process", {
        "cmd": "powershell"
    })["error"] == "unknown_tool"
    assert registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "terminal;calc.exe"
    })["error"] == "invalid_arguments"
    assert registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "terminal"
    })["error"] == "pending_approval"
    assert calls == []


def test_explicit_host_approval_single_use_cancel_and_revocation(tmp_path):
    project, catalog, proposals, registry, calls = _ready(tmp_path)
    registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "terminal",
    })
    assert proposals.reject()["status"] == "rejected"
    assert proposals.approve()["error"] == "no_pending_project"
    assert calls == []
    registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "terminal",
    })
    assert proposals.approve()["status"] == "launched"
    assert proposals.approve()["error"] == "no_pending_project"
    assert len(calls) == 1

    registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "vscode",
    })
    catalog.remove_project(project.id)
    assert proposals.approve()["error"] == "project_or_application_unavailable"
    assert len(calls) == 1


def test_duplicate_project_name_requires_disambiguation(tmp_path):
    project, catalog, proposals, registry, calls = _ready(tmp_path)
    duplicate = tmp_path / "different"
    duplicate.mkdir()
    catalog.add_project(duplicate, project.name)
    result = registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": "New Project", "application": "vscode",
    })
    assert result["error"] == "ambiguous_project"
    assert proposals.pending() is None
    assert calls == []


def test_project_tools_are_disabled_in_default_voice_provider(tmp_path):
    project, catalog, proposals, registry, calls = _ready(tmp_path)
    settings = Settings()
    default = GeminiLiveProvider(settings, enable_local_clock=True)
    assert LIST_PROJECTS_TOOL not in {
        t["name"] for t in default._tool_registry.declarations()
    }
    enabled = GeminiLiveProvider(
        settings, enable_local_clock=True, project_proposals=proposals,
    )
    assert {LIST_PROJECTS_TOOL, PROPOSE_LAUNCH_TOOL}.issubset({
        t["name"] for t in enabled._tool_registry.declarations()
    })
    assert calls == []


def test_expired_proposal_cannot_be_approved(tmp_path, monkeypatch):
    from friday.tools import project_voice

    project, catalog, proposals, registry, calls = _ready(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(project_voice, "monotonic", lambda: clock[0])
    registry.execute(PROPOSE_LAUNCH_TOOL, {
        "project": project.id, "application": "vscode",
    })
    clock[0] += 301.0
    assert proposals.approve()["error"] == "no_pending_project"
    assert calls == []
