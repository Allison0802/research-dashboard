"""Persistence boundary for semantic dashboard events."""

import errno
import sqlite3
from typing import Any

from .db import connect_db
from .domain import SemanticEventInput
from .events import ingest_event
from .settings import load_settings


class DashboardWriteError(RuntimeError):
    def __init__(self, code: str, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


def _sqlite_error_text(error: sqlite3.Error) -> str:
    return " ".join(
        value
        for value in (
            getattr(error, "sqlite_errorname", ""),
            str(error),
        )
        if value
    ).casefold()


def _classify_sqlite_error(error: sqlite3.Error) -> DashboardWriteError:
    details = _sqlite_error_text(error)
    if "sqlite_readonly" in details or "readonly" in details or "read-only" in details:
        return DashboardWriteError(
            "DASHBOARD_DATABASE_NOT_WRITABLE",
            f"dashboard database is not writable: {error}",
            transient=False,
        )
    if "sqlite_busy" in details or "sqlite_locked" in details or "locked" in details:
        return DashboardWriteError(
            "DASHBOARD_DATABASE_BUSY",
            f"dashboard database is busy: {error}",
            transient=True,
        )
    return DashboardWriteError(
        "DASHBOARD_DATABASE_ERROR",
        f"dashboard database error: {error}",
        transient=False,
    )


def _classify_filesystem_error(error: OSError) -> DashboardWriteError:
    if isinstance(error, PermissionError) or error.errno in {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    }:
        return DashboardWriteError(
            "DASHBOARD_DATABASE_NOT_WRITABLE",
            f"dashboard database is not writable: {error}",
            transient=False,
        )
    return DashboardWriteError(
        "DASHBOARD_DATABASE_ERROR",
        f"dashboard database error: {error}",
        transient=False,
    )


def _preflight_writable(connection):
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
    except sqlite3.Error as error:
        connection.rollback()
        raise _classify_sqlite_error(error) from error


def _event_input(event: SemanticEventInput | dict[str, Any]) -> SemanticEventInput:
    if isinstance(event, SemanticEventInput):
        return event
    return SemanticEventInput.model_validate(event)


def submit_event(event: SemanticEventInput | dict[str, Any]) -> dict[str, Any]:
    """Persist one validated event and return its compact acceptance receipt."""
    value = _event_input(event)
    settings = load_settings()
    connection = None
    try:
        connection = connect_db(settings)
        _preflight_writable(connection)
        result = ingest_event(connection, value)
    except sqlite3.Error as error:
        raise _classify_sqlite_error(error) from error
    except OSError as error:
        raise _classify_filesystem_error(error) from error
    finally:
        if connection is not None:
            connection.close()

    event_record = result["event"]
    return {
        "accepted": result["accepted"],
        "event_id": event_record["event_id"],
        "sequence": event_record["sequence"],
        "status": result["status"],
        "current_state": result["current_state"],
        "conflict": result["conflict"] is not None,
    }
