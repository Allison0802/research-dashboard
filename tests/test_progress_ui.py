from uuid import uuid4

from fastapi.testclient import TestClient

from research_dashboard.db import init_db
from research_dashboard.events import ingest_event
from research_dashboard.plan_sync import bootstrap_roadmap_from_governing_plan
from research_dashboard.registry import add_project, set_governing_plan
from research_dashboard.settings import Settings
from research_dashboard.web import create_app


def test_generic_domains_progress_archive_and_unknown_evidence(tmp_path):
    settings = Settings(tmp_path / "runtime")
    connection = init_db(settings)
    for project_id, domain, lifecycle in [("climate", "Climate & Environment", "Active"), ("energy", "Energy", "Active"), ("pending", "Energy", "Needs classification"), ("retired", "Energy", "Archived"), ("done", "Energy", "Completed")]:
        add_project(connection, {"project_id": project_id, "name": project_id, "domain": domain, "lifecycle": lifecycle})
    event = {
        "event_id": str(uuid4()), "project_id": "climate", "task_key": "run-review", "event_type": "session_summary",
        "importance": "Routine change", "epistemic_status": "Observed", "context": "Review remains pending.",
        "what_changed": "Job 1234567 finished; results need review.",
        "progress": {"current_step": "Step 2: validate estimates", "purpose": "Confirm that outputs match the approved model.", "next_step": "Draft tables after validation."},
        "source_agent": "terminal-agent", "observed_at": "2026-10-01T12:00:00+00:00",
        "evidence": [{"evidence_type": "fixture", "locator": "tests/test_progress_ui.py", "authority": 1}],
    }
    ingest_event(connection, event)
    connection.close()
    with TestClient(create_app(settings)) as client:
        home = client.get("/").text
        assert home.count('data-dashboard-item="project"') == 2
        assert 'Climate &amp; Environment' in home and 'Energy' in home
        assert 'domain=Climate%20%26%20Environment' in home
        filtered = client.get("/", params={"domain": "Climate & Environment"}).text
        assert filtered.count('data-dashboard-item="project"') == 1
        assert "Step 2: validate estimates" in filtered
        assert "Confirm that outputs match the approved model." in filtered
        assert "Job 1234567" in filtered  # Original execution details retained.
        archive = client.get("/", params={"archived": "true", "domain": "Energy"}).text
        assert archive.count('data-dashboard-item="project"') == 2
        assert 'id="project-brief-climate"' not in archive
        assert 'id="project-brief-pending"' not in home
        project_page = client.get("/projects/climate").text
        assert f'id="event-{event["event_id"]}"' in project_page
        assert client.post("/api/inbox", json={}).status_code == 404


def test_current_roadmap_leads_stale_recorded_progress(tmp_path):
    settings = Settings(tmp_path / "runtime")
    connection = init_db(settings)
    add_project(connection, {"project_id": "study", "name": "Study", "domain": "Any Domain", "lifecycle": "Active"})
    plan = tmp_path / "plan.md"
    plan.write_text("### Task 1: Prepare inputs\n- [x] Done\n### Task 2: Validate estimates\n- [ ] Check diagnostics\n### Task 3: Deliver report\n- [ ] Draft\n")
    set_governing_plan(connection, "study", plan)
    assert bootstrap_roadmap_from_governing_plan(connection, "study")["status"] == "current"
    ingest_event(connection, {
        "event_id": str(uuid4()), "project_id": "study", "event_type": "session_summary", "importance": "Routine change", "epistemic_status": "Observed",
        "context": "Earlier analysis version.", "what_changed": "Earlier estimates were inspected.", "governing_plan_path": str(tmp_path / "old-plan.md"),
        "progress": {"current_step": "Earlier analysis", "purpose": "Old work", "next_step": "Old report"},
        "observed_at": "2026-10-01T12:00:00+00:00", "evidence": [{"evidence_type": "fixture", "locator": "tests/test_progress_ui.py", "authority": 1}],
    })
    connection.close()
    with TestClient(create_app(settings)) as client:
        home = client.get("/").text
        assert home.index("Current plan step:") < home.index("Earlier recorded step:")
        assert "Validate estimates" in home and "Deliver report" in home
        plan.write_text(plan.read_text() + "\n### Task 4: Review\n- [ ] Review\n")
        stale = client.get("/").text
        assert "<strong>Current plan step:</strong> Validate estimates" not in stale
