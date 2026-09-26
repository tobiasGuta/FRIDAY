"""Read-only, user-initiated public GitHub PR and Actions status.

The repo is derived only from a registered local Git root's strict GitHub
origin URL. No credentials, Git fetch, arbitrary host, model-supplied URL,
shell, repository write, background polling, or automatic retries.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from friday.tools.projects import ProjectCatalog, ProjectError

GIT_TIMEOUT = 3
NETWORK_TIMEOUT = 5
MAX_GIT_OUTPUT = 4096
MAX_HTTP_BYTES = 256 * 1024
MAX_PULLS = 10
MAX_RUNS = 5
ORIGIN_PREFIX = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})/?",
    flags=re.IGNORECASE,
)


class GitHubReadError(ValueError):
    """Stable UI/voice error; never contains Git remote, headers or credentials."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _clean(value: Any, max_len: int = 100) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable()).strip()[:max_len]


def parse_public_origin(origin: str) -> str:
    """Accept only explicit github.com origin forms, never an arbitrary URL."""
    if not isinstance(origin, str) or len(origin) > 250:
        raise GitHubReadError("unsupported_origin")
    match = ORIGIN_PREFIX.fullmatch(origin.strip())
    if match is None:
        raise GitHubReadError("unsupported_origin")
    owner, name = match.groups()
    if name.casefold().endswith(".git"):
        name = name[:-4]
    if name in ("", ".", "..") or name.startswith(".") or name.endswith("."):
        raise GitHubReadError("unsupported_origin")
    if owner.endswith("-") or ".." in name:
        raise GitHubReadError("unsupported_origin")
    return f"{owner}/{name}"


def _git_output(path: Path, args: list[str], *,
                runner: Callable[..., Any] = subprocess.run) -> str:
    binary = shutil.which("git")
    if not binary:
        raise GitHubReadError("git_unavailable")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = runner(
            [binary, "--no-pager", "--no-optional-locks",
             "-c", "core.fsmonitor=false", "-C", str(path), *args],
            cwd=str(path), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, shell=False, timeout=GIT_TIMEOUT, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise GitHubReadError("git_unavailable") from exc
    if proc.returncode != 0 or not isinstance(proc.stdout, bytes):
        raise GitHubReadError("git_unavailable_or_not_repository")
    if len(proc.stdout) > MAX_GIT_OUTPUT:
        raise GitHubReadError("git_output_too_large")
    return proc.stdout.decode("utf-8", "replace").strip()


def registered_public_repo(catalog: ProjectCatalog, project_id: str, *,
                           runner: Callable[..., Any] = subprocess.run) -> tuple[str, str]:
    try:
        project = catalog.find(project_id)
    except ProjectError as exc:
        raise GitHubReadError("unknown_project") from exc
    root = _git_output(project.path, ["rev-parse", "--show-toplevel"], runner=runner)
    try:
        resolved_root = Path(root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GitHubReadError("git_unavailable_or_not_repository") from exc
    if os.path.normcase(str(resolved_root)) != os.path.normcase(str(project.path)):
        raise GitHubReadError("not_repository_root")
    try:
        origin = _git_output(
            project.path, ["config", "--local", "--get-all", "remote.origin.url"],
            runner=runner,
        )
    except GitHubReadError as exc:
        if exc.code == "git_unavailable_or_not_repository":
            raise GitHubReadError("unsupported_origin") from exc
        raise
    # Never echo origin: it could contain a token or a different private host.
    if len(origin.splitlines()) != 1:
        raise GitHubReadError("unsupported_origin")
    return project.name, parse_public_origin(origin)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _public_api_get(url: str) -> Any:
    """No auth header, redirect, ambient proxy or mutable HTTP method."""
    if not url.startswith("https://api.github.com/repos/"):
        raise GitHubReadError("invalid_api_target")
    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "FRIDAY-public-readonly",
        },
        method="GET",
    )
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=NETWORK_TIMEOUT) as response:
            if response.status != 200:
                raise GitHubReadError("github_request_failed")
            raw = response.read(MAX_HTTP_BYTES + 1)
    except error.HTTPError as exc:
        if exc.code in (403, 429):
            raise GitHubReadError("github_rate_limited_or_forbidden") from exc
        if exc.code == 404:
            raise GitHubReadError("github_public_repo_unavailable") from exc
        raise GitHubReadError("github_request_failed") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise GitHubReadError("github_unreachable") from exc
    if len(raw) > MAX_HTTP_BYTES:
        raise GitHubReadError("github_response_too_large")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise GitHubReadError("github_invalid_response") from exc


def _pulls(data: Any) -> dict[str, Any]:
    if not isinstance(data, list):
        raise GitHubReadError("github_invalid_response")
    items = []
    for item in data[:MAX_PULLS]:
        if not isinstance(item, dict) or type(item.get("number")) is not int:
            raise GitHubReadError("github_invalid_response")
        number = item["number"]
        if not 1 <= number <= 10**10:
            raise GitHubReadError("github_invalid_response")
        items.append({
            "number": number, "title": _clean(item.get("title"), 100),
            "draft": item.get("draft") is True,
            "updated_at": _clean(item.get("updated_at"), 40),
        })
    return {
        "state": "ok", "scope": "up to 10 open PRs ordered by update time",
        "items": items, "more": len(data) > MAX_PULLS,
    }


def _runs(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
        raise GitHubReadError("github_invalid_response")
    items = []
    for item in data["workflow_runs"][:MAX_RUNS]:
        if (
            not isinstance(item, dict) or type(item.get("id")) is not int
            or not 1 <= item["id"] <= 10**20
        ):
            raise GitHubReadError("github_invalid_response")
        status = _clean(item.get("status"), 30)
        conclusion = _clean(item.get("conclusion"), 30) or None
        items.append({
            "id": item["id"], "name": _clean(item.get("name"), 80),
            "status": status, "conclusion": conclusion,
            "branch": _clean(item.get("head_branch"), 100),
            "event": _clean(item.get("event"), 40),
            "created_at": _clean(item.get("created_at"), 40),
        })
    return {
        "state": "ok", "scope": "latest 5 repository workflow runs across branches",
        "items": items,
        "more": isinstance(data.get("total_count"), int)
        and data["total_count"] > len(items),
    }


class PublicGitHubStatus:
    def __init__(
        self, catalog: ProjectCatalog, *,
        runner: Callable[..., Any] = subprocess.run,
        getter: Callable[[str], Any] = _public_api_get,
    ) -> None:
        self.catalog = catalog
        self.runner = runner
        self.getter = getter

    def read(self, project_id: str) -> dict[str, Any]:
        name, repo = registered_public_repo(
            self.catalog, project_id, runner=self.runner,
        )
        owner, slug = repo.split("/")
        base = f"https://api.github.com/repos/{owner}/{slug}"
        result: dict[str, Any] = {
            "status": "ok", "project": name, "repository": repo,
            "checked_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        endpoints = (
            ("pulls", f"{base}/pulls?state=open&sort=updated&direction=desc&per_page=11",
             _pulls),
            ("workflows", f"{base}/actions/runs?per_page=6", _runs),
        )
        for key, url, parser in endpoints:
            try:
                result[key] = parser(self.getter(url))
            except GitHubReadError as exc:
                result[key] = {"state": "error", "error": exc.code, "items": []}
            except Exception:
                result[key] = {
                    "state": "error", "error": "github_unavailable", "items": [],
                }
        ok = sum(result[key]["state"] == "ok" for key in ("pulls", "workflows"))
        result["status"] = "ok" if ok == 2 else "partial" if ok == 1 else "error"
        return result
