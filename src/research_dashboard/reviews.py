"""Explicit review state and named checkpoint writes."""

from datetime import datetime, timezone
import sqlite3
from typing import Any
from uuid import uuid4

from .db import transaction


def _validate_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("review connection must be a sqlite3.Connection")
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "review connection must use sqlite3.Row as row_factory; "
            "use research_dashboard.db.connect_db()"
        )
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError(
            "review connection must enable SQLite foreign keys; "
            "use research_dashboard.db.connect_db()"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_sequence(connection: sqlite3.Connection) -> int:
    return connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM events"
    ).fetchone()[0]


def get_review_state(connection: sqlite3.Connection) -> dict[str, Any]:
    """Read the review checkpoint without changing it."""
    _validate_connection(connection)
    row = connection.execute(
        "SELECT singleton, reviewed_through_sequence, reviewed_at "
        "FROM review_state WHERE singleton = 1"
    ).fetchone()
    assert row is not None
    return dict(row)


def mark_reviewed(
    connection: sqlite3.Connection,
    through_sequence: int | None = None,
) -> dict[str, Any]:
    """Explicitly advance the global review checkpoint to an event sequence."""
    _validate_connection(connection)
    with transaction(connection, immediate=True):
        latest = _latest_sequence(connection)
        target = latest if through_sequence is None else through_sequence
        if not isinstance(target, int) or target < 0:
            raise ValueError("review sequence must be a non-negative integer")
        if target > latest:
            raise ValueError("review sequence cannot exceed the latest event sequence")
        current = connection.execute(
            "SELECT reviewed_through_sequence FROM review_state WHERE singleton = 1"
        ).fetchone()
        assert current is not None
        advanced_to = max(current["reviewed_through_sequence"], target)
        connection.execute(
            "UPDATE review_state SET reviewed_through_sequence = ?, reviewed_at = ? "
            "WHERE singleton = 1",
            (advanced_to, _now()),
        )
        row = connection.execute(
            "SELECT singleton, reviewed_through_sequence, reviewed_at "
            "FROM review_state WHERE singleton = 1"
        ).fetchone()
    assert row is not None
    return dict(row)


def create_named_checkpoint(
    connection: sqlite3.Connection,
    name: str,
    through_sequence: int | None = None,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    """Persist a named checkpoint at an exact existing event sequence."""
    _validate_connection(connection)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("checkpoint name must not be empty")
    checkpoint_id = checkpoint_id or str(uuid4())

    with transaction(connection, immediate=True):
        latest = _latest_sequence(connection)
        target = latest if through_sequence is None else through_sequence
        if not isinstance(target, int) or target < 0:
            raise ValueError("checkpoint sequence must be a non-negative integer")
        if target > latest:
            raise ValueError("checkpoint sequence cannot exceed the latest event sequence")
        connection.execute(
            "INSERT INTO named_checkpoints "
            "(checkpoint_id, name, through_sequence, created_at) "
            "VALUES (?, ?, ?, ?)",
            (checkpoint_id, name, target, _now()),
        )
        row = connection.execute(
            "SELECT checkpoint_id, name, through_sequence, created_at "
            "FROM named_checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def get_named_checkpoint(
    connection: sqlite3.Connection,
    name: str,
) -> dict[str, Any] | None:
    """Return a named checkpoint without changing review state."""
    _validate_connection(connection)
    row = connection.execute(
        "SELECT checkpoint_id, name, through_sequence, created_at "
        "FROM named_checkpoints WHERE name = ?",
        (name,),
    ).fetchone()
    return dict(row) if row is not None else None
