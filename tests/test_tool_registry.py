"""No network or microphone: a model request is never an authorization token."""

import pytest
from pydantic import ConfigDict, Field

from friday.tools.builtins import build_builtin_registry
from friday.tools.registry import (
    NoArguments,
    ToolArguments,
    ToolPolicy,
    ToolRegistry,
    ToolSpec,
)
from friday.ui.cli import main


class ForecastArguments(ToolArguments):
    location: str = Field(min_length=1, max_length=80)
    days: int = Field(ge=1, le=7)


def test_default_registry_is_deny_by_default():
    registry = build_builtin_registry()
    assert registry.available_tools() == ()
    assert registry.declarations() == []
    assert registry.execute("get_local_time", {}) == {"status": "error", "error": "unknown_tool"}
    assert registry.audit_snapshot()[0].tool_name == "<unknown>"


def test_clock_declaration_and_strict_arguments_match_existing_wire_contract(monkeypatch):
    registry = build_builtin_registry(enable_local_clock=True)
    assert [d["name"] for d in registry.declarations()] == ["get_local_time"]
    assert "parameters" not in registry.declarations()[0]
    calls = []
    monkeypatch.setattr(
        "friday.tools.local_clock.read_local_clock",
        lambda: calls.append("clock") or {"time_12h": "10:18 PM"},
    )
    assert registry.execute("get_local_time", None) == {
        "status": "ok", "time_12h": "10:18 PM"
    }
    assert registry.execute("get_local_time", {"unused": None}) == {
        "status": "error", "error": "invalid_arguments"
    }
    assert registry.execute("get_local_time", ["anything"]) == {
        "status": "error", "error": "invalid_arguments"
    }
    assert registry.execute("run_shell", {"cmd": "whoami"}) == {
        "status": "error", "error": "unknown_tool"
    }
    assert calls == ["clock"]
    assert registry.notice_for("get_local_time", {"status": "ok"}) == (
        "Read computer local clock"
    )
    assert "rejected" in registry.notice_for("get_local_time", {"status": "error"})


def test_typed_arguments_reject_unknown_missing_and_coerced_values():
    calls = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="forecast_test",
            description="Fake forecast - never makes a request",
            arguments=ForecastArguments,
            handler=lambda args: calls.append(args) or {"status": "ok", "days": args.days},
            notice="Read fake forecast",
        )
    )
    decl = registry.declarations()[0]
    assert decl["parameters"]["additionalProperties"] is False
    assert set(decl["parameters"]["required"]) == {"location", "days"}
    for invalid in (
        {},
        {"location": "Queens", "days": "2"},
        {"location": "Queens", "days": True},
        {"location": "Queens", "days": 0},
        {"location": "Queens", "days": 8},
        {"location": "Queens", "days": 2, "approved": True},
        [],
    ):
        assert registry.execute("forecast_test", invalid) == {
            "status": "error", "error": "invalid_arguments"
        }
    assert calls == []
    assert registry.execute("forecast_test", {"location": "Queens", "days": 2}) == {
        "status": "ok", "days": 2
    }
    assert len(calls) == 1
    assert calls[0].location == "Queens"


def test_approval_required_tool_is_neither_advertised_nor_executable():
    registry = ToolRegistry()
    calls = []
    registry.register(
        ToolSpec(
            name="delete_test_file",
            description="Only a mock for policy tests",
            arguments=NoArguments,
            handler=lambda _: calls.append("delete") or {"status": "ok"},
            notice="Deleted test file",
            policy=ToolPolicy.APPROVAL_REQUIRED,
        )
    )
    assert registry.declarations() == []
    assert registry.execute("delete_test_file", {"approved": True}) == {
        "status": "error", "error": "approval_required"
    }
    assert calls == []
    assert registry.audit_snapshot()[-1].outcome == "approval_required"


def test_tool_failure_is_redacted_and_audit_is_bounded():
    registry = ToolRegistry(audit_limit=2)

    def fail(_: NoArguments):
        raise RuntimeError("private-data-value")

    registry.register(
        ToolSpec("broken_test", "Test failure", NoArguments, fail, "Read test value")
    )
    assert registry.execute("broken_test", {}) == {
        "status": "error", "error": "execution_failed"
    }
    registry.execute("untrusted-sensitive-name", {"private": "secret"})
    registry.execute("broken_test", {"private": "secret"})
    audit = registry.audit_snapshot()
    assert len(audit) == 2
    assert [entry.outcome for entry in audit] == ["unknown_tool", "invalid_arguments"]
    assert "secret" not in repr(audit)
    assert "private-data-value" not in repr(audit)
    assert "untrusted-sensitive-name" not in repr(audit)
    with pytest.raises(ValueError, match="positive"):
        ToolRegistry(audit_limit=0)


def test_registry_rejects_duplicate_and_permissive_argument_models():
    registry = ToolRegistry()
    spec = ToolSpec("clock_like", "Example", NoArguments, lambda _: {}, "Example")
    registry.register(spec)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(spec)

    class PermissiveArguments(ToolArguments):
        model_config = ConfigDict(extra="allow")

    with pytest.raises(ValueError, match="Invalid tool name"):
        registry.register(ToolSpec("bad-tool", "Example", NoArguments, lambda _: {}, "Example"))

    with pytest.raises(ValueError, match="strict"):
        registry.register(
            ToolSpec("unsafe", "Example", PermissiveArguments, lambda _: {}, "Example")
        )


def test_tools_cli_does_not_need_api_key(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["tools"]) == 0
    assert capsys.readouterr().out.strip() == "get_local_time: read_only"
