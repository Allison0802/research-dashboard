"""Transactional risk lifecycle operations."""

from datetime import datetime, timezone
import sqlite3
from typing import Any
from uuid import uuid4

from .db import transaction
from .domain import RiskInput
from .registry import get_project


RISK_STATUSES = (
    "New",
    "Investigating",
    "Understood",
    "Action planned",
    "Resolved",
    "Accepted",
    "Waiting",
)
_STATUS_SET = frozenset(RISK_STATUSES)

# Manual transitions are deliberately explicit.  Changed evidence reopens a
# risk through open_or_update_risk rather than bypassing this lifecycle.
_ALLOWED_TRANSITIONS = {
    "New": frozenset({"Investigating", "Waiting", "Resolved", "Accepted"}),
    "Investigating": frozenset(
        {"New", "Understood", "Waiting", "Resolved", "Accepted"}
    ),
    "Understood": frozenset(
        {"New", "Investigating", "Action planned", "Waiting", "Resolved", "Accepted"}
    ),
    "Action planned": frozenset(
        {"New", "Investigating", "Waiting", "Resolved", "Accepted"}
    ),
    "Waiting": frozenset({"New", "Investigating", "Resolved", "Accepted"}),
    "Accepted": frozenset({"New", "Investigating"}),
    "Resolved": frozenset({"New", "Investigating"}),
}
_RISK_COLUMNS = (
    "risk_id, risk_key, project_id, workstream, task_key, risk_type, severity, "
    "title, current_status, fingerprint, originating_event_id, created_at, updated_at"
)


def _validate_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("risk connection must be a sqlite3.Connection")
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "risk connection must use sqlite3.Row as row_factory; "
            "use research_dashboard.db.connect_db()"
        )
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError("risk connection must enable SQLite foreign keys")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _risk_input(risk: RiskInput | dict[str, Any]) -> RiskInput:
    return risk if isinstance(risk, RiskInput) else RiskInput.model_validate(risk)


def _validate_status(status: str) -> None:
    if status not in _STATUS_SET:
        raise ValueError(
            f"invalid risk status {status!r}; expected one of {', '.join(RISK_STATUSES)}"
        )


def _fetch_risk(connection: sqlite3.Connection, risk_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT {_RISK_COLUMNS} FROM risks WHERE risk_id = ?", (risk_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def _require_risk(connection: sqlite3.Connection, risk_id: str) -> dict[str, Any]:
    risk = _fetch_risk(connection, risk_id)
    if risk is None:
        raise ValueError(f"risk {risk_id!r} not found")
    return risk


def _require_originating_event(
    connection: sqlite3.Connection, event_id: str, project_id: str
) -> None:
    event = connection.execute(
        "SELECT project_id FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if event is None:
        raise ValueError(f"originating event {event_id!r} not found")
    if event["project_id"] != project_id:
        raise ValueError(
            f"originating event {event_id!r} belongs to project "
            f"{event['project_id']!r}, not {project_id!r}"
        )


def _append_transition(
    connection: sqlite3.Connection,
    risk_id: str,
    from_status: str | None,
    to_status: str,
    note: str | None,
    event_id: str | None,
    created_at: str,
) -> None:
    connection.execute(
        "INSERT INTO risk_transitions ("
        "transition_id, risk_id, from_status, to_status, note, event_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid4()), risk_id, from_status, to_status, note, event_id, created_at),
    )


def open_or_update_risk(
    connection: sqlite3.Connection,
    risk: RiskInput | dict[str, Any],
) -> dict[str, Any]:
    """Open a risk or reopen it when its evidence fingerprint changes.

    An unchanged fingerprint is idempotent, including for Accepted and
    Resolved risks.  A changed fingerprint records the new originating event
    and returns the risk to New.
    """
    _validate_connection(connection)
    value = _risk_input(risk)
    _validate_status(value.current_status)
    event_id = str(value.originating_event_id)
    risk_id = value.risk_id or str(uuid4())
    now = _utc_now()

    if get_project(connection, value.project_id) is None:
        raise ValueError(f"project {value.project_id!r} not found")

    with transaction(connection, immediate=True):
        _require_originating_event(connection, event_id, value.project_id)
        existing_row = connection.execute(
            f"SELECT {_RISK_COLUMNS} FROM risks WHERE project_id = ? AND risk_key = ?",
            (value.project_id, value.risk_key),
        ).fetchone()

        if existing_row is None:
            connection.execute(
                "INSERT INTO risks ("
                + _RISK_COLUMNS
                + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    risk_id,
                    value.risk_key,
                    value.project_id,
                    value.workstream,
                    value.task_key,
                    value.risk_type.value,
                    value.severity,
                    value.title,
                    value.current_status,
                    value.fingerprint,
                    event_id,
                    value.created_at.astimezone(timezone.utc).isoformat(),
                    now,
                ),
            )
            _append_transition(
                connection,
                risk_id,
                None,
                value.current_status,
                "risk opened",
                event_id,
                now,
            )
        else:
            existing = dict(existing_row)
            if value.risk_id is not None and value.risk_id != existing["risk_id"]:
                raise ValueError(
                    f"risk key {value.risk_key!r} belongs to risk "
                    f"{existing['risk_id']!r}, not {value.risk_id!r}"
                )
            risk_id = existing["risk_id"]
            if existing["fingerprint"] != value.fingerprint:
                connection.execute(
                    "UPDATE risks SET workstream = ?, task_key = ?, risk_type = ?, "
                    "severity = ?, title = ?, current_status = ?, fingerprint = ?, "
                    "originating_event_id = ?, updated_at = ? WHERE risk_id = ?",
                    (
                        value.workstream,
                        value.task_key,
                        value.risk_type.value,
                        value.severity,
                        value.title,
                        "New",
                        value.fingerprint,
                        event_id,
                        now,
                        risk_id,
                    ),
                )
                if existing["current_status"] != "New":
                    _append_transition(
                        connection,
                        risk_id,
                        existing["current_status"],
                        "New",
                        "materially changed evidence reopened risk",
                        event_id,
                        now,
                    )

    result = _fetch_risk(connection, risk_id)
    assert result is not None
    return result


def transition_risk(
    connection: sqlite3.Connection,
    risk_id: str,
    to_status: str,
    note: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Append one valid lifecycle transition and update the current projection."""
    _validate_connection(connection)
    _validate_status(to_status)
    if event_id is not None:
        event_id = str(event_id)

    with transaction(connection, immediate=True):
        risk = _require_risk(connection, risk_id)
        from_status = risk["current_status"]
        _validate_status(from_status)
        if from_status == to_status:
            raise ValueError(f"risk {risk_id!r} is already {to_status!r}")
        if to_status not in _ALLOWED_TRANSITIONS[from_status]:
            raise ValueError(
                f"invalid risk transition {from_status!r} -> {to_status!r}"
            )
        if event_id is not None:
            _require_originating_event(connection, event_id, risk["project_id"])
        now = _utc_now()
        connection.execute(
            "UPDATE risks SET current_status = ?, updated_at = ? WHERE risk_id = ?",
            (to_status, now, risk_id),
        )
        _append_transition(
            connection, risk_id, from_status, to_status, note, event_id, now
        )

    result = _fetch_risk(connection, risk_id)
    assert result is not None
    return result


def accept_risk(
    connection: sqlite3.Connection,
    risk_id: str,
    note: str | None = None,
) -> dict[str, Any]:
    return transition_risk(connection, risk_id, "Accepted", note)


def resolve_risk(
    connection: sqlite3.Connection,
    risk_id: str,
    note: str | None = None,
) -> dict[str, Any]:
    return transition_risk(connection, risk_id, "Resolved", note)


def list_needs_attention(
    connection: sqlite3.Connection,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return all current risks except Accepted and Resolved risks."""
    _validate_connection(connection)
    parameters: list[Any] = []
    query = (
        f"SELECT {_RISK_COLUMNS} FROM risks "
        "WHERE current_status NOT IN ('Accepted', 'Resolved')"
    )
    if project_id is not None:
        query += " AND project_id = ?"
        parameters.append(project_id)
    query += " ORDER BY updated_at DESC, risk_id"
    rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]
