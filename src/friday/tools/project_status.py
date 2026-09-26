"""Bounded, read-only Git summary for an exactly registered project.

No shell, Git network operation, file contents, diffs or filenames are returned.
Git is never run for an arbitrary model-supplied filesystem path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from friday.tools.projects import ProjectCatalog, ProjectError

MAX_OUTPUT = 64 * 1024
GIT_TIMEOUT_SECONDS = 3


class ProjectStatusError(ValueError):
    """A stable error code; never expose Git stderr or private paths."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _plain(value: str, *, limit: int) -> str:
    """Display Git-controlled labels as bounded plain text, never instructions."""
    return "".join(c for c in value if c.isprintable()).strip()[:limit]


class ProjectGitStatus:
    def __init__(
        self,
        catalog: ProjectCatalog,
        *,
        git_executable: str | None = None,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.catalog = catalog
        self.git_executable = git_executable
        self.runner = runner

    def _git(self, path: Path, *args: str, allow_failure: bool = False) -> bytes | None:
        binary = self.git_executable or shutil.which("git")
        if not binary:
            raise ProjectStatusError("git_unavailable")
        # Absolute Git program, fixed argv, no shell or optional index lock.
        # Disable repository-configured fsmonitor commands during status.
        argv = [
            str(binary), "--no-pager", "--no-optional-locks",
            "-c", "core.fsmonitor=false", "-C", str(path), *args,
        ]
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        try:
            done = self.runner(
                argv, cwd=str(path), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, shell=False, timeout=GIT_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProjectStatusError("git_timeout") from exc
        except OSError as exc:
            raise ProjectStatusError("git_unavailable") from exc
        if done.returncode != 0:
            if allow_failure:
                return None
            raise ProjectStatusError("git_unavailable_or_not_repository")
        raw = done.stdout
        if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT:
            raise ProjectStatusError("git_output_too_large")
        return raw

    def read(self, project_id: str) -> dict[str, Any]:
        try:
            project = self.catalog.find(project_id)
        except ProjectError as exc:
            raise ProjectStatusError("unknown_project") from exc

        root = self._git(project.path, "rev-parse", "--show-toplevel")
        assert root is not None
        try:
            root_path = Path(os.fsdecode(root.strip())).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProjectStatusError("git_unavailable_or_not_repository") from exc
        # A registered child folder cannot silently read an ancestor repository.
        if os.path.normcase(str(root_path)) != os.path.normcase(str(project.path)):
            raise ProjectStatusError("not_repository_root")

        raw_status = self._git(
            project.path, "status", "--porcelain=v1", "-z",
            "--no-renames", "--untracked-files=normal",
        )
        assert raw_status is not None
        staged = changed = untracked = 0
        if raw_status:
            if not raw_status.endswith(b"\x00"):
                raise ProjectStatusError("git_invalid_output")
            for entry in raw_status[:-1].split(b"\x00"):
                if len(entry) < 4 or entry[2:3] != b" ":
                    raise ProjectStatusError("git_invalid_output")
                code = entry[:2]
                if code == b"??":
                    untracked += 1  # May represent an untracked directory.
                elif code == b"!!":
                    continue
                else:
                    staged += int(code[:1] != b" ")
                    changed += int(code[1:2] != b" ")

        raw_branch = self._git(
            project.path, "symbolic-ref", "--quiet", "--short", "HEAD",
            allow_failure=True,
        )
        branch = (
            _plain(raw_branch.decode("utf-8", "replace"), limit=100)
            if raw_branch is not None else "detached HEAD"
        ) or "unknown"

        raw_commit = self._git(
            project.path, "log", "-1", "--no-show-signature",
            "--format=%h%x00%s%x00%cI", allow_failure=True,
        )
        short_hash = last_subject = last_at = None
        if raw_commit is not None:
            fields = raw_commit.decode("utf-8", "replace").rstrip("\r\n").split("\x00")
            if len(fields) == 3:
                short_hash = _plain(fields[0], limit=16) or None
                last_subject = _plain(fields[1], limit=120) or None
                last_at = _plain(fields[2], limit=40) or None

        return {
            "status": "ok", "project": project.name, "branch": branch,
            "staged": staged, "modified": changed, "untracked": untracked,
            "clean": (staged + changed + untracked) == 0,
            "last_commit": short_hash, "last_subject": last_subject,
            "last_at": last_at,
        }
