from datetime import datetime, timezone
import sqlite3
from uuid import UUID

import pytest
from pydantic import ValidationError

from research_dashboard.db import init_db
from research_dashboard.domain import (
    EvidenceInput,
    ProjectInput,
    RiskInput,
    SemanticEventInput,
)
from research_dashboard.events import STATE_CONFLICT, ingest_event
from research_dashboard.registry import add_project
from research_dashboard.settings import Settings


EVENT_1 = "123e4567-e89b-42d3-a456-426614174000"
EVENT_2 = "123e4567-e89b-42d3-a456-426614174001"
EVENT_3 = "123e4567-e89b-42d3-a456-426614174002"
EVENT_4 = "123e4567-e89b-42d3-a456-426614174003"


@pytest.fixture
def connection(tmp_path):
    database = init_db(Settings(tmp_path / "runtime"))
    add_project(
        database,
        {"project_id": "project-1", "name": "Project", "domain": "Research"},
    )
    try:
        yield database
    finally:
        database.close()


def event_payload(**overrides):
    payload = {
        "event_id": EVENT_1,
        "project_id": "project-1",
        "event_type": "state_change",
        "previous_state": "Waiting",
        "new_state": "Active",
        "importance": "High",
        "epistemic_status": "Observed",
        "context": "The project registry was reviewed.",
        "what_changed": "The project is ready for active work.",
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


def risk_payload(**overrides):
    payload = {
        "risk_key": "risk-1",
        "project_id": "project-1",
        "risk_type": "Research",
        "severity": "High",
        "title": "A research risk",
        "current_status": "Open",
        "fingerprint": "risk-fingerprint",
        "originating_event_id": EVENT_1,
    }
    payload.update(overrides)
    return payload


def model_payload(model):
    if model is EvidenceInput:
        return {
            "evidence_type": "repository",
            "locator": "tests/fixtures/project/README.md",
            "authority": 1,
        }
    if model is RiskInput:
        return risk_payload()
    if model is SemanticEventInput:
        return event_payload()
    if model is ProjectInput:
        return {
            "project_id": "project-1",
            "name": "Project",
            "domain": "Research",
        }
    raise AssertionError(f"No payload defined for {model.__name__}")


def test_valid_semantic_event_is_parsed_with_explicit_enums():
    event = SemanticEventInput(**event_payload())

    assert event.event_id == UUID(EVENT_1)
    assert event.epistemic_status.value == "Observed"
    assert event.evidence[0].authority == 1


def test_event_round_trips_agent_provenance(connection):
    result = ingest_event(
        connection,
        event_payload(
            source_agent="example-agent",
            source_session="session-001",
        ),
    )

    assert result["event"]["source_agent"] == "example-agent"
    assert result["event"]["source_session"] == "session-001"


def test_event_replay_rejects_changed_agent_provenance(connection):
    ingest_event(connection, event_payload(source_agent="example-agent"))

    with pytest.raises(ValueError, match="different payload"):
        ingest_event(connection, event_payload(source_agent="another-agent"))


def test_remaining_named_models_accept_explicit_payloads():
    risk = RiskInput(**model_payload(RiskInput))

    assert risk.originating_event_id == UUID(EVENT_1)


def test_risk_requires_originating_event_id():
    payload = risk_payload()
    del payload["originating_event_id"]

    with pytest.raises(ValidationError, match="originating_event_id"):
        RiskInput(**payload)


def test_project_accepts_an_arbitrary_non_empty_domain():
    project = ProjectInput(
        project_id="project-1",
        name="Project",
        domain="Accounting",
    )

    assert project.domain == "Accounting"


def test_semantic_event_rejects_invalid_epistemic_status():
    with pytest.raises(ValidationError, match="epistemic_status"):
        SemanticEventInput(**event_payload(epistemic_status="Assumed"))


def test_state_change_requires_new_state():
    with pytest.raises(ValidationError, match="new_state"):
        SemanticEventInput(**event_payload(new_state=None))


def test_risk_event_requires_risk_type():
    with pytest.raises(ValidationError, match="risk_type"):
        SemanticEventInput(
            **event_payload(
                event_type="risk",
                previous_state=None,
                new_state=None,
                risk_type=None,
            )
        )


def test_evidence_rejects_negative_authority():
    with pytest.raises(ValidationError, match="authority"):
        EvidenceInput(
            evidence_type="repository",
            locator="tests/fixtures/project/README.md",
            authority=-1,
        )


@pytest.mark.parametrize(
    ("model", "field_name"),
    [
        (EvidenceInput, "observed_at"),
        (RiskInput, "created_at"),
        (RiskInput, "updated_at"),
        (SemanticEventInput, "observed_at"),
        (SemanticEventInput, "ingested_at"),
        (ProjectInput, "created_at"),
        (ProjectInput, "updated_at"),
    ],
)
def test_timestamp_fields_reject_naive_datetimes(model, field_name):
    payload = model_payload(model)
    payload[field_name] = datetime(2026, 8, 7, 12, 0)

    with pytest.raises(ValidationError, match=field_name):
        model(**payload)


@pytest.mark.parametrize(
    ("model", "field_names"),
    [
        (RiskInput, ("created_at", "updated_at")),
        (SemanticEventInput, ("ingested_at",)),
        (ProjectInput, ("created_at", "updated_at")),
    ],
)
def test_default_timestamps_are_utc(model, field_names):
    instance = model(**model_payload(model))

    for field_name in field_names:
        timestamp = getattr(instance, field_name)
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def test_model_dump_json_serializes_enum_values():
    event = SemanticEventInput(
        **event_payload(risk_type="Research", confidence="High")
    )

    dumped = event.model_dump(mode="json")

    assert dumped["epistemic_status"] == "Observed"
    assert dumped["risk_type"] == "Research"
    assert dumped["confidence"] == "High"
    assert dumped["evidence"][0]["availability"] == "available"


def test_domain_models_reject_arbitrary_metadata():
    with pytest.raises(ValidationError, match="metadata"):
        SemanticEventInput(**event_payload(metadata={"unexpected": True}))


def test_correction_preserves_corrected_event_id():
    event = SemanticEventInput(
        **event_payload(
            event_id=EVENT_2,
            event_type="correction",
            previous_state=None,
            new_state=None,
            corrects_event_id=EVENT_1,
        )
    )

    assert event.corrects_event_id == UUID(EVENT_1)


def test_event_ids_are_uuid_validated_and_json_serialized():
    with pytest.raises(ValidationError, match="event_id"):
        SemanticEventInput(**event_payload(event_id="event-1"))

    event = SemanticEventInput(
        **event_payload(corrects_event_id=EVENT_1, event_type="correction")
    )

    assert isinstance(event.event_id, UUID)
    assert event.model_dump(mode="json")["event_id"] == EVENT_1
    assert event.model_dump(mode="json")["corrects_event_id"] == EVENT_1


def _create_open_conflict(
    connection,
    task_key="task-1",
    first_event_id=EVENT_1,
    second_event_id=EVENT_2,
):
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(task_key=task_key, event_id=first_event_id)
        ),
    )
    return ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=second_event_id,
                task_key=task_key,
                previous_state="Waiting",
                new_state="Paused",
            )
        ),
    )


def test_ingest_appends_semantic_event_and_evidence(connection):
    result = ingest_event(
        connection, SemanticEventInput(**event_payload(task_key="task-1"))
    )

    assert result["status"] == "accepted"
    assert result["conflict"] is None
    assert result["current_state"] == "Active"
    assert result["event"]["event_id"] == EVENT_1
    assert result["event"]["observed_at"].endswith("+00:00")
    assert result["evidence"][0]["event_id"] == EVENT_1
    UUID(result["evidence"][0]["evidence_id"])
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 1


def test_ordinary_duplicate_event_is_idempotent_but_payload_collision_is_rejected(
    connection,
):
    event = SemanticEventInput(**event_payload(task_key="task-1"))
    first = ingest_event(connection, event)
    duplicate = ingest_event(
        connection, SemanticEventInput(**event_payload(task_key="task-1"))
    )

    assert duplicate["event"]["event_id"] == first["event"]["event_id"]
    assert duplicate["evidence"] == first["evidence"]
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 1

    with pytest.raises(ValueError, match="already exists|payload"):
        ingest_event(
            connection,
            SemanticEventInput(
                **event_payload(
                    task_key="task-1",
                    context="A different context.",
                    what_changed="A different change.",
                )
            ),
        )

    stored = connection.execute(
        "SELECT context, what_changed FROM events WHERE event_id = ?",
        (EVENT_1,),
    ).fetchone()
    assert dict(stored) == {
        "context": "The project registry was reviewed.",
        "what_changed": "The project is ready for active work.",
    }
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_ingest_accepts_expected_same_task_transition(connection):
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(task_key="task-1", new_state="Active")
        ),
    )
    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_2,
                task_key="task-1",
                previous_state="Active",
                new_state="Completed",
            )
        ),
    )

    assert result["conflict"] is None
    assert result["current_state"] == "Completed"
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_ingest_preserves_incompatible_transition_and_creates_state_conflict(
    connection,
):
    ingest_event(
        connection,
        SemanticEventInput(**event_payload(task_key="task-1", new_state="Active")),
    )
    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_2,
                task_key="task-1",
                previous_state="Waiting",
                new_state="Paused",
            )
        ),
    )

    assert result["current_state"] == "STATE_CONFLICT"
    assert result["conflict"]["left_event_id"] == EVENT_1
    assert result["conflict"]["right_event_id"] == EVENT_2
    UUID(result["conflict"]["conflict_id"])
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 1


def test_evidence_correction_preserves_incompatible_transition_as_conflict(connection):
    ingest_event(
        connection,
        SemanticEventInput(**event_payload(task_key="task-1", new_state="Active")),
    )

    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_2,
                task_key="task-1",
                event_type="evidence_correction",
                previous_state="Waiting",
                new_state="Paused",
                corrects_event_id=EVENT_1,
                context="The incompatible state evidence was clarified.",
            )
        ),
    )

    assert result["current_state"] == STATE_CONFLICT
    assert result["conflict"]["left_event_id"] == EVENT_1
    assert result["conflict"]["right_event_id"] == EVENT_2
    assert connection.execute(
        "SELECT resolved_by_event_id FROM state_conflicts"
    ).fetchone()[0] is None


def test_explicit_conflict_ingestion_requires_a_distinct_historical_event(connection):
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(task_key="new-conflict-task", event_id=EVENT_1)
        ),
    )
    event = SemanticEventInput(
        **event_payload(
            event_id=EVENT_2,
            task_key="new-conflict-task",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )

    result = ingest_event(connection, event, record_conflict=True)

    assert result["status"] == "conflict"
    assert result["conflict"]["left_event_id"] == EVENT_1
    assert result["conflict"]["right_event_id"] == EVENT_2
    assert result["conflict"]["left_event_id"] != result["conflict"]["right_event_id"]
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 1


@pytest.mark.parametrize("task_key", [None, "", " \t"])
def test_explicit_conflict_rejects_blank_task_key_before_mutation(connection, task_key):
    event = SemanticEventInput(
        **event_payload(
            task_key=task_key,
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )

    with pytest.raises(ValueError, match="task_key"):
        ingest_event(connection, event, record_conflict=True)

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0


@pytest.mark.parametrize("task_key", [None, "", " \t"])
def test_automatic_conflict_rejects_blank_task_key_before_mutation(connection, task_key):
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                task_key=task_key,
                event_id=EVENT_1,
                previous_state="Waiting",
                new_state="Active",
            )
        ),
    )

    with pytest.raises(ValueError, match="task_key"):
        ingest_event(
            connection,
            SemanticEventInput(
                **event_payload(
                    task_key=task_key,
                    event_id=EVENT_2,
                    previous_state="Waiting",
                    new_state="Paused",
                )
            ),
        )

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0


def test_explicit_conflict_replay_returns_existing_rows_without_duplication(connection):
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(task_key="replay-task", event_id=EVENT_1)
        ),
    )
    event = SemanticEventInput(
        **event_payload(
            event_id=EVENT_2,
            task_key="replay-task",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )

    first = ingest_event(connection, event, record_conflict=True)
    replay = ingest_event(connection, event, record_conflict=True)

    assert replay["event"] == first["event"]
    assert replay["evidence"] == first["evidence"]
    assert replay["conflict"] == first["conflict"]
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 1


def test_explicit_conflict_replay_uses_original_counterpart_after_later_history(
    connection,
):
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(task_key="counterpart-task", event_id=EVENT_1)
        ),
    )
    event = SemanticEventInput(
        **event_payload(
            event_id=EVENT_2,
            task_key="counterpart-task",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )
    ingest_event(connection, event, record_conflict=True)
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_3,
                task_key="counterpart-task",
                event_type="note",
                previous_state=None,
                new_state=None,
            )
        ),
    )

    replay = ingest_event(connection, event, record_conflict=True)

    assert replay["status"] == "conflict"
    assert replay["conflict"]["left_event_id"] == EVENT_1
    assert replay["conflict"]["right_event_id"] == EVENT_2
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 1


def test_explicit_conflict_rejects_event_id_payload_collision(connection):
    event = SemanticEventInput(
        **event_payload(
            task_key="collision-task",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )
    ingest_event(connection, event)

    with pytest.raises(ValueError, match="payload"):
        ingest_event(
            connection,
            SemanticEventInput(
                **event_payload(
                    task_key="collision-task",
                    event_type="note",
                    previous_state=None,
                    new_state=None,
                    context="A spoofed payload.",
                )
            ),
            record_conflict=True,
        )

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0


def test_explicit_conflict_rejects_self_conflict_without_mutation(connection):
    event = SemanticEventInput(
        **event_payload(
            task_key="self-conflict-task",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )

    with pytest.raises(ValueError, match="historical|distinct|conflict"):
        ingest_event(connection, event, record_conflict=True)

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0


def test_explicit_conflict_rejects_reused_event_id_without_state_mutation(connection):
    event = SemanticEventInput(
        **event_payload(
            task_key="reused-conflict-task",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )
    ingest_event(connection, event)

    with pytest.raises(ValueError, match="matching conflict|already exists"):
        ingest_event(connection, event, record_conflict=True)

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM state_conflicts WHERE left_event_id = right_event_id"
    ).fetchone()[0] == 0


def test_ingest_correction_preserves_original_event(connection):
    ingest_event(
        connection,
        SemanticEventInput(**event_payload(task_key="task-1", new_state="Active")),
    )
    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_2,
                task_key="task-1",
                event_type="correction",
                previous_state=None,
                new_state=None,
                corrects_event_id=EVENT_1,
                context="The original context was corrected.",
            )
        ),
    )

    assert result["conflict"] is None
    assert result["event"]["corrects_event_id"] == EVENT_1
    original = connection.execute(
        "SELECT context, new_state FROM events WHERE event_id = ?",
        (EVENT_1,),
    ).fetchone()
    assert dict(original) == {
        "context": "The project registry was reviewed.",
        "new_state": "Active",
    }
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_open_conflict_remains_visible_after_ordinary_transition(connection):
    _create_open_conflict(connection)

    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_3,
                task_key="task-1",
                previous_state=STATE_CONFLICT,
                new_state="Completed",
            )
        ),
    )

    assert result["current_state"] == STATE_CONFLICT
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 1
    assert connection.execute(
        "SELECT resolved_by_event_id FROM state_conflicts"
    ).fetchone()[0] is None


def test_correction_resolves_conflict_when_it_corrects_an_endpoint(connection):
    _create_open_conflict(connection)

    correction = SemanticEventInput(
        **event_payload(
            event_id=EVENT_3,
            task_key="task-1",
            event_type="correction",
            previous_state="Paused",
            new_state="Waiting",
            corrects_event_id=EVENT_2,
            context="The conflicting event was clarified.",
        )
    )
    result = ingest_event(connection, correction)
    replay = ingest_event(connection, correction)

    assert result["current_state"] == "Waiting"
    assert replay["current_state"] == "Waiting"
    assert result["event"]["corrects_event_id"] == EVENT_2
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    original = connection.execute(
        "SELECT new_state FROM events WHERE event_id = ?", (EVENT_2,)
    ).fetchone()
    assert original[0] == "Paused"
    conflict = connection.execute(
        "SELECT resolved_by_event_id FROM state_conflicts"
    ).fetchone()
    assert conflict[0] == EVENT_3


def test_evidence_correction_leaves_conflict_open_without_an_endpoint_match(connection):
    _create_open_conflict(connection)
    ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_3,
                task_key="task-1",
                event_type="note",
                previous_state=None,
                new_state=None,
            )
        ),
    )

    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                event_id=EVENT_4,
                task_key="task-1",
                event_type="evidence_correction",
                previous_state="Paused",
                new_state="Waiting",
                corrects_event_id=EVENT_3,
                context="An unrelated event was clarified.",
            )
        ),
    )

    assert result["current_state"] == STATE_CONFLICT
    assert connection.execute(
        "SELECT resolved_by_event_id FROM state_conflicts"
    ).fetchone()[0] is None


def test_ingest_persists_full_utc_iso8601_timestamps(connection):
    result = ingest_event(
        connection,
        SemanticEventInput(
            **event_payload(
                task_key="task-1",
                observed_at=datetime(2026, 8, 7, 7, 0, tzinfo=timezone.utc),
                ingested_at=datetime(
                    2026, 8, 7, 7, 30, 12, 345678, tzinfo=timezone.utc
                ),
                evidence=[
                    {
                        "evidence_type": "repository",
                        "locator": "tests/fixtures/project/README.md",
                        "authority": 1,
                        "observed_at": datetime(
                            2026, 8, 7, 1, 0, tzinfo=timezone.utc
                        ),
                    }
                ],
            )
        ),
    )

    assert result["event"]["observed_at"] == "2026-08-07T07:00:00+00:00"
    assert result["event"]["ingested_at"] == "2026-08-07T07:30:12.345678+00:00"
    assert result["evidence"][0]["observed_at"] == "2026-08-07T01:00:00+00:00"

    conflict = _create_open_conflict(
        connection,
        task_key="task-2",
        first_event_id=EVENT_3,
        second_event_id=EVENT_4,
    )
    created_at = datetime.fromisoformat(conflict["conflict"]["created_at"])
    assert created_at.tzinfo == timezone.utc


def test_ingest_rolls_back_prior_writes_when_conflict_write_fails(connection):
    ingest_event(connection, SemanticEventInput(**event_payload(task_key="task-1")))
    connection.execute(
        """
        CREATE TRIGGER fail_state_conflict_insert
        BEFORE INSERT ON state_conflicts
        BEGIN
            SELECT RAISE(ABORT, 'forced conflict write failure');
        END;
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced conflict write failure"):
        ingest_event(
            connection,
            SemanticEventInput(
                **event_payload(
                    event_id=EVENT_4,
                    task_key="task-1",
                    previous_state="Waiting",
                    new_state="Paused",
                )
            ),
        )

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0


def test_ingest_rejects_unknown_project(connection):
    event = SemanticEventInput(**event_payload(project_id="missing-project"))

    with pytest.raises(ValueError, match="unknown project|not registered"):
        ingest_event(connection, event)

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_conflict_ingestion_rejects_event_id_spoofed_from_another_project(connection):
    add_project(
        connection,
        {"project_id": "project-2", "name": "Other Project", "domain": "Research"},
    )
    ingest_event(connection, SemanticEventInput(**event_payload(task_key="task-1")))
    spoofed = SemanticEventInput(
        **event_payload(
            project_id="project-2",
            task_key="task-2",
            event_type="note",
            previous_state=None,
            new_state=None,
        )
    )

    with pytest.raises(ValueError, match="already exists|already present|project"):
        ingest_event(connection, spoofed, record_conflict=True)

    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM state_conflicts").fetchone()[0] == 0
