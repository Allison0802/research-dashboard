"""Explicit, bounded discovery and admission of independently marked projects."""

import os
from pathlib import Path
import re
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from .db import transaction
from .registry import (
    ADMITTED_LIFECYCLES,
    add_project,
    add_project_root,
    get_project,
    list_projects,
    list_project_roots,
    set_project_lifecycle,
)


_EXCLUDED_PARTS = frozenset({
    "archive", "archives", "closedarchives", "closed", "retired", "completed",
    "data", "output", "outputs", "results", "figures", "tables", "scripts",
    "docs", "tests", "backups", "src", "node_modules", ".git", ".worktrees",
    "infrastructure", "tools", "worktrees", "venv", "project_reports",
})
_MARKERS = ("CONTEXT.md", "PROJECT_LOG.md", "CHANGES.md")


def _marked(folder: Path) -> bool:
    return any((folder / name).is_file() for name in _MARKERS) or (
        any(path.is_file() and "plan" in path.stem.casefold() for path in folder.glob("*.md"))
        or any((folder / "project_reports" / "plans").glob("*.md"))
    ) or (
        (folder / "AGENTS.md").is_file()
        and (folder / "project_reports").is_dir()
    )


def _eligible(folder: Path) -> bool:
    return folder.is_dir() and not folder.is_symlink() and not any(
        (folder / marker).exists() for marker in ("RETIRED.md", "CLOSED.md", "ARCHIVED.md")
    ) and not (
        folder.name.startswith(".") or folder.name.casefold() in _EXCLUDED_PARTS
        or re.search(r"(?:^|[-_ ])(?:closed|archived|retired)(?:$|[-_ 0-9])", folder.name.casefold())
    )


def _candidate(folder: Path, domain: str) -> dict[str, str | None]:
    context = folder / "CONTEXT.md"
    return {
        "project_id": str(uuid5(NAMESPACE_URL, "research-dashboard:project:" + str(folder))),
        "name": folder.name.replace("_", " "),
        "domain": domain,
        "root_path": str(folder),
        "context_path": str(context) if context.is_file() else None,
    }


def discover_projects(root: str | Path, domain: str) -> list[dict[str, str | None]]:
    """Inspect only an explicitly supplied directory, at most four levels deep."""
    if not domain.strip():
        raise ValueError("domain must be nonempty")
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("discovery root is not a directory")
    found = []
    for base, names, _ in os.walk(path, followlinks=False):
        folder = Path(base)
        depth = len(folder.relative_to(path).parts)
        names[:] = sorted(name for name in names if depth < 4 and _eligible(folder / name))
        if _marked(folder) and _eligible(folder):
            found.append(_candidate(folder, domain.strip()))
    return found


def sync_projects(
    connection: sqlite3.Connection,
    roots: list[tuple[str | Path, str]],
    *,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Record discovered roots as hidden candidates; never admit them implicitly."""
    receipts = []
    seen = set()
    for root, domain in roots:
        for candidate in discover_projects(root, domain):
            path = candidate["root_path"]
            if path in seen:
                continue
            seen.add(path)
            owner = connection.execute(
                "SELECT project_id FROM project_roots WHERE root_path = ?", (path,)
            ).fetchone()
            if owner:
                receipts.append({"status": "existing", "project_id": owner["project_id"], "root_path": path})
                continue
            project_id = candidate["project_id"]
            if get_project(connection, project_id):
                receipts.append({"status": "needs_assignment", "project_id": project_id, "root_path": path})
                continue
            if not dry_run:
                with transaction(connection, immediate=True):
                    add_project(connection, {key: candidate[key] for key in ("project_id", "name", "domain", "context_path")})
                    add_project_root(connection, project_id, path)
            receipts.append({"status": "would_add" if dry_run else "candidate", "project_id": project_id, "root_path": path})
    return receipts


def admit_project(
    connection: sqlite3.Connection, *, path: str, domain: str, reason: str,
    name: str | None = None, lifecycle: str = "Active"
) -> dict:
    """Admit one explicitly marked root, preserving any prior root identity."""
    if not Path(path).is_absolute() or not domain.strip() or not reason.strip() or lifecycle not in ADMITTED_LIFECYCLES:
        raise ValueError("admission requires an absolute path, domain, reason, and ongoing lifecycle")
    folder = Path(path).resolve()
    if not _eligible(folder) or not _marked(folder):
        raise ValueError("admission requires an independently marked project root")
    with transaction(connection, immediate=True):
        owner = connection.execute("SELECT project_id FROM project_roots WHERE root_path = ?", (str(folder),)).fetchone()
        project_id = owner["project_id"] if owner else _candidate(folder, domain)["project_id"]
        existing = get_project(connection, project_id)
        if existing:
            if existing["lifecycle"] in {"Completed", "Archived"}:
                raise ValueError("completed or archived projects require an explicit reopening decision")
            if existing["domain"] != domain.strip():
                raise ValueError("domain differs from existing project")
            if not owner:
                raise ValueError("project ID belongs to another root")
            set_project_lifecycle(connection, project_id, lifecycle)
        else:
            candidate = _candidate(folder, domain.strip())
            add_project(connection, {
                "project_id": project_id, "name": name or candidate["name"],
                "domain": domain.strip(), "context_path": candidate["context_path"],
                "lifecycle": lifecycle,
            })
            add_project_root(connection, project_id, folder)
        row = connection.execute("SELECT description FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        note = "Dashboard admission: " + reason.strip()
        description = row["description"] or ""
        if note not in description.splitlines():
            connection.execute("UPDATE projects SET description = ? WHERE project_id = ?", ((description + "\n" + note).strip(), project_id))
    return {**get_project(connection, project_id), "roots": list_project_roots(connection, project_id), "admission_reason": reason.strip()}
