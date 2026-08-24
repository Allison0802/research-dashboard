"""Backend-neutral execution registration and observation persistence."""

import json
import sqlite3
from typing import Any

from .db import transaction
from .domain import ExecutionInput, ExecutionObservationInput
from .registry import _validate_connection


_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _execution_input(
    execution: ExecutionInput | dict[str, Any],
) -> ExecutionInput:
    return (
        execution
        if isinstance(execution, ExecutionInput)
        else ExecutionInput.model_validate(execution)
    )


def _observation_input(
    observation: ExecutionObservationInput | dict[str, Any],
) -> ExecutionObservationInput:
    return (
        observation
        if isinstance(observation, ExecutionObservationInput)
        else ExecutionObservationInput.model_validate(observation)
    )


def _is_active(state: str) -> bool:
    return state not in _TERMINAL_STATES


def _canonical_raw_record(raw_record: dict[str, object]) -> str:
    try:
        return json.dumps(raw_record, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("raw_record must be JSON serializable") from error


def _execution_record(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _observation_record(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def register_execution(
    connection: sqlite3.Connection,
    execution: ExecutionInput | dict[str, Any],
) -> dict[str, Any]:
    """Register one execution without requiring a particular backend."""
    _validate_connection(connection)
    value = _execution_input(execution)

    with transaction(connection, immediate=True):
        if value.project_id is not None:
            project = connection.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (value.project_id,),
            ).fetchone()
            if project is None:
                raise ValueError(f"project {value.project_id!r} is not registered")
        connection.execute(
            "INSERT INTO executions ("
            "execution_id, backend, external_id, current_state, project_id, "
            "workstream, task_key, created_at, last_observed_at, active"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                value.execution_id,
                value.backend,
                value.external_id,
                value.current_state,
                value.project_id,
                value.workstream,
                value.task_key,
                value.created_at.isoformat(),
                (
                    value.last_observed_at.isoformat()
                    if value.last_observed_at is not None
                    else None
                ),
                int(_is_active(value.current_state)),
            ),
        )
        row = connection.execute(
            "SELECT execution_id, backend, external_id, current_state, project_id, "
            "workstream, task_key, created_at, last_observed_at, active "
            "FROM executions WHERE execution_id = ?",
            (value.execution_id,),
        ).fetchone()
    assert row is not None
    return _execution_record(row)


def record_execution_observation(
    connection: sqlite3.Connection,
    observation: ExecutionObservationInput | dict[str, Any],
) -> dict[str, Any]:
    """Append an observation and update the execution summary in ingestion order."""
    _validate_connection(connection)
    value = _observation_input(observation)
    canonical_raw_record = _canonical_raw_record(value.raw_record)

    with transaction(connection, immediate=True):
        existing = connection.execute(
            "SELECT observation_id, execution_id, state, observed_at, raw_record "
            "FROM execution_observations "
            "WHERE execution_id = ? AND state = ? AND raw_record = ?",
            (value.execution_id, value.state, canonical_raw_record),
        ).fetchone()
        if existing is not None:
            return _observation_record(existing)

        execution = connection.execute(
            "SELECT execution_id FROM executions WHERE execution_id = ?",
            (value.execution_id,),
        ).fetchone()
        if execution is None:
            raise ValueError(f"execution {value.execution_id!r} is not registered")

        connection.execute(
            "INSERT INTO execution_observations ("
            "observation_id, execution_id, state, observed_at, raw_record"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                value.observation_id,
                value.execution_id,
                value.state,
                value.observed_at.isoformat(),
                canonical_raw_record,
            ),
        )
        connection.execute(
            "UPDATE executions SET current_state = ?, last_observed_at = ?, active = ? "
            "WHERE execution_id = ?",
            (
                value.state,
                value.observed_at.isoformat(),
                int(_is_active(value.state)),
                value.execution_id,
            ),
        )
        row = connection.execute(
            "SELECT observation_id, execution_id, state, observed_at, raw_record "
            "FROM execution_observations WHERE observation_id = ?",
            (value.observation_id,),
        ).fetchone()
    assert row is not None
    return _observation_record(row)


def list_executions(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[dict[str, Any]]:
    """List stored execution summaries without querying a backend."""
    _validate_connection(connection)
    query = (
        "SELECT execution_id, backend, external_id, current_state, project_id, "
        "workstream, task_key, created_at, last_observed_at, active "
        "FROM executions"
    )
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY execution_id"
    return [_execution_record(row) for row in connection.execute(query).fetchall()]
