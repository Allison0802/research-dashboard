import json
import sqlite3
from datetime import datetime, timezone

import pytest

from research_dashboard.db import init_db
from research_dashboard.domain import ExecutionInput, ExecutionObservationInput
from research_dashboard.executions import (
    list_executions,
    record_execution_observation,
    register_execution,
)
from research_dashboard.registry import add_project
from research_dashboard.settings import Settings


@pytest.fixture
def connection(tmp_path):
    database = init_db(Settings(tmp_path / "runtime"))
    try:
        add_project(
            database,
            {"project_id": "project-1", "name": "Project", "domain": "Research"},
        )
        yield database
    finally:
        database.close()


@pytest.fixture
def execution(connection):
    return register_execution(
        connection,
        ExecutionInput(
            execution_id="execution-1",
            backend="example",
            external_id="external-1",
        ),
    )


def test_register_execution_allows_standalone_execution(connection):
    registered = register_execution(
        connection,
        {
            "execution_id": "standalone",
            "backend": "example",
            "external_id": "external-standalone",
        },
    )

    assert registered == {
        "execution_id": "standalone",
        "backend": "example",
        "external_id": "external-standalone",
        "current_state": "SUBMITTED",
        "project_id": None,
        "workstream": None,
        "task_key": None,
        "created_at": registered["created_at"],
        "last_observed_at": None,
        "active": 1,
    }


def test_register_execution_preserves_project_linkage(connection):
    registered = register_execution(
        connection,
        {
            "execution_id": "linked",
            "backend": "example",
            "external_id": "external-linked",
            "project_id": "project-1",
            "workstream": "analysis",
            "task_key": "fit-model",
        },
    )

    assert registered["project_id"] == "project-1"
    assert registered["workstream"] == "analysis"
    assert registered["task_key"] == "fit-model"


def test_register_execution_rejects_missing_referenced_project(connection):
    with pytest.raises(ValueError, match="not registered"):
        register_execution(
            connection,
            {
                "execution_id": "missing-project",
                "backend": "example",
                "external_id": "external-missing-project",
                "project_id": "not-registered",
            },
        )


def test_register_execution_enforces_unique_backend_and_external_id(connection):
    register_execution(
        connection,
        {
            "execution_id": "first",
            "backend": "example",
            "external_id": "external-duplicate",
        },
    )

    with pytest.raises(sqlite3.IntegrityError):
        register_execution(
            connection,
            {
                "execution_id": "second",
                "backend": "example",
                "external_id": "external-duplicate",
            },
        )


def test_record_execution_observation_canonicalizes_raw_record(connection, execution):
    observation = record_execution_observation(
        connection,
        ExecutionObservationInput(
            observation_id="observation-canonical",
            execution_id=execution["execution_id"],
            state="RUNNING",
            observed_at=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
            raw_record={"z": "last", "a": {"b": 2, "a": 1}},
        ),
    )

    assert observation["raw_record"] == '{"a":{"a":1,"b":2},"z":"last"}'
    assert json.loads(observation["raw_record"]) == {
        "a": {"a": 1, "b": 2},
        "z": "last",
    }


def test_exact_observation_replay_is_idempotent(connection, execution):
    observation = ExecutionObservationInput(
        observation_id="observation-first",
        execution_id=execution["execution_id"],
        state="RUNNING",
        raw_record={"state": "running", "attempt": 1},
    )

    first = record_execution_observation(connection, observation)
    second = record_execution_observation(connection, observation)

    assert second["observation_id"] == first["observation_id"]
    assert connection.execute(
        "SELECT COUNT(*) FROM execution_observations WHERE execution_id = ?",
        (execution["execution_id"],),
    ).fetchone()[0] == 1


def test_latest_accepted_observation_controls_summary_in_ingestion_order(
    connection, execution
):
    record_execution_observation(
        connection,
        {
            "observation_id": "observation-completed",
            "execution_id": execution["execution_id"],
            "state": "COMPLETED",
            "observed_at": "2026-08-24T14:00:00+00:00",
            "raw_record": {"state": "completed"},
        },
    )
    assert list_executions(connection)[0]["active"] == 0

    record_execution_observation(
        connection,
        {
            "observation_id": "observation-running",
            "execution_id": execution["execution_id"],
            "state": "RUNNING",
            "observed_at": "2026-08-24T12:00:00+00:00",
            "raw_record": {"state": "running"},
        },
    )
    current = list_executions(connection)[0]

    assert current["current_state"] == "RUNNING"
    assert current["last_observed_at"] == "2026-08-24T12:00:00+00:00"
    assert current["active"] == 1


def test_unknown_observation_remains_active_and_is_not_completed(connection, execution):
    record_execution_observation(
        connection,
        {
            "observation_id": "observation-unknown",
            "execution_id": execution["execution_id"],
            "state": "UNKNOWN",
            "raw_record": {"state": "unrecognized"},
        },
    )

    current = list_executions(connection, active_only=True)
    assert current[0]["current_state"] == "UNKNOWN"
    assert current[0]["active"] == 1


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELLED"])
def test_terminal_observations_deactivate_execution(connection, execution, state):
    record_execution_observation(
        connection,
        {
            "observation_id": f"observation-{state.lower()}",
            "execution_id": execution["execution_id"],
            "state": state,
            "raw_record": {"state": state.lower()},
        },
    )

    assert list_executions(connection, active_only=True) == []
