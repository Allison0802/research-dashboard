"""Controlled import and review of governing-plan roadmap proposals."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from .db import transaction
from .domain import (
    PlanningActor,
    RoadmapProposalAdd,
    RoadmapProposalBatchInput,
    RoadmapProposalOperation,
    RoadmapProposalRemove,
    RoadmapProposalReorder,
)
from .planning import (
    create_roadmap_item,
    delete_roadmap_item,
    list_roadmap_items,
    reorder_roadmap_items,
)
from .registry import get_active_governing_plan


_TASK_HEADING = re.compile(r"^### Task \d+:\s+(.+)$")
_CHECKBOX = re.compile(r"^\s*[-*+] \[([ xX])\]\s+")
_TOP_LEVEL_CHECKBOX = re.compile(r"^- \[([ xX])\]\s+(.+)$")
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,}).*$")
_PROPOSAL_LIST = TypeAdapter(list[RoadmapProposalOperation])


def _validate_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("plan-sync connection must be a sqlite3.Connection")
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "plan-sync connection must use sqlite3.Row as row_factory; "
            "use research_dashboard.db.connect_db()"
        )
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError(
            "plan-sync connection must enable SQLite foreign keys; "
            "use research_dashboard.db.connect_db()"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: str | Path) -> str | None:
    try:
        return sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _plan_snapshot(path: str | Path) -> tuple[str, str] | None:
    try:
        content = Path(path).read_bytes()
    except OSError:
        return None
    return content.decode("utf-8"), sha256(content).hexdigest()


def _outside_fenced_code(lines: list[str]) -> list[str]:
    visible: list[str] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        if fence is not None:
            character, length = fence
            if re.match(rf"^ {{0,3}}{re.escape(character)}{{{length},}}\s*$", line):
                fence = None
            continue

        opening = _FENCE_OPEN.match(line)
        if opening is not None:
            marker = opening.group(1)
            fence = (marker[0], len(marker))
            continue
        visible.append(line)
    return visible


def _status_from_checkboxes(lines: list[str]) -> str:
    marks = [
        match.group(1).casefold() == "x"
        for line in lines
        if (match := _CHECKBOX.match(line))
    ]
    if marks and all(marks):
        return "Done"
    if any(marks):
        return "In progress"
    return "Not started"


def _parse_structured_plan_text(content: str) -> list[dict[str, str]] | None:
    lines = _outside_fenced_code(content.splitlines())

    headings = [
        (index, match.group(1).strip())
        for index, line in enumerate(lines)
        if (match := _TASK_HEADING.match(line)) is not None
    ]
    if headings:
        parsed: list[dict[str, str]] = []
        for position, (start, title) in enumerate(headings):
            end = (
                headings[position + 1][0]
                if position + 1 < len(headings)
                else len(lines)
            )
            parsed.append(
                {
                    "title": title,
                    "status": _status_from_checkboxes(lines[start + 1 : end]),
                }
            )
        return parsed

    checklist_items = [
        {
            "title": match.group(2).strip(),
            "status": (
                "Done" if match.group(1).casefold() == "x" else "Not started"
            ),
        }
        for line in lines
        if (match := _TOP_LEVEL_CHECKBOX.match(line)) is not None
    ]
    return checklist_items or None


def parse_structured_plan(path: str | Path) -> list[dict[str, str]] | None:
    """Parse one explicit roadmap task per heading or top-level checklist row."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_structured_plan_text(content)


def _normalized_source_key(title: str, ordinal: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return f"{normalized or 'roadmap-item'}-{ordinal}"


def _sync_state(
    connection: sqlite3.Connection, project_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT project_id, plan_path, acknowledged_sha256, updated_at "
        "FROM roadmap_sync_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _acknowledge_plan(
    connection: sqlite3.Connection,
    project_id: str,
    plan_path: str,
    plan_sha256: str,
) -> None:
    connection.execute(
        "INSERT INTO roadmap_sync_state "
        "(project_id, plan_path, acknowledged_sha256, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET plan_path = excluded.plan_path, "
        "acknowledged_sha256 = excluded.acknowledged_sha256, "
        "updated_at = excluded.updated_at",
        (project_id, plan_path, plan_sha256, _now()),
    )


def _batch_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "batch_id": row["batch_id"],
        "project_id": row["project_id"],
        "source_plan_path": row["source_plan_path"],
        "source_plan_sha256": row["source_plan_sha256"],
        "status": row["status"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
    }


def _get_batch(connection: sqlite3.Connection, batch_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT batch_id, project_id, source_plan_path, source_plan_sha256, "
        "proposals_json, status, created_by, created_at, reviewed_at "
        "FROM roadmap_proposal_batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()


def _deserialize_proposals(proposals_json: str) -> list[RoadmapProposalOperation]:
    return _PROPOSAL_LIST.validate_json(proposals_json)


def _pending_batch_rows_for_version(
    connection: sqlite3.Connection,
    project_id: str,
    plan_path: str,
    plan_sha256: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT batch_id, project_id, source_plan_path, source_plan_sha256, "
        "proposals_json, status, created_by, created_at, reviewed_at "
        "FROM roadmap_proposal_batches "
        "WHERE project_id = ? AND source_plan_path = ? "
        "AND source_plan_sha256 = ? AND status = 'Pending' "
        "ORDER BY created_at, batch_id",
        (project_id, plan_path, plan_sha256),
    ).fetchall()


def _pending_proposal_count(
    connection: sqlite3.Connection,
    project_id: str,
    plan_path: str,
    plan_sha256: str,
) -> int:
    rows = _pending_batch_rows_for_version(
        connection, project_id, plan_path, plan_sha256
    )
    return sum(len(_deserialize_proposals(row["proposals_json"])) for row in rows)


def _ensure_batch_source_is_current(
    connection: sqlite3.Connection, batch: sqlite3.Row
) -> str:
    plan = get_active_governing_plan(connection, batch["project_id"], None)
    if plan is None or plan["path"] != batch["source_plan_path"]:
        raise ValueError("proposal batch no longer matches the current plan version")
    current_sha256 = _file_sha256(plan["path"])
    if current_sha256 is None or current_sha256 != batch["source_plan_sha256"]:
        raise ValueError("proposal batch no longer matches the current plan version")
    return current_sha256


def plan_sync_status(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    """Return the governing-plan version state without mutating roadmap rows."""
    _validate_connection(connection)
    plan = get_active_governing_plan(connection, project_id, None)
    if plan is None:
        return {
            "status": "uninitialized",
            "plan_path": None,
            "current_sha256": None,
            "acknowledged_sha256": None,
            "pending_proposal_count": 0,
        }

    plan_path = plan["path"]
    current_sha256 = _file_sha256(plan_path)
    state = _sync_state(connection, project_id)
    acknowledged_sha256 = (
        state["acknowledged_sha256"]
        if state is not None and state["plan_path"] == plan_path
        else None
    )
    pending_count = (
        0
        if current_sha256 is None or acknowledged_sha256 == current_sha256
        else _pending_proposal_count(
            connection, project_id, plan_path, current_sha256
        )
    )
    if current_sha256 is None:
        status = "unavailable"
    elif acknowledged_sha256 == current_sha256:
        status = "current"
    elif parse_structured_plan(plan_path) is None:
        status = "ambiguous"
    else:
        status = "changed"
    return {
        "status": status,
        "plan_path": plan_path,
        "current_sha256": current_sha256,
        "acknowledged_sha256": acknowledged_sha256,
        "pending_proposal_count": pending_count,
    }


def bootstrap_roadmap_from_governing_plan(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    actor: PlanningActor = "agent",
) -> dict[str, Any]:
    """Initialize only an empty roadmap from the active project-level plan."""
    _validate_connection(connection)
    with transaction(connection, immediate=True):
        plan = get_active_governing_plan(connection, project_id, None)
        if plan is None:
            return {
                "status": "uninitialized",
                "plan_path": None,
                "imported_count": 0,
            }

        plan_path = plan["path"]
        snapshot = _plan_snapshot(plan_path)
        if snapshot is None:
            return {
                "status": "unavailable",
                "plan_path": plan_path,
                "imported_count": 0,
            }
        content, current_sha256 = snapshot
        parsed = _parse_structured_plan_text(content)
        if parsed is None:
            return {
                "status": "ambiguous",
                "plan_path": plan_path,
                "imported_count": 0,
            }
        if list_roadmap_items(connection, project_id):
            state = _sync_state(connection, project_id)
            status = (
                "current"
                if state is not None
                and state["plan_path"] == plan_path
                and state["acknowledged_sha256"] == current_sha256
                else "changed"
            )
            return {
                "status": status,
                "plan_path": plan_path,
                "imported_count": 0,
            }

        for ordinal, item in enumerate(parsed, start=1):
            create_roadmap_item(
                connection,
                {
                    "project_id": project_id,
                    "title": item["title"],
                    "status": item["status"],
                    "source_plan_path": plan_path,
                    "source_key": _normalized_source_key(item["title"], ordinal),
                    "actor": actor,
                },
            )
        _acknowledge_plan(connection, project_id, plan_path, current_sha256)
        return {
            "status": "current",
            "plan_path": plan_path,
            "imported_count": len(parsed),
        }


def _proposal_batch_input(
    value: RoadmapProposalBatchInput | dict[str, Any],
) -> RoadmapProposalBatchInput:
    if isinstance(value, RoadmapProposalBatchInput):
        return value
    return RoadmapProposalBatchInput.model_validate(value)


def create_proposal_batch(
    connection: sqlite3.Connection,
    value: RoadmapProposalBatchInput | dict[str, Any],
) -> dict[str, Any]:
    """Store one agent-authored, reviewable change proposal for the active plan."""
    _validate_connection(connection)
    batch = _proposal_batch_input(value)
    batch_id = uuid4().hex
    created_at = _now()
    proposals_json = json.dumps(
        [proposal.model_dump(mode="json") for proposal in batch.proposals],
        separators=(",", ":"),
        sort_keys=True,
    )

    with transaction(connection, immediate=True):
        plan = get_active_governing_plan(connection, batch.project_id, None)
        if plan is None or plan["path"] != batch.source_plan_path:
            raise ValueError("proposal source plan must be the active registered plan")
        current_sha256 = _file_sha256(plan["path"])
        if current_sha256 is None:
            raise ValueError("proposal source plan is unavailable")
        if current_sha256 != batch.source_plan_sha256:
            raise ValueError(
                "proposal source plan hash must match the current plan hash"
            )

        state = _sync_state(connection, batch.project_id)
        if (
            state is not None
            and state["plan_path"] == batch.source_plan_path
            and state["acknowledged_sha256"] == batch.source_plan_sha256
        ):
            raise ValueError("current plan version is already acknowledged")
        if _pending_batch_rows_for_version(
            connection,
            batch.project_id,
            batch.source_plan_path,
            batch.source_plan_sha256,
        ):
            raise ValueError("current plan version already has a pending proposal")

        connection.execute(
            "INSERT INTO roadmap_proposal_batches "
            "(batch_id, project_id, source_plan_path, source_plan_sha256, "
            "proposals_json, status, created_by, created_at, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, 'Pending', 'agent', ?, NULL)",
            (
                batch_id,
                batch.project_id,
                batch.source_plan_path,
                batch.source_plan_sha256,
                proposals_json,
                created_at,
            ),
        )
        stored = _get_batch(connection, batch_id)
    assert stored is not None
    return _batch_record(stored)


def list_pending_proposal_batches(
    connection: sqlite3.Connection, project_id: str
) -> list[dict[str, Any]]:
    """Return only reviewable batches for the active, unacknowledged plan version."""
    _validate_connection(connection)
    plan = get_active_governing_plan(connection, project_id, None)
    if plan is None:
        return []
    current_sha256 = _file_sha256(plan["path"])
    if current_sha256 is None:
        return []
    state = _sync_state(connection, project_id)
    if (
        state is not None
        and state["plan_path"] == plan["path"]
        and state["acknowledged_sha256"] == current_sha256
    ):
        return []
    rows = _pending_batch_rows_for_version(
        connection, project_id, plan["path"], current_sha256
    )
    return [
        {
            **_batch_record(row),
            "proposals": [
                proposal.model_dump(mode="json")
                for proposal in _deserialize_proposals(row["proposals_json"])
            ],
        }
        for row in rows
    ]


def _validate_accepted_indices(
    accepted_indices: list[int], proposal_count: int
) -> set[int]:
    if (
        not isinstance(accepted_indices, list)
        or any(type(index) is not int for index in accepted_indices)
        or len(set(accepted_indices)) != len(accepted_indices)
        or any(index < 0 or index >= proposal_count for index in accepted_indices)
    ):
        raise ValueError("accepted indices must be unique valid proposal indices")
    return set(accepted_indices)


def review_proposal_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    *,
    accepted_indices: list[int],
    actor: PlanningActor = "user",
) -> dict[str, Any]:
    """Apply user-selected proposal operations and acknowledge their plan version."""
    _validate_connection(connection)
    with transaction(connection, immediate=True):
        batch = _get_batch(connection, batch_id)
        if batch is None:
            raise ValueError("proposal batch does not exist")
        if batch["status"] != "Pending":
            raise ValueError("proposal batch has already been reviewed")
        _ensure_batch_source_is_current(connection, batch)
        state = _sync_state(connection, batch["project_id"])
        if (
            state is not None
            and state["plan_path"] == batch["source_plan_path"]
            and state["acknowledged_sha256"] == batch["source_plan_sha256"]
        ):
            raise ValueError("current plan version is already acknowledged")

        proposals = _deserialize_proposals(batch["proposals_json"])
        selected = _validate_accepted_indices(accepted_indices, len(proposals))

        for index, proposal in enumerate(proposals):
            if index not in selected:
                continue
            if isinstance(proposal, RoadmapProposalAdd):
                create_roadmap_item(
                    connection,
                    {
                        "project_id": batch["project_id"],
                        "title": proposal.title,
                        "status": proposal.status,
                        "note": proposal.note,
                        "parent_item_id": proposal.parent_item_id,
                        "source_plan_path": batch["source_plan_path"],
                        "actor": actor,
                    },
                )
            elif isinstance(proposal, RoadmapProposalRemove):
                delete_roadmap_item(
                    connection, batch["project_id"], proposal.roadmap_item_id
                )
            elif isinstance(proposal, RoadmapProposalReorder):
                reorder_roadmap_items(
                    connection,
                    batch["project_id"],
                    parent_item_id=proposal.parent_item_id,
                    ordered_item_ids=proposal.ordered_item_ids,
                    actor=actor,
                )

        _ensure_batch_source_is_current(connection, batch)
        connection.execute(
            "UPDATE roadmap_proposal_batches SET status = 'Reviewed', reviewed_at = ? "
            "WHERE batch_id = ?",
            (_now(), batch_id),
        )
        _acknowledge_plan(
            connection,
            batch["project_id"],
            batch["source_plan_path"],
            batch["source_plan_sha256"],
        )
        reviewed = _get_batch(connection, batch_id)
    assert reviewed is not None
    return _batch_record(reviewed)
