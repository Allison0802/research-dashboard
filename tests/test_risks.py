from datetime import datetime, timezone
import json
import sys
from uuid import uuid4

import pytest

from research_dashboard.cli import main
from research_dashboard.db import init_db
from research_dashboard.events import ingest_event
from research_dashboard.registry import add_project
from research_dashboard.risks import (
    accept_risk,
    list_needs_attention,
    open_or_update_risk,
    resolve_risk,
    transition_risk,
)
from research_dashboard.settings import Settings


@pytest.fixture
def connection(tmp_path):
    connection = init_db(Settings(tmp_path / "runtime"))
    add_project(
        connection,
        {"project_id": "project-1", "name": "Project", "domain": "Research"},
    )
    try:
        yield connection
    finally:
        connection.close()


def add_event(connection, event_id=None, project_id="project-1"):
    event_id = event_id or uuid4()
    ingest_event(
        connection,
        {
            "event_id": str(event_id),
            "project_id": project_id,
            "event_type": "risk",
            "importance": "Research risk",
            "risk_type": "Research",
            "risk_severity": "High",
            "epistemic_status": "Observed",
            "context": "A risk was observed.",
            "what_changed": "The risk evidence changed.",
            "observed_at": datetime.now(timezone.utc),
        },
    )
    return event_id


def risk_payload(event_id, **overrides):
    payload = {
        "risk_key": "risk-1",
        "project_id": "project-1",
        "risk_type": "Research",
        "severity": "High",
        "title": "A research risk",
        "current_status": "New",
        "fingerprint": "evidence-v1",
        "originating_event_id": str(event_id),
    }
    payload.update(overrides)
    return payload


def test_open_risk_appends_initial_transition(connection):
    event_id = add_event(connection)

    risk = open_or_update_risk(connection, risk_payload(event_id))

    assert risk["current_status"] == "New"
    transitions = connection.execute(
        "SELECT from_status, to_status FROM risk_transitions WHERE risk_id = ?",
        (risk["risk_id"],),
    ).fetchall()
    assert [(row["from_status"], row["to_status"]) for row in transitions] == [
        (None, "New")
    ]


def test_open_risk_rejects_cross_project_originating_event_without_writes(connection):
    add_project(
        connection,
        {"project_id": "project-2", "name": "Other project", "domain": "Research"},
    )
    event_id = add_event(connection, project_id="project-2")

    with pytest.raises(ValueError, match="originating event.*project-2.*project-1"):
        open_or_update_risk(connection, risk_payload(event_id))

    assert connection.execute("SELECT COUNT(*) FROM risks").fetchone()[0] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM risk_transitions").fetchone()[0] == 0
    )


def test_update_risk_rejects_cross_project_originating_event_without_writes(connection):
    add_project(
        connection,
        {"project_id": "project-2", "name": "Other project", "domain": "Research"},
    )
    original_event = add_event(connection)
    risk = open_or_update_risk(connection, risk_payload(original_event))
    cross_project_event = add_event(connection, project_id="project-2")
    before = connection.execute(
        "SELECT current_status, fingerprint, originating_event_id, updated_at "
        "FROM risks WHERE risk_id = ?",
        (risk["risk_id"],),
    ).fetchone()
    transition_count = connection.execute(
        "SELECT COUNT(*) FROM risk_transitions WHERE risk_id = ?",
        (risk["risk_id"],),
    ).fetchone()[0]

    with pytest.raises(ValueError, match="originating event.*project-2.*project-1"):
        open_or_update_risk(
            connection,
            risk_payload(
                cross_project_event,
                risk_id=risk["risk_id"],
                fingerprint="evidence-v2",
            ),
        )

    after = connection.execute(
        "SELECT current_status, fingerprint, originating_event_id, updated_at "
        "FROM risks WHERE risk_id = ?",
        (risk["risk_id"],),
    ).fetchone()
    assert tuple(after) == tuple(before)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM risk_transitions WHERE risk_id = ?",
            (risk["risk_id"],),
        ).fetchone()[0]
        == transition_count
    )


def test_transition_risk_rejects_cross_project_event_without_writes(connection):
    add_project(
        connection,
        {"project_id": "project-2", "name": "Other project", "domain": "Research"},
    )
    original_event = add_event(connection)
    risk = open_or_update_risk(connection, risk_payload(original_event))
    cross_project_event = add_event(connection, project_id="project-2")
    transition_count = connection.execute(
        "SELECT COUNT(*) FROM risk_transitions WHERE risk_id = ?",
        (risk["risk_id"],),
    ).fetchone()[0]

    with pytest.raises(ValueError, match="event.*project-2.*project-1"):
        transition_risk(
            connection, risk["risk_id"], "Waiting", event_id=cross_project_event
        )

    assert (
        connection.execute(
            "SELECT current_status FROM risks WHERE risk_id = ?",
            (risk["risk_id"],),
        ).fetchone()["current_status"]
        == "New"
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM risk_transitions WHERE risk_id = ?",
            (risk["risk_id"],),
        ).fetchone()[0]
        == transition_count
    )


def test_transition_risk_accepts_same_project_event_and_appends(connection):
    event_id = add_event(connection)
    risk = open_or_update_risk(connection, risk_payload(event_id))

    waiting = transition_risk(connection, risk["risk_id"], "Waiting", event_id=event_id)

    assert waiting["current_status"] == "Waiting"
    transition = connection.execute(
        "SELECT from_status, to_status, event_id FROM risk_transitions "
        "WHERE risk_id = ? ORDER BY rowid DESC LIMIT 1",
        (risk["risk_id"],),
    ).fetchone()
    assert tuple(transition) == ("New", "Waiting", str(event_id))


def test_accepted_and_resolved_risks_leave_needs_attention(connection):
    accepted_event = add_event(connection)
    resolved_event = add_event(connection)
    accepted = open_or_update_risk(
        connection,
        risk_payload(accepted_event, risk_key="accepted", fingerprint="accepted-v1"),
    )
    resolved = open_or_update_risk(
        connection,
        risk_payload(resolved_event, risk_key="resolved", fingerprint="resolved-v1"),
    )

    accept_risk(connection, accepted["risk_id"])
    resolve_risk(connection, resolved["risk_id"])

    assert list_needs_attention(connection) == []


def test_waiting_is_a_distinct_attention_status_not_blocked(connection):
    event_id = add_event(connection)
    risk = open_or_update_risk(connection, risk_payload(event_id))

    waiting = transition_risk(connection, risk["risk_id"], "Waiting")

    assert waiting["current_status"] == "Waiting"
    assert waiting["current_status"] != "Blocked"
    assert [item["risk_id"] for item in list_needs_attention(connection)] == [
        risk["risk_id"]
    ]


def test_accepted_unchanged_fingerprint_is_suppressed_but_new_evidence_reopens(
    connection,
):
    first_event = add_event(connection)
    risk = open_or_update_risk(connection, risk_payload(first_event))
    accept_risk(connection, risk["risk_id"])
    transition_count = connection.execute(
        "SELECT COUNT(*) FROM risk_transitions WHERE risk_id = ?",
        (risk["risk_id"],),
    ).fetchone()[0]

    unchanged = open_or_update_risk(
        connection,
        risk_payload(
            first_event,
            risk_id=risk["risk_id"],
            fingerprint="evidence-v1",
            title="A revised title that is not new evidence",
        ),
    )
    assert unchanged["current_status"] == "Accepted"
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM risk_transitions WHERE risk_id = ?",
            (risk["risk_id"],),
        ).fetchone()[0]
        == transition_count
    )

    second_event = add_event(connection)
    reopened = open_or_update_risk(
        connection,
        risk_payload(
            second_event,
            risk_id=risk["risk_id"],
            fingerprint="evidence-v2",
        ),
    )

    assert reopened["current_status"] == "New"
    transition = connection.execute(
        "SELECT from_status, to_status FROM risk_transitions "
        "WHERE risk_id = ? ORDER BY created_at DESC LIMIT 1",
        (risk["risk_id"],),
    ).fetchone()
    assert (transition["from_status"], transition["to_status"]) == (
        "Accepted",
        "New",
    )


def test_unknown_and_invalid_risk_operations_fail_clearly(connection):
    with pytest.raises(ValueError, match="risk.*not found"):
        transition_risk(connection, "missing-risk", "Waiting")

    event_id = add_event(connection)
    risk = open_or_update_risk(connection, risk_payload(event_id))
    with pytest.raises(ValueError, match="invalid risk status"):
        transition_risk(connection, risk["risk_id"], "Blocked")


def test_risk_cli_transitions_return_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("RESEARCH_DASHBOARD_HOME", str(tmp_path / "runtime"))
    assert main(["init"]) == 0

    from research_dashboard.db import connect_db
    from research_dashboard.settings import load_settings

    connection = connect_db(load_settings())
    try:
        add_project(
            connection,
            {"project_id": "project-1", "name": "Project", "domain": "Research"},
        )
        event_id = add_event(connection)
        risk = open_or_update_risk(connection, risk_payload(event_id))
        second_event = add_event(connection)
        second_risk = open_or_update_risk(
            connection,
            risk_payload(second_event, risk_key="risk-2", fingerprint="evidence-v2"),
        )
    finally:
        connection.close()
    capsys.readouterr()

    for command, expected_status, target in (
        ("waiting", "Waiting", risk["risk_id"]),
        ("accept", "Accepted", risk["risk_id"]),
        ("resolve", "Resolved", second_risk["risk_id"]),
    ):
        monkeypatch.setattr(
            sys, "argv", ["research-dashboard", "risk", command, target]
        )
        assert main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["current_status"] == expected_status
