"""Offline safety and behavior checks for the v0.6 read-only agent preview."""

from __future__ import annotations

from friday.agent.engine import AgentEngine
from friday.agent.planners import DemoPlanner
from friday.agent import tools as agent_tools
from friday.agent.tools import build_agent_registry
from friday.tools.registry import NoArguments, ToolPolicy, ToolRegistry, ToolSpec
from friday.ui.cli import main


class Decisions:
    def __init__(self, *items):
        self.items = list(items)

    def next_action(self, goal, observations, tools):
        return self.items.pop(0)


def test_read_only_offline_demo(capsys):
    registry = build_agent_registry()
    run = AgentEngine(DemoPlanner(), registry).run("Check my computer Python version")
    assert run.status == "completed"
    assert [item.tool for item in run.observations] == ["computer_environment"]
    assert run.observations[0].result["status"] == "ok"
    assert "operating_system" in run.answer


def test_workspace_is_explicit_names_only(tmp_path):
    (tmp_path / ".env").write_text("SECRET_DO_NOT_READ")
    (tmp_path / "normal.py").write_text("SECRET_FILE_BODY")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "outside_link").symlink_to(tmp_path / "normal.py")
    registry = build_agent_registry(workspace=tmp_path)
    run = AgentEngine(DemoPlanner(), registry).run("List workspace files")
    assert run.status == "completed"
    result = run.observations[0].result
    assert {item["name"] for item in result["entries"]} == {"normal.py", "subdir"}
    assert "SECRET" not in run.answer


def test_github_requires_opt_in_and_fixed_repo(monkeypatch):
    import pytest

    with pytest.raises(ValueError, match="allow-network"):
        build_agent_registry(github_repo="owner/repo")
    with pytest.raises(ValueError):
        build_agent_registry(github_repo="../other", allow_network=True)

    paths = []

    def fake_get(path):
        paths.append(path)
        if path.endswith("per_page=5"):
            return [{"sha": "a" * 40, "commit": {"message": "Fix CI\nDetails"}}]
        return {"name": "repo", "default_branch": "main", "description": "Demo"}

    monkeypatch.setattr(agent_tools, "_github_get", fake_get)
    registry = build_agent_registry(github_repo="owner/repo", allow_network=True)
    run = AgentEngine(DemoPlanner(), registry).run("Check GitHub repo and recent commits")
    assert run.status == "completed"
    assert [item.tool for item in run.observations] == [
        "github_repository", "github_recent_commits"
    ]
    assert paths == ["/repos/owner/repo", "/repos/owner/repo/commits?per_page=5"]
    assert run.observations[-1].result["commits"][0]["message"] == "Fix CI"


def test_no_unknown_or_parameterized_or_repeated_model_tools():
    registry = build_agent_registry()
    unknown = Decisions({"kind": "tool", "name": "execute_shell", "arguments": {}})
    assert AgentEngine(unknown, registry).run("Execute a command").status == "blocked"
    param = Decisions({"kind": "tool", "name": "computer_environment",
                       "arguments": {"command": "whoami"}})
    assert AgentEngine(param, registry).run("test").status == "blocked"
    repeat = Decisions(
        {"kind": "tool", "name": "computer_environment", "arguments": {}},
        {"kind": "tool", "name": "computer_environment", "arguments": {}},
    )
    result = AgentEngine(repeat, registry).run("test")
    assert result.status == "blocked"
    assert len(result.observations) == 1


def test_approval_required_tool_cannot_be_called():
    called = []
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="write_file", description="Write a file", arguments=NoArguments,
        handler=lambda _: called.append(1) or {"status": "ok"}, notice="wrote file",
        policy=ToolPolicy.APPROVAL_REQUIRED,
    ))
    run = AgentEngine(
        Decisions({"kind": "tool", "name": "write_file", "arguments": {}}), registry
    ).run("write")
    assert run.status == "blocked"
    assert called == []


def test_limits_and_planner_fail_closed():
    import pytest

    registry = build_agent_registry()
    with pytest.raises(ValueError):
        AgentEngine(DemoPlanner(), registry, max_steps=7)
    with pytest.raises(ValueError):
        AgentEngine(DemoPlanner(), registry).run("x" * 501)
    assert AgentEngine(
        Decisions({"kind": "tool", "name": "computer_environment", "arguments": {}}),
        registry, max_steps=1,
    ).run("test").status == "step_limit"
    assert AgentEngine(
        Decisions({"kind": "finish", "answer": ""}), registry
    ).run("test").status == "blocked"


def test_error_result_stops_followup_calls():
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="fail_tool", description="Always fail", arguments=NoArguments,
        handler=lambda _: {"status": "error", "error": "unavailable"}, notice="fail",
    ))
    run = AgentEngine(
        Decisions({"kind": "tool", "name": "fail_tool", "arguments": {}}), registry,
    ).run("test")
    assert run.status == "tool_error"
    assert len(run.observations) == 1


def test_cli_explicit_offline_agent(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["agent", "--goal", "Check Python on my computer"]) == 0
    output = capsys.readouterr().out
    assert "Deterministic offline demo" in output
    assert "AGENT [completed]" in output
    assert "TOOL computer_environment" in output
    assert main(["agent", "--goal", "Read GitHub", "--github-repo", "owner/repo"]) == 1
    assert "allow-network" in capsys.readouterr().out
