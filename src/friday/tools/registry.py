"""Provider-neutral, deny-by-default registry for explicit model capabilities.

Tool declarations and handlers are application-owned. A model tool request never
confers approval for a side-effecting operation. Audit metadata deliberately omits
argument values, tool results and exception messages.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class ToolPolicy(StrEnum):
    READ_ONLY = "read_only"
    APPROVAL_REQUIRED = "approval_required"


class ToolArguments(BaseModel):
    """All registered argument models must reject unknown fields and coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class NoArguments(ToolArguments):
    """A function with no model-supplied arguments."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[ToolArguments]
    handler: Callable[[ToolArguments], dict[str, Any]]
    notice: str
    policy: ToolPolicy = ToolPolicy.READ_ONLY

    def declaration(self) -> dict[str, Any]:
        """Return a fresh SDK-neutral declaration derived from typed arguments."""
        declaration: dict[str, Any] = {"name": self.name, "description": self.description}
        schema = self.arguments.model_json_schema()
        # Gemini Live accepts parameterless function declarations; preserve the
        # already field-tested clock declaration rather than changing its wire shape.
        if schema.get("properties"):
            schema.pop("title", None)
            declaration["parameters"] = schema
        return declaration


@dataclass(frozen=True, slots=True)
class ToolAuditEvent:
    """In-memory decision metadata; never stores arguments, secrets or results."""

    tool_name: str
    policy: str
    outcome: str


class ToolRegistry:
    """Only explicitly registered read-only handlers are executable in v0.3.0."""

    def __init__(self, *, audit_limit: int = 128) -> None:
        if audit_limit < 1:
            raise ValueError("audit_limit must be positive")
        self._specs: dict[str, ToolSpec] = {}
        self._audit: deque[ToolAuditEvent] = deque(maxlen=audit_limit)

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or not spec.description or not spec.notice:
            raise ValueError("Tool name, description and notice are required")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", spec.name) is None:
            raise ValueError("Invalid tool name")
        if spec.name in self._specs:
            raise ValueError("Duplicate tool name")
        if not isinstance(spec.arguments, type) or not issubclass(
            spec.arguments, ToolArguments
        ):
            raise ValueError("Tool arguments must use strict ToolArguments")
        if (
            spec.arguments.model_config.get("extra") != "forbid"
            or spec.arguments.model_config.get("strict") is not True
        ):
            raise ValueError("Tool arguments must use strict ToolArguments")
        if not isinstance(spec.policy, ToolPolicy):
            raise ValueError("Unknown tool policy")
        self._specs[spec.name] = spec

    def declarations(self) -> list[dict[str, Any]]:
        # Approval-gated tools are deliberately not offered to the model until
        # the application has a real human-approval interaction.
        return [
            spec.declaration()
            for spec in self._specs.values()
            if spec.policy is ToolPolicy.READ_ONLY
        ]

    def available_tools(self) -> tuple[tuple[str, ToolPolicy], ...]:
        return tuple((spec.name, spec.policy) for spec in self._specs.values())

    def audit_snapshot(self) -> tuple[ToolAuditEvent, ...]:
        return tuple(self._audit)

    def _record(self, name: str, policy: str, outcome: str) -> None:
        self._audit.append(ToolAuditEvent(name, policy, outcome))

    def execute(self, name: str | None, arguments: Any) -> dict[str, Any]:
        spec = self._specs.get(name) if isinstance(name, str) else None
        if spec is None:
            # Never echo untrusted function names or arguments into audit data.
            self._record("<unknown>", "none", "unknown_tool")
            return {"status": "error", "error": "unknown_tool"}
        if spec.policy is not ToolPolicy.READ_ONLY:
            self._record(spec.name, spec.policy.value, "approval_required")
            return {"status": "error", "error": "approval_required"}
        if arguments is not None and not isinstance(arguments, Mapping):
            self._record(spec.name, spec.policy.value, "invalid_arguments")
            return {"status": "error", "error": "invalid_arguments"}
        try:
            validated = spec.arguments.model_validate(
                {} if arguments is None else dict(arguments), strict=True
            )
        except (ValidationError, TypeError, ValueError):
            self._record(spec.name, spec.policy.value, "invalid_arguments")
            return {"status": "error", "error": "invalid_arguments"}
        try:
            result = spec.handler(validated)
        except Exception:
            # No private exception details are sent to the model or logged.
            self._record(spec.name, spec.policy.value, "execution_failed")
            return {"status": "error", "error": "execution_failed"}
        self._record(spec.name, spec.policy.value, "ok")
        return result

    def notice_for(self, name: str | None, result: Mapping[str, Any]) -> str:
        spec = self._specs.get(name) if isinstance(name, str) else None
        if spec is not None and result.get("status") == "ok":
            return spec.notice
        return "Unsupported, invalid, or unapproved tool request rejected"
