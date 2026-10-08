"""Narrow read-only computer/GitHub tools; model arguments are never paths or URLs."""

from __future__ import annotations

import json
import platform
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from friday.tools.registry import NoArguments, ToolRegistry, ToolSpec

_REPO = re.compile(r"[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}\Z")
_MAX_GITHUB_BYTES = 64_000


def _environment(_args: NoArguments) -> dict[str, str]:
    return {
        "status": "ok",
        "operating_system": platform.system(),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
    }


def _workspace_listing(root: Path) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        return {"status": "error", "error": "invalid_workspace"}
    entries: list[dict[str, str]] = []
    # Only immediate public-looking names; no file bytes, hidden files, traversal,
    # links, shell execution, or arbitrarily large directory inventories.
    children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    for child in children:
        if child.name.startswith(".") or child.is_symlink():
            continue
        entries.append({
            "name": child.name[:100],
            "kind": "directory" if child.is_dir() else "file",
        })
        if len(entries) == 20:
            break
    return {"status": "ok", "entries": entries, "capped": len(entries) == 20}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _github_get(path: str) -> object:
    """Fixed GitHub API host, no redirects/proxies/auth, bounded JSON response."""
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), _NoRedirect())
    request = Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "FRIDAY-readonly-agent"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=8) as response:
            payload = response.read(_MAX_GITHUB_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(payload) > _MAX_GITHUB_BYTES:
        return None
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        return None


def _github_metadata(repo: str) -> dict[str, object]:
    payload = _github_get(f"/repos/{repo}")
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return {"status": "error", "error": "github_unavailable"}
    return {
        "status": "ok",
        "repository": repo,
        "url": f"https://github.com/{repo}",
        "description": str(payload.get("description") or "")[:160],
        "default_branch": str(payload.get("default_branch") or "")[:80],
        "archived": payload.get("archived") is True,
    }


def _github_commits(repo: str) -> dict[str, object]:
    payload = _github_get(f"/repos/{repo}/commits?per_page=5")
    if not isinstance(payload, list):
        return {"status": "error", "error": "github_unavailable"}
    commits = []
    for item in payload[:5]:
        if not isinstance(item, dict):
            continue
        info = item.get("commit")
        if not isinstance(info, dict):
            continue
        sha = item.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        commits.append({
            "sha": sha[:12],
            "message": str(info.get("message") or "").split("\n", 1)[0][:160],
        })
    return {"status": "ok", "repository": repo, "commits": commits}


def build_agent_registry(
    *, workspace: Path | None = None, github_repo: str | None = None,
    allow_network: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="computer_environment",
        description="Read operating system family, architecture and Python version.",
        arguments=NoArguments,
        handler=_environment,
        notice="Read limited computer environment information",
    ))
    if workspace is not None:
        if workspace.is_symlink() or not workspace.is_dir():
            raise ValueError("workspace must be an existing non-symlink directory")
        root = workspace.resolve(strict=True)
        registry.register(ToolSpec(
            name="workspace_entries",
            description=(
                "List at most 20 non-hidden immediate names "
                "in the explicitly selected workspace."
            ),
            arguments=NoArguments,
            handler=lambda _args: _workspace_listing(root),
            notice="Listed top-level workspace names without reading file contents",
        ))
    if github_repo is not None:
        if not _REPO.fullmatch(github_repo) or ".." in github_repo or github_repo.endswith("."):
            raise ValueError("github repo must be a valid owner/repo")
        if not allow_network:
            raise ValueError("--github-repo requires explicit --allow-network")
        registry.register(ToolSpec(
            name="github_repository",
            description="Read public metadata for the single repository selected by the human.",
            arguments=NoArguments,
            handler=lambda _args: _github_metadata(github_repo),
            notice="Read public GitHub repository metadata",
        ))
        registry.register(ToolSpec(
            name="github_recent_commits",
            description="Read five latest public commit subjects for the selected repository.",
            arguments=NoArguments,
            handler=lambda _args: _github_commits(github_repo),
            notice="Read public GitHub commit subjects",
        ))
    return registry
