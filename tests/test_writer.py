import errno
import sqlite3

import pytest

import research_dashboard.writer as writer
from research_dashboard.db import init_db
from research_dashboard.registry import add_project
from research_dashboard.settings import Settings


def _event_payload(event_id="00000000-0000-0000-0000-000000000001"):
    return {
        "event_id": event_id,
        "project_id": "writer-project",
        "workstream": "writer boundary",
        "task_key": "submit-event",
        "event_type": "state_change",
        "previous_state": None,
        "new_state": "Waiting",
        "importance": "High",
        "epistemic_status": "Observed",
        "context": "Writer boundary test",
        "what_changed": "Submitted an event",
        "observed_at": "2026-08-27T12:00:00+00:00",
    }


def _initialized_runtime(tmp_path):
    settings = Settings(tmp_path / "runtime")
    connection = init_db(settings)
    try:
        add_project(
            connection,
            {
                "project_id": "writer-project",
                "name": "Writer project",
                "domain": "General Research",
            },
        )
    finally:
        connection.close()
    return settings


def test_submit_event_returns_acceptance_receipt(monkeypatch, tmp_path):
    settings = _initialized_runtime(tmp_path)
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(settings.runtime_root))

    receipt = writer.submit_event(_event_payload())

    assert receipt == {
        "accepted": True,
        "event_id": "00000000-0000-0000-0000-000000000001",
        "sequence": 1,
        "status": "accepted",
        "current_state": "Waiting",
        "conflict": False,
    }


def test_submit_event_replays_exact_event_idempotently(monkeypatch, tmp_path):
    settings = _initialized_runtime(tmp_path)
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(settings.runtime_root))
    event = _event_payload()

    first = writer.submit_event(event)
    replay = writer.submit_event(event)

    assert replay == first
    assert replay["sequence"] == 1


def test_submit_event_classifies_readonly_database(monkeypatch, tmp_path):
    settings = Settings(tmp_path / "runtime")
    connection = init_db(settings)
    connection.close()
    read_only_connection = sqlite3.connect(
        f"{settings.database_path.absolute().as_uri()}?mode=ro",
        uri=True,
    )
    read_only_connection.execute("PRAGMA query_only = ON")
    monkeypatch.setattr(writer, "connect_db", lambda _settings: read_only_connection)

    with pytest.raises(writer.DashboardWriteError) as error:
        writer.submit_event(_event_payload())

    assert error.value.code == "DASHBOARD_DATABASE_NOT_WRITABLE"
    assert error.value.transient is False


def test_submit_event_classifies_busy_database(monkeypatch, tmp_path):
    settings = _initialized_runtime(tmp_path)
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(settings.runtime_root))
    lock_connection = sqlite3.connect(settings.database_path, timeout=0)
    original_connect_db = writer.connect_db
    lock_connection.execute("BEGIN IMMEDIATE")

    def connect_with_zero_timeout(current_settings):
        connection = original_connect_db(current_settings)
        connection.execute("PRAGMA busy_timeout = 0")
        return connection

    monkeypatch.setattr(writer, "connect_db", connect_with_zero_timeout)
    try:
        with pytest.raises(writer.DashboardWriteError) as error:
            writer.submit_event(_event_payload())
    finally:
        lock_connection.rollback()
        lock_connection.close()

    assert error.value.code == "DASHBOARD_DATABASE_BUSY"
    assert error.value.transient is True


def test_submit_event_classifies_filesystem_permission_error(monkeypatch):
    def raise_permission_error(_settings):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(writer, "connect_db", raise_permission_error)

    with pytest.raises(writer.DashboardWriteError) as error:
        writer.submit_event(_event_payload())

    assert error.value.code == "DASHBOARD_DATABASE_NOT_WRITABLE"
    assert error.value.transient is False
