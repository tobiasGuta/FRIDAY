"""Windows-only, allowlisted project launcher. Never executes model-supplied shell text."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from friday.tools.projects import ProjectCatalog, ProjectError

Application = Literal["vscode", "terminal"]
LaunchTarget = Literal["vscode", "terminal", "workspace"]


def _installed_executable(application: Application) -> Path:
    """Resolve only an explicit Code.exe or wt.exe, not an arbitrary shell alias."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates: list[Path] = []
    if application == "vscode":
        for base in (
            local,
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
        ):
            if base:
                candidates.append(Path(base) / (
                    "Programs/Microsoft VS Code/Code.exe"
                    if base == local else "Microsoft VS Code/Code.exe"
                ))
        search_name = "Code.exe"
    elif application == "terminal":
        if local:
            candidates.append(Path(local) / "Microsoft/WindowsApps/wt.exe")
        search_name = "wt.exe"
    else:
        raise ProjectError("Unsupported application.")
    match = shutil.which(search_name)
    if match:
        candidates.append(Path(match))
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.casefold() == ".exe":
            return candidate
    label = "Visual Studio Code" if application == "vscode" else "Windows Terminal"
    raise ProjectError(f"{label} is not installed or its launcher is unavailable.")


class ProjectLauncher:
    """Process creation is available only after app-owned user approval."""

    def __init__(
        self,
        catalog: ProjectCatalog,
        *,
        platform_name: str | None = None,
        resolver: Callable[[Application], Path] = _installed_executable,
        spawn: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.catalog = catalog
        self.platform_name = sys.platform if platform_name is None else platform_name
        self.resolver = resolver
        self.spawn = spawn

    def launch(self, project_id: str, application: LaunchTarget) -> dict[str, str]:
        """One confirmed request; workspace preflights both known executables.

        A successful spawn confirms a process-start request only, not readiness.
        If Terminal fails after Code starts, report that partial result rather
        than pretending the workspace was opened or retrying automatically.
        """
        if self.platform_name != "win32":
            raise ProjectError("Project launching is currently supported on Windows only.")
        if application not in ("vscode", "terminal", "workspace"):
            raise ProjectError("Unsupported application.")
        # Fresh registered-project lookup; neither voice nor UI supplies a path.
        project = self.catalog.find(project_id)
        targets: tuple[Application, ...] = (
            ("vscode", "terminal") if application == "workspace" else (application,)
        )
        # Preflight BOTH applications before attempting either process. An
        # unavailable Terminal should not unexpectedly leave only Code open.
        executables: dict[Application, Path] = {}
        for target in targets:
            executable = self.resolver(target)
            if not executable.is_file() or executable.suffix.casefold() != ".exe":
                raise ProjectError("The selected application launcher is unavailable.")
            executables[target] = executable

        opened: list[Application] = []
        for target in targets:
            executable = executables[target]
            argv = (
                [str(executable), "--new-window", str(project.path)]
                if target == "vscode" else
                [str(executable), "-w", "0", "new-tab", "-d", str(project.path)]
            )
            try:
                self.spawn(argv, cwd=str(project.path), shell=False, close_fds=True)
            except OSError as exc:
                if not opened:
                    raise ProjectError(
                        "Windows could not start the selected application."
                    ) from exc
                return {
                    "status": "partial", "project": project.name,
                    "application": application,
                    "opened": ",".join(opened), "failed": target,
                }
            opened.append(target)
        return {
            "status": "launched", "project": project.name,
            "application": application,
        }
