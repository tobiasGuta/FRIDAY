"""Explicit opt-in voice tool for public GitHub status of registered projects."""

from __future__ import annotations

from pydantic import Field

from friday.tools.github_status import GitHubReadError, PublicGitHubStatus
from friday.tools.projects import ProjectCatalog, ProjectError
from friday.tools.registry import ToolArguments, ToolRegistry, ToolSpec

GITHUB_STATUS_TOOL = "get_public_github_status"


class GitHubStatusArguments(ToolArguments):
    project: str = Field(min_length=1, max_length=80)


def register_public_github_status(
    registry: ToolRegistry, catalog: ProjectCatalog, service: PublicGitHubStatus,
) -> None:
    def read(args: GitHubStatusArguments) -> dict:
        try:
            project = catalog.match(args.project)
            return service.read(project.id)
        except ProjectError as exc:
            return {
                "status": "error",
                "error": "ambiguous_project"
                if str(exc).startswith("Several") else "unknown_project",
            }
        except GitHubReadError as exc:
            return {"status": "error", "error": exc.code}

    registry.register(ToolSpec(
        name=GITHUB_STATUS_TOOL,
        description=(
            "Read public GitHub open PR previews and most recent repository-wide "
            "Actions workflow runs for an exactly registered local project whose "
            "origin is github.com. No credentials or GitHub writes. Runs may "
            "belong to different branches and are not PR-specific checks. "
            "Never infer remote sync or guarantee CI passed for a particular PR."
        ),
        arguments=GitHubStatusArguments,
        handler=read,
        notice="Read public GitHub pull-request and workflow status on request",
    ))
