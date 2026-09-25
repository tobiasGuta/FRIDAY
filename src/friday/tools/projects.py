"""Local, user-authorized project catalog. No model, Qt or process dependency.

Only direct child directories of explicitly selected roots are discovered.
Manual entries can be anywhere. Nothing scans the whole computer by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_ROOTS = 8
MAX_PROJECTS = 256
MAX_SCAN_ENTRIES = 1024
MAX_FILE_BYTES = 128 * 1024


class ProjectError(ValueError):
    """Safe, user-facing project catalog error."""


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    path: Path
    source: str


def default_project_path() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        root = os.environ.get("XDG_DATA_HOME")
        base = Path(root) if root else Path.home() / ".local" / "share"
    return base / "FRIDAY" / "projects.json"


def _directory(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    try:
        if not raw.is_dir():
            raise ProjectError("Choose an existing project folder.")
        return raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectError("Project folder is unavailable.") from exc


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _identity(path: Path) -> str:
    return hashlib.sha256(_key(path).encode("utf-8")).hexdigest()[:20]


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ProjectError("Project name must be 1–80 printable characters.")
    return name


def _safe_child(candidate: Path, root: Path) -> Path | None:
    try:
        # Do not discover symlinks or Windows junctions into unrelated locations.
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            return None
        resolved = candidate.resolve(strict=True)
        if resolved.parent != root or not resolved.is_dir():
            return None
        return resolved
    except (OSError, RuntimeError):
        return None


class ProjectCatalog:
    """Persistent manual entries, opt-in roots and hidden discoveries."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_project_path() if path is None else Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "roots": [], "manual": [], "hidden": []}
        try:
            if self.path.stat().st_size > MAX_FILE_BYTES:
                raise ProjectError("Project configuration is too large.")
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectError("Cannot read project configuration.") from exc
        if (
            not isinstance(data, dict) or data.get("version") != 1
            or any(not isinstance(data.get(k), list) for k in ("roots", "manual", "hidden"))
            or len(data["roots"]) > MAX_ROOTS
            or len(data["manual"]) > MAX_PROJECTS
            or len(data["hidden"]) > MAX_PROJECTS
            or any(not isinstance(p, str) for p in data["roots"] + data["hidden"])
            or any(
                not isinstance(p, dict)
                or not isinstance(p.get("path"), str)
                or not isinstance(p.get("name"), str)
                for p in data["manual"]
            )
        ):
            raise ProjectError("Unsupported or invalid project configuration.")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".projects-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = stream.name
                json.dump(data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ProjectError("Cannot save project configuration.") from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def roots(self) -> tuple[Path, ...]:
        return tuple(Path(raw) for raw in self._load()["roots"])

    def add_root(self, folder: str | Path) -> None:
        root = _directory(folder)
        if root == Path(root.anchor):
            raise ProjectError("Choose a projects folder, not an entire drive.")
        data = self._load()
        if _key(root) in {_key(Path(item)) for item in data["roots"]}:
            return
        if len(data["roots"]) >= MAX_ROOTS:
            raise ProjectError("Maximum of eight discovery folders reached.")
        data["roots"].append(str(root))
        self._save(data)

    def remove_root(self, folder: str | Path) -> None:
        data = self._load()
        target = _key(Path(folder))
        data["roots"] = [raw for raw in data["roots"] if _key(Path(raw)) != target]
        self._save(data)

    def add_project(self, folder: str | Path, name: str | None = None) -> Project:
        path = _directory(folder)
        label = _name(path.name if name is None else name)
        data = self._load()
        existing = next(
            (p for p in data["manual"] if _key(Path(p["path"])) == _key(path)), None
        )
        if existing is None:
            if len(data["manual"]) >= MAX_PROJECTS:
                raise ProjectError("Maximum manual project count reached.")
            data["manual"].append({"path": str(path), "name": label})
        else:
            existing["name"] = label
        data["hidden"] = [
            raw for raw in data["hidden"] if _key(Path(raw)) != _key(path)
        ]
        self._save(data)
        return Project(_identity(path), label, path, "manual")

    def remove_project(self, project_id: str) -> None:
        project = self.find(project_id)
        data = self._load()
        data["manual"] = [
            p for p in data["manual"]
            if _key(Path(p["path"])) != _key(project.path)
        ]
        if project.source == "discovered" or any(
            path == project.path for path in self._discovered_paths(data)
        ):
            if len(data["hidden"]) >= MAX_PROJECTS:
                raise ProjectError("Maximum hidden project count reached.")
            if _key(project.path) not in {_key(Path(x)) for x in data["hidden"]}:
                data["hidden"].append(str(project.path))
        self._save(data)

    def _discovered_paths(self, data: dict):
        for raw in data["roots"]:
            count = 0
            try:
                root = _directory(raw)
                with os.scandir(root) as entries:
                    for entry in entries:
                        count += 1
                        if count > MAX_SCAN_ENTRIES:
                            break
                        if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                            continue
                        path = _safe_child(Path(entry.path), root)
                        if path is not None:
                            yield path
            except (OSError, ProjectError):
                continue

    def projects(self) -> tuple[Project, ...]:
        data = self._load()
        by_path: dict[str, Project] = {}
        for record in data["manual"]:
            raw = Path(record["path"])
            try:
                path = _directory(raw)
                label = _name(record["name"])
            except ProjectError:
                continue  # A missing project is not silently launched elsewhere.
            by_path[_key(path)] = Project(_identity(path), label, path, "manual")
        hidden = {_key(Path(raw)) for raw in data["hidden"]}
        for path in self._discovered_paths(data):
            key = _key(path)
            if key not in by_path and key not in hidden and len(by_path) < MAX_PROJECTS:
                by_path[key] = Project(_identity(path), path.name, path, "discovered")
        return tuple(sorted(by_path.values(), key=lambda p: (p.name.casefold(), str(p.path))))

    def find(self, project_id: str) -> Project:
        for project in self.projects():
            if project.id == project_id:
                return project
        raise ProjectError("Project is no longer registered or available.")

    def match(self, query: str) -> Project:
        if not isinstance(query, str) or not 1 <= len(query) <= 80:
            raise ProjectError("Unknown project.")
        normalized = " ".join(query.casefold().split())
        matches = [
            p for p in self.projects()
            if p.id == query or " ".join(p.name.casefold().split()) == normalized
        ]
        if not matches:
            raise ProjectError("Unknown project; refresh or add it in Projects.")
        if len(matches) != 1:
            raise ProjectError("Several projects share that name; select one in Projects.")
        return matches[0]
