import json
import importlib
from datetime import date, datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from research_dashboard import cli
from research_dashboard.cli import main
from research_dashboard.db import connect_db
from research_dashboard.plan_sync import plan_sync_status
from research_dashboard.planning import list_roadmap_items, list_todos
from research_dashboard.settings import load_settings
from research_dashboard.state import changes_since_checkpoint, changes_since_last_review
from research_dashboard.writer import DashboardWriteError


EVENT = {
    "event_id": "123e4567-e89b-42d3-a456-426614174000",
    "project_id": "demo",
    "task_key": "task-1",
    "event_type": "state_change",
    "previous_state": "Waiting",
    "new_state": "Active",
    "importance": "Important result change",
    "epistemic_status": "Observed",
    "context": "A synthetic CLI test event.",
    "what_changed": "The synthetic task became active.",
    "observed_at": "2026-08-07T12:00:00+00:00",
    "evidence": [
        {
            "evidence_type": "test",
            "locator": "tests/test_cli.py",
            "authority": 1,
        }
    ],
}

def invoke(monkeypatch, capsys, *arguments):
    monkeypatch.setattr(sys, "argv", ["research-dashboard", *arguments])
    exit_code = main()
    captured = capsys.readouterr()
    return exit_code, captured


def read_json(captured):
    return json.loads(captured.out)


def test_init_creates_database_under_runtime_home(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))

    exit_code, captured = invoke(monkeypatch, capsys, "init")

    assert exit_code == 0
    assert read_json(captured)["initialized"] is True
    assert load_settings().database_path.is_file()


def test_registry_commands_add_resolve_plan_and_list(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    root = tmp_path / "project-root"
    nested = root / "results" / "summary.csv"
    plan = tmp_path / "plan.md"

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "add",
        "--project-id",
        "demo",
        "--name",
        "Demo project",
        "--domain",
        "Research",
        "--lifecycle",
        "Active",
    )
    assert exit_code == 0
    assert read_json(captured)["project_id"] == "demo"

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "root-add",
        "--project-id",
        "demo",
        "--path",
        str(root),
    )
    assert exit_code == 0
    assert read_json(captured)["root_path"] == str(root.resolve())

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "resolve-path",
        "--path",
        str(nested),
    )
    assert exit_code == 0
    assert read_json(captured)["project_id"] == "demo"

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "plan-set",
        "--project-id",
        "demo",
        "--path",
        str(plan),
        "--workstream",
        "analysis",
    )
    assert exit_code == 0
    assert read_json(captured)["path"] == str(plan.resolve())

    exit_code, captured = invoke(monkeypatch, capsys, "project", "list")
    assert exit_code == 0
    assert [project["project_id"] for project in read_json(captured)] == ["demo"]

def test_execution_and_slurm_commands_use_the_generic_execution_store(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    assert invoke(monkeypatch, capsys, "init")[0] == 0

    exit_code, captured = invoke(monkeypatch, capsys, "execution", "list")
    assert exit_code == 0
    assert read_json(captured) == []

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "slurm",
        "register",
        "--execution-id",
        "slurm-12345",
        "--job-id",
        "12345",
    )
    assert exit_code == 0
    assert read_json(captured)["backend"] == "slurm"
    assert read_json(captured)["external_id"] == "12345"

    exit_code, captured = invoke(monkeypatch, capsys, "execution", "list", "--active-only")
    assert exit_code == 0
    assert [item["execution_id"] for item in read_json(captured)] == ["slurm-12345"]

    adapter = importlib.import_module("research_dashboard.adapters.slurm")
    monkeypatch.setattr(
        adapter,
        "poll_slurm_execution",
        lambda _connection, **kwargs: {"polled": kwargs},
    )
    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "slurm",
        "poll",
        "--execution-id",
        "slurm-12345",
        "--ssh-target",
        "user@login.example.org",
    )
    assert exit_code == 0
    assert read_json(captured)["polled"] == {
        "execution_id": "slurm-12345",
        "ssh_target": "user@login.example.org",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "cluster",
            "add",
            "--cluster-id",
            "legacy",
            "--scheduler",
            "slurm",
            "--ssh-target",
            "login.example.org",
        ],
        ["job", "list"],
        ["poll-hpc"],
    ],
)
def test_legacy_scheduler_commands_are_invalid(arguments):
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(arguments)
def test_project_plan_set_bootstraps_structured_roadmap_and_acknowledges_plan(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    plan = tmp_path / "governing-plan.md"
    plan.write_text(
        "### Task 1: Prepare data\n- [ ] Review sources\n\n"
        "### Task 2: Analyze data\n- [x] Fit model\n",
        encoding="utf-8",
    )

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "plan-set",
        "--project-id",
        "demo",
        "--path",
        str(plan),
        "--plan-id",
        "project-plan",
    )

    assert exit_code == 0
    expected_path = str(plan.resolve())
    expected_sha256 = sha256(plan.read_bytes()).hexdigest()
    assert read_json(captured) == {
        "governing_plan": {
            "plan_id": "project-plan",
            "project_id": "demo",
            "workstream": None,
            "path": expected_path,
            "active": 1,
        },
        "roadmap_sync": {
            "status": "current",
            "plan_path": expected_path,
            "imported_count": 2,
        },
    }
    connection = connect_db(load_settings())
    try:
        assert [
            (item["title"], item["status"], item["created_by"])
            for item in list_roadmap_items(connection, "demo")
        ] == [
            ("Prepare data", "Not started", "agent"),
            ("Analyze data", "Done", "agent"),
        ]
        assert plan_sync_status(connection, "demo") == {
            "status": "current",
            "plan_path": expected_path,
            "current_sha256": expected_sha256,
            "acknowledged_sha256": expected_sha256,
            "pending_proposal_count": 0,
        }
    finally:
        connection.close()


def test_project_plan_set_leaves_prose_plan_ambiguous_without_guessing(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    plan = tmp_path / "governing-plan.md"
    plan.write_text(
        "# Research plan\n\nUse the available evidence before choosing next steps.\n",
        encoding="utf-8",
    )

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "plan-set",
        "--project-id",
        "demo",
        "--path",
        str(plan),
        "--plan-id",
        "prose-plan",
    )

    assert exit_code == 0
    expected_path = str(plan.resolve())
    assert read_json(captured) == {
        "governing_plan": {
            "plan_id": "prose-plan",
            "project_id": "demo",
            "workstream": None,
            "path": expected_path,
            "active": 1,
        },
        "roadmap_sync": {
            "status": "ambiguous",
            "plan_path": expected_path,
            "imported_count": 0,
        },
    }
    connection = connect_db(load_settings())
    try:
        assert list_roadmap_items(connection, "demo") == []
        assert plan_sync_status(connection, "demo") == {
            "status": "ambiguous",
            "plan_path": expected_path,
            "current_sha256": sha256(plan.read_bytes()).hexdigest(),
            "acknowledged_sha256": None,
            "pending_proposal_count": 0,
        }
    finally:
        connection.close()


def test_workstream_plan_set_remains_registry_only_and_preserves_project_sync(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    project_plan = tmp_path / "project-plan.md"
    project_plan.write_text("### Task 1: Project work\n", encoding="utf-8")
    workstream_plan = tmp_path / "workstream-plan.md"
    workstream_plan.write_text("### Task 1: Secondary work\n", encoding="utf-8")

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "plan-set",
            "--project-id",
            "demo",
            "--path",
            str(project_plan),
        )[0]
        == 0
    )
    connection = connect_db(load_settings())
    try:
        before_sync = plan_sync_status(connection, "demo")
        before_roadmap = list_roadmap_items(connection, "demo")
    finally:
        connection.close()

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "project",
        "plan-set",
        "--project-id",
        "demo",
        "--path",
        str(workstream_plan),
        "--workstream",
        "secondary",
        "--plan-id",
        "secondary-plan",
    )

    assert exit_code == 0
    assert read_json(captured) == {
        "plan_id": "secondary-plan",
        "project_id": "demo",
        "workstream": "secondary",
        "path": str(workstream_plan.resolve()),
        "active": 1,
    }
    connection = connect_db(load_settings())
    try:
        assert plan_sync_status(connection, "demo") == before_sync
        assert list_roadmap_items(connection, "demo") == before_roadmap
    finally:
        connection.close()


def test_planning_sync_plan_leaves_roadmap_bootstrapped_by_project_plan_set(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    plan = tmp_path / "governing-plan.md"
    plan.write_text("### Task 1: Prepare data\n- [ ] Review sources\n", encoding="utf-8")

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "plan-set",
            "--project-id",
            "demo",
            "--path",
            str(plan),
        )[0]
        == 0
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "sync-plan", "--project-id", "demo"
    )

    assert exit_code == 0
    assert read_json(captured) == {
        "status": "current",
        "plan_path": str(plan.resolve()),
        "imported_count": 0,
    }
    connection = connect_db(load_settings())
    try:
        roadmap = list_roadmap_items(connection, "demo")
    finally:
        connection.close()
    assert roadmap[0]["created_by"] == "agent"


def test_planning_apply_accepts_stdin_stamps_agent_and_preserves_omitted_values(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "project_id": "demo",
                    "operations": [
                        {
                            "operation": "roadmap_add",
                            "title": "Prepare data",
                            "note": "Keep this note",
                        }
                    ],
                }
            )
        ),
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", "-"
    )

    assert exit_code == 0
    roadmap_item = read_json(captured)[0]
    assert roadmap_item["created_by"] == "agent"
    assert roadmap_item["updated_by"] == "agent"
    update_path = tmp_path / "update.json"
    update_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "operations": [
                    {
                        "operation": "roadmap_update",
                        "roadmap_item_id": roadmap_item["roadmap_item_id"],
                        "status": "Waiting",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", str(update_path)
    )

    assert exit_code == 0
    assert read_json(captured)[0]["status"] == "Waiting"
    assert read_json(captured)[0]["note"] == "Keep this note"


def test_planning_apply_clears_explicit_null_todo_values(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    input_path = tmp_path / "create.json"
    input_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "operations": [
                    {
                        "operation": "todo_add",
                        "title": "Review draft",
                        "due_date": "2026-08-31",
                        "note": "Initial note",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", str(input_path)
    )
    assert exit_code == 0
    todo = read_json(captured)[0]
    input_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "operations": [
                    {
                        "operation": "todo_update",
                        "todo_id": todo["todo_id"],
                        "due_date": None,
                        "note": None,
                        "roadmap_item_id": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", str(input_path)
    )

    assert exit_code == 0
    assert {
        key: read_json(captured)[0][key]
        for key in ("due_date", "note", "roadmap_item_id", "updated_by")
    } == {
        "due_date": None,
        "note": None,
        "roadmap_item_id": None,
        "updated_by": "agent",
    }


def test_planning_apply_creates_a_reviewable_proposal_batch(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    plan = tmp_path / "governing-plan.md"
    plan.write_text("# Plan\n\nReview the evidence.\n", encoding="utf-8")
    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "plan-set",
            "--project-id",
            "demo",
            "--path",
            str(plan),
        )[0]
        == 0
    )
    input_path = tmp_path / "proposal.json"
    input_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "operations": [
                    {
                        "operation": "proposal_batch",
                        "source_plan_path": str(plan.resolve()),
                        "source_plan_sha256": sha256(plan.read_bytes()).hexdigest(),
                        "proposals": [
                            {"operation": "add", "title": "Review evidence"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", str(input_path)
    )

    assert exit_code == 0
    created = read_json(captured)[0]
    assert created["project_id"] == "demo"
    assert created["created_by"] == "agent"
    assert created["status"] == "Pending"


def test_planning_apply_rejects_invalid_operations_and_rolls_back_batch(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    input_path = tmp_path / "invalid.json"
    input_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "operations": [
                    {"operation": "roadmap_add", "title": "Must roll back"},
                    {
                        "operation": "todo_add",
                        "title": "Invalid attachment",
                        "roadmap_item_id": "missing-roadmap",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", str(input_path)
    )

    assert exit_code == 2
    assert "attachment must belong" in captured.err
    connection = connect_db(load_settings())
    try:
        assert list_roadmap_items(connection, "demo") == []
        assert list_todos(connection, "demo") == []
    finally:
        connection.close()

    input_path.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "operations": [{"operation": "roadmap_delete"}],
            }
        ),
        encoding="utf-8",
    )
    exit_code, captured = invoke(
        monkeypatch, capsys, "planning", "apply", "--input", str(input_path)
    )
    assert exit_code == 2
    assert "roadmap_delete" in captured.err


def test_planning_parser_exposes_only_the_supported_agent_write_commands():
    parser = cli._build_parser()

    assert parser.parse_args(["planning", "sync-plan", "--project-id", "demo"])
    assert parser.parse_args(["planning", "apply", "--input", "batch.json"])
    with pytest.raises(SystemExit):
        parser.parse_args(["planning", "roadmap-add"])
    with pytest.raises(SystemExit):
        parser.parse_args(["planning", "todo-update"])


def test_event_add_accepts_file_and_returns_sequence_and_conflict(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT), encoding="utf-8")

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )

    exit_code, captured = invoke(
        monkeypatch, capsys, "event", "add", "--input", str(event_path)
    )

    assert exit_code == 0
    assert read_json(captured) == {
        "accepted": True,
        "event_id": EVENT["event_id"],
        "sequence": 1,
        "status": "accepted",
        "current_state": "Active",
        "conflict": False,
    }


def test_event_add_prints_writer_receipt(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT), encoding="utf-8")
    receipt = {
        "accepted": True,
        "event_id": EVENT["event_id"],
        "sequence": 1,
        "status": "accepted",
        "current_state": "Active",
        "conflict": False,
    }
    monkeypatch.setattr(cli, "submit_event", lambda event: receipt, raising=False)

    exit_code, captured = invoke(
        monkeypatch, capsys, "event", "add", "--input", str(event_path)
    )

    assert exit_code == 0
    assert read_json(captured) == receipt


@pytest.mark.parametrize(
    ("code", "transient"),
    [
        ("DASHBOARD_DATABASE_BUSY", True),
        ("DASHBOARD_DATABASE_NOT_WRITABLE", False),
    ],
)
def test_event_add_serializes_dashboard_write_error(
    monkeypatch, capsys, tmp_path, code, transient
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT), encoding="utf-8")

    def raise_write_error(event):
        raise DashboardWriteError(code, "database is locked", transient=transient)

    monkeypatch.setattr(cli, "submit_event", raise_write_error, raising=False)

    exit_code, captured = invoke(
        monkeypatch, capsys, "event", "add", "--input", str(event_path)
    )

    assert exit_code == 2
    assert captured.err == (
        json.dumps(
            {
                "error": {
                    "code": code,
                    "transient": transient,
                    "message": "database is locked",
                }
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def test_event_add_accepts_agent_provenance_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                **EVENT,
                "source_agent": "example-agent",
                "source_session": "session-001",
            }
        ),
        encoding="utf-8",
    )

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    assert invoke(monkeypatch, capsys, "event", "add", "--input", str(event_path))[0] == 0

    connection = connect_db(load_settings(), read_only=True)
    try:
        event = changes_since_checkpoint(connection, 0)[0]
    finally:
        connection.close()

    assert event["source_agent"] == "example-agent"
    assert event["source_session"] == "session-001"


def test_event_add_accepts_stdin_and_invalid_input_fails(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(EVENT)))

    exit_code, captured = invoke(monkeypatch, capsys, "event", "add", "--input", "-")

    assert exit_code == 0
    assert read_json(captured)["event_id"] == EVENT["event_id"]

    invalid = dict(EVENT)
    invalid["event_id"] = "not-a-uuid"
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    exit_code, captured = invoke(
        monkeypatch, capsys, "event", "add", "--input", str(invalid_path)
    )
    assert exit_code != 0
    assert "error:" in captured.err


def test_review_and_named_checkpoint_update_application_state(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(EVENT), encoding="utf-8")

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    assert invoke(monkeypatch, capsys, "event", "add", "--input", str(event_path))[0] == 0

    exit_code, captured = invoke(monkeypatch, capsys, "review", "mark")
    assert exit_code == 0
    assert read_json(captured)["reviewed_through_sequence"] == 1

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "checkpoint",
        "add",
        "--name",
        "after-review",
    )
    assert exit_code == 0
    assert read_json(captured)["through_sequence"] == 1

    connection = connect_db(load_settings())
    try:
        assert changes_since_last_review(connection) == []
        assert changes_since_checkpoint(connection, "after-review") == []
    finally:
        connection.close()


def test_serve_hands_off_to_app_factory_and_uvicorn(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    calls = []
    fake_web = SimpleNamespace(
        create_app=lambda: calls.append("create_app") or "app"
    )
    fake_uvicorn = SimpleNamespace(
        run=lambda app, host, port: calls.append((app, host, port))
    )
    monkeypatch.setitem(sys.modules, "research_dashboard.web", fake_web)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    exit_code, captured = invoke(
        monkeypatch, capsys, "serve", "--host", "127.0.0.2", "--port", "8123"
    )

    assert exit_code == 0
    assert captured.out == ""
    assert calls == ["create_app", ("app", "127.0.0.2", 8123)]


def test_portfolio_query_cli_accepts_english_alias_and_rejects_invalid_inputs(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    event_path = tmp_path / "blocked-event.json"
    blocked_event = {
        **EVENT,
        "event_id": "123e4567-e89b-42d3-a456-426614174001",
        "previous_state": "Waiting",
        "new_state": "Blocked",
        "importance": "Critical blocker",
        "observed_at": "2026-08-08T09:00:00+00:00",
    }
    event_path.write_text(json.dumps(blocked_event), encoding="utf-8")

    assert invoke(monkeypatch, capsys, "init")[0] == 0
    assert (
        invoke(
            monkeypatch,
            capsys,
            "project",
            "add",
            "--project-id",
            "demo",
            "--name",
            "Demo",
            "--domain",
            "Research",
        )[0]
        == 0
    )
    assert invoke(monkeypatch, capsys, "event", "add", "--input", str(event_path))[0] == 0

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "portfolio",
        "query",
        "What became blocked today?",
        "--as-of",
        "2026-08-08T12:00:00+00:00",
    )
    assert exit_code == 0
    assert read_json(captured)["results"][0]["task_key"] == "task-1"

    exit_code, captured = invoke(
        monkeypatch,
        capsys,
        "portfolio",
        "query",
        "not-a-query",
    )
    assert exit_code == 2
    assert "unknown portfolio query" in captured.err

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research-dashboard",
            "portfolio",
            "query",
            "blocked_today",
            "--as-of",
            "2026-08-08T12:00:00",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "timezone" in captured.err


def test_portfolio_query_cli_preserves_date_only_and_precise_as_of_values():
    assert cli._parse_query_time("2026-08-08") == date(2026, 8, 8)
    assert cli._parse_query_time("2026-08-08T12:00:00+00:00") == datetime(
        2026, 8, 8, 12, 0, tzinfo=timezone.utc
    )


def test_portfolio_parser_does_not_accept_obsolete_validate_subcommand():
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["portfolio", "validate"])


@pytest.mark.parametrize(
    "arguments",
    [
        ("integration", "install"),
        ("investigation", "add", "--risk", "risk-1", "--input", "risk.json"),
        ("correction", "list", "--project-id", "project-1"),
    ],
)
def test_parser_rejects_removed_private_workflow_commands(arguments):
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(arguments)
