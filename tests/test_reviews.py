from datetime import datetime, timezone
import sqlite3

import pytest

from research_dashboard.db import connect_db, init_db
from research_dashboard.events import ingest_event
from research_dashboard.registry import add_project
from research_dashboard.reviews import (
    create_named_checkpoint,
    get_named_checkpoint,
    get_review_state,
    mark_reviewed,
)
from research_dashboard.settings import Settings
from research_dashboard.state import (
    changes_since_checkpoint,
    changes_since_last_review,
)


PROJECT_ID = "project-1"
EVENT_IDS = [
    "123e4567-e89b-42d3-a456-426614174200",
    "123e4567-e89b-42d3-a456-426614174201",
    "123e4567-e89b-42d3-a456-426614174202",
]


@pytest.fixture
def connection(tmp_path):
    database = init_db(Settings(tmp_path / "runtime"))
    add_project(
        database,
        {"project_id": PROJECT_ID, "name": "Project", "domain": "Research"},
    )
    try:
        yield database
    finally:
        database.close()


def add_event(connection, index):
    return ingest_event(
        connection,
        {
            "event_id": EVENT_IDS[index],
            "project_id": PROJECT_ID,
            "event_type": "note",
            "importance": "Routine change",
            "epistemic_status": "Observed",
            "context": "The project was reviewed.",
            "what_changed": f"Change {index + 1}.",
            "observed_at": datetime(2026, 8, 7, 12, index, tzinfo=timezone.utc),
        },
    )


def test_initial_review_state_is_zero_and_queries_do_not_mutate_it(connection):
    add_event(connection, 0)

    assert get_review_state(connection)["reviewed_through_sequence"] == 0
    assert [row["sequence"] for row in changes_since_last_review(connection)] == [1]
    assert get_review_state(connection)["reviewed_through_sequence"] == 0


def test_mark_reviewed_advances_to_exact_sequence(connection):
    add_event(connection, 0)
    add_event(connection, 1)

    reviewed = mark_reviewed(connection, through_sequence=1)

    assert reviewed["reviewed_through_sequence"] == 1
    assert reviewed["reviewed_at"]
    assert [row["sequence"] for row in changes_since_last_review(connection)] == [2]


def test_mark_reviewed_without_argument_uses_latest_event(connection):
    add_event(connection, 0)
    add_event(connection, 1)

    reviewed = mark_reviewed(connection)

    assert reviewed["reviewed_through_sequence"] == 2
    assert changes_since_last_review(connection) == []


@pytest.mark.parametrize("checkpoint_writer", ["review", "named checkpoint"])
def test_implicit_checkpoint_target_is_resolved_in_write_snapshot(
    tmp_path,
    checkpoint_writer,
):
    settings = Settings(tmp_path / "runtime")
    first = init_db(settings)
    second = connect_db(settings)
    second.execute("PRAGMA busy_timeout = 0")
    add_project(
        first,
        {"project_id": PROJECT_ID, "name": "Project", "domain": "Research"},
    )
    add_event(first, 0)
    lock_observed = False

    def interleave(statement):
        nonlocal lock_observed
        if "SELECT COALESCE(MAX(sequence), 0) FROM events" not in statement:
            return
        try:
            add_event(second, 1)
        except sqlite3.OperationalError as error:
            lock_observed = "locked" in str(error).lower()

    first.set_trace_callback(interleave)
    try:
        if checkpoint_writer == "review":
            checkpoint = mark_reviewed(first)
            assert checkpoint["reviewed_through_sequence"] == 1
        else:
            checkpoint = create_named_checkpoint(first, "before-analysis")
            assert checkpoint["through_sequence"] == 1
    finally:
        first.set_trace_callback(None)

    try:
        assert lock_observed
        assert first.execute("SELECT MAX(sequence) FROM events").fetchone()[0] == 1
        assert add_event(second, 1)["event"]["sequence"] == 2
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    ("checkpoint_writer", "error_message"),
    [
        ("review", "review sequence cannot exceed"),
        ("named checkpoint", "checkpoint sequence cannot exceed"),
    ],
)
def test_explicit_checkpoint_validation_reads_latest_inside_write_transaction(
    connection,
    checkpoint_writer,
    error_message,
):
    add_event(connection, 0)
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        with pytest.raises(ValueError, match=error_message):
            if checkpoint_writer == "review":
                mark_reviewed(connection, through_sequence=2)
            else:
                create_named_checkpoint(
                    connection,
                    "before-analysis",
                    through_sequence=2,
                )
    finally:
        connection.set_trace_callback(None)

    begin_index = statements.index("BEGIN IMMEDIATE")
    latest_index = next(
        index
        for index, statement in enumerate(statements)
        if "SELECT COALESCE(MAX(sequence), 0) FROM events" in statement
    )
    assert begin_index < latest_index


def test_named_checkpoint_records_sequence_and_can_be_queried(connection):
    add_event(connection, 0)
    add_event(connection, 1)

    checkpoint = create_named_checkpoint(
        connection,
        "before-analysis",
        through_sequence=1,
    )

    assert checkpoint["name"] == "before-analysis"
    assert checkpoint["through_sequence"] == 1
    assert get_named_checkpoint(connection, "before-analysis") == checkpoint

    add_event(connection, 2)

    assert [
        row["sequence"]
        for row in changes_since_checkpoint(connection, "before-analysis")
    ] == [2, 3]
