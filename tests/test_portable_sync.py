"""Public admission, bounded sync, and conservative activity attribution."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from research_dashboard.db import init_db
from research_dashboard.cli import main
from research_dashboard.discovery import _candidate, admit_project, sync_projects
from research_dashboard.events import ingest_event
from research_dashboard.inbox_routing import create_inbox_item, route_inbox_items
from research_dashboard.log_sync import sync_project_logs
from research_dashboard.registry import add_project, add_project_root, get_project, set_project_lifecycle
from research_dashboard.settings import Settings


@pytest.fixture
def connection(tmp_path):
    db = init_db(Settings(tmp_path / "runtime"))
    yield db
    db.close()


def marked(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "CONTEXT.md").write_text("Independent project\n", encoding="utf-8")
    return path


def test_admission_preserves_exact_root_identity_and_terminal_status(connection, tmp_path):
    root = marked(tmp_path / "Studies" / "Alpha")
    add_project(connection, {"project_id": "existing", "name": "Alpha", "domain": "Open Science", "lifecycle": "Active"})
    add_project_root(connection, "existing", root)
    assert admit_project(connection, path=str(root), domain="Open Science", reason="ongoing")["project_id"] == "existing"
    assert admit_project(connection, path=str(root), domain="Open Science", reason="ongoing")["project_id"] == "existing"
    assert connection.execute("SELECT description FROM projects WHERE project_id = 'existing'").fetchone()[0].count("Dashboard admission: ongoing") == 1
    set_project_lifecycle(connection, "existing", "Completed")
    with pytest.raises(ValueError, match="reopening"):
        admit_project(connection, path=str(root), domain="Open Science", reason="ongoing")
    assert get_project(connection, "existing")["lifecycle"] == "Completed"


def test_discovery_is_opt_in_and_candidates_stay_pending(connection, tmp_path):
    root = tmp_path / "Scope"
    marked(root / "Alpha")
    marked(root / "Beta")
    assert sync_projects(connection, []) == []
    assert connection.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
    results = sync_projects(connection, [(root, "Any Domain")])
    assert len(results) == 2
    assert {row["lifecycle"] for row in connection.execute("SELECT lifecycle FROM projects")} == {"Needs classification"}
    assert sync_projects(connection, [(root, "Any Domain")])[0]["status"] == "existing"


def test_discovery_refuses_id_collision_and_closed_marker(connection, tmp_path):
    scope = tmp_path / "Scope"
    colliding = marked(scope / "Alpha")
    retired = marked(scope / "Beta")
    (retired / "RETIRED.md").write_text("Closed\n", encoding="utf-8")
    project_id = _candidate(colliding, "Any")["project_id"]
    add_project(connection, {"project_id": project_id, "name": "Prior owner", "domain": "Any"})
    add_project_root(connection, project_id, marked(tmp_path / "Elsewhere"))
    assert sync_projects(connection, [(scope, "Any")]) == [
        {"status": "needs_assignment", "project_id": project_id, "root_path": str(colliding)}
    ]
    with pytest.raises(ValueError, match="independently marked"):
        admit_project(connection, path=str(retired), domain="Any", reason="reopen")


def test_plan_markers_are_independent_discovery_signals(connection, tmp_path):
    scope = tmp_path / "Scope"
    standalone = scope / "Standalone"
    standalone.mkdir(parents=True)
    (standalone / "ANALYSIS_PLAN.md").write_text("# Plan\n", encoding="utf-8")
    reports = scope / "Reported" / "project_reports" / "plans"
    reports.mkdir(parents=True)
    (reports / "governing.md").write_text("# Plan\n", encoding="utf-8")
    results = sync_projects(connection, [(scope, "Any")], dry_run=True)
    assert {row["root_path"] for row in results} == {str(standalone), str(reports.parents[1])}


def test_explicit_root_ignores_storage_ancestors_but_skips_excluded_descendants(connection, tmp_path):
    storage = tmp_path / ".local" / "data"
    trial = marked(storage / "Trial")
    assert admit_project(connection, path=str(trial), domain="Any", reason="active")["roots"] == [str(trial)]
    scope = tmp_path / "Scope"
    allowed = marked(scope / "Study")
    for excluded in ("data", "outputs", "worktrees", "ClosedArchives"):
        marked(scope / excluded / "Hidden")
    assert {row["root_path"] for row in sync_projects(connection, [(scope, "Any")], dry_run=True)} == {str(allowed)}
    assert {row["root_path"] for row in sync_projects(connection, [(storage, "Any")], dry_run=True)} == {str(trial)}


def test_cli_sync_never_scans_unrequested_roots(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    marked(tmp_path / "Projects" / "Unrequested")
    db = init_db(Settings(tmp_path / "runtime"))
    db.close()
    assert main(["sync"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "projects": [], "candidates": [], "discovery": [], "log_updates": [], "inbox_routing": []
    }


def test_cli_sync_includes_rootless_admitted_and_hidden_candidates(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    db = init_db(Settings(tmp_path / "runtime"))
    add_project(db, {"project_id": "rootless", "name": "Rootless", "domain": "Any", "lifecycle": "Active"})
    add_project(db, {"project_id": "pending", "name": "Pending", "domain": "Any"})
    add_project_root(db, "pending", marked(tmp_path / "Pending"))
    db.close()
    assert main(["sync"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [(project["project_id"], project["roots"]) for project in result["projects"]] == [("rootless", [])]
    assert [(project["project_id"], project["roots"]) for project in result["candidates"]] == [("pending", [str(tmp_path / "Pending")])]
    assert result["discovery"] == []


def test_cli_inbox_add_routes_exact_owner_immediately(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    db = init_db(Settings(tmp_path / "runtime"))
    add_project(db, {"project_id": "rootless", "name": "Rootless", "domain": "Any", "lifecycle": "Active"})
    db.close()
    assert main(["inbox", "add", "--title", "Activity", "--note", "Project: Rootless completed an analysis"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["item"]["assigned_project_id"] == "rootless"
    assert result["routing"][0]["status"] == "routed"
    assert main(["inbox", "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_log_sync_imports_once_without_state_or_pending_scan(connection, tmp_path):
    root = marked(tmp_path / "Alpha")
    other = marked(tmp_path / "Beta")
    (root / "PROJECT_LOG.md").write_text("## 2026-09-01 — First\nLine one\n## 2026-09-01 — Second\nLine two\n", encoding="utf-8")
    (other / "PROJECT_LOG.md").write_text("## 2026-09-01 — Hidden\nNot admitted\n", encoding="utf-8")
    admitted = admit_project(connection, path=str(root), domain="Any Domain", reason="ongoing")
    ingest_event(connection, {
        "event_id": str(uuid4()), "project_id": admitted["project_id"],
        "event_type": "state_change", "importance": "Routine",
        "epistemic_status": "Observed", "context": "Project is active",
        "what_changed": "Project became active", "new_state": "Active",
        "observed_at": "2026-08-31T00:00:00+00:00",
    })
    sync_projects(connection, [(tmp_path, "Any Domain")])
    first = sync_project_logs(connection)
    assert len(first) == 1 and first[0]["project_id"] == admitted["project_id"]
    assert sync_project_logs(connection) == []
    row = connection.execute("SELECT what_changed, context, new_state, task_key FROM events WHERE event_type = 'project_log_update'").fetchone()
    assert row["what_changed"] == "2 project updates recorded"
    assert "midnight UTC is an ordering convention" in row["context"]
    assert row["new_state"] is None and row["task_key"] == "project-log:PROJECT_LOG.md"
    assert connection.execute("SELECT count(*) FROM state_conflicts").fetchone()[0] == 0


def test_inbox_routes_only_unique_owner_and_preserves_payload(connection, tmp_path):
    first = admit_project(connection, path=str(marked(tmp_path / "Alpha")), domain="Any", reason="active")
    second = admit_project(connection, path=str(marked(tmp_path / "Beta")), domain="Any", reason="active")
    source_id = str(uuid4())
    ingest_event(connection, {
        "event_id": str(uuid4()), "project_id": first["project_id"],
        "event_type": "state_change", "importance": "Routine",
        "epistemic_status": "Observed", "context": "Project is active",
        "what_changed": "Project became active", "new_state": "Active",
        "observed_at": "2026-08-31T00:00:00+00:00",
    })
    source = create_inbox_item(connection, title="Alpha report", note=f"Project: Alpha\nOriginal detail", inbox_id=source_id, created_at="2026-09-01T12:00:00+00:00")
    mixed = create_inbox_item(connection, title="Alpha and Beta report", note="Shared work")
    assert route_inbox_items(connection, dry_run=True)[0]["status"] == "would_route"
    receipts = route_inbox_items(connection)
    assert [receipt["status"] for receipt in receipts] == ["routed", "pending"]
    assert route_inbox_items(connection) == [{"inbox_id": mixed["id"], "status": "pending", "project_id": None, "reason": "shared_work"}]
    event = connection.execute("SELECT project_id, context, observed_at, new_state, task_key FROM events WHERE event_type = 'inbox_import'").fetchone()
    assert event["project_id"] == first["project_id"]
    assert json.loads(event["context"])["note"] == source["note"]
    assert event["observed_at"] == source["created_at"] and event["new_state"] is None
    assert event["task_key"] == "inbox-import:" + source["id"]
    assert connection.execute("SELECT count(*) FROM state_conflicts").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM todos").fetchone()[0] == 0
    linked = create_inbox_item(connection, title="Follow-up", note=f"Source {source_id}")
    assert route_inbox_items(connection, inbox_id=linked["id"])[0]["project_id"] == first["project_id"]
    unresolved = create_inbox_item(connection, title="Follow-up", note=f"Source {source_id} and {uuid4()}")
    assert route_inbox_items(connection, inbox_id=unresolved["id"])[0]["reason"] == "unassigned_inbox_reference"
    assert second["project_id"] != first["project_id"]


def test_inbox_uses_deepest_root_and_requires_qualified_names(connection, tmp_path):
    parent_root = marked(tmp_path / "Parent")
    child_root = marked(parent_root / "Child")
    other_root = marked(tmp_path / "Other")
    parent = admit_project(connection, path=str(parent_root), domain="Any", reason="active")
    child = admit_project(connection, path=str(child_root), domain="Any", reason="active")
    other = admit_project(connection, path=str(other_root), domain="Any", reason="active")
    common = create_inbox_item(connection, title="Child comparison")
    assert route_inbox_items(connection, inbox_id=common["id"])[0]["status"] == "pending"
    nested = create_inbox_item(connection, title="Nested result", note=f"Source at {child_root}/analysis.txt")
    assert route_inbox_items(connection, inbox_id=nested["id"])[0]["project_id"] == child["project_id"]
    mixed = create_inbox_item(connection, title="Multiple roots", note=f"See {parent_root} and {other_root}")
    assert route_inbox_items(connection, inbox_id=mixed["id"])[0]["reason"] == "multiple_project_references"
    qualified = create_inbox_item(connection, title="Project: Child result")
    assert route_inbox_items(connection, inbox_id=qualified["id"])[0]["project_id"] == child["project_id"]
    assert parent["project_id"] != other["project_id"]


def test_inbox_path_requires_lexical_left_boundary(connection, tmp_path):
    root = marked(tmp_path / "study")
    project = admit_project(connection, path=str(root), domain="Any", reason="active")
    embedded = create_inbox_item(connection, title="Result", note=f"See /shared{root}/analysis.txt")
    assert route_inbox_items(connection, inbox_id=embedded["id"])[0]["status"] == "pending"
    exact = create_inbox_item(connection, title="Result", note=f"See ({root}/analysis.txt)")
    assert route_inbox_items(connection, inbox_id=exact["id"])[0]["project_id"] == project["project_id"]
