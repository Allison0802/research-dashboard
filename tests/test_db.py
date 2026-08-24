import sqlite3
from pathlib import Path
import re

import pytest

import research_dashboard.db as db
from research_dashboard.cli import main
from research_dashboard.db import connect_db, init_db, transaction
from research_dashboard.registry import add_project
from research_dashboard.settings import DEFAULT_RUNTIME_ROOT, Settings, load_settings


REQUIRED_TABLES = {
    "projects",
    "project_roots",
    "governing_plans",
    "events",
    "event_evidence",
    "state_conflicts",
    "risks",
    "risk_transitions",
    "review_state",
    "named_checkpoints",
    "executions",
    "execution_observations",
    "roadmap_items",
    "todos",
    "roadmap_sync_state",
    "roadmap_proposal_batches",
}


def test_init_db_creates_required_tables(tmp_path):
    settings = Settings(tmp_path / "runtime")
    connection = init_db(settings)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == REQUIRED_TABLES
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute(
            "SELECT reviewed_through_sequence FROM review_state WHERE singleton = 1"
        ).fetchone()[0] == 0
        assert not (settings.runtime_root / "actions").exists()
        assert not (settings.runtime_root / "logs").exists()
    finally:
        connection.close()


def test_read_only_connection_rejects_partial_dashboard_schema(tmp_path):
    settings = Settings(tmp_path / "runtime")
    settings.runtime_root.mkdir(parents=True)
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY);
            CREATE TABLE project_roots (project_id TEXT, root_path TEXT);
            CREATE TABLE governing_plans (plan_id TEXT PRIMARY KEY);
            """
        )

    with pytest.raises(RuntimeError) as error:
        connect_db(settings, read_only=True)

    message = str(error.value)
    assert "missing tables:" in message
    assert "events" in message


def test_scheduler_specific_tables_are_absent_from_the_fresh_schema(tmp_path):
    connection = init_db(Settings(tmp_path / "runtime"))
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"clusters", "hpc_jobs", "hpc_observations"}.isdisjoint(tables)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("table", "columns", "values"),
    [
        (
            "roadmap_items",
            "roadmap_item_id, project_id, title, status, position, created_by, "
            "updated_by, created_at, updated_at",
            "'roadmap-1', 'project-1', 'Roadmap item', 'Unknown', 0, 'user', "
            "'user', '2026-08-22T00:00:00+00:00', '2026-08-22T00:00:00+00:00'",
        ),
        (
            "todos",
            "todo_id, project_id, title, status, priority, position, created_by, "
            "updated_by, created_at, updated_at",
            "'todo-status', 'project-1', 'Todo', 'Unknown', 'Normal', 0, 'user', "
            "'user', '2026-08-22T00:00:00+00:00', '2026-08-22T00:00:00+00:00'",
        ),
        (
            "todos",
            "todo_id, project_id, title, status, priority, position, created_by, "
            "updated_by, created_at, updated_at",
            "'todo-priority', 'project-1', 'Todo', 'Open', 'Urgent', 0, 'user', "
            "'user', '2026-08-22T00:00:00+00:00', '2026-08-22T00:00:00+00:00'",
        ),
        (
            "roadmap_proposal_batches",
            "batch_id, project_id, source_plan_path, source_plan_sha256, "
            "proposals_json, status, created_by, created_at",
            "'batch-1', 'project-1', '/tmp/plan.md', 'a' * 64, '[]', 'Unknown', "
            "'agent', '2026-08-22T00:00:00+00:00'",
        ),
    ],
)
def test_planning_schema_rejects_invalid_status_values(tmp_path, table, columns, values):
    connection = init_db(Settings(tmp_path / "runtime"))
    try:
        add_project(
            connection,
            {"project_id": "project-1", "name": "Project One", "domain": "Research"},
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({values})")
    finally:
        connection.close()


def test_fresh_schema_rejects_invalid_raw_state_conflicts(tmp_path):
    connection = init_db(Settings(tmp_path / "runtime"))

    def insert_event(event_id, project_id):
        connection.execute(
            "INSERT INTO events ("
            "event_id, project_id, event_type, importance, epistemic_status, "
            "context, what_changed, observed_at, ingested_at"
            ") VALUES (?, ?, 'note', 'High', 'Observed', 'context', 'change', ?, ?)",
            (
                event_id,
                project_id,
                "2026-08-08T00:00:00+00:00",
                "2026-08-08T00:00:00+00:00",
            ),
        )

    try:
        add_project(
            connection,
            {"project_id": "project-1", "name": "Project One", "domain": "Research"},
        )
        add_project(
            connection,
            {"project_id": "project-2", "name": "Project Two", "domain": "Research"},
        )
        insert_event("left-event", "project-1")
        insert_event("right-event", "project-1")
        insert_event("other-project-event", "project-2")
        connection.commit()

        for index, task_key in enumerate((None, "", " \t")):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO state_conflicts ("
                    "conflict_id, project_id, workstream, task_key, left_event_id, "
                    "right_event_id, created_at"
                    ") VALUES (?, 'project-1', NULL, ?, 'left-event', 'right-event', ?)",
                    (f"blank-task-{index}", task_key, "2026-08-08T00:00:00+00:00"),
                )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO state_conflicts ("
                "conflict_id, project_id, task_key, left_event_id, right_event_id, created_at"
                ") VALUES ('self-conflict', 'project-1', 'task-1', 'left-event', 'left-event', ?)",
                ("2026-08-08T00:00:00+00:00",),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO state_conflicts ("
                "conflict_id, project_id, task_key, left_event_id, right_event_id, created_at"
                ") VALUES ('cross-project', 'project-1', 'task-1', 'left-event', 'other-project-event', ?)",
                ("2026-08-08T00:00:00+00:00",),
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO state_conflicts ("
                "conflict_id, project_id, task_key, left_event_id, right_event_id, "
                "resolved_by_event_id, created_at"
                ") VALUES ('cross-project-resolution', 'project-1', 'task-1', "
                "'left-event', 'right-event', 'other-project-event', ?)",
                ("2026-08-08T00:00:00+00:00",),
            )
    finally:
        connection.close()


def test_default_runtime_database_is_outside_cloudstorage(monkeypatch):
    monkeypatch.delenv("RESEARCH_DASHBOARD_HOME", raising=False)
    settings = load_settings()

    expected_root = Path.home() / ".research-dashboard"
    assert DEFAULT_RUNTIME_ROOT == expected_root
    assert settings.database_path == expected_root / "dashboard.sqlite3"
    assert "CloudStorage" not in settings.database_path.parts


def test_nested_transaction_preserves_outer_rollback_atomicity():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    try:
        with pytest.raises(RuntimeError, match="outer failure"):
            with transaction(connection):
                connection.execute("INSERT INTO records VALUES ('outer')")
                with transaction(connection):
                    connection.execute("INSERT INTO records VALUES ('inner')")
                raise RuntimeError("outer failure")

        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
    finally:
        connection.close()


def test_immediate_transaction_holds_a_write_lock_for_its_snapshot(tmp_path):
    database_path = tmp_path / "dashboard.sqlite3"
    first = sqlite3.connect(database_path)
    second = sqlite3.connect(database_path)
    second.execute("PRAGMA busy_timeout = 0")
    first.execute("CREATE TABLE records (value TEXT NOT NULL)")
    first.commit()
    try:
        with transaction(first, immediate=True):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                second.execute("INSERT INTO records VALUES ('blocked')")

        second.execute("INSERT INTO records VALUES ('after')")
        second.commit()
        assert second.execute("SELECT value FROM records").fetchall() == [
            ("after",)
        ]
    finally:
        first.close()
        second.close()


def test_transaction_rolls_back_when_commit_fails():
    class CommitRaisesConnection(sqlite3.Connection):
        def commit(self):
            raise RuntimeError("commit failure")

    connection = sqlite3.connect(":memory:", factory=CommitRaisesConnection)
    connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            with transaction(connection):
                connection.execute("INSERT INTO records VALUES ('pending')")

        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0
    finally:
        connection.close()


def test_snapshot_create_writes_immutable_sqlite_backup(monkeypatch, capsys, tmp_path):
    runtime_root = tmp_path / "runtime"
    destination = tmp_path / "backups"
    destination.mkdir()
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(runtime_root))

    connection = init_db(Settings(runtime_root))
    connection.close()

    assert main(
        ["snapshot", "create", "--destination", str(destination)]
    ) == 0
    snapshot_path = Path(capsys.readouterr().out.strip().strip('"'))

    assert re.fullmatch(
        r"research-dashboard-\d{8}T\d{6}Z\.sqlite3", snapshot_path.name
    )
    assert snapshot_path.parent == destination
    assert snapshot_path.is_file()
    with sqlite3.connect(snapshot_path) as snapshot:
        assert snapshot.execute(
            "SELECT name FROM sqlite_master WHERE name = 'projects'"
        ).fetchone() == ("projects",)


def test_snapshot_backup_publishes_final_path_after_backup_completes(
    monkeypatch, tmp_path
):
    runtime_root = tmp_path / "runtime"
    destination = tmp_path / "backups"
    destination.mkdir()
    settings = Settings(runtime_root)

    connection = init_db(settings)
    connection.close()
    source_connection = connect_db(settings, read_only=True)

    def final_snapshots():
        return list(destination.glob("research-dashboard-*.sqlite3"))

    class ControlledSource:
        def backup(self, target_connection):
            assert final_snapshots() == []
            source_connection.backup(target_connection)
            assert final_snapshots() == []

        def close(self):
            source_connection.close()

    monkeypatch.setattr(
        db,
        "connect_db",
        lambda _settings, *, read_only=False: ControlledSource(),
    )

    snapshot_path = db.create_snapshot(destination, settings)

    assert snapshot_path in final_snapshots()
    assert snapshot_path.is_file()
