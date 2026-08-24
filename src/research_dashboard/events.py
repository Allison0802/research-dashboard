"""Append-only semantic event ingestion and task-state conflict detection."""

from datetime import datetime, timezone
import sqlite3
from typing import Any
from uuid import uuid4

from .db import transaction
from .domain import SemanticEventInput
from .registry import get_project


STATE_CONFLICT = "STATE_CONFLICT"
_CORRECTION_EVENT_TYPES = frozenset({"correction", "evidence_correction"})

_EVENT_COLUMNS = (
    "event_id, project_id, workstream, task_key, event_type, previous_state, "
    "new_state, importance, risk_type, risk_severity, epistemic_status, context, "
    "what_changed, cause, impact, next_action, confidence, governing_plan_path, "
    "source_agent, source_session, observed_at, ingested_at, corrects_event_id"
)


def _event_input(event: SemanticEventInput | dict[str, Any]) -> SemanticEventInput:
    if isinstance(event, SemanticEventInput):
        return event
    return SemanticEventInput.model_validate(event)


def _normalized_event_type(event_type: str) -> str:
    return event_type.casefold().replace("-", "_").replace(" ", "_")


def _is_evidence_correction(value: SemanticEventInput) -> bool:
    return _normalized_event_type(value.event_type) in _CORRECTION_EVENT_TYPES


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _fetch_event(
    connection: sqlite3.Connection, event_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT sequence, {_EVENT_COLUMNS} FROM events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _fetch_current_event(
    connection: sqlite3.Connection,
    value: SemanticEventInput,
) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT sequence, {_EVENT_COLUMNS} FROM events "
        "WHERE project_id = ? AND workstream IS ? AND task_key IS ? "
        "ORDER BY sequence DESC LIMIT 1",
        (value.project_id, value.workstream, value.task_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _fetch_current_state_event(
    connection: sqlite3.Connection,
    value: SemanticEventInput,
) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT sequence, {_EVENT_COLUMNS} FROM events "
        "WHERE project_id = ? AND workstream IS ? AND task_key IS ? "
        "AND new_state IS NOT NULL ORDER BY sequence DESC LIMIT 1",
        (value.project_id, value.workstream, value.task_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _validate_conflict_task_key(value: SemanticEventInput) -> None:
    if not isinstance(value.task_key, str) or not value.task_key.strip():
        raise ValueError("a state conflict requires a non-blank task_key")


def _fetch_open_conflict(
    connection: sqlite3.Connection,
    value: SemanticEventInput,
) -> dict[str, Any] | None:
    if value.task_key is None:
        return None
    row = connection.execute(
        "SELECT conflict_id, project_id, workstream, task_key, left_event_id, "
        "right_event_id, resolved_by_event_id, created_at FROM state_conflicts "
        "WHERE project_id = ? AND workstream IS ? AND task_key = ? "
        "AND resolved_by_event_id IS NULL ORDER BY created_at LIMIT 1",
        (value.project_id, value.workstream, value.task_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _correction_resolves_open_conflict(
    value: SemanticEventInput,
    conflict: dict[str, Any] | None,
) -> bool:
    return (
        conflict is not None
        and _is_evidence_correction(value)
        and value.corrects_event_id is not None
        and str(value.corrects_event_id)
        in {conflict["left_event_id"], conflict["right_event_id"]}
    )


def _resolve_open_conflict(
    connection: sqlite3.Connection,
    conflict: dict[str, Any],
    resolution_event_id: str,
) -> bool:
    updated = connection.execute(
        "UPDATE state_conflicts SET resolved_by_event_id = ? "
        "WHERE conflict_id = ? AND resolved_by_event_id IS NULL",
        (resolution_event_id, conflict["conflict_id"]),
    )
    return updated.rowcount == 1


def _insert_event(connection: sqlite3.Connection, value: SemanticEventInput) -> None:
    connection.execute(
        "INSERT INTO events ("
        + _EVENT_COLUMNS
        + ") VALUES ("
        + ", ".join("?" for _ in range(23))
        + ")",
        (
            str(value.event_id),
            value.project_id,
            value.workstream,
            value.task_key,
            value.event_type,
            value.previous_state,
            value.new_state,
            value.importance,
            value.risk_type.value if value.risk_type is not None else None,
            value.risk_severity,
            value.epistemic_status.value,
            value.context,
            value.what_changed,
            value.cause,
            value.impact,
            value.next_action,
            value.confidence.value if value.confidence is not None else None,
            value.governing_plan_path,
            value.source_agent,
            value.source_session,
            _utc_iso(value.observed_at),
            _utc_iso(value.ingested_at),
            str(value.corrects_event_id) if value.corrects_event_id else None,
        ),
    )


def _expected_event_payload(value: SemanticEventInput) -> dict[str, Any]:
    return {
        "event_id": str(value.event_id),
        "project_id": value.project_id,
        "workstream": value.workstream,
        "task_key": value.task_key,
        "event_type": value.event_type,
        "previous_state": value.previous_state,
        "new_state": value.new_state,
        "importance": value.importance,
        "risk_type": value.risk_type.value if value.risk_type is not None else None,
        "risk_severity": value.risk_severity,
        "epistemic_status": value.epistemic_status.value,
        "context": value.context,
        "what_changed": value.what_changed,
        "cause": value.cause,
        "impact": value.impact,
        "next_action": value.next_action,
        "confidence": value.confidence.value if value.confidence is not None else None,
        "governing_plan_path": value.governing_plan_path,
        "source_agent": value.source_agent,
        "source_session": value.source_session,
        "observed_at": _utc_iso(value.observed_at),
        "corrects_event_id": str(value.corrects_event_id)
        if value.corrects_event_id
        else None,
    }


def _fetch_event_evidence(
    connection: sqlite3.Connection, event_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT evidence_id, event_id, evidence_type, locator, authority, "
        "observed_at, availability "
        "FROM event_evidence WHERE event_id = ? ORDER BY rowid",
        (event_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _validate_existing_event_identity(
    connection: sqlite3.Connection,
    value: SemanticEventInput,
    existing_event: dict[str, Any],
) -> None:
    if existing_event["project_id"] != value.project_id:
        raise ValueError(
            f"event_id {value.event_id} already exists for another project"
        )
    expected = _expected_event_payload(value)
    if any(existing_event[field] != expected[field] for field in expected):
        raise ValueError(
            f"event_id {value.event_id} already exists with a different payload"
        )
    expected_evidence = [
        {
            "event_id": str(value.event_id),
            "evidence_type": evidence.evidence_type,
            "locator": evidence.locator,
            "authority": evidence.authority,
            "observed_at": _utc_iso(evidence.observed_at)
            if evidence.observed_at
            else None,
            "availability": evidence.availability,
        }
        for evidence in value.evidence
    ]
    stored_evidence_rows = _fetch_event_evidence(connection, str(value.event_id))
    stored_evidence = [
        {key: item[key] for key in item if key != "evidence_id"}
        for item in stored_evidence_rows
    ]
    if stored_evidence != expected_evidence:
        raise ValueError(
            f"event_id {value.event_id} already exists with a different evidence payload"
        )


def _validate_conflict_event_projects(
    connection: sqlite3.Connection,
    value: SemanticEventInput,
    left_event_id: str,
) -> None:
    for event_id in {left_event_id, str(value.event_id)}:
        row = connection.execute(
            "SELECT project_id FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"conflict event {event_id!r} does not exist")
        if row["project_id"] != value.project_id:
            raise ValueError(
                f"conflict event {event_id!r} belongs to another project"
            )


def _validate_correction_project(
    connection: sqlite3.Connection, value: SemanticEventInput
) -> None:
    if value.corrects_event_id is None:
        return
    row = connection.execute(
        "SELECT project_id FROM events WHERE event_id = ?",
        (str(value.corrects_event_id),),
    ).fetchone()
    if row is not None and row["project_id"] != value.project_id:
        raise ValueError(
            f"corrected event {value.corrects_event_id} belongs to another project"
        )


def _insert_evidence(
    connection: sqlite3.Connection, value: SemanticEventInput
) -> list[dict[str, Any]]:
    for evidence in value.evidence:
        connection.execute(
            "INSERT INTO event_evidence ("
            "evidence_id, event_id, evidence_type, locator, authority, "
            "observed_at, availability"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                str(value.event_id),
                evidence.evidence_type,
                evidence.locator,
                evidence.authority,
                _utc_iso(evidence.observed_at) if evidence.observed_at else None,
                evidence.availability,
            ),
        )

    rows = connection.execute(
        "SELECT evidence_id, event_id, evidence_type, locator, authority, "
        "observed_at, availability FROM event_evidence WHERE event_id = ? "
        "ORDER BY rowid",
        (str(value.event_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _insert_conflict(
    connection: sqlite3.Connection,
    value: SemanticEventInput,
    current_event_id: str,
) -> dict[str, Any]:
    _validate_conflict_task_key(value)
    if current_event_id == str(value.event_id):
        raise ValueError("state conflict endpoints must use distinct event IDs")
    _validate_conflict_event_projects(connection, value, current_event_id)
    conflict_id = str(uuid4())
    connection.execute(
        "INSERT INTO state_conflicts ("
        "conflict_id, project_id, workstream, task_key, left_event_id, "
        "right_event_id, resolved_by_event_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, NULL, ?) ON CONFLICT DO NOTHING",
        (
            conflict_id,
            value.project_id,
            value.workstream,
            value.task_key,
            current_event_id,
            str(value.event_id),
            _utc_iso(datetime.now(timezone.utc)),
        ),
    )
    row = connection.execute(
        "SELECT conflict_id, project_id, workstream, task_key, left_event_id, "
        "right_event_id, resolved_by_event_id, created_at FROM state_conflicts "
        "WHERE project_id = ? AND workstream IS ? AND task_key = ? "
        "AND left_event_id = ? AND right_event_id = ?",
        (
            value.project_id,
            value.workstream,
            value.task_key,
            current_event_id,
            str(value.event_id),
        ),
    ).fetchone()
    assert row is not None
    return dict(row)


def _fetch_conflict_for_event(
    connection: sqlite3.Connection, value: SemanticEventInput
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT conflict_id, project_id, workstream, task_key, left_event_id, "
        "right_event_id, resolved_by_event_id, created_at FROM state_conflicts "
        "WHERE project_id = ? AND workstream IS ? AND task_key = ? "
        "AND right_event_id = ? ORDER BY created_at LIMIT 1",
        (value.project_id, value.workstream, value.task_key, str(value.event_id)),
    ).fetchone()
    return dict(row) if row is not None else None


def ingest_event(
    connection: sqlite3.Connection,
    event: SemanticEventInput | dict[str, Any],
    *,
    record_conflict: bool = False,
) -> dict[str, Any]:
    """Append a semantic event, its evidence, and any required conflict row.

    The returned mapping contains the stored ``event``, appended ``evidence``,
    the optional ``conflict``, and the resulting ``current_state``. Existing
    event rows are never updated or deleted.
    """
    value = _event_input(event)
    if record_conflict:
        _validate_conflict_task_key(value)
    if get_project(connection, value.project_id) is None:
        raise ValueError(
            f"unknown project {value.project_id!r}: project is not registered"
        )

    with transaction(connection):
        _validate_correction_project(connection, value)
        existing_event = _fetch_event(connection, str(value.event_id))
        if existing_event is not None:
            _validate_existing_event_identity(connection, value, existing_event)
            evidence = _fetch_event_evidence(connection, str(value.event_id))
            if record_conflict:
                conflict = _fetch_conflict_for_event(connection, value)
                if conflict is None:
                    raise ValueError(
                        "explicit conflict event ID already exists without a "
                        "matching conflict"
                    )
                if conflict["left_event_id"] == conflict["right_event_id"]:
                    raise ValueError("state conflict endpoints must use distinct event IDs")
                counterpart = _fetch_event(connection, conflict["left_event_id"])
                if (
                    counterpart is None
                    or conflict["project_id"] != value.project_id
                    or conflict["workstream"] != value.workstream
                    or conflict["task_key"] != value.task_key
                    or conflict["left_event_id"] != counterpart["event_id"]
                    or counterpart["project_id"] != value.project_id
                    or counterpart["workstream"] != value.workstream
                    or counterpart["task_key"] != value.task_key
                ):
                    raise ValueError(
                        "explicit conflict replay has a different counterpart or identity"
                    )
                return {
                    "status": "conflict",
                    "accepted": True,
                    "event": existing_event,
                    "evidence": evidence,
                    "conflict": conflict,
                    "current_state": STATE_CONFLICT,
                }
            open_conflict = _fetch_open_conflict(connection, value)
            duplicate_conflict = None
            if open_conflict is not None:
                resulting_state = STATE_CONFLICT
                status = "conflict"
                duplicate_conflict = _fetch_conflict_for_event(connection, value)
            else:
                current_state_event = _fetch_current_state_event(connection, value)
                resulting_state = (
                    current_state_event["new_state"]
                    if current_state_event is not None
                    else None
                )
                status = "accepted"
            return {
                "status": status,
                "accepted": True,
                "event": existing_event,
                "evidence": evidence,
                "conflict": duplicate_conflict,
                "current_state": resulting_state,
            }
        current_event = _fetch_current_event(connection, value)
        if record_conflict and current_event is None:
            raise ValueError(
                "an explicit conflict requires a distinct historical event"
            )
        current_state_event = _fetch_current_state_event(connection, value)
        open_conflict = _fetch_open_conflict(connection, value)

        conflict_required = False
        if current_state_event is not None and open_conflict is None:
            current_state = (
                STATE_CONFLICT if open_conflict else current_state_event["new_state"]
            )
            is_correction = _normalized_event_type(value.event_type) == "correction"
            correction_matches = (
                is_correction
                and current_event is not None
                and value.corrects_event_id is not None
                and str(value.corrects_event_id) == current_event["event_id"]
            )
            expected_transition = value.previous_state == current_state
            if not correction_matches and not expected_transition:
                conflict_required = True

        if conflict_required:
            _validate_conflict_task_key(value)

        if existing_event is None:
            _insert_event(connection, value)
            evidence = _insert_evidence(connection, value)
        else:
            evidence = _fetch_event_evidence(connection, str(value.event_id))
        if _correction_resolves_open_conflict(value, open_conflict):
            assert open_conflict is not None
            if _resolve_open_conflict(connection, open_conflict, str(value.event_id)):
                open_conflict = None
        conflict = _fetch_conflict_for_event(connection, value) if record_conflict else None
        if record_conflict and conflict is None:
            conflict = _insert_conflict(
                connection,
                value,
                current_event["event_id"],
            )
        elif conflict_required and current_event is not None:
            conflict = _insert_conflict(connection, value, current_event["event_id"])
        stored_event = _fetch_event(connection, str(value.event_id))
        assert stored_event is not None

        if open_conflict is not None or conflict is not None:
            resulting_state = STATE_CONFLICT
            status = "conflict" if conflict is not None else "accepted"
        elif value.new_state is not None:
            resulting_state = value.new_state
            status = "accepted"
        elif current_state_event is not None:
            resulting_state = current_state_event["new_state"]
            status = "accepted"
        else:
            resulting_state = None
            status = "accepted"

    return {
        "status": status,
        "accepted": True,
        "event": stored_event,
        "evidence": evidence,
        "conflict": conflict,
        "current_state": resulting_state,
    }
