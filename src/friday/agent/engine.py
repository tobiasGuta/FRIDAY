"""Small provider-neutral agent loop with a hard read-only tool boundary.

The planner proposes data; only the app-owned ToolRegistry has authority.
No shell, filesystem writes, arbitrary network requests or approval bypasses.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from friday.tools.registry import ToolPolicy, ToolRegistry

MAX_GOAL_LENGTH = 500
MAX_ANSWER_LENGTH = 1500
MAX_TOOL_RESULT_CHARS = 3000


@dataclass(frozen=True, slots=True)
class AgentObservation:
    tool: str
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentRun:
    status: str
    answer: str
    observations: tuple[AgentObservation, ...]


class AgentPlanner(Protocol):
    def next_action(
        self, goal: str, observations: tuple[AgentObservation, ...], tools: tuple[str, ...]
    ) -> Mapping[str, Any]: ...


class AgentEngine:
    """A separate, explicitly triggered invocation with strict step and tool limits."""

    def __init__(
        self, planner: AgentPlanner, registry: ToolRegistry, *, max_steps: int = 4
    ) -> None:
        if not 1 <= max_steps <= 6:
            raise ValueError("max_steps must be between 1 and 6")
        self.planner = planner
        self.registry = registry
        self.max_steps = max_steps

    def run(self, goal: str) -> AgentRun:
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= MAX_GOAL_LENGTH:
            raise ValueError("goal must be between 1 and 500 characters")
        goal = goal.strip()
        allowed = tuple(
            name for name, policy in self.registry.available_tools()
            if policy is ToolPolicy.READ_ONLY
        )
        observations: list[AgentObservation] = []
        called: set[str] = set()
        for _ in range(self.max_steps):
            try:
                proposal = self.planner.next_action(goal, tuple(observations), allowed)
            except Exception:
                return AgentRun(
                    "planner_error", "Planner failed; no further tools ran.", tuple(observations)
                )
            if not isinstance(proposal, Mapping):
                return AgentRun("blocked", "Invalid planner decision.", tuple(observations))
            if proposal.get("kind") == "finish" and set(proposal) == {"kind", "answer"}:
                answer = proposal.get("answer")
                if isinstance(answer, str) and 1 <= len(answer.strip()) <= MAX_ANSWER_LENGTH:
                    return AgentRun("completed", answer.strip(), tuple(observations))
                return AgentRun("blocked", "Invalid final answer.", tuple(observations))
            if proposal.get("kind") != "tool" or set(proposal) != {"kind", "name", "arguments"}:
                return AgentRun("blocked", "Invalid planner decision.", tuple(observations))
            name, arguments = proposal["name"], proposal["arguments"]
            if not isinstance(name, str) or name not in allowed:
                return AgentRun("blocked", "Unapproved tool request blocked.", tuple(observations))
            # All preview tools have empty arguments, keeping the authority entirely
            # in CLI flags. Model-supplied paths, URLs, commands and repo names are forbidden.
            if arguments != {} or name in called:
                return AgentRun("blocked", "Repeated or parameterized tool request blocked.",
                                tuple(observations))
            called.add(name)
            result = self.registry.execute(name, {})
            if not isinstance(result, dict):
                return AgentRun("blocked", "Invalid tool result.", tuple(observations))
            # JSON round trip gives the model only bounded, serializable data.
            try:
                serialized = json.dumps(result, ensure_ascii=True, sort_keys=True)
            except (TypeError, ValueError):
                return AgentRun("blocked", "Unserializable tool result.", tuple(observations))
            if len(serialized) > MAX_TOOL_RESULT_CHARS:
                result = {"status": "error", "error": "result_too_large"}
            else:
                result = json.loads(serialized)
            observations.append(AgentObservation(name, result))
            if result.get("status") != "ok":
                return AgentRun("tool_error", "Tool failed; execution stopped.",
                                tuple(observations))
        return AgentRun("step_limit", "Step limit reached without a final answer.",
                        tuple(observations))
