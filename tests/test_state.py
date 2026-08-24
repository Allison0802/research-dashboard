from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from research_dashboard import state
from research_dashboard.db import init_db
from research_dashboard.events import STATE_CONFLICT, ingest_event
from research_dashboard.executions import register_execution
from research_dashboard.planning import create_roadmap_item, create_todo
from research_dashboard.registry import add_project
from research_dashboard.registry import set_governing_plan
from research_dashboard.reviews import mark_reviewed
from research_dashboard.reviews import create_named_checkpoint
from research_dashboard.risks import accept_risk, open_or_update_risk, resolve_risk
from research_dashboard.settings import Settings
from research_dashboard.state import (
    _local_day_bounds,
    _next_work,
    _open_conflicts,
    _workstream_states,
    changes_since_monday,
    changes_since_checkpoint,
    changes_since_last_review,
    completed_this_week,
    current_task_state,
    needs_attention,
    portfolio_query,
    projects_waiting_on_me,
    project_read_model,
    project_summary,
    project_timeline,
    recently_completed,
    what_became_blocked_today,
)


PROJECT_ID = "project-1"
EVENT_IDS = [
    "123e4567-e89b-42d3-a456-426614174100",
    "123e4567-e89b-42d3-a456-426614174101",
    "123e4567-e89b-42d3-a456-426614174102",
    "123e4567-e89b-42d3-a456-426614174103",
    "123e4567-e89b-42d3-a456-426614174104",
    "123e4567-e89b-42d3-a456-426614174105",
    "123e4567-e89b-42d3-a456-426614174106",
    "123e4567-e89b-42d3-a456-426614174107",
    "123e4567-e89b-42d3-a456-426614174108",
    "123e4567-e89b-42d3-a456-426614174109",
    "123e4567-e89b-42d3-a456-426614174110",
    "123e4567-e89b-42d3-a456-426614174111",
    "123e4567-e89b-42d3-a456-426614174112",
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


def event_payload(event_id, **overrides):
    payload = {
        "event_id": event_id,
        "project_id": PROJECT_ID,
        "event_type": "state_change",
        "previous_state": "Waiting",
        "new_state": "Active",
        "importance": "Routine change",
        "epistemic_status": "Observed",
        "context": "The project was reviewed.",
        "what_changed": "The project state changed.",
        "observed_at": datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        "evidence": [
            {
                "evidence_type": "repository",
                "locator": "tests/fixtures/project/README.md",
                "authority": 1,
            }
        ],
    }
    payload.update(overrides)
    return payload


def add_event(connection, index, **overrides):
    return ingest_event(
        connection,
        event_payload(EVENT_IDS[index], **overrides),
    )


def test_changes_since_sequence_and_project_timeline_are_deterministic(connection):
    add_event(connection, 0, task_key="task-1")
    add_event(
        connection,
        1,
        task_key="task-1",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
    )
    add_event(
        connection,
        2,
        task_key="task-2",
        previous_state=None,
        new_state=None,
        event_type="note",
    )

    assert [row["sequence"] for row in changes_since_checkpoint(connection, 1)] == [
        2,
        3,
    ]
    assert [row["sequence"] for row in project_timeline(connection, PROJECT_ID)] == [
        1,
        2,
        3,
    ]


def test_derived_event_views_include_agent_provenance(connection):
    add_event(
        connection,
        0,
        source_agent="example-agent",
        source_session="session-001",
    )

    timeline_event = project_timeline(connection, PROJECT_ID)[0]
    change_event = changes_since_checkpoint(connection, 0)[0]

    for event in (timeline_event, change_event):
        assert event["source_agent"] == "example-agent"
        assert event["source_session"] == "session-001"


def test_project_read_model_includes_only_its_backend_neutral_executions(
    connection,
):
    register_execution(
        connection,
        {
            "execution_id": "example-execution",
            "backend": "example",
            "external_id": "example-001",
            "current_state": "RUNNING",
            "project_id": PROJECT_ID,
        },
    )
    add_project(
        connection,
        {"project_id": "other-project", "name": "Other", "domain": "Other"},
    )
    register_execution(
        connection,
        {
            "execution_id": "other-execution",
            "backend": "other",
            "external_id": "other-001",
            "project_id": "other-project",
        },
    )

    executions = project_read_model(connection, PROJECT_ID)["executions"]

    assert [(item["backend"], item["current_state"]) for item in executions] == [
        ("example", "RUNNING")
    ]


def test_needs_attention_orders_risks_and_blockers_first(connection):
    add_event(connection, 0, task_key="routine")
    add_event(
        connection,
        1,
        task_key="operational",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Operational risk",
        risk_type="Operational",
    )
    add_event(
        connection,
        2,
        task_key="research",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Research risk",
        risk_type="Research",
    )
    add_event(
        connection,
        3,
        task_key="blocker",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Critical blocker",
        risk_type="Research",
    )

    attention = needs_attention(connection, PROJECT_ID)

    assert [row["importance"] for row in attention] == [
        "Critical blocker",
        "Research risk",
        "Operational risk",
    ]


def test_review_changes_and_attention_redact_actions_for_current_conflicts(
    connection,
):
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

    changes = changes_since_last_review(connection)
    attention = needs_attention(connection)

    assert [item["task_key"] for item in changes] == [
        "conflicted-task",
        "conflicted-task",
    ]
    assert all(item["next_action"] is None for item in changes)
    assert changes[0]["importance"] == "Critical blocker"
    assert changes[0]["evidence_availability"] == "available"
    assert current_task_state(
        connection, PROJECT_ID, "conflicted-task", "analysis"
    )["current_state"] == STATE_CONFLICT
    assert [item["task_key"] for item in attention] == ["conflicted-task"]
    assert attention[0]["next_action"] is None
    assert attention[0]["what_changed"] == "The analysis state conflicts."


def test_changes_since_checkpoint_redacts_actions_for_current_conflicts(
    connection,
):
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
        epistemic_status="Derived",
    )

    changes = changes_since_checkpoint(connection, 0)

    assert [item["sequence"] for item in changes] == [2, 1]
    assert all(item["next_action"] is None for item in changes)
    assert changes[0]["new_state"] == "Paused"
    assert changes[0]["evidence"]
    assert changes[0]["epistemic_status"] == "Derived"


def test_global_checkpoint_changes_redact_only_matching_project_conflicts(
    connection,
):
    add_project(
        connection,
        {"project_id": "project-2", "name": "Project 2", "domain": "Research"},
    )
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="same-task-key",
        next_action="Run the original analysis.",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="same-task-key",
        previous_state="Waiting",
        new_state="Paused",
        next_action="Use the conflicting action.",
        importance="Critical blocker",
    )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="project-2",
            workstream="analysis",
            task_key="same-task-key",
            next_action="Run the supported analysis.",
        ),
    )

    changes = changes_since_checkpoint(connection, 0)

    project_two_change = next(
        item for item in changes if item["project_id"] == "project-2"
    )
    assert project_two_change["next_action"] == "Run the supported analysis."


def test_project_scoped_changes_filter_conflicts_by_project_id(connection):
    add_project(
        connection,
        {"project_id": "project-2", "name": "Project 2", "domain": "Research"},
    )
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="project-one-task",
        next_action="Run the project one analysis.",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="project-one-task",
        previous_state="Waiting",
        new_state="Paused",
        next_action="Use the conflicting project one action.",
        importance="Critical blocker",
    )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="project-2",
            workstream="analysis",
            task_key="project-two-task",
            next_action="Run the project two analysis.",
        ),
    )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="project-2",
            workstream="analysis",
            task_key="project-two-task",
            previous_state="Waiting",
            new_state="Paused",
            next_action="Use the conflicting project two action.",
            importance="Critical blocker",
        ),
    )

    statements = []
    connection.set_trace_callback(statements.append)
    try:
        changes = changes_since_checkpoint(connection, 0, PROJECT_ID)
    finally:
        connection.set_trace_callback(None)

    conflict_queries = [
        statement
        for statement in statements
        if "FROM state_conflicts" in statement
    ]
    assert len(conflict_queries) == 1
    assert "project_id = 'project-1'" in conflict_queries[0]
    assert "project-2" not in conflict_queries[0]
    assert changes[0]["next_action"] is None


def test_project_timeline_redacts_conflicted_actions_and_retains_event_details(
    connection,
):
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
        epistemic_status="Derived",
    )
    add_event(
        connection,
        2,
        task_key="supported-task",
        next_action="Run the supported analysis.",
    )

    timeline = project_timeline(connection, PROJECT_ID)
    conflicted = [
        item for item in timeline if item["task_key"] == "conflicted-task"
    ]
    supported = [
        item for item in timeline if item["task_key"] == "supported-task"
    ]

    assert len(conflicted) == 2
    assert all(item["next_action"] is None for item in conflicted)
    assert conflicted[1]["new_state"] == "Paused"
    assert conflicted[1]["evidence"]
    assert conflicted[1]["epistemic_status"] == "Derived"
    assert supported[0]["next_action"] == "Run the supported analysis."


def test_changes_are_priority_ordered_with_stable_sequence_ties(connection):
    priorities = [
        "Routine change",
        "Completed milestone",
        "Important result change",
        "Decision needed",
        "Operational risk",
        "Research risk",
        "Critical blocker",
    ]
    for index, importance in enumerate(priorities):
        values = {
            "task_key": f"priority-{index}",
            "previous_state": None,
            "new_state": None,
            "event_type": "note",
            "importance": importance,
        }
        if "risk" in importance:
            values["event_type"] = "risk"
            values["risk_type"] = (
                "Research" if importance == "Research risk" else "Operational"
            )
        add_event(connection, index, **values)

    changes = changes_since_checkpoint(connection, 0)

    assert [row["importance"] for row in changes] == list(reversed(priorities))


def test_current_task_state_keeps_conflict_visible(connection):
    add_event(connection, 0, task_key="task-1")
    add_event(
        connection,
        1,
        task_key="task-1",
        previous_state="Waiting",
        new_state="Paused",
        epistemic_status="Derived",
    )

    state = current_task_state(connection, PROJECT_ID, "task-1")

    assert state["current_state"] == STATE_CONFLICT
    assert state["conflict"]["task_key"] == "task-1"
    assert state["latest_event"]["epistemic_status"] == "Derived"


def test_current_task_state_redacts_actions_for_conflict_without_losing_evidence(
    connection,
):
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
        epistemic_status="Derived",
        importance="Critical blocker",
    )

    state = current_task_state(connection, PROJECT_ID, "conflicted-task", "analysis")

    assert state["current_state"] == STATE_CONFLICT
    assert state["conflict"]["task_key"] == "conflicted-task"
    assert state["evidence_availability"] == "available"
    assert state["latest_event"]["next_action"] is None
    assert state["latest_event"]["evidence"]
    assert state["state_event"]["next_action"] is None
    assert state["state_event"]["evidence_availability"] == "available"


def test_project_read_model_does_not_offer_next_work_for_conflicted_task(connection):
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
    )

    model = project_read_model(connection, PROJECT_ID)

    assert model["next_work"] == []
    assert model["next_work_uncertainty"] == []
    assert model["workstream_states"][0]["current_state"] == STATE_CONFLICT
    assert model["workstream_states"][0]["epistemic_status"] == "Observed"
    assert model["workstream_states"][0]["latest_event"]["next_action"] is None
    assert model["workstream_states"][0]["state_event"]["next_action"] is None
    assert all(
        event["next_action"] is None
        for event in model["timeline"]
        if event["task_key"] == "conflicted-task"
    )
    assert [item["task_key"] for item in model["blockers"]] == [
        "conflicted-task"
    ]
    assert model["blockers"][0]["next_action"] is None
    assert model["evidence_records"][1]["evidence"]


def test_next_work_checks_current_state_before_actionable_or_uncertain_entries(
    connection,
):
    add_event(
        connection,
        0,
        task_key="active-task",
        next_action="Run the supported analysis.",
    )
    add_event(
        connection,
        1,
        task_key="unconfirmed-completed-task",
        new_state="Completed",
        next_action="Do not show this unconfirmed completion.",
        evidence=[],
    )
    add_event(
        connection,
        2,
        task_key="unavailable-task",
        next_action="Do not show this unavailable action.",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-123",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )
    add_event(
        connection,
        3,
        workstream="analysis",
        task_key="conflicted-task",
        next_action="Run the first analysis.",
    )
    add_event(
        connection,
        4,
        workstream="analysis",
        task_key="conflicted-task",
        previous_state="Waiting",
        new_state="Paused",
        next_action="Use the conflicting action.",
        importance="Critical blocker",
    )

    timeline = project_timeline(connection, PROJECT_ID)
    states = _workstream_states(timeline, _open_conflicts(connection, PROJECT_ID))
    next_work, uncertainty = _next_work(timeline, states)

    assert [item["task_key"] for item in next_work] == ["active-task"]
    assert uncertainty == []


def test_project_read_model_excludes_conflict_from_current_work_but_keeps_blocker(
    connection,
):
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
    )

    model = project_read_model(connection, PROJECT_ID)

    assert model["current_work"] == []
    assert [item["task_key"] for item in model["blockers"]] == [
        "conflicted-task"
    ]


def test_unavailable_evidence_does_not_erase_last_confirmed_state(connection):
    add_event(connection, 0, task_key="task-1")
    add_event(
        connection,
        1,
        task_key="task-1",
        previous_state="Active",
        new_state="Completed",
        epistemic_status="Inferred",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-123",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )

    state = current_task_state(connection, PROJECT_ID, "task-1")

    assert state["current_state"] == "Active"
    assert state["state_event"]["sequence"] == 1
    assert state["latest_event"]["new_state"] == "Completed"
    assert state["latest_event"]["epistemic_status"] == "Inferred"
    assert state["latest_event"]["evidence_availability"] == "unavailable"


def test_unavailable_only_state_has_no_confirmed_state_or_completion(connection):
    add_event(
        connection,
        0,
        task_key="task-1",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        epistemic_status="Inferred",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-123",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )

    state = current_task_state(connection, PROJECT_ID, "task-1")

    assert state["current_state"] is None
    assert state["state_event"] is None
    assert state["latest_event"]["evidence_availability"] == "unavailable"
    assert recently_completed(connection, PROJECT_ID) == []


def test_recently_completed_is_in_sequence_order_not_attention_priority(connection):
    add_event(
        connection,
        0,
        task_key="task-1",
        new_state="Completed",
        importance="Routine change",
    )
    add_event(
        connection,
        1,
        task_key="task-2",
        new_state="Completed",
        importance="Critical blocker",
    )

    completed = recently_completed(connection, PROJECT_ID)

    assert [row["sequence"] for row in completed] == [1, 2]


def test_recently_completed_and_project_summary_expose_derived_rows(connection):
    add_event(connection, 0, task_key="task-1")
    add_event(
        connection,
        1,
        task_key="task-1",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
    )
    add_event(
        connection,
        2,
        task_key="task-2",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Research risk",
        risk_type="Research",
    )

    completed = recently_completed(connection, PROJECT_ID)
    summary = project_summary(connection, PROJECT_ID)

    assert [row["task_key"] for row in completed] == ["task-1"]
    assert summary["project"]["project_id"] == PROJECT_ID
    assert summary["event_count"] == 3
    assert summary["open_conflict_count"] == 0
    assert summary["attention_count"] == 1


def test_completion_views_require_completed_current_task_state(connection):
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="completed-task",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="reopened-task",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
    )
    add_event(
        connection,
        2,
        workstream="analysis",
        task_key="reopened-task",
        previous_state="Completed",
        new_state="Active",
        next_action="Continue the reopened task.",
    )
    add_event(
        connection,
        3,
        workstream="analysis",
        task_key="conflicted-task",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
    )
    add_event(
        connection,
        4,
        workstream="analysis",
        task_key="conflicted-task",
        previous_state="Active",
        new_state="Paused",
        importance="Critical blocker",
    )

    completed = recently_completed(connection, PROJECT_ID)
    model = project_read_model(connection, PROJECT_ID)

    assert [row["task_key"] for row in completed] == ["completed-task"]
    assert [row["task_key"] for row in model["completed"]] == ["completed-task"]
    assert [row["task_key"] for row in model["recently_completed"]] == [
        "completed-task"
    ]
    assert model["workstream_states"][1]["current_state"] == STATE_CONFLICT
    assert [row["task_key"] for row in model["blockers"]] == ["conflicted-task"]


def test_project_read_model_orders_scientific_state_and_reports_plan_progress(
    connection, tmp_path
):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "\n".join(
            [
                *["- [x] completed step" for _ in range(11)],
                *["- [ ] pending step" for _ in range(3)],
            ]
        ),
        encoding="utf-8",
    )
    set_governing_plan(connection, PROJECT_ID, plan, plan_id="plan-1")
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="fit-model",
        new_state="Active",
        next_action="Run the sensitivity analysis.",
        what_changed="Model fitting is underway.",
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="primary-results",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        what_changed="Primary results were validated.",
    )
    add_event(
        connection,
        2,
        workstream="analysis",
        task_key="blocked-review",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Critical blocker",
        risk_type="Research",
        what_changed="The review is blocked by missing context.",
    )
    add_event(
        connection,
        3,
        workstream="operations",
        task_key="data-refresh",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Operational risk",
        risk_type="Operational",
        what_changed="The data refresh is delayed.",
    )

    model = project_read_model(connection, PROJECT_ID)

    assert model is not None
    assert [item["task_key"] for item in model["completed"]] == ["primary-results"]
    assert [item["task_key"] for item in model["current_work"]] == ["fit-model"]
    assert model["next_work"][0]["next_action"] == "Run the sensitivity analysis."
    assert [item["task_key"] for item in model["blockers"]] == ["blocked-review"]
    assert [item["task_key"] for item in model["risks"]] == ["data-refresh"]
    assert model["workstream_states"][0]["workstream"] == "analysis"
    assert [item["sequence"] for item in model["timeline"]] == [1, 2, 3, 4]
    assert model["governing_plan"]["plan_id"] == "plan-1"
    assert model["context_coverage"] == "Context coverage incomplete"
    assert model["evidence_availability"] == "available"
    assert model["plan_progress"]["label"] == "Plan execution progress: 11 / 14 tasks"


def test_project_read_model_derives_project_status_from_latest_confirmed_project_event(
    connection,
):
    add_event(
        connection,
        0,
        task_key=None,
        workstream=None,
        previous_state="Active",
        new_state="Waiting",
        importance="Routine change",
        what_changed="The project is waiting on an external dependency.",
    )
    add_event(
        connection,
        1,
        task_key=None,
        workstream=None,
        previous_state="Waiting",
        new_state="Paused",
        importance="Routine change",
        what_changed="The unconfirmed project pause was reported.",
        evidence=[],
    )
    add_event(
        connection,
        2,
        task_key=None,
        workstream=None,
        previous_state="Paused",
        new_state="Completed",
        importance="Routine change",
        what_changed="The unavailable project completion was reported.",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "project-status-unavailable",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )

    model = project_read_model(connection, PROJECT_ID)

    assert model["status"] == "Waiting"
    assert "current_stage" not in model


def test_portfolio_read_model_selects_focus_result_decision_blocker_next_action_and_freshness(
    connection,
):
    connection.execute(
        "UPDATE projects SET update_horizon_minutes = ? WHERE project_id = ?",
        (30, PROJECT_ID),
    )
    # Active task becomes current focus.
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="sensitivity-model",
        previous_state="Waiting",
        new_state="Active",
        importance="Routine change",
        what_changed="Estimate the sensitivity model.",
        observed_at=datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc),
    )
    # A later routine event must not displace an earlier Important result change.
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="primary-estimate",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Important result change",
        what_changed="The primary estimate was validated.",
        observed_at=datetime(2026, 8, 20, 14, 10, tzinfo=timezone.utc),
    )
    # Decision needed must appear under needs_me.
    add_event(
        connection,
        2,
        workstream="analysis",
        task_key="primary-specification",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        next_action="Choose the primary specification.",
        what_changed="Choose the primary specification.",
        observed_at=datetime(2026, 8, 20, 14, 20, tzinfo=timezone.utc),
    )
    # Research risk without Decision needed must appear under watch.
    add_event(
        connection,
        3,
        workstream="data",
        task_key="external-data",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        what_changed="External data are delayed.",
        observed_at=datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        4,
        workstream="analysis",
        task_key="primary-estimate",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Routine change",
        what_changed="A routine validation note was added.",
        observed_at=datetime(2026, 8, 20, 14, 40, tzinfo=timezone.utc),
    )

    overview = state.portfolio_read_model(
        connection,
        now=datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc),
    )

    summary = overview["projects"][0]
    assert summary["current_focus"]["what_changed"] == "Estimate the sensitivity model."
    assert summary["latest_confirmed_result"]["what_changed"] == (
        "The primary estimate was validated."
    )
    assert summary["decision_needed"]["what_changed"] == (
        "Choose the primary specification."
    )
    assert summary["primary_blocker"]["what_changed"] == "External data are delayed."
    assert summary["immediate_next_action"] == "Choose the primary specification."
    assert summary["freshness"]["stale"] is True
    assert [item["what_changed"] for item in overview["needs_me"]] == [
        "Choose the primary specification."
    ]
    assert [item["what_changed"] for item in overview["watch"]] == [
        "External data are delayed."
    ]


def test_portfolio_read_model_compact_now_prefers_latest_waiting_task(
    connection,
):
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="active-task",
        previous_state="Waiting",
        new_state="Active",
        what_changed="Fit the primary model.",
    )
    add_event(
        connection,
        1,
        workstream="data",
        task_key="earlier-waiting-task",
        previous_state="Active",
        new_state="Waiting",
        what_changed="Wait for the first external extract.",
    )
    add_event(
        connection,
        2,
        workstream="data",
        task_key="later-waiting-task",
        previous_state="Active",
        new_state="Waiting",
        what_changed="Wait for the corrected external extract.",
    )

    summary = state.portfolio_read_model(connection)["projects"][0]

    assert summary["current_focus"]["what_changed"] == "Fit the primary model."
    assert summary["now"]["what_changed"] == "Wait for the corrected external extract."
    assert summary["now_label"] == "Wait for the corrected external extract."


def test_evidence_correction_resolves_conflict_and_restores_waiting_now(connection):
    add_event(
        connection,
        0,
        workstream="sensitivity",
        task_key="recovery",
        previous_state="Waiting",
        new_state="Active",
        what_changed="Start the sensitivity recovery.",
    )
    add_event(
        connection,
        1,
        workstream="sensitivity",
        task_key="recovery",
        previous_state="Waiting",
        new_state="Paused",
        what_changed="The recovery was incorrectly marked paused.",
    )

    corrected = ingest_event(
        connection,
        event_payload(
            EVENT_IDS[2],
            workstream="sensitivity",
            task_key="recovery",
            event_type="evidence_correction",
            previous_state="Paused",
            new_state="Waiting",
            corrects_event_id=EVENT_IDS[1],
            what_changed="The recovery remains waiting for remote execution service.",
        ),
    )

    summary = project_read_model(connection, PROJECT_ID)

    assert corrected["current_state"] == "Waiting"
    assert summary["now"]["event_id"] == EVENT_IDS[2]
    assert summary["now_label"] == "The recovery remains waiting for remote execution service."
    assert connection.execute(
        "SELECT resolved_by_event_id FROM state_conflicts"
    ).fetchone()[0] == EVENT_IDS[2]


def test_portfolio_read_model_keeps_plan_ready_brief_separate_from_manual_planning(
    connection, tmp_path
):
    set_governing_plan(connection, PROJECT_ID, tmp_path / "governing-plan.md")

    before_planning = state.portfolio_read_model(connection)["projects"][0]

    create_roadmap_item(
        connection,
        {"project_id": PROJECT_ID, "title": "Fit the primary model"},
    )
    create_todo(
        connection,
        {"project_id": PROJECT_ID, "title": "Review the primary model"},
    )
    after_planning = state.portfolio_read_model(connection)["projects"][0]

    assert before_planning["now"] is None
    assert before_planning["now_label"] == "Plan ready — execution not started"
    assert after_planning["now"] is None
    assert after_planning["now_label"] == "Plan ready — execution not started"
    assert after_planning["planning_summary"] == {
        "roadmap_progress": {"completed": 0, "total": 1},
        "open_todo_count": 1,
    }


def test_portfolio_read_model_keeps_plan_ready_label_for_routine_metadata_only(
    connection, tmp_path
):
    set_governing_plan(connection, PROJECT_ID, tmp_path / "governing-plan.md")
    add_event(
        connection,
        0,
        task_key=None,
        workstream=None,
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Routine change",
        what_changed="The project was registered for portfolio tracking.",
    )

    routine_only = state.portfolio_read_model(connection)["projects"][0]

    assert routine_only["now"] is None
    assert routine_only["now_label"] == "Plan ready — execution not started"

    add_event(
        connection,
        1,
        task_key="analysis-decision",
        workstream="analysis",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Choose the primary analysis population.",
    )

    with_current_decision = state.portfolio_read_model(connection)["projects"][0]

    assert with_current_decision["now"] is None
    assert with_current_decision["now_label"] is None


def test_portfolio_read_model_omits_unconfirmed_attention_from_plan_ready_brief(
    connection, tmp_path
):
    set_governing_plan(connection, PROJECT_ID, tmp_path / "governing-plan.md")
    add_event(
        connection,
        0,
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
        1,
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

    overview = state.portfolio_read_model(connection)
    summary = overview["projects"][0]

    assert summary["decision_needed"] is None
    assert summary["primary_blocker"] is None
    assert summary["blockers"] == []
    assert summary["risks"] == []
    assert summary["attention_count"] == 0
    assert summary["now"] is None
    assert summary["now_label"] == "Plan ready — execution not started"
    assert overview["needs_me"] == []
    assert overview["watch"] == []


def test_portfolio_read_model_freshness_and_brief_ignore_newer_unknown_evidence(
    connection,
):
    connection.execute(
        "UPDATE projects SET update_horizon_minutes = ? WHERE project_id = ?",
        (60, PROJECT_ID),
    )
    last_available = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    add_event(
        connection,
        0,
        task_key="supported-state",
        observed_at=last_available,
        what_changed="The evidence-backed analysis is active.",
    )
    add_event(
        connection,
        1,
        task_key="unknown-update",
        event_type="note",
        previous_state=None,
        new_state=None,
        observed_at=datetime(2026, 8, 20, 9, 55, tzinfo=timezone.utc),
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

    summary = state.portfolio_read_model(
        connection,
        now=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
    )["projects"][0]

    assert summary["freshness"]["last_material_update"] == last_available
    assert summary["freshness"]["stale"] is True
    assert summary["brief_updated_at"] == last_available


def test_latest_confirmed_result_excludes_decisions_and_blockers_and_uses_latest_qualifier(
    connection,
):
    add_event(
        connection,
        0,
        task_key="blocked-analysis",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Critical blocker",
        risk_type="Research",
        what_changed="The analysis is blocked by missing data.",
    )
    add_event(
        connection,
        1,
        task_key="analysis-decision",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Choose the primary analysis population.",
    )

    assert state._latest_confirmed_result(project_timeline(connection, PROJECT_ID)) is None

    add_event(
        connection,
        2,
        task_key="primary-result",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Important result change",
        what_changed="The primary result was validated.",
    )
    add_event(
        connection,
        3,
        task_key="completed-analysis",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        what_changed="The sensitivity analysis was completed.",
    )

    latest_result = state._latest_confirmed_result(project_timeline(connection, PROJECT_ID))

    assert latest_result["what_changed"] == "The sensitivity analysis was completed."


def test_portfolio_read_model_requires_current_decision_and_routes_blockers_to_watch(
    connection,
):
    decision = add_event(
        connection,
        0,
        workstream="analysis",
        task_key="primary-specification",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        next_action="Choose the primary specification.",
        what_changed="Choose the primary specification.",
    )
    decision_event_id = decision["event"]["event_id"]
    assert decision_event_id in {
        item["event_id"] for item in state.portfolio_read_model(connection)["needs_me"]
    }

    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="primary-specification",
        event_type="state_change",
        previous_state=None,
        new_state="Active",
        importance="Routine change",
        what_changed="The primary specification was selected.",
    )
    resolved_decision_overview = state.portfolio_read_model(connection)
    assert decision_event_id not in {
        item["event_id"] for item in resolved_decision_overview["needs_me"]
    }

    blocker = add_event(
        connection,
        2,
        workstream="operations",
        task_key="ordinary-blocker",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Critical blocker",
        risk_type="Operational",
        what_changed="The ordinary blocker needs monitoring.",
    )
    blocker_event_id = blocker["event"]["event_id"]
    blocker_overview = state.portfolio_read_model(connection)

    assert blocker_event_id in {
        item["event_id"] for item in blocker_overview["watch"]
    }
    assert blocker_event_id not in {
        item["event_id"] for item in blocker_overview["needs_me"]
    }


def test_portfolio_read_model_uses_update_horizon_without_creating_heartbeat_events(
    connection,
):
    connection.execute(
        "UPDATE projects SET update_horizon_minutes = ? WHERE project_id = ?",
        (30, PROJECT_ID),
    )
    add_event(
        connection,
        0,
        task_key=None,
        workstream=None,
        previous_state="Waiting",
        new_state="Active",
        importance="Routine change",
        what_changed="The project became active.",
        observed_at=datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc),
    )
    timeline_before = project_timeline(connection, PROJECT_ID)
    timeline_before_ids = [item["event_id"] for item in timeline_before]

    just_inside = state.portfolio_read_model(
        connection,
        now=datetime(2026, 8, 20, 15, 29, 59, tzinfo=timezone.utc),
    )
    just_outside = state.portfolio_read_model(
        connection,
        now=datetime(2026, 8, 20, 15, 30, 1, tzinfo=timezone.utc),
    )
    connection.execute(
        "UPDATE projects SET update_horizon_minutes = ? WHERE project_id = ?",
        (31, PROJECT_ID),
    )
    expanded_horizon = state.portfolio_read_model(
        connection,
        now=datetime(2026, 8, 20, 15, 30, 1, tzinfo=timezone.utc),
    )
    timeline_after = project_timeline(connection, PROJECT_ID)

    assert just_inside["projects"][0]["freshness"]["stale"] is False
    assert just_outside["projects"][0]["freshness"]["stale"] is True
    assert expanded_horizon["projects"][0]["freshness"]["stale"] is False
    assert [item["event_id"] for item in timeline_after] == timeline_before_ids
    assert len(timeline_after) == len(timeline_before)


def test_portfolio_read_model_ranks_presentation_states_without_domain_ordering(
    connection,
):
    add_event(
        connection,
        0,
        task_key="study-design",
        workstream=None,
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Select the next study design.",
    )

    project_states = [
        ("blocked-project", "Quantitative Science", "Active"),
        ("active-project", "Research", "Active"),
        ("waiting-project", "Quantitative Science", "Waiting"),
        ("paused-project", "Research", "Paused"),
        ("completed-project", "Quantitative Science", "Completed"),
    ]
    for project_id, domain, project_state in project_states:
        add_project(
            connection,
            {"project_id": project_id, "name": project_id, "domain": domain},
        )
        ingest_event(
            connection,
            event_payload(
                str(uuid4()),
                project_id=project_id,
                task_key=None,
                workstream=None,
                previous_state=None,
                new_state=project_state,
                importance="Routine change",
                what_changed=f"The project is {project_state.lower()}.",
            ),
        )
        if project_id == "blocked-project":
            ingest_event(
                connection,
                event_payload(
                    str(uuid4()),
                    project_id=project_id,
                    workstream=None,
                    task_key="blocked-task",
                    event_type="risk",
                    previous_state=None,
                    new_state=None,
                    importance="Critical blocker",
                    risk_type="Operational",
                    what_changed="An external dependency is blocked.",
                ),
            )

    overview = state.portfolio_read_model(connection)

    assert [summary["project"]["project_id"] for summary in overview["projects"]] == [
        PROJECT_ID,
        "blocked-project",
        "active-project",
        "waiting-project",
        "paused-project",
        "completed-project",
    ]


def test_portfolio_read_model_excludes_taskless_decisions(connection):
    taskless_decision = add_event(
        connection,
        0,
        task_key=None,
        workstream=None,
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        next_action="Choose the next study design.",
        what_changed="Choose the next study design.",
    )
    add_event(
        connection,
        1,
        task_key="study-design",
        workstream=None,
        previous_state="Waiting",
        new_state="Active",
        importance="Routine change",
        what_changed="Study-design selection is active.",
    )

    overview = state.portfolio_read_model(connection)
    summary = overview["projects"][0]

    assert summary["decision_needed"] is None
    assert summary["immediate_next_action"] is None
    assert taskless_decision["event"]["event_id"] not in {
        item["event_id"] for item in overview["needs_me"]
    }


def test_portfolio_read_model_retains_all_current_task_decisions(connection):
    first = add_event(
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
    second = add_event(
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

    overview = state.portfolio_read_model(connection)
    summary = overview["projects"][0]
    expected_ids = [first["event"]["event_id"], second["event"]["event_id"]]

    assert [item["event_id"] for item in summary["decisions_needed"]] == expected_ids
    assert [item["event_id"] for item in overview["needs_me"]] == expected_ids
    assert summary["decision_needed"]["event_id"] == second["event"]["event_id"]


def test_portfolio_read_model_does_not_rank_project_state_blocked_without_critical_blocker(
    connection,
):
    add_event(
        connection,
        0,
        task_key=None,
        workstream=None,
        event_type="note",
        previous_state=None,
        new_state="Blocked",
        importance="Routine change",
        what_changed="The project-level blocked state was reported.",
    )
    add_project(
        connection,
        {
            "project_id": "active-project",
            "name": "Active Project",
            "domain": "Research",
        },
    )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="active-project",
            task_key=None,
            workstream=None,
            previous_state=None,
            new_state="Active",
            importance="Routine change",
            what_changed="The project is active.",
        ),
    )

    overview = state.portfolio_read_model(connection)

    assert [summary["project"]["project_id"] for summary in overview["projects"]] == [
        "active-project",
        PROJECT_ID,
    ]
    assert overview["projects"][1]["status"] == "Needs classification"


def test_portfolio_read_model_excludes_unknown_or_unavailable_result_evidence(
    connection,
):
    add_event(
        connection,
        0,
        task_key="unknown-result",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Important result change",
        what_changed="The unknown result was recorded.",
        evidence=[],
    )
    add_event(
        connection,
        1,
        task_key="unavailable-result",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Important result change",
        what_changed="The unavailable result was recorded.",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-unavailable",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )

    summary = state.portfolio_read_model(connection)["projects"][0]

    assert summary["latest_confirmed_result"] is None


def test_portfolio_read_model_keeps_summary_and_current_attention_after_review(
    connection,
):
    add_event(
        connection,
        0,
        task_key=None,
        workstream=None,
        previous_state="Waiting",
        new_state="Active",
        importance="Routine change",
        what_changed="The project became active.",
    )
    decision = add_event(
        connection,
        1,
        task_key="decision",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        next_action="Choose the next analysis.",
        what_changed="Choose the next analysis.",
    )
    risk = add_event(
        connection,
        2,
        task_key="risk",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        what_changed="The sensitivity estimate may be unstable.",
    )
    blocker = add_event(
        connection,
        3,
        task_key="blocker",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Critical blocker",
        risk_type="Operational",
        what_changed="An external dependency is blocked.",
    )
    before = state.portfolio_read_model(connection)
    summary_before = deepcopy(before["projects"][0])
    needs_me_ids_before = [item["event_id"] for item in before["needs_me"]]
    watch_ids_before = [item["event_id"] for item in before["watch"]]

    assert decision["event"]["event_id"] in needs_me_ids_before
    assert risk["event"]["event_id"] in watch_ids_before
    assert blocker["event"]["event_id"] in watch_ids_before

    mark_reviewed(connection)

    after = state.portfolio_read_model(connection)
    summary_after = after["projects"][0]

    assert [item["event_id"] for item in after["needs_me"]] == needs_me_ids_before
    assert [item["event_id"] for item in after["watch"]] == watch_ids_before
    assert summary_after["status"] == summary_before["status"]
    assert summary_after["immediate_next_action"] == (
        summary_before["immediate_next_action"]
    )
    assert summary_after == summary_before


def test_portfolio_read_model_keeps_needs_me_and_watch_after_review(connection):
    add_event(
        connection,
        0,
        task_key="decision",
        event_type="note",
        previous_state=None,
        new_state=None,
        importance="Decision needed",
        what_changed="Choose the next analysis.",
    )
    add_event(
        connection,
        1,
        task_key="risk",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        what_changed="The sensitivity estimate may be unstable.",
    )

    mark_reviewed(connection)

    assert state.portfolio_read_model(connection)["needs_me"]
    assert state.portfolio_read_model(connection)["watch"]


def test_portfolio_read_model_resolves_tracked_and_semantic_current_risks(connection):
    # Tracked risk: resolving the risks-table row removes its originating event from Watch.
    tracked_event = add_event(
        connection,
        5,
        workstream="analysis",
        task_key="tracked-risk",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        what_changed="The tracked estimate may be unstable.",
    )
    tracked_event_id = tracked_event["event"]["event_id"]
    risk = open_or_update_risk(
        connection,
        {
            "risk_key": "tracked-estimate-instability",
            "project_id": PROJECT_ID,
            "workstream": "analysis",
            "task_key": "tracked-risk",
            "risk_type": "Research",
            "severity": "High",
            "title": "Tracked estimate instability",
            "current_status": "New",
            "fingerprint": "tracked-estimate-instability-v1",
            "originating_event_id": tracked_event_id,
        },
    )
    resolve_risk(connection, risk["risk_id"])
    assert tracked_event_id not in {
        item["event_id"] for item in state.portfolio_read_model(connection)["watch"]
    }

    # Event-only semantic risk: a later event on the same task identity supersedes it.
    semantic_risk = add_event(
        connection,
        6,
        workstream="analysis",
        task_key="semantic-risk",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Operational risk",
        risk_type="Operational",
        what_changed="The semantic dependency is unavailable.",
    )
    semantic_risk_event_id = semantic_risk["event"]["event_id"]
    add_event(
        connection,
        7,
        workstream="analysis",
        task_key="semantic-risk",
        event_type="state_change",
        previous_state=None,
        new_state="Active",
        importance="Routine change",
        what_changed="The semantic dependency recovered.",
    )
    assert semantic_risk_event_id not in {
        item["event_id"] for item in state.portfolio_read_model(connection)["watch"]
    }


def test_project_read_model_does_not_claim_completion_without_evidence(connection):
    add_event(
        connection,
        0,
        task_key="unverified-task",
        new_state="Completed",
        importance="Completed milestone",
        evidence=[],
    )

    model = project_read_model(connection, PROJECT_ID)

    assert model is not None
    assert model["completed"] == []
    assert model["evidence_availability"] == "unknown"
    assert model["context_coverage"] == "Context coverage incomplete"


def test_project_read_model_keeps_current_risks_after_review(connection):
    add_event(
        connection,
        0,
        task_key="persistent-blocker",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Critical blocker",
        risk_type="Research",
        what_changed="The analysis is blocked by missing context.",
    )
    add_event(
        connection,
        1,
        task_key="persistent-risk",
        previous_state=None,
        new_state=None,
        event_type="risk",
        importance="Research risk",
        risk_type="Research",
        what_changed="The sensitivity analysis may not be stable.",
    )
    mark_reviewed(connection)

    model = project_read_model(connection, PROJECT_ID)

    assert [item["task_key"] for item in model["blockers"]] == [
        "persistent-blocker"
    ]
    assert [item["task_key"] for item in model["risks"]] == ["persistent-risk"]


def test_project_read_model_only_exposes_current_supported_next_actions(connection):
    add_event(
        connection,
        0,
        task_key="active-task",
        next_action="Run the supported analysis.",
    )
    add_event(
        connection,
        1,
        task_key="completed-task",
        next_action="Do not show this completed-task action.",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
    )
    add_event(
        connection,
        2,
        task_key="uncertain-task",
    )
    add_event(
        connection,
        3,
        task_key="uncertain-task",
        previous_state="Active",
        new_state=None,
        event_type="note",
        next_action="Do not show this unconfirmed action.",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-123",
                "authority": 1,
                "availability": "unknown",
            }
        ],
    )

    model = project_read_model(connection, PROJECT_ID)

    assert [item["task_key"] for item in model["next_work"]] == ["active-task"]
    assert model["next_work"][0]["next_action"] == "Run the supported analysis."
    assert [item["task_key"] for item in model["next_work_uncertainty"]] == [
        "uncertain-task"
    ]


def test_portfolio_queries_cover_all_public_generic_questions(connection):
    add_project(
        connection,
        {
            "project_id": "waiting-project",
            "name": "Waiting Project",
            "domain": "Research",
            "lifecycle": "Waiting",
        },
    )
    add_event(
        connection,
        0,
        task_key="blocked-today",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        1,
        task_key="completed-this-week",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        2,
        task_key="completed-last-week",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        4,
        task_key="new-risk",
        event_type="risk",
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        observed_at=datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc),
    )

    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    from research_dashboard import state

    assert [row["task_key"] for row in state.portfolio_query(
        connection, "blocked_today", as_of=as_of
    )] == ["blocked-today"]
    assert [row["task_key"] for row in state.portfolio_query(
        connection, "changed_since_monday", as_of=as_of
    )] == ["blocked-today", "completed-this-week", "new-risk"]
    assert [row["task_key"] for row in state.portfolio_query(
        connection, "completed_this_week", as_of=as_of
    )] == ["completed-this-week"]
    assert [row["project_id"] for row in state.portfolio_query(
        connection, "waiting_on_me"
    )] == ["waiting-project"]
    assert [row["task_key"] for row in state.portfolio_query(
        connection, "new_research_risks", as_of=as_of
    )] == ["new-risk"]


def test_portfolio_query_supports_last_reviewed_and_named_checkpoint_baselines(
    connection,
):
    add_event(
        connection,
        0,
        task_key="before-review",
        observed_at=datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        1,
        task_key="after-review",
        observed_at=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
    )
    mark_reviewed(connection, through_sequence=1)
    create_named_checkpoint(connection, "analysis-start", through_sequence=2)
    add_event(
        connection,
        2,
        task_key="after-named-checkpoint",
        observed_at=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc),
    )

    from research_dashboard import state

    assert [row["sequence"] for row in state.portfolio_query(
        connection,
        "changed_since_monday",
        as_of=datetime(2026, 8, 8, tzinfo=timezone.utc),
        baseline="last_reviewed",
    )] == [2, 3]
    assert [row["sequence"] for row in state.portfolio_query(
        connection,
        "changed_since_monday",
        as_of=datetime(2026, 8, 8, tzinfo=timezone.utc),
        baseline="named_checkpoint",
        checkpoint_name="analysis-start",
    )] == [3]


def test_portfolio_query_names_match_public_generic_contract():
    from research_dashboard.state import PORTFOLIO_QUERY_NAMES

    assert PORTFOLIO_QUERY_NAMES == (
        "blocked_today",
        "changed_since_monday",
        "completed_this_week",
        "waiting_on_me",
        "new_research_risks",
    )


def test_historical_portfolio_query_uses_risk_history_as_of_cutoff(connection):
    cutoff = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    historical_event = event_payload(
        str(uuid4()),
        task_key="historical-risk",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        observed_at=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
    )
    future_created_event = event_payload(
        str(uuid4()),
        task_key="future-created-risk",
        event_type="risk",
        previous_state=None,
        new_state=None,
        importance="Research risk",
        risk_type="Research",
        observed_at=datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc),
    )
    ingest_event(connection, historical_event)
    ingest_event(connection, future_created_event)
    historical_risk = open_or_update_risk(
        connection,
        {
            "risk_key": "historical-risk",
            "project_id": PROJECT_ID,
            "risk_type": "Research",
            "severity": "High",
            "title": "Historical risk",
            "current_status": "New",
            "fingerprint": "historical-risk-v1",
            "originating_event_id": historical_event["event_id"],
            "created_at": datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
        },
    )
    future_risk = open_or_update_risk(
        connection,
        {
            "risk_key": "future-created-risk",
            "project_id": PROJECT_ID,
            "risk_type": "Research",
            "severity": "High",
            "title": "Future-created risk",
            "current_status": "New",
            "fingerprint": "future-created-risk-v1",
            "originating_event_id": future_created_event["event_id"],
            "created_at": datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
        },
    )
    connection.execute(
        "UPDATE risk_transitions SET created_at = ? WHERE risk_id = ? AND to_status = 'New'",
        ("2026-08-07T09:00:00+00:00", historical_risk["risk_id"]),
    )
    accept_risk(connection, historical_risk["risk_id"])
    connection.execute(
        "UPDATE risk_transitions SET created_at = ? WHERE risk_id = ? AND to_status = 'Accepted'",
        ("2026-08-08T09:00:00+00:00", historical_risk["risk_id"]),
    )
    connection.commit()

    risks = portfolio_query(connection, "new_research_risks", as_of=cutoff)

    assert [row["task_key"] for row in risks] == ["historical-risk"]
    assert risks[0]["risk_id"] == historical_risk["risk_id"]
    assert risks[0]["risk_status"] == "New"
    assert future_risk["risk_id"] not in {row.get("risk_id") for row in risks}


def test_completed_historical_task_ignores_conflict_created_after_cutoff(connection):
    cutoff = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="completed-before-conflict",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="completed-before-conflict",
        event_type="note",
        previous_state=None,
        new_state=None,
        observed_at=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc),
    )
    connection.execute(
        "UPDATE state_conflicts SET created_at = ? WHERE project_id = ? "
        "AND workstream = ? AND task_key = ?",
        (
            "2026-08-09T09:00:00+00:00",
            PROJECT_ID,
            "analysis",
            "completed-before-conflict",
        ),
    )
    connection.commit()

    completed_rows = completed_this_week(connection, as_of=cutoff)

    assert [row["task_key"] for row in completed_rows] == [
        "completed-before-conflict"
    ]


def test_week_and_day_queries_use_as_of_timezone_for_boundaries(connection):
    eastern = timezone(timedelta(hours=-5))
    as_of = datetime(2026, 8, 10, 0, 30, tzinfo=eastern)
    add_event(
        connection,
        0,
        task_key="sunday-local",
        previous_state="Waiting",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 9, 23, 45, tzinfo=eastern),
    )
    add_event(
        connection,
        1,
        task_key="monday-local",
        previous_state="Waiting",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 10, 0, 15, tzinfo=eastern),
    )

    assert [row["task_key"] for row in what_became_blocked_today(
        connection, as_of=as_of
    )] == ["monday-local"]
    assert [row["task_key"] for row in changes_since_monday(
        connection, as_of=as_of
    )] == ["monday-local"]


def test_blocked_today_includes_late_event_on_new_york_fall_back_day(connection):
    eastern = ZoneInfo("America/New_York")
    add_event(
        connection,
        0,
        task_key="late-fall-back-blocker",
        previous_state="Waiting",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 11, 1, 23, 30, tzinfo=eastern),
    )

    blocked = what_became_blocked_today(
        connection,
        as_of=datetime(2026, 11, 1, 23, 59, tzinfo=eastern),
    )

    assert [row["task_key"] for row in blocked] == ["late-fall-back-blocker"]


def test_blocked_today_ends_at_next_new_york_midnight_on_spring_forward_day(
    connection,
):
    eastern = ZoneInfo("America/New_York")
    as_of = datetime(2026, 3, 8, 23, 59, tzinfo=eastern)
    add_event(
        connection,
        0,
        task_key="late-spring-forward-blocker",
        previous_state="Waiting",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 3, 8, 23, 30, tzinfo=eastern),
    )

    start, end = _local_day_bounds(as_of)

    assert start == datetime(2026, 3, 8, 5, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 9, 4, tzinfo=timezone.utc)
    assert end - start == timedelta(hours=23)

    blocked = what_became_blocked_today(
        connection,
        as_of=as_of,
    )

    assert [row["task_key"] for row in blocked] == [
        "late-spring-forward-blocker"
    ]


def test_blocked_today_excludes_future_and_duplicate_blocked_transitions(connection):
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    add_event(
        connection,
        0,
        task_key="blocked-twice",
        previous_state="Waiting",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        1,
        task_key="blocked-twice",
        previous_state="Blocked",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        2,
        task_key="blocked-twice",
        previous_state="Blocked",
        new_state="Paused",
        observed_at=datetime(2026, 8, 8, 11, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        3,
        task_key="blocked-twice",
        previous_state="Paused",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 8, 11, 30, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        4,
        task_key="future-blocker",
        previous_state="Waiting",
        new_state="Blocked",
        importance="Critical blocker",
        observed_at=datetime(2026, 8, 8, 13, 0, tzinfo=timezone.utc),
    )

    blocked = what_became_blocked_today(connection, as_of=as_of)

    assert [(row["task_key"], row["sequence"]) for row in blocked] == [
        ("blocked-twice", 1),
        ("blocked-twice", 4),
    ]


def test_completed_this_week_excludes_tasks_reopened_after_completion(connection):
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="still-completed",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="reopened",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        2,
        workstream="analysis",
        task_key="reopened",
        previous_state="Completed",
        new_state="Active",
        observed_at=datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc),
    )

    completed = completed_this_week(
        connection,
        as_of=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
    )

    assert [row["task_key"] for row in completed] == ["still-completed"]


def test_completed_this_week_evaluates_reopens_at_as_of(connection):
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="reopened-after-as-of",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        1,
        workstream="analysis",
        task_key="reopened-after-as-of",
        previous_state="Completed",
        new_state="Active",
        observed_at=datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        2,
        workstream="analysis",
        task_key="reopened-before-as-of",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc),
    )
    add_event(
        connection,
        3,
        workstream="analysis",
        task_key="reopened-before-as-of",
        previous_state="Completed",
        new_state="Active",
        observed_at=datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
    )

    completed = completed_this_week(
        connection,
        as_of=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
    )

    assert [row["task_key"] for row in completed] == ["reopened-after-as-of"]


def test_weekly_queries_treat_date_only_as_of_as_end_of_day(connection):
    add_event(
        connection,
        0,
        workstream="analysis",
        task_key="same-day-completion",
        previous_state="Active",
        new_state="Completed",
        importance="Completed milestone",
        observed_at=datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc),
    )

    assert [row["task_key"] for row in changes_since_monday(
        connection,
        as_of=date(2026, 8, 8),
    )] == ["same-day-completion"]
    assert [row["task_key"] for row in completed_this_week(
        connection,
        as_of=date(2026, 8, 8),
    )] == ["same-day-completion"]
    assert changes_since_monday(
        connection,
        as_of=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
    ) == []


def test_waiting_on_me_rejects_historical_selection_without_lifecycle_history(
    connection,
):
    with pytest.raises(ValueError, match="waiting_on_me"):
        projects_waiting_on_me(
            connection,
            baseline="named_checkpoint",
            checkpoint_name="missing",
        )


def test_portfolio_query_supports_exact_english_aliases(connection):
    aliases = {
        "What became blocked today?": "blocked_today",
        "What changed since Monday?": "changed_since_monday",
        "What did I complete this week?": "completed_this_week",
        "Which projects are waiting on me?": "waiting_on_me",
        "What new research risks appeared?": "new_research_risks",
    }

    for alias, query_name in aliases.items():
        assert portfolio_query(connection, alias) == portfolio_query(
            connection, query_name
        )


def test_portfolio_query_rejects_unknown_query_checkpoint_and_invalid_timestamp(
    connection,
):
    with pytest.raises(ValueError, match="unknown portfolio query"):
        portfolio_query(connection, "not a portfolio query")
    with pytest.raises(ValueError, match="unknown named checkpoint"):
        portfolio_query(
            connection,
            "changed_since_monday",
            baseline="named_checkpoint",
            checkpoint_name="missing",
        )
    with pytest.raises(ValueError, match="timezone"):
        portfolio_query(
            connection,
            "blocked_today",
            as_of=datetime(2026, 8, 8, 12, 0),
        )


def test_project_read_model_exposes_evidence_details_and_status(connection, tmp_path):
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
            },
            {
                "evidence_type": "test report",
                "locator": "tests/test_state.py::test_project",
                "authority": 1,
            },
        ],
    )
    add_event(
        connection,
        1,
        task_key="unavailable-task",
        epistemic_status="Inferred",
        evidence=[
            {
                "evidence_type": "scheduler",
                "locator": "job-123",
                "authority": 1,
                "availability": "unavailable",
            }
        ],
    )

    model = project_read_model(connection, PROJECT_ID)

    assert model["evidence_records"] == [
        {
            "sequence": 1,
            "task_key": "supported-task",
            "what_changed": "The project state changed.",
            "epistemic_status": "Observed",
            "evidence_availability": "available",
            "evidence": [
                {
                    "evidence_id": model["evidence_records"][0]["evidence"][0][
                        "evidence_id"
                    ],
                    "event_id": EVENT_IDS[0],
                    "evidence_type": "repository",
                    "locator": str(results_path),
                    "authority": 1,
                    "observed_at": None,
                    "availability": "available",
                },
                {
                    "evidence_id": model["evidence_records"][0]["evidence"][1][
                        "evidence_id"
                    ],
                    "event_id": EVENT_IDS[0],
                    "evidence_type": "test report",
                    "locator": "tests/test_state.py::test_project",
                    "authority": 1,
                    "observed_at": None,
                    "availability": "available",
                },
            ],
        },
        {
            "sequence": 2,
            "task_key": "unavailable-task",
            "what_changed": "The project state changed.",
            "epistemic_status": "Inferred",
            "evidence_availability": "unavailable",
            "evidence": [
                {
                    "evidence_id": model["evidence_records"][1]["evidence"][0][
                        "evidence_id"
                    ],
                    "event_id": EVENT_IDS[1],
                    "evidence_type": "scheduler",
                    "locator": "job-123",
                    "authority": 1,
                    "observed_at": None,
                    "availability": "unavailable",
                }
            ],
        },
    ]


def _project_read_model_query_count(connection):
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        project_read_model(connection, PROJECT_ID)
    finally:
        connection.set_trace_callback(None)

    read_statements = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "PRAGMA"))
    ]
    return len(read_statements)


def _recently_completed_query_count(connection, project_id=None):
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        recently_completed(connection, project_id)
    finally:
        connection.set_trace_callback(None)

    read_statements = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "PRAGMA"))
    ]
    return len(read_statements)


def test_project_read_model_batches_project_state_queries(connection):
    for index in range(2):
        add_event(connection, index, task_key=f"task-{index}")
    small_timeline_query_count = _project_read_model_query_count(connection)

    for index in range(2, 13):
        add_event(connection, index, task_key=f"task-{index}")
    large_timeline_query_count = _project_read_model_query_count(connection)

    assert large_timeline_query_count == small_timeline_query_count


def test_recently_completed_query_count_does_not_scale_with_timeline_size(
    connection,
):
    for index in range(2):
        add_event(
            connection,
            index,
            task_key=f"completed-task-{index}",
            new_state="Completed",
            importance="Completed milestone",
        )
    small_timeline_query_count = _recently_completed_query_count(connection)

    for index in range(2, 22):
        ingest_event(
            connection,
            event_payload(
                str(uuid4()),
                task_key=f"completed-task-{index}",
                new_state="Completed",
                importance="Completed milestone",
            ),
        )
    large_timeline_query_count = _recently_completed_query_count(connection)

    assert large_timeline_query_count == small_timeline_query_count
    assert [row["task_key"] for row in recently_completed(connection, PROJECT_ID)] == [
        f"completed-task-{index}" for index in range(22)
    ]


def test_recently_completed_global_query_count_does_not_scale_with_projects_or_events(
    connection,
):
    for project_id in ("project-2", "project-3"):
        add_project(
            connection,
            {"project_id": project_id, "name": project_id, "domain": "Research"},
        )

    for project_id in (PROJECT_ID, "project-2"):
        for index in range(2):
            ingest_event(
                connection,
                event_payload(
                    str(uuid4()),
                    project_id=project_id,
                    workstream="analysis",
                    task_key=f"{project_id}-completed-{index}",
                    previous_state="Active",
                    new_state="Completed",
                    importance="Completed milestone",
                ),
            )
    small_query_count = _recently_completed_query_count(connection)

    for project_id in (PROJECT_ID, "project-2", "project-3"):
        for index in range(2, 22):
            ingest_event(
                connection,
                event_payload(
                    str(uuid4()),
                    project_id=project_id,
                    workstream="analysis",
                    task_key=f"{project_id}-completed-{index}",
                    previous_state="Active",
                    new_state="Completed",
                    importance="Completed milestone",
                ),
            )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="project-3",
            workstream="analysis",
            task_key="project-3-conflicted",
            previous_state="Active",
            new_state="Completed",
            importance="Completed milestone",
        ),
    )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="project-3",
            workstream="analysis",
            task_key="project-3-conflicted",
            previous_state="Active",
            new_state="Paused",
            importance="Critical blocker",
        ),
    )
    ingest_event(
        connection,
        event_payload(
            str(uuid4()),
            project_id="project-3",
            workstream="analysis",
            task_key="project-3-unavailable",
            previous_state="Active",
            new_state="Completed",
            importance="Completed milestone",
            evidence=[
                {
                    "evidence_type": "scheduler",
                    "locator": "job-unavailable",
                    "authority": 1,
                    "availability": "unavailable",
                }
            ],
        ),
    )
    large_query_count = _recently_completed_query_count(connection)

    assert large_query_count == small_query_count
    completed = recently_completed(connection)
    assert [row["sequence"] for row in completed] == sorted(
        row["sequence"] for row in completed
    )
    assert {row["project_id"] for row in completed} == {
        PROJECT_ID,
        "project-2",
        "project-3",
    }
    assert "project-3-conflicted" not in {row["task_key"] for row in completed}
    assert "project-3-unavailable" not in {row["task_key"] for row in completed}
