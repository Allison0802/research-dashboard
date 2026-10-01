from datetime import datetime, timezone
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from research_dashboard.db import init_db
from research_dashboard.events import ingest_event
from research_dashboard.executions import register_execution
from research_dashboard.plan_sync import create_proposal_batch
from research_dashboard.planning import create_roadmap_item, create_todo
from research_dashboard.registry import add_project, add_project_root, set_governing_plan
from research_dashboard.reviews import get_review_state, mark_reviewed
from research_dashboard.settings import Settings


EVENT_IDS = [
    "123e4567-e89b-42d3-a456-426614174200",
    "123e4567-e89b-42d3-a456-426614174201",
    "123e4567-e89b-42d3-a456-426614174202",
    "123e4567-e89b-42d3-a456-426614174203",
]


@pytest.fixture
def dashboard(tmp_path):
    settings = Settings(tmp_path / "runtime")
    connection = init_db(settings)
    for project_id, name, domain in (
        ("alpha-project", "Portfolio Alpha", "Research"),
        ("beta-project", "Portfolio Beta", "Quantitative Science"),
        ("gamma-project", "Portfolio Gamma", "Data Engineering"),
    ):
        add_project(
            connection,
            {
                "project_id": project_id,
                "name": name,
                "domain": domain,
                "lifecycle": "Active",
            },
        )
    try:
        yield settings, connection
    finally:
        connection.close()


def add_event(connection, index, **overrides):
    payload = {
        "event_id": EVENT_IDS[index],
        "project_id": "alpha-project",
        "event_type": "state_change",
        "previous_state": "Waiting",
        "new_state": "Active",
        "importance": "Routine change",
        "epistemic_status": "Observed",
        "context": "A recorded dashboard change.",
        "what_changed": "The project state changed.",
        "observed_at": datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        "evidence": [
            {
                "evidence_type": "test fixture",
                "locator": "tests/test_web.py",
                "authority": 1,
            }
        ],
    }
    payload.update(overrides)
    return ingest_event(connection, payload)


def _removed_private_action_labels() -> tuple[str, ...]:
    return tuple(
        " ".join(words)
        for words in (
            ("Investigate", "with", "Codex"),
            ("Ask", "Codex", "to", "fix"),
            ("Reconcile", "project"),
            ("Investigate", "with", "agent"),
            ("Ask", "agent", "to", "fix"),
        )
    )


@pytest.fixture
def client(dashboard):
    from research_dashboard.web import create_app

    settings, _ = dashboard
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_homepage_renders_with_empty_database(tmp_path):
    from research_dashboard.web import create_app

    settings = Settings(tmp_path / "runtime")
    with TestClient(create_app(settings)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "No projects yet" in response.text
    assert "semantic event" in response.text.lower()


def test_homepage_returns_200_and_shows_latest_update_filters(dashboard, client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Project progress" in response.text
    for category in ("all", "needs-me", "blocked", "regular"):
        assert f'data-dashboard-filter="{category}"' in response.text
    assert "Blocked and Watch" not in response.text


def test_homepage_only_shows_latest_project_event(dashboard, client):
    _, connection = dashboard
    add_event(connection, 0, task_key="old", what_changed="Old decision", importance="Decision needed")
    add_event(connection, 1, task_key="new", what_changed="Latest progress")
    page = client.get("/").text
    assert "Latest progress" in page
    assert "Old decision" not in page
    assert page.count('data-dashboard-item="project"') == 3
    assert 'data-dashboard-kinds="needs-me"' not in page


def test_homepage_labels_latest_unverified_update(dashboard, client):
    _, connection = dashboard
    add_event(connection, 0, what_changed="Unverified report", next_action="Unconfirmed action", evidence=[])
    page = client.get("/").text
    assert "Unverified report" in page
    assert "Unverified update" in page
    assert "Unconfirmed action" not in page


def test_homepage_latest_category_survives_review(dashboard, client):
    _, connection = dashboard
    add_event(connection, 0, importance="Decision needed", what_changed="Choose the population")
    mark_reviewed(connection)
    page = client.get("/").text
    assert "Choose the population" in page
    assert 'data-dashboard-kinds="needs-me"' in page


def test_latest_feed_retains_earlier_decisions_on_project_page(dashboard, client):
    _, connection = dashboard
    add_event(connection, 0, task_key="population", importance="Decision needed", what_changed="Choose population")
    add_event(connection, 1, task_key="method", importance="Decision needed", what_changed="Choose method")
    home = client.get("/").text
    project = client.get("/projects/alpha-project").text
    assert "Choose method" in home
    assert "Choose population" not in home
    assert "Choose method" in project and "Choose population" in project


def test_homepage_redacts_actions_for_conflicted_tasks_but_keeps_blocker_visible(
    dashboard, client
):
    _, connection = dashboard
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="conflicted-task",
        next_action="Run the original analysis.",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="conflicted-task",
        previous_state="Waiting",
        new_state="Paused",
        next_action="Use the conflicting action.",
        importance="Critical blocker",
        what_changed="The analysis state conflicts.",
    )

    page = client.get("/").text

    assert "The analysis state conflicts." in page
    assert "Critical blocker" in page
    assert "Run the original analysis." not in page
    assert "Use the conflicting action." not in page


def test_homepage_shows_all_projects_without_historical_feeds(dashboard, client):
    page = client.get("/").text
    for name in ("Portfolio Alpha", "Portfolio Beta", "Portfolio Gamma"):
        assert name in page
    assert "No updates recorded yet." in page
    assert "Recent changes" not in page
    assert "Recently completed" not in page


def test_get_homepage_does_not_mark_reviewed(dashboard, client):
    _, connection = dashboard
    add_event(connection, 0)

    assert get_review_state(connection)["reviewed_through_sequence"] == 0
    assert client.get("/").status_code == 200
    assert get_review_state(connection)["reviewed_through_sequence"] == 0


def test_project_route_renders_project_page_with_auditable_sections(client):
    response = client.get("/projects/alpha-project")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text

    headings = [
        "Project brief",
        "Current workstreams",
        "Decisions, blockers, and risks",
        "Recent findings",
        "History and evidence",
    ]
    assert [page.index(heading) for heading in headings] == sorted(
        page.index(heading) for heading in headings
    )
    assert "Portfolio Alpha" in page
    assert "Research" in page
    assert "Active" in page
    assert "Context coverage incomplete" in page
    assert "Current status:</strong> Active" in page
    assert "Plan execution progress: unavailable" in page
    assert "Evidence availability: unknown" in page
    assert '<details class="history-and-evidence">' in page
    assert '<details class="history-and-evidence" open' not in page
    assert "Research progress:" not in page
    assert "HPC" not in page
    assert "Source:" not in page


def test_project_route_renders_arbitrary_domain_provenance_and_execution(
    dashboard, client
):
    _, connection = dashboard
    add_project(
        connection,
        {
            "project_id": "climate-science-project",
            "name": "Climate Evidence Review",
            "domain": "Climate Science",
            "lifecycle": "Active",
        },
    )
    add_event(
        connection,
        0,
        project_id="climate-science-project",
        workstream="synthesis",
        task_key="review-observations",
        source_agent="example-agent",
        what_changed="Reviewed climate observations.",
    )
    register_execution(
        connection,
        {
            "execution_id": "example-execution",
            "backend": "example",
            "external_id": "example-001",
            "current_state": "RUNNING",
            "project_id": "climate-science-project",
        },
    )

    homepage = client.get("/").text
    page = client.get("/projects/climate-science-project").text

    assert "Climate Evidence Review" in homepage
    assert "Climate Evidence Review" in page
    assert "Climate Science" in page
    assert "Source: example-agent" in page
    assert "Backend: example" in page
    assert "State:</strong> RUNNING" in page
    assert "slurm" not in page.lower()


def test_project_route_returns_404_for_unknown_project(client):
    response = client.get("/projects/unknown-project")

    assert response.status_code == 404


def test_project_route_keeps_risk_visible_after_service_review(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="persistent-blocker",
        event_type="risk",
        new_state=None,
        importance="Critical blocker",
        risk_type="Research",
        what_changed="The analysis is blocked by missing context.",
    )

    mark_reviewed(connection)
    page = client.get("/projects/alpha-project").text
    assert "The analysis is blocked by missing context." in page


def test_project_route_renders_evidence_details_and_uncertainty(
    dashboard, client, tmp_path
):
    _, connection = dashboard
    results_path = tmp_path / "results.csv"
    add_event(
        connection,
        0,
        task_key="supported-task",
        epistemic_status="Observed",
        evidence=[
            {
                "evidence_type": "repository",
                "locator": str(results_path),
                "authority": 1,
            }
        ],
    )
    add_event(
        connection,
        1,
        task_key="uncertain-task",
    )
    add_event(
        connection,
        2,
        task_key="uncertain-task",
        next_action="Do not show this unconfirmed action.",
        previous_state="Active",
        new_state=None,
        event_type="note",
        epistemic_status="Inferred",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-123",
                "authority": 1,
                "availability": "unknown",
            }
        ],
    )

    page = client.get("/projects/alpha-project").text

    assert f"repository: {results_path}" in page
    assert "Epistemic status: Observed" in page
    assert "Evidence status: unknown" in page
    assert "scheduler: job-123" in page
    assert "Next work is uncertain" in page
    assert "Do not show this unconfirmed action." not in page


def test_project_route_shows_conflict_without_conflict_next_action(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="conflicted-task",
        next_action="Run the unsupported analysis.",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="conflicted-task",
        previous_state="Waiting",
        new_state="Paused",
        next_action="Use the conflicting action.",
        importance="Critical blocker",
        what_changed="The analysis state conflicts.",
        epistemic_status="Derived",
    )

    page = client.get("/projects/alpha-project").text

    assert "STATE_CONFLICT" in page
    assert "The analysis state conflicts." in page
    assert "Epistemic status: Derived" in page
    assert "test fixture: tests/test_web.py" in page
    assert "Run the unsupported analysis." not in page
    assert "Use the conflicting action." not in page


def test_risk_placeholder_route_is_read_only(client):
    response = client.get("/risks/risk-1")

    assert response.status_code == 501
    assert response.json() == {
        "status": "unavailable",
        "resource": "risk:risk-1",
        "message": "This dashboard action is reserved for a later task.",
    }


def test_portfolio_query_endpoint_returns_deterministic_results_and_actions(
    dashboard, client
):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="research-risk",
        event_type="risk",
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        observed_at=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
    )

    response = client.get(
        "/portfolio/queries/new_research_risks?as_of=2026-08-08T12:00:00%2B00:00"
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["task_key"] for item in payload["results"]] == [
        "research-risk"
    ]
    assert "Open project" in payload["results"][0]["actions"]
    assert "Accept risk" not in payload["results"][0]["actions"]


def test_weekly_query_date_only_as_of_includes_same_day_event(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="same-day-completion",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc),
    )

    response = client.get(
        "/portfolio/queries/completed_this_week?as_of=2026-08-08"
    )

    assert response.status_code == 200
    assert [item["task_key"] for item in response.json()["results"]] == [
        "same-day-completion"
    ]


def test_context_actions_only_include_actions_supported_by_the_record():
    from research_dashboard import web

    risk = {
        "kind": "event",
        "project_id": "project-1",
        "risk_id": "risk-1",
        "risk_type": "Research",
        "risk_status": "New",
        "event_type": "risk",
    }
    routine = {
        "kind": "event",
        "project_id": "project-1",
        "event_type": "note",
        "importance": "Routine change",
    }
    waiting_project = {
        "kind": "project",
        "project_id": "project-1",
        "lifecycle": "Waiting",
    }
    active_project = {
        "kind": "project",
        "project_id": "project-1",
        "lifecycle": "Active",
    }

    assert web.context_actions(risk) == ["Open project"]
    assert web.context_actions(routine) == ["Open project"]
    assert web.context_actions(routine, baseline="named_checkpoint") == ["Open project"]
    assert web.context_actions(waiting_project) == ["Open project"]
    assert web.context_actions(active_project) == ["Open project"]
    assert not set(web.context_actions(risk)).intersection(
        _removed_private_action_labels()
    )


def test_active_risk_page_omits_private_worker_actions(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="active-risk",
        event_type="risk",
        new_state=None,
        importance="Research risk",
        risk_type="Research",
    )

    page = client.get("/projects/alpha-project").text

    assert all(label not in page for label in _removed_private_action_labels())


def test_portfolio_query_rejects_inappropriate_named_checkpoint_arguments(
    client,
):
    response = client.get(
        "/portfolio/queries/changed_since_monday?baseline=named_checkpoint"
    )

    assert response.status_code == 400
    assert "checkpoint" in response.json()["detail"]


def test_waiting_on_me_rejects_selected_baseline_and_invalid_timestamp(client):
    response = client.get(
        "/portfolio/queries/waiting_on_me?baseline=named-checkpoint&checkpoint=missing"
    )

    assert response.status_code == 400
    assert "waiting_on_me" in response.json()["detail"]

    response = client.get(
        "/portfolio/queries/blocked_today?as_of=2026-08-08T12:00:00"
    )

    assert response.status_code == 400
    assert "timezone" in response.json()["detail"]


def test_portfolio_query_rejects_unknown_query_and_checkpoint(client):
    response = client.get("/portfolio/queries/not-a-query")
    assert response.status_code == 400
    assert "unknown portfolio query" in response.json()["detail"]

    response = client.get(
        "/portfolio/queries/changed_since_monday"
        "?baseline=named-checkpoint&checkpoint=missing"
    )
    assert response.status_code == 400
    assert "unknown named checkpoint" in response.json()["detail"]


def test_project_page_renders_manual_planning_separately_from_evidence(
    dashboard, client, tmp_path
):
    _, connection = dashboard
    set_governing_plan(connection, "alpha-project", tmp_path / "governing-plan.md")
    create_roadmap_item(
        connection,
        {
            "project_id": "alpha-project",
            "title": "Run sensitivity analysis",
            "note": "Primary manuscript analysis",
        },
    )
    create_todo(
        connection,
        {
            "project_id": "alpha-project",
            "title": "Review sensitivity analysis",
            "priority": "High",
        },
    )
    create_todo(
        connection,
        {
            "project_id": "alpha-project",
            "title": "Archive earlier notes",
            "status": "Done",
        },
    )

    page = client.get("/projects/alpha-project").text

    assert page.count('id="planning"') == 1
    assert "Source plan" in page
    assert "Run sensitivity analysis" in page
    assert "Review sensitivity analysis" in page
    assert "Archive earlier notes" in page
    assert '<details class="completed-todos">' in page
    assert '<details class="completed-todos" open' not in page
    assert "Suggested roadmap:" in page


def test_project_page_planning_is_read_only(dashboard, client):
    _, connection = dashboard
    create_roadmap_item(
        connection,
        {
            "project_id": "alpha-project",
            "title": "Read-only roadmap item",
            "status": "In progress",
        },
    )

    page = client.get("/projects/alpha-project").text

    assert "Read-only roadmap item" in page
    assert "data-planning-action" not in page
    assert "data-planning-status" not in page
    assert "planning.js" not in page


def test_project_page_renders_pending_plan_proposals_as_read_only(
    dashboard, client, tmp_path
):
    _, connection = dashboard
    plan_path = tmp_path / "governing-plan.md"
    plan_path.write_text("### Task 1: Read source\n- [ ] Inspect input\n", encoding="utf-8")
    set_governing_plan(connection, "alpha-project", plan_path)
    create_proposal_batch(
        connection,
        {
            "project_id": "alpha-project",
            "source_plan_path": str(plan_path),
            "source_plan_sha256": sha256(plan_path.read_bytes()).hexdigest(),
            "proposals": [
                {"operation": "add", "title": "Prepare changed input"},
                {"operation": "add", "title": "Run changed analysis"},
            ],
        },
    )

    page = client.get("/projects/alpha-project").text

    assert "Source plan" in page
    assert "Plan changed — 2 proposed roadmap updates await CLI review" in page
    assert "Prepare changed input" in page
    assert "Run changed analysis" in page
    assert "data-proposal-index" not in page
    assert "Apply selected updates" not in page
    assert "Reject all" not in page
