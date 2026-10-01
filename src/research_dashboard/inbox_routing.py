"""Conservative attribution of standalone activity inbox records."""

from datetime import datetime, timezone
import json
import re
import sqlite3
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from .db import transaction
from .events import ingest_event
from .registry import is_admitted_project, list_projects, list_project_roots


_COLUMNS = "id, title, note, candidate_project_ids_json, created_at, assigned_project_id"
_UUID = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{8}-){1}[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])", re.I)
_SHARED = re.compile(r"\b(?:cross[ -]project|shared work|multiple projects|no single project)\b", re.I)


def _record(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["candidate_project_ids"] = json.loads(result.pop("candidate_project_ids_json"))
    return result


def create_inbox_item(
    connection: sqlite3.Connection, *, title: str, note: str | None = None,
    candidate_project_ids: list[str] | None = None, inbox_id: UUID | str | None = None,
    created_at: str | None = None,
) -> dict:
    """Store ambiguous activity without creating a task or project event."""
    if not title.strip():
        raise ValueError("inbox title must be nonempty")
    candidates = sorted(set(candidate_project_ids or []))
    known = {project["project_id"] for project in list_projects(connection)}
    if not set(candidates) <= known:
        raise ValueError("candidate projects must be registered")
    item_id = str(UUID(str(inbox_id))) if inbox_id else str(uuid4())
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    if datetime.fromisoformat(timestamp).tzinfo is None:
        raise ValueError("created_at must include a timezone")
    with transaction(connection, immediate=True):
        row = connection.execute(f"SELECT {_COLUMNS} FROM activity_inbox WHERE id = ?", (item_id,)).fetchone()
        if row is not None:
            result = _record(row)
            if (result["title"], result["note"], result["candidate_project_ids"]) != (title, note, candidates) or (created_at is not None and result["created_at"] != timestamp):
                raise ValueError("inbox id already has a different payload")
            return result
        connection.execute(
            "INSERT INTO activity_inbox (id, title, note, candidate_project_ids_json, created_at, assigned_project_id) VALUES (?, ?, ?, ?, ?, NULL)",
            (item_id, title, note, json.dumps(candidates), timestamp),
        )
        return _record(connection.execute(f"SELECT {_COLUMNS} FROM activity_inbox WHERE id = ?", (item_id,)).fetchone())


def list_inbox_items(connection: sqlite3.Connection, *, include_assigned: bool = False) -> list[dict]:
    where = "" if include_assigned else "WHERE assigned_project_id IS NULL"
    return [_record(row) for row in connection.execute(f"SELECT {_COLUMNS} FROM activity_inbox {where} ORDER BY created_at, id")]


def _owner(item: dict, projects: dict, roots: list[tuple[str, str]], assigned: dict[str, str]) -> tuple[str | None, str]:
    content = item["title"] + "\n" + (item["note"] or "")
    if _SHARED.search(content):
        return None, "shared_work"
    named = set()
    for project_id, project in projects.items():
        if re.search(r"(?<![\w-])" + re.escape(project_id) + r"(?![\w-])", content, re.I):
            named.add(project_id)
        if re.search(r"\bproject\s*:\s*" + re.escape(project["name"]) + r"(?![\w-])", content, re.I):
            named.add(project_id)
    path_owners = set()
    matches = []
    for project_id, root in roots:
        matches.extend((match.start(), root, project_id) for match in re.finditer(r"(?<![\w./\\])" + re.escape(root) + r"(?=$|[/\\\s.,;:)`])", content))
    for start, root, project_id in matches:
        if not any(other_start == start and len(other_root) > len(root) and other_root.startswith(root.rstrip("/") + "/") for other_start, other_root, _ in matches):
            path_owners.add(project_id)
    references = {str(UUID(value)) for value in _UUID.findall(content)} - {item["id"]} - projects.keys()
    if references - assigned.keys():
        return None, "unassigned_inbox_reference"
    referenced = {assigned[value] for value in references if value in assigned}
    owners = named | path_owners | referenced
    if len(owners) > 1:
        return None, "multiple_project_references"
    if len(owners) == 1:
        return owners.pop(), "explicit_project_reference"
    return None, "no_definitive_owner"


def route_inbox_items(
    connection: sqlite3.Connection, *, dry_run: bool = False, inbox_id: str | None = None
) -> list[dict[str, str | None]]:
    """Import only uniquely attributable activity, retaining original payload and date."""
    projects = {project["project_id"]: project for project in list_projects(connection)}
    roots = [(project_id, root) for project_id in projects for root in list_project_roots(connection, project_id)]
    assigned = {row["id"]: row["assigned_project_id"] for row in connection.execute("SELECT id, assigned_project_id FROM activity_inbox WHERE assigned_project_id IS NOT NULL")}
    items = [item for item in list_inbox_items(connection) if inbox_id is None or item["id"] == inbox_id]
    receipts = []
    for item in items:
        project_id, reason = _owner(item, projects, roots, assigned)
        if project_id and not is_admitted_project(projects[project_id]):
            project_id, reason = None, "project_not_admitted"
        receipt = {"inbox_id": item["id"], "status": "would_route" if project_id and dry_run else "routed" if project_id else "pending", "project_id": project_id, "reason": reason}
        if project_id and not dry_run:
            with transaction(connection, immediate=True):
                current = connection.execute("SELECT assigned_project_id FROM activity_inbox WHERE id = ?", (item["id"],)).fetchone()
                if current["assigned_project_id"] is not None:
                    receipt["status"] = "already_assigned"
                else:
                    ingest_event(connection, {
                        "event_id": str(uuid5(NAMESPACE_URL, "research-dashboard:activity-inbox:" + item["id"])),
                        "project_id": project_id, "task_key": "inbox-import:" + item["id"],
                        "event_type": "inbox_import",
                        "importance": "Historical activity imported from Needs assignment",
                        "epistemic_status": "Observed", "context": json.dumps({
                            "source": "activity_inbox", "inbox_id": item["id"],
                            "title": item["title"], "note": item["note"],
                            "candidate_project_ids": item["candidate_project_ids"],
                            "created_at": item["created_at"],
                        }, ensure_ascii=False),
                        "what_changed": item["title"], "observed_at": item["created_at"],
                        "evidence": [{"evidence_type": "activity_inbox", "locator": "activity_inbox:" + item["id"], "authority": 0, "availability": "unknown"}],
                    })
                    connection.execute("UPDATE activity_inbox SET assigned_project_id = ? WHERE id = ? AND assigned_project_id IS NULL", (project_id, item["id"]))
        receipts.append(receipt)
        if project_id:
            assigned[item["id"]] = project_id
    return receipts
