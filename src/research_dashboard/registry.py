"""SQLite-backed project and plan registry.

Registry functions require connections from :func:`research_dashboard.db.connect_db`:
they must use ``sqlite3.Row`` and have SQLite foreign-key enforcement enabled.
"""

from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .db import transaction
from .domain import ProjectInput


CONTEXT_COVERAGE_INCOMPLETE = "Context coverage incomplete"


def _validate_connection(connection: sqlite3.Connection) -> None:
    """Validate the SQLite settings required by the registry API."""
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("registry connection must be a sqlite3.Connection")
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "registry connection must use sqlite3.Row as row_factory; "
            "use research_dashboard.db.connect_db()"
        )
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError(
            "registry connection must enable SQLite foreign keys; "
            "use research_dashboard.db.connect_db()"
        )


def _normalize_path(path: str | Path) -> str:
    """Return an absolute, normalized path without requiring it to exist."""
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return str(Path(path).expanduser().absolute())


def _project_input(project: ProjectInput | dict[str, Any]) -> ProjectInput:
    if isinstance(project, ProjectInput):
        return project
    return ProjectInput.model_validate(project)


def _context_status(context_path: str | None) -> str:
    if context_path and Path(context_path).is_file():
        return "available"
    return CONTEXT_COVERAGE_INCOMPLETE


def _project_record(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    record["context_status"] = _context_status(record["context_path"])
    return record


def _fetch_project(
    connection: sqlite3.Connection, project_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT project_id, name, domain, context_path, lifecycle, "
        "update_horizon_minutes, created_at, updated_at "
        "FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return _project_record(row)


def add_project(
    connection: sqlite3.Connection,
    project: ProjectInput | dict[str, Any],
) -> dict[str, Any]:
    """Add a validated project to the authoritative SQLite registry.

    ``connection`` must be configured by :func:`connect_db` or equivalently
    use ``sqlite3.Row`` with foreign-key enforcement enabled.
    """
    _validate_connection(connection)
    value = _project_input(project)
    context_path = (
        _normalize_path(value.context_path) if value.context_path else None
    )

    with transaction(connection):
        connection.execute(
            "INSERT INTO projects ("
            "project_id, name, domain, context_path, lifecycle, "
            "update_horizon_minutes, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                value.project_id,
                value.name,
                value.domain,
                context_path,
                value.lifecycle,
                value.update_horizon_minutes,
                value.created_at.isoformat(),
                value.updated_at.isoformat(),
            ),
        )
        record = _fetch_project(connection, value.project_id)

    assert record is not None
    return record


def add_project_root(
    connection: sqlite3.Connection,
    project_id: str,
    root_path: str | Path,
) -> dict[str, str]:
    """Register a normalized root for an existing project.

    A normalized root may belong to only one project. Nested roots remain
    valid when they are registered to different projects.
    """
    _validate_connection(connection)
    normalized_root = _normalize_path(root_path)
    with transaction(connection):
        existing = connection.execute(
            "SELECT project_id FROM project_roots WHERE root_path = ?",
            (normalized_root,),
        ).fetchone()
        if existing is not None and existing["project_id"] != project_id:
            raise ValueError(
                f"root path {normalized_root!r} is already registered "
                f"to project {existing['project_id']!r}"
            )
        connection.execute(
            "INSERT INTO project_roots (project_id, root_path) VALUES (?, ?)",
            (project_id, normalized_root),
        )
    return {"project_id": project_id, "root_path": normalized_root}


def resolve_project_for_path(
    connection: sqlite3.Connection,
    path: str | Path,
) -> dict[str, Any] | None:
    """Resolve a path using only explicitly registered roots."""
    _validate_connection(connection)
    normalized_path = Path(_normalize_path(path))
    roots = connection.execute(
        "SELECT project_id, root_path FROM project_roots ORDER BY root_path"
    ).fetchall()

    matches: list[tuple[int, str, str]] = []
    for root in roots:
        root_path = Path(root["root_path"])
        try:
            normalized_path.relative_to(root_path)
        except ValueError:
            continue
        matches.append((len(root_path.parts), root["root_path"], root["project_id"]))

    if not matches:
        return None

    _, _, project_id = max(matches)
    return _fetch_project(connection, project_id)


def set_governing_plan(
    connection: sqlite3.Connection,
    project_id: str,
    path: str | Path,
    workstream: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Set one active governing plan for a project and workstream."""
    _validate_connection(connection)
    normalized_path = _normalize_path(path)
    plan_id = plan_id or uuid4().hex

    with transaction(connection):
        if workstream is None:
            connection.execute(
                "UPDATE governing_plans SET active = 0 "
                "WHERE project_id = ? AND workstream IS NULL",
                (project_id,),
            )
        else:
            connection.execute(
                "UPDATE governing_plans SET active = 0 "
                "WHERE project_id = ? AND workstream = ?",
                (project_id, workstream),
            )
        connection.execute(
            "INSERT INTO governing_plans "
            "(plan_id, project_id, workstream, path, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (plan_id, project_id, workstream, normalized_path),
        )
        row = connection.execute(
            "SELECT plan_id, project_id, workstream, path, active "
            "FROM governing_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()

    assert row is not None
    return dict(row)


def get_active_governing_plan(
    connection: sqlite3.Connection,
    project_id: str,
    workstream: str | None = None,
) -> dict[str, Any] | None:
    """Return the active governing plan for a project/workstream."""
    _validate_connection(connection)
    if workstream is None:
        row = connection.execute(
            "SELECT plan_id, project_id, workstream, path, active "
            "FROM governing_plans "
            "WHERE project_id = ? AND workstream IS NULL AND active = 1",
            (project_id,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT plan_id, project_id, workstream, path, active "
            "FROM governing_plans "
            "WHERE project_id = ? AND workstream = ? AND active = 1",
            (project_id, workstream),
        ).fetchone()
    return dict(row) if row is not None else None


def get_project(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any] | None:
    """Return a registered project, or ``None`` when it is not registered."""
    _validate_connection(connection)
    return _fetch_project(connection, project_id)


def list_projects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all registered projects in stable ID order."""
    _validate_connection(connection)
    rows = connection.execute(
        "SELECT project_id, name, domain, context_path, lifecycle, "
        "update_horizon_minutes, created_at, updated_at "
        "FROM projects ORDER BY project_id"
    ).fetchall()
    return [_project_record(row) for row in rows]


def list_project_roots(
    connection: sqlite3.Connection,
    project_id: str,
) -> list[str]:
    _validate_connection(connection)
    rows = connection.execute(
        "SELECT root_path FROM project_roots WHERE project_id = ? ORDER BY root_path",
        (project_id,),
    ).fetchall()
    return [row["root_path"] for row in rows]
