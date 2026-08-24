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


def test_homepage_returns_200_and_uses_project_first_screen_order(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="completed-task",
        new_state="Completed",
        importance="Completed milestone",
        what_changed="Completed task",
    )
    response = client.get("/")

    assert response.status_code == 200
    page = response.text
    headings = [
        "Research portfolio",
        "Needs me",
        "Watch",
        "Recent changes",
        "Recently completed",
    ]
    assert [page.index(heading) for heading in headings] == sorted(
        page.index(heading) for heading in headings
    )
    assert "Live HPC" not in page


def test_homepage_orders_changes_by_priority(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="routine",
        what_changed="Routine change",
    )
    add_event(
        connection,
        1,
        task_key="blocker",
        event_type="risk",
        new_state=None,
        importance="Critical blocker",
        risk_type="Research",
        what_changed="Critical blocker",
    )

    page = client.get("/").text
    history_start = page.index("Recent changes")

    assert page.index("Critical blocker", history_start) < page.index(
        "Routine change", history_start
    )


def test_homepage_renders_compact_evidence_safe_project_briefs(
    dashboard, client, tmp_path
):
    _, connection = dashboard
    set_governing_plan(connection, "alpha-project", tmp_path / "alpha-plan.md")
    set_governing_plan(connection, "beta-project", tmp_path / "beta-plan.md")
    connection.execute(
        "UPDATE projects SET update_horizon_minutes = ? WHERE project_id = ?",
        (30, "alpha-project"),
    )
    connection.execute(
        "UPDATE projects SET updated_at = ? WHERE project_id = ?",
        ("2026-08-01T08:30:00+00:00", "beta-project"),
    )
    connection.commit()
    create_roadmap_item(
        connection,
        {"project_id": "alpha-project", "title": "Fit sensitivity model"},
    )
    create_todo(
        connection,
        {"project_id": "alpha-project", "title": "Review sensitivity model"},
    )
    add_event(
        connection,
        0,
        task_key="external-extract",
        previous_state="Active",
        new_state="Waiting",
        what_changed="Wait for the corrected external extract.",
    )
    add_event(
        connection,
        1,
        project_id="beta-project",
        task_key="unconfirmed-decision",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Choose an unsupported primary analysis.",
        evidence=[],
    )
    add_event(
        connection,
        2,
        project_id="beta-project",
        task_key="unavailable-blocker",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Critical blocker",
        risk_type="Research",
        what_changed="An unavailable scheduler report claims a blocker.",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-unavailable",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )
    add_event(
        connection,
        3,
        task_key="unknown-update",
        event_type="note",
        previous_state=None,
        new_state=None,
        observed_at=datetime.now(timezone.utc),
        what_changed="An unknown update claims a newer status.",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-unknown",
                "authority": 1,
                "availability": "unknown",
            }
        ],
    )

    page = client.get("/").text
    portfolio_start = page.index("Research portfolio")
    needs_me_start = page.index("Needs me")
    portfolio = page[portfolio_start:needs_me_start]

    def brief(project_id):
        start = page.index(f'aria-labelledby="project-brief-{project_id}"')
        return page[start : page.index("</article>", start)]

    alpha_brief = brief("alpha-project")
    beta_brief = brief("beta-project")
    gamma_brief = brief("gamma-project")

    assert alpha_brief.count('<span class="status ') == 1
    assert "<dt>Status</dt>" not in portfolio
    assert "Domain:" not in portfolio
    assert "project-links" not in portfolio
    assert "portfolio-domain" not in portfolio
    assert "None recorded" not in portfolio
    assert "No immediate action recorded" not in portfolio
    assert "No current evidence-backed work recorded" not in portfolio
    assert "<strong>Now:</strong> Wait for the corrected external extract." in alpha_brief
    assert 'href="http://testserver/projects/alpha-project#planning"' in alpha_brief
    assert "Roadmap 0/1" in alpha_brief
    assert "1 TODO" in alpha_brief
    assert "Updated 2026-08-07 · status may be stale" in alpha_brief
    assert "Plan ready — execution not started" in beta_brief
    assert "Updated 2026-08-01" in beta_brief
    assert "Choose an unsupported primary analysis." not in beta_brief
    assert "An unavailable scheduler report claims a blocker." not in beta_brief
    assert "project-attention" not in beta_brief
    assert "project-attention" not in gamma_brief
    assert "project-latest" not in gamma_brief
    assert "project-planning-summary" not in gamma_brief


def test_homepage_separates_decisions_from_watch_after_service_review(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="decision-task",
        importance="Decision needed",
        what_changed="Choose the primary analysis population.",
        next_action="Select the analysis population.",
    )
    add_event(
        connection,
        1,
        task_key="risk-task",
        event_type="risk",
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        risk_severity="High",
        what_changed="Unresolved analysis risk",
    )

    mark_reviewed(connection)
    page = client.get("/").text
    needs_me_start = page.index("Needs me")
    watch_start = page.index("Watch")
    changes_start = page.index("Recent changes")
    needs_me = page[needs_me_start:watch_start]
    watch = page[watch_start:changes_start]

    assert "Choose the primary analysis population." in needs_me
    assert "Choose the primary analysis population." not in watch
    assert "Unresolved analysis risk" in watch
    assert "Unresolved analysis risk" not in needs_me


def test_homepage_and_project_route_keep_all_current_task_decisions(
    dashboard, client
):
    _, connection = dashboard
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="analysis-population",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Choose the analysis population.",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="missing-data-method",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Choose the missing-data method.",
    )

    homepage = client.get("/").text
    needs_me_start = homepage.index("Needs me")
    watch_start = homepage.index("Watch")
    needs_me = homepage[needs_me_start:watch_start]
    project_page = client.get("/projects/alpha-project").text
    decisions_start = project_page.index("Decisions, blockers, and risks")
    findings_start = project_page.index("Recent findings")
    decisions = project_page[decisions_start:findings_start]

    for decision in (
        "Choose the analysis population.",
        "Choose the missing-data method.",
    ):
        assert decision in needs_me
        assert decision in decisions


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


def test_homepage_shows_all_projects_and_collapsed_history(dashboard, client):
    _, connection = dashboard
    add_event(
        connection,
        0,
        task_key="completed-task",
        new_state="Completed",
        importance="Completed milestone",
        what_changed="Recently completed work",
    )

    page = client.get("/").text

    assert "Portfolio Alpha" in page
    assert "Portfolio Beta" in page
    assert "Portfolio Gamma" in page
    assert "<details class=\"recent-history\">" in page
    assert "<details class=\"recent-completions\">" in page
    assert '<details class="recent-history" open' not in page
    assert '<details class="recent-completions" open' not in page
    assert "Recently completed" in page


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
