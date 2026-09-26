"""Voice-safe project lookup and non-executing launch proposals.

The Gemini tool handler cannot start applications. Only a later, explicit
host-owned UI command can consume the exact pending proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Literal

from pydantic import Field

from friday.tools.project_launcher import Application, ProjectLauncher
from friday.tools.projects import ProjectCatalog, ProjectError
from friday.tools.registry import ToolArguments, ToolRegistry, ToolSpec

LIST_PROJECTS_TOOL = "list_local_projects"
PROPOSE_LAUNCH_TOOL = "propose_project_launch"
PROPOSAL_TTL_SECONDS = 300.0


class ListProjectsArguments(ToolArguments):
    query: str = Field(default="", max_length=80)


class ProposeLaunchArguments(ToolArguments):
    project: str = Field(min_length=1, max_length=80)
    application: Literal["vscode", "terminal"]


@dataclass(frozen=True, slots=True)
class PendingProjectLaunch:
    project_id: str
    name: str
    path: str
    application: Application
    created_at: float


class ProjectLaunchProposals:
    def __init__(self, catalog: ProjectCatalog, launcher: ProjectLauncher) -> None:
        self.catalog = catalog
        self.launcher = launcher
        self._pending: PendingProjectLaunch | None = None

    def pending(self) -> PendingProjectLaunch | None:
        if (
            self._pending is not None
            and monotonic() - self._pending.created_at >= PROPOSAL_TTL_SECONDS
        ):
            self._pending = None
        return self._pending

    def list_projects(self, args: ListProjectsArguments) -> dict:
        try:
            entries = self.catalog.projects()
        except ProjectError:
            return {"status": "error", "error": "projects_unavailable"}
        query = " ".join(args.query.casefold().split())
        matching = [
            {"id": entry.id, "name": entry.name}
            for entry in entries
            if not query or query in entry.name.casefold()
        ]
        # Do not transmit local filesystem paths to the model.
        return {
            "status": "ok", "projects": matching[:30],
            "more": len(matching) > 30,
        }

    def propose(self, args: ProposeLaunchArguments) -> dict[str, str]:
        if self.pending() is not None:
            return {"status": "error", "error": "pending_approval"}
        try:
            project = self.catalog.match(args.project)
        except ProjectError as exc:
            message = str(exc)
            code = "ambiguous_project" if message.startswith("Several") else "unknown_project"
            return {"status": "error", "error": code}
        self._pending = PendingProjectLaunch(
            project.id, project.name, str(project.path), args.application, monotonic()
        )
        return {
            "status": "ok", "state": "pending_approval",
            "project": project.name, "application": args.application,
        }

    def display(self) -> dict[str, str] | None:
        pending = self.pending()
        if pending is None:
            return None
        return {
            "id": pending.project_id, "name": pending.name,
            "path": pending.path, "application": pending.application,
        }

    def reject(self) -> dict[str, str]:
        if self.pending() is None:
            return {"status": "error", "error": "no_pending_project"}
        self._pending = None
        return {"status": "rejected"}

    def approve(self) -> dict[str, str]:
        pending = self.pending()
        if pending is None:
            return {"status": "error", "error": "no_pending_project"}
        self._pending = None  # Single-use even if Windows launch fails.
        try:
            # Re-resolve the current catalog at authorization time.
            return self.launcher.launch(pending.project_id, pending.application)
        except ProjectError:
            return {"status": "error", "error": "project_or_application_unavailable"}


def register_project_tools(
    registry: ToolRegistry, proposals: ProjectLaunchProposals
) -> None:
    registry.register(ToolSpec(
        name=LIST_PROJECTS_TOOL,
        description=(
            "List registered local project names and opaque IDs; optional name filter. "
            "Never returns paths or opens applications."
        ),
        arguments=ListProjectsArguments,
        handler=proposals.list_projects,
        notice="Read approved project names",
    ))
    registry.register(ToolSpec(
        name=PROPOSE_LAUNCH_TOOL,
        description=(
            "Draft an opening request for an exactly registered project in VS Code "
            "or Windows Terminal. No application is opened by this tool. "
            "A separate human click in FRIDAY is mandatory."
        ),
        arguments=ProposeLaunchArguments,
        handler=proposals.propose,
        notice="Project launch draft awaiting FRIDAY confirmation",
    ))
