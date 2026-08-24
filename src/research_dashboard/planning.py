"""Editable project-planning persistence, separate from evidence-backed state."""

from datetime import date, datetime, timezone
from difflib import SequenceMatcher
import sqlite3
from typing import Any
from uuid import uuid4

from .db import transaction
from .domain import (
    PlanningActor,
    RoadmapItemInput,
    RoadmapStatus,
    TodoInput,
    TodoPriority,
    TodoStatus,
)


UNSET = object()


def _validate_connection(connection: sqlite3.Connection) -> None:
    """Validate the SQLite settings required by the planning API."""
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("planning connection must be a sqlite3.Connection")
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "planning connection must use sqlite3.Row as row_factory; "
            "use research_dashboard.db.connect_db()"
        )
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError(
            "planning connection must enable SQLite foreign keys; "
            "use research_dashboard.db.connect_db()"
        )


def _roadmap_item_input(
    value: RoadmapItemInput | dict[str, Any],
) -> RoadmapItemInput:
    if isinstance(value, RoadmapItemInput):
        return value
    return RoadmapItemInput.model_validate(value)


def _todo_input(value: TodoInput | dict[str, Any]) -> TodoInput:
    if isinstance(value, TodoInput):
        return value
    return TodoInput.model_validate(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_roadmap_item(
    connection: sqlite3.Connection,
    roadmap_item_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT roadmap_item_id, project_id, parent_item_id, title, status, note, "
        "position, source_plan_path, source_key, created_by, updated_by, "
        "created_at, updated_at, completed_at "
        "FROM roadmap_items WHERE roadmap_item_id = ?",
        (roadmap_item_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _roadmap_item_for_project(
    connection: sqlite3.Connection,
    project_id: str,
    roadmap_item_id: str,
) -> dict[str, Any] | None:
    item = _fetch_roadmap_item(connection, roadmap_item_id)
    if item is None or item["project_id"] != project_id:
        return None
    return item


def _fetch_todo(
    connection: sqlite3.Connection,
    todo_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT todo_id, project_id, roadmap_item_id, title, status, priority, "
        "due_date, note, position, created_by, updated_by, created_at, updated_at, "
        "completed_at FROM todos WHERE todo_id = ?",
        (todo_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _todo_for_project(
    connection: sqlite3.Connection,
    project_id: str,
    todo_id: str,
) -> dict[str, Any] | None:
    todo = _fetch_todo(connection, todo_id)
    if todo is None or todo["project_id"] != project_id:
        return None
    return todo


def _sibling_items(
    connection: sqlite3.Connection,
    project_id: str,
    parent_item_id: str | None,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT roadmap_item_id, project_id, parent_item_id, title, status, note, "
        "position, source_plan_path, source_key, created_by, updated_by, "
        "created_at, updated_at, completed_at "
        "FROM roadmap_items WHERE project_id = ? AND parent_item_id IS ? "
        "ORDER BY position, roadmap_item_id",
        (project_id, parent_item_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _next_sibling_position(
    connection: sqlite3.Connection,
    project_id: str,
    parent_item_id: str | None,
) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_position "
        "FROM roadmap_items WHERE project_id = ? AND parent_item_id IS ?",
        (project_id, parent_item_id),
    ).fetchone()
    return int(row["next_position"])


def _next_todo_position(
    connection: sqlite3.Connection,
    project_id: str,
    status: TodoStatus,
) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_position "
        "FROM todos WHERE project_id = ? AND status = ?",
        (project_id, status),
    ).fetchone()
    return int(row["next_position"])


def _todos_for_project(
    connection: sqlite3.Connection,
    project_id: str,
    status: TodoStatus | None = None,
) -> list[dict[str, Any]]:
    where = "WHERE project_id = ?"
    parameters: tuple[str, ...] = (project_id,)
    if status is not None:
        where += " AND status = ?"
        parameters += (status,)
    rows = connection.execute(
        "SELECT todo_id, project_id, roadmap_item_id, title, status, priority, "
        "due_date, note, position, created_by, updated_by, created_at, updated_at, "
        f"completed_at FROM todos {where} ORDER BY status, position, todo_id",
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def _validate_parent(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    roadmap_item_id: str | None,
    parent_item_id: str,
    item_has_children: bool = False,
) -> None:
    if parent_item_id == roadmap_item_id:
        raise ValueError("a roadmap item cannot be its own parent")

    parent = _roadmap_item_for_project(connection, project_id, parent_item_id)
    if parent is None:
        raise ValueError("roadmap parent must belong to the same project")

    ancestor = parent
    while ancestor["parent_item_id"] is not None:
        ancestor = _fetch_roadmap_item(connection, ancestor["parent_item_id"])
        if ancestor is None:
            break
        if ancestor["roadmap_item_id"] == roadmap_item_id:
            raise ValueError("roadmap hierarchy cannot contain cycles")

    if parent["parent_item_id"] is not None or item_has_children:
        raise ValueError("roadmap hierarchy is limited to two levels")


def create_roadmap_item(
    connection: sqlite3.Connection,
    value: RoadmapItemInput | dict[str, Any],
) -> dict[str, Any]:
    """Create one editable roadmap item, optionally below a top-level parent."""
    _validate_connection(connection)
    item = _roadmap_item_input(value)
    roadmap_item_id = item.roadmap_item_id or uuid4().hex
    now = _now()
    completed_at = now if item.status == "Done" else None

    with transaction(connection):
        if item.parent_item_id is not None:
            _validate_parent(
                connection,
                project_id=item.project_id,
                roadmap_item_id=roadmap_item_id,
                parent_item_id=item.parent_item_id,
            )
        position = (
            item.position
            if item.position is not None
            else _next_sibling_position(
                connection, item.project_id, item.parent_item_id
            )
        )
        connection.execute(
            "INSERT INTO roadmap_items ("
            "roadmap_item_id, project_id, parent_item_id, title, status, note, "
            "position, source_plan_path, source_key, created_by, updated_by, "
            "created_at, updated_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                roadmap_item_id,
                item.project_id,
                item.parent_item_id,
                item.title,
                item.status,
                item.note,
                position,
                item.source_plan_path,
                item.source_key,
                item.actor,
                item.actor,
                now,
                now,
                completed_at,
            ),
        )
        stored = _fetch_roadmap_item(connection, roadmap_item_id)

    assert stored is not None
    return stored


def update_roadmap_item(
    connection: sqlite3.Connection,
    roadmap_item_id: str,
    *,
    title: str | None = None,
    status: RoadmapStatus | None = None,
    note: str | None | object = UNSET,
    parent_item_id: str | None | object = UNSET,
    actor: PlanningActor = "user",
) -> dict[str, Any]:
    """Update editable fields while preserving the two-level roadmap hierarchy."""
    _validate_connection(connection)

    with transaction(connection):
        current = _fetch_roadmap_item(connection, roadmap_item_id)
        if current is None:
            raise ValueError("roadmap item does not exist")

        new_parent_item_id = current["parent_item_id"]
        position = current["position"]
        if parent_item_id is not UNSET:
            new_parent_item_id = parent_item_id
            if parent_item_id is not None:
                has_children = connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM roadmap_items "
                    "WHERE parent_item_id = ?) AS has_children",
                    (roadmap_item_id,),
                ).fetchone()["has_children"]
                _validate_parent(
                    connection,
                    project_id=current["project_id"],
                    roadmap_item_id=roadmap_item_id,
                    parent_item_id=parent_item_id,
                    item_has_children=bool(has_children),
                )
            if parent_item_id != current["parent_item_id"]:
                position = _next_sibling_position(
                    connection, current["project_id"], parent_item_id
                )

        completed_at = current["completed_at"]
        if status == "Done" and current["status"] != "Done":
            completed_at = _now()
        elif status is not None and status != "Done" and current["status"] == "Done":
            completed_at = None

        now = _now()
        connection.execute(
            "UPDATE roadmap_items SET title = ?, status = ?, note = ?, "
            "parent_item_id = ?, position = ?, updated_by = ?, updated_at = ?, "
            "completed_at = ? WHERE roadmap_item_id = ?",
            (
                current["title"] if title is None else title,
                current["status"] if status is None else status,
                current["note"] if note is UNSET else note,
                new_parent_item_id,
                position,
                actor,
                now,
                completed_at,
                roadmap_item_id,
            ),
        )
        stored = _fetch_roadmap_item(connection, roadmap_item_id)

    assert stored is not None
    return stored


def delete_roadmap_item(
    connection: sqlite3.Connection,
    project_id: str,
    roadmap_item_id: str,
) -> None:
    """Delete one childless roadmap item from a project."""
    _validate_connection(connection)

    with transaction(connection):
        item = _roadmap_item_for_project(connection, project_id, roadmap_item_id)
        if item is None:
            raise ValueError("roadmap item does not exist in the project")
        has_children = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM roadmap_items WHERE parent_item_id = ?) "
            "AS has_children",
            (roadmap_item_id,),
        ).fetchone()["has_children"]
        if has_children:
            raise ValueError("cannot delete a roadmap item with children")
        connection.execute(
            "DELETE FROM roadmap_items WHERE project_id = ? AND roadmap_item_id = ?",
            (project_id, roadmap_item_id),
        )


def reorder_roadmap_items(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    parent_item_id: str | None,
    ordered_item_ids: list[str],
    actor: PlanningActor = "user",
) -> list[dict[str, Any]]:
    """Replace one sibling group's order after validating its exact membership."""
    _validate_connection(connection)

    with transaction(connection):
        siblings = _sibling_items(connection, project_id, parent_item_id)
        sibling_ids = {item["roadmap_item_id"] for item in siblings}
        if (
            set(ordered_item_ids) != sibling_ids
            or len(ordered_item_ids) != len(sibling_ids)
        ):
            raise ValueError("ordered roadmap item IDs must exactly match the current siblings")

        now = _now()
        for position, roadmap_item_id in enumerate(ordered_item_ids):
            connection.execute(
                "UPDATE roadmap_items SET position = ?, updated_by = ?, "
                "updated_at = ? WHERE roadmap_item_id = ?",
                (position, actor, now, roadmap_item_id),
            )
        return _sibling_items(connection, project_id, parent_item_id)


def list_roadmap_items(
    connection: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return one project's editable roadmap items in stable sibling order."""
    _validate_connection(connection)
    rows = connection.execute(
        "SELECT roadmap_item_id, project_id, parent_item_id, title, status, note, "
        "position, source_plan_path, source_key, created_by, updated_by, "
        "created_at, updated_at, completed_at "
        "FROM roadmap_items WHERE project_id = ? "
        "ORDER BY parent_item_id IS NOT NULL, parent_item_id, position, roadmap_item_id",
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def roadmap_progress(items: list[dict[str, Any]]) -> dict[str, int] | None:
    """Summarize completed and total non-skipped leaf roadmap items."""
    if not items:
        return None

    parent_item_ids = {
        item["parent_item_id"]
        for item in items
        if item["parent_item_id"] is not None
    }
    leaves = [item for item in items if item["roadmap_item_id"] not in parent_item_ids]
    included = [item for item in leaves if item["status"] != "Skipped"]
    return {
        "completed": sum(item["status"] == "Done" for item in included),
        "total": len(included),
    }


def create_todo(
    connection: sqlite3.Connection,
    value: TodoInput | dict[str, Any],
) -> dict[str, Any]:
    """Create one editable TODO, optionally attached to this project's roadmap."""
    _validate_connection(connection)
    todo = _todo_input(value)
    todo_id = todo.todo_id or uuid4().hex
    now = _now()
    completed_at = now if todo.status == "Done" else None
    due_date = todo.due_date.isoformat() if todo.due_date is not None else None

    with transaction(connection):
        if (
            todo.roadmap_item_id is not None
            and _roadmap_item_for_project(
                connection, todo.project_id, todo.roadmap_item_id
            )
            is None
        ):
            raise ValueError("todo roadmap attachment must belong to the same project")
        position = (
            todo.position
            if todo.position is not None
            else _next_todo_position(connection, todo.project_id, todo.status)
        )
        connection.execute(
            "INSERT INTO todos ("
            "todo_id, project_id, roadmap_item_id, title, status, priority, due_date, "
            "note, position, created_by, updated_by, created_at, updated_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                todo_id,
                todo.project_id,
                todo.roadmap_item_id,
                todo.title,
                todo.status,
                todo.priority,
                due_date,
                todo.note,
                position,
                todo.actor,
                todo.actor,
                now,
                now,
                completed_at,
            ),
        )
        stored = _fetch_todo(connection, todo_id)

    assert stored is not None
    return stored


def update_todo(
    connection: sqlite3.Connection,
    todo_id: str,
    *,
    title: str | None = None,
    status: TodoStatus | None = None,
    priority: TodoPriority | None = None,
    due_date: date | None | object = UNSET,
    note: str | None | object = UNSET,
    roadmap_item_id: str | None | object = UNSET,
    actor: PlanningActor = "user",
) -> dict[str, Any]:
    """Update editable TODO fields while preserving project-local attachments."""
    _validate_connection(connection)

    with transaction(connection):
        current = _fetch_todo(connection, todo_id)
        if current is None:
            raise ValueError("todo does not exist")

        new_status = current["status"] if status is None else status
        new_roadmap_item_id = current["roadmap_item_id"]
        if roadmap_item_id is not UNSET:
            new_roadmap_item_id = roadmap_item_id
            if (
                roadmap_item_id is not None
                and _roadmap_item_for_project(
                    connection, current["project_id"], roadmap_item_id
                )
                is None
            ):
                raise ValueError(
                    "todo roadmap attachment must belong to the same project"
                )

        new_due_date = current["due_date"]
        if due_date is not UNSET:
            new_due_date = due_date.isoformat() if due_date is not None else None

        position = current["position"]
        if new_status != current["status"]:
            position = _next_todo_position(
                connection, current["project_id"], new_status
            )

        completed_at = current["completed_at"]
        if new_status == "Done" and current["status"] != "Done":
            completed_at = _now()
        elif new_status != "Done" and current["status"] == "Done":
            completed_at = None

        now = _now()
        connection.execute(
            "UPDATE todos SET title = ?, status = ?, priority = ?, due_date = ?, "
            "note = ?, roadmap_item_id = ?, position = ?, updated_by = ?, "
            "updated_at = ?, completed_at = ? WHERE todo_id = ?",
            (
                current["title"] if title is None else title,
                new_status,
                current["priority"] if priority is None else priority,
                new_due_date,
                current["note"] if note is UNSET else note,
                new_roadmap_item_id,
                position,
                actor,
                now,
                completed_at,
                todo_id,
            ),
        )
        stored = _fetch_todo(connection, todo_id)

    assert stored is not None
    return stored


def delete_todo(
    connection: sqlite3.Connection,
    project_id: str,
    todo_id: str,
) -> None:
    """Delete one TODO only when it belongs to the specified project."""
    _validate_connection(connection)

    with transaction(connection):
        if _todo_for_project(connection, project_id, todo_id) is None:
            raise ValueError("todo does not exist in the project")
        connection.execute(
            "DELETE FROM todos WHERE project_id = ? AND todo_id = ?",
            (project_id, todo_id),
        )


def reorder_todos(
    connection: sqlite3.Connection,
    project_id: str,
    ordered_todo_ids: list[str],
    *,
    status: TodoStatus = "Open",
    actor: PlanningActor = "user",
) -> list[dict[str, Any]]:
    """Replace one project's per-status TODO order after exact validation."""
    _validate_connection(connection)

    with transaction(connection):
        todos = _todos_for_project(connection, project_id, status)
        todo_ids = {todo["todo_id"] for todo in todos}
        if (
            set(ordered_todo_ids) != todo_ids
            or len(ordered_todo_ids) != len(todo_ids)
        ):
            raise ValueError("ordered TODO IDs must exactly match the current status")

        now = _now()
        for position, todo_id in enumerate(ordered_todo_ids):
            connection.execute(
                "UPDATE todos SET position = ?, updated_by = ?, updated_at = ? "
                "WHERE todo_id = ?",
                (position, actor, now, todo_id),
            )
        return _todos_for_project(connection, project_id, status)


def list_todos(
    connection: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return one project's TODOs in their stored status/display order."""
    _validate_connection(connection)
    return _todos_for_project(connection, project_id)


def _normalized_title(title: str) -> str:
    return " ".join(title.lower().split())


def suggest_todo_attachment(
    todo: dict[str, Any],
    roadmap_items: list[dict[str, Any]],
    *,
    cutoff: float = 0.60,
) -> dict[str, Any] | None:
    """Return the best eligible title match without changing the TODO attachment."""
    todo_title = _normalized_title(todo["title"])
    candidates = [
        item
        for item in roadmap_items
        if item["status"] not in {"Done", "Skipped"}
    ]
    if not candidates:
        return None

    suggested = max(
        candidates,
        key=lambda item: SequenceMatcher(
            None, todo_title, _normalized_title(item["title"])
        ).ratio(),
    )
    score = SequenceMatcher(
        None, todo_title, _normalized_title(suggested["title"])
    ).ratio()
    return suggested if score >= cutoff else None


def planning_summary(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any]:
    """Return the derived planning view without persisting attachment suggestions."""
    roadmap = list_roadmap_items(connection, project_id)
    todos = list_todos(connection, project_id)
    open_todos: list[dict[str, Any]] = []
    for todo in todos:
        if todo["status"] != "Open":
            continue
        view_todo = dict(todo)
        if todo["roadmap_item_id"] is None:
            view_todo["suggested_roadmap_item"] = suggest_todo_attachment(
                todo, roadmap
            )
        open_todos.append(view_todo)
    completed_todos = sorted(
        (todo for todo in todos if todo["status"] == "Done"),
        key=lambda todo: todo["completed_at"] or "",
        reverse=True,
    )
    return {
        "roadmap": roadmap,
        "roadmap_progress": roadmap_progress(roadmap),
        "open_todos": open_todos,
        "completed_todos": completed_todos,
        "open_todo_count": len(open_todos),
    }
