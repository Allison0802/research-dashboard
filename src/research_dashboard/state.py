"""Deterministic, read-only queries over the dashboard event store."""

from datetime import date, datetime, time, timedelta, timezone
import sqlite3
import re
from pathlib import Path
from typing import Any

from .events import STATE_CONFLICT
from .executions import list_executions
from .planning import planning_summary
from .provenance import event_provenance
from .registry import (
    get_active_governing_plan,
    get_project,
    list_project_roots,
    list_projects,
)


ATTENTION_PRIORITY = {
    "Critical blocker": 0,
    "Research risk": 1,
    "Operational risk": 2,
    "Decision needed": 3,
    "Important result change": 4,
    "Completed milestone": 5,
    "Routine change": 6,
}

_PROJECT_STATUS_VALUES = {
    "Active",
    "Waiting",
    "Paused",
    "Completed",
    "Archived",
    "Needs classification",
}

_PRESENTATION_RANK = {
    "Needs me": 0,
    "Blocked": 1,
    "Active": 2,
    "Waiting": 3,
    "Paused": 4,
    "Completed": 5,
    "Archived": 6,
    "Needs classification": 7,
}

PORTFOLIO_QUERY_NAMES = (
    "blocked_today",
    "changed_since_monday",
    "completed_this_week",
    "waiting_on_me",
    "new_research_risks",
)

_PORTFOLIO_QUERY_ALIASES = {
    "what became blocked today?": "blocked_today",
    "what changed since monday?": "changed_since_monday",
    "what did i complete this week?": "completed_this_week",
    "which projects are waiting on me?": "waiting_on_me",
    "what new research risks appeared?": "new_research_risks",
}


class UnsupportedPortfolioSelectionError(ValueError):
    """Raised when a portfolio query cannot honor a requested selector."""


_EVENT_COLUMNS = (
    "sequence, event_id, project_id, workstream, task_key, event_type, "
    "previous_state, new_state, importance, risk_type, risk_severity, "
    "epistemic_status, context, what_changed, cause, impact, next_action, "
    "confidence, governing_plan_path, source_agent, source_session, observed_at, "
    "ingested_at, corrects_event_id"
)


def _validate_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("state connection must be a sqlite3.Connection")
    if connection.row_factory is not sqlite3.Row:
        raise ValueError(
            "state connection must use sqlite3.Row as row_factory; "
            "use research_dashboard.db.connect_db()"
        )
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError(
            "state connection must enable SQLite foreign keys; "
            "use research_dashboard.db.connect_db()"
        )


def _evidence_for_event(
    connection: sqlite3.Connection, event_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT evidence_id, event_id, evidence_type, locator, authority, "
        "observed_at, availability FROM event_evidence "
        "WHERE event_id = ? ORDER BY rowid",
        (event_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _evidence_availability(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "unknown"
    availability = {item["availability"] for item in evidence}
    if "unavailable" in availability:
        return "unavailable"
    if "unknown" in availability:
        return "unknown"
    return "available"


def _evidence_by_event_ids(
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    evidence_by_event: dict[str, list[dict[str, Any]]] = {
        event_id: [] for event_id in event_ids
    }
    if not event_ids:
        return evidence_by_event

    placeholders = ", ".join("?" for _ in event_ids)
    rows = connection.execute(
        "SELECT evidence_id, event_id, evidence_type, locator, authority, "
        "observed_at, availability FROM event_evidence "
        f"WHERE event_id IN ({placeholders}) ORDER BY rowid",
        tuple(event_ids),
    ).fetchall()
    for row in rows:
        evidence_by_event[row["event_id"]].append(dict(row))
    return evidence_by_event


def _event_record(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record = dict(row)
    event_evidence = (
        _evidence_for_event(connection, record["event_id"])
        if evidence is None
        else evidence
    )
    record["evidence"] = event_evidence
    record["evidence_availability"] = _evidence_availability(event_evidence)
    record["provenance"] = event_provenance(record)
    return record


def _event_records(
    connection: sqlite3.Connection,
    where: str = "",
    parameters: tuple[Any, ...] = (),
    order_by: str = "sequence ASC",
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"SELECT {_EVENT_COLUMNS} FROM events {where} ORDER BY {order_by}",
        parameters,
    ).fetchall()
    evidence_by_event = _evidence_by_event_ids(
        connection, [row["event_id"] for row in rows]
    )
    return [
        _event_record(connection, row, evidence_by_event[row["event_id"]])
        for row in rows
    ]


def _project_timeline(
    connection: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """Load one project's events and evidence without per-event queries."""
    rows = connection.execute(
        f"SELECT {_EVENT_COLUMNS} FROM events "
        "WHERE project_id = ? ORDER BY sequence ASC",
        (project_id,),
    ).fetchall()
    if not rows:
        return []

    event_ids = [row["event_id"] for row in rows]
    evidence_by_event = _evidence_by_event_ids(connection, event_ids)
    return [
        _event_record(connection, row, evidence_by_event[row["event_id"]])
        for row in rows
    ]


def _project_filter(project_id: str | None) -> tuple[str, tuple[Any, ...]]:
    if project_id is None:
        return "", ()
    return " AND project_id = ?", (project_id,)


def _changes_after(
    connection: sqlite3.Connection,
    through_sequence: int,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    project_clause, parameters = _project_filter(project_id)
    records = _event_records(
        connection,
        f"WHERE sequence > ?{project_clause}",
        (through_sequence, *parameters),
    )
    return sorted(records, key=_priority)


def _redact_conflicted_actions(
    connection: sqlite3.Connection,
    records: list[dict[str, Any]],
    project_id: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Keep conflict records visible while removing unsupported actions."""
    if not records:
        return records
    project_clause, parameters = _project_filter(project_id)
    if as_of is None:
        conflict_rows = connection.execute(
            "SELECT project_id, workstream, task_key FROM state_conflicts "
            f"WHERE resolved_by_event_id IS NULL{project_clause}",
            parameters,
        ).fetchall()
    else:
        effective_as_of = _as_of_upper_bound(as_of)
        conflict_rows = [
            row
            for row in connection.execute(
                "SELECT conflict_id, project_id, workstream, task_key, "
                "left_event_id, right_event_id, resolved_by_event_id, created_at "
                "FROM state_conflicts "
                f"WHERE 1 = 1{project_clause}",
                parameters,
            ).fetchall()
            if _conflict_visible_as_of(connection, row, effective_as_of)
        ]
    conflict_keys = {
        (row["project_id"], row["workstream"], row["task_key"])
        for row in conflict_rows
    }
    return _normalize_conflicted_actions(
        records,
        {key: {} for key in conflict_keys},
    )


def changes_since_checkpoint(
    connection: sqlite3.Connection,
    checkpoint: int | str,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return events after a sequence or named checkpoint, priority first."""
    _validate_connection(connection)
    if isinstance(checkpoint, str):
        row = connection.execute(
            "SELECT through_sequence FROM named_checkpoints WHERE name = ?",
            (checkpoint,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown named checkpoint {checkpoint!r}")
        through_sequence = row["through_sequence"]
    else:
        through_sequence = checkpoint
    if not isinstance(through_sequence, int) or through_sequence < 0:
        raise ValueError("checkpoint sequence must be a non-negative integer")
    return _redact_conflicted_actions(
        connection,
        _changes_after(connection, through_sequence, project_id),
        project_id,
    )


def changes_since_last_review(
    connection: sqlite3.Connection,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return events after the explicit global review checkpoint."""
    _validate_connection(connection)
    reviewed = connection.execute(
        "SELECT reviewed_through_sequence FROM review_state WHERE singleton = 1"
    ).fetchone()
    assert reviewed is not None
    changes = _changes_after(
        connection, reviewed["reviewed_through_sequence"], project_id
    )
    return _redact_conflicted_actions(connection, changes, project_id)


def _as_utc(value: datetime | date | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if value.tzinfo is None:
        raise ValueError("portfolio query time must include a timezone")
    return value.astimezone(timezone.utc)


def _as_of_upper_bound(value: datetime | date | None) -> datetime:
    """Return the inclusive UTC upper bound represented by a query time."""
    current = _as_utc(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return current + timedelta(days=1) - timedelta(microseconds=1)
    return current


def _as_of_local(value: datetime | date | None) -> datetime:
    """Return the query instant in the caller's calendar timezone."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if value.tzinfo is None:
        raise ValueError("portfolio query time must include a timezone")
    return value


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("stored dashboard timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _observed_at(event: dict[str, Any]) -> datetime:
    return _timestamp(event["observed_at"])


def _portfolio_baseline(
    connection: sqlite3.Connection,
    baseline: str,
    checkpoint_name: str | None,
) -> int:
    normalized = baseline.casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"last_reviewed", "reviewed"}:
        if checkpoint_name is not None:
            raise ValueError(
                "checkpoint_name is only valid with the named_checkpoint baseline"
            )
        row = connection.execute(
            "SELECT reviewed_through_sequence FROM review_state WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        return row["reviewed_through_sequence"]
    if normalized in {"named_checkpoint", "checkpoint"}:
        if not isinstance(checkpoint_name, str) or not checkpoint_name.strip():
            raise ValueError(
                "checkpoint_name is required with the named_checkpoint baseline"
            )
        row = connection.execute(
            "SELECT through_sequence FROM named_checkpoints WHERE name = ?",
            (checkpoint_name,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown named checkpoint {checkpoint_name!r}")
        return row["through_sequence"]
    raise ValueError(
        "baseline must be either 'last_reviewed' or 'named_checkpoint'"
    )


def _portfolio_context(
    connection: sqlite3.Connection,
    records: list[dict[str, Any]],
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    if not records:
        return []
    project_ids = sorted({record["project_id"] for record in records})
    placeholders = ", ".join("?" for _ in project_ids)
    projects = {}
    for row in connection.execute(
        "SELECT project_id, name, domain, lifecycle FROM projects "
        f"WHERE project_id IN ({placeholders})",
        tuple(project_ids),
    ).fetchall():
        projects[row["project_id"]] = dict(row)
    effective_as_of = _as_of_upper_bound(as_of) if as_of is not None else None
    event_ids = [record["event_id"] for record in records]
    event_placeholders = ", ".join("?" for _ in event_ids)
    risks: dict[str, dict[str, Any]] = {}
    risk_rows = connection.execute(
        "SELECT risk_id, originating_event_id, risk_type, current_status, created_at "
        "FROM risks "
        f"WHERE originating_event_id IN ({event_placeholders})",
        tuple(event_ids),
    ).fetchall()
    risk_event_ids = {row["originating_event_id"] for row in risk_rows}
    for row in risk_rows:
        risk = dict(row)
        if effective_as_of is not None:
            if _timestamp(risk["created_at"]) > effective_as_of:
                continue
            transition_rows = connection.execute(
                "SELECT transition_id, to_status, created_at FROM risk_transitions "
                "WHERE risk_id = ?",
                (risk["risk_id"],),
            ).fetchall()
            visible_transitions = [
                transition
                for transition in transition_rows
                if _timestamp(transition["created_at"]) <= effective_as_of
            ]
            if not visible_transitions:
                continue
            risk["current_status"] = max(
                visible_transitions,
                key=lambda transition: (
                    _timestamp(transition["created_at"]),
                    transition["transition_id"],
                ),
            )["to_status"]
        risks[row["originating_event_id"]] = risk
    contextualized = []
    for record in records:
        if (
            effective_as_of is not None
            and record["event_id"] in risk_event_ids
            and record["event_id"] not in risks
        ):
            continue
        result = record.copy()
        result["kind"] = "event"
        project = projects.get(record["project_id"])
        if project is not None:
            result["project_name"] = project["name"]
            result["project_domain"] = project["domain"]
            if effective_as_of is None:
                result["project_lifecycle"] = project["lifecycle"]
        risk = risks.get(record["event_id"])
        if risk is not None:
            result["risk_id"] = risk["risk_id"]
            result["risk_type"] = record.get("risk_type") or risk["risk_type"]
            result["risk_status"] = risk["current_status"]
        contextualized.append(result)
    return contextualized


def _portfolio_events(
    connection: sqlite3.Connection,
    *,
    baseline: str,
    checkpoint_name: str | None,
    as_of: datetime | date | None,
) -> list[dict[str, Any]]:
    through_sequence = _portfolio_baseline(
        connection, baseline, checkpoint_name
    )
    effective_as_of = _as_of_upper_bound(as_of)
    records = _event_records(
        connection,
        "WHERE sequence > ?",
        (through_sequence,),
    )
    records = [
        record for record in records if _observed_at(record) <= effective_as_of
    ]
    records = _redact_conflicted_actions(
        connection,
        records,
        as_of=as_of,
    )
    records = _portfolio_context(connection, records, as_of=as_of)
    return records


def _week_start(as_of: datetime | date | None) -> datetime:
    current = _as_of_local(as_of)
    monday = current.date() - timedelta(days=current.weekday())
    return datetime.combine(monday, time.min, tzinfo=current.tzinfo).astimezone(
        timezone.utc
    )


def _local_day_bounds(as_of: datetime | date | None) -> tuple[datetime, datetime]:
    current = _as_of_local(as_of)
    start = datetime.combine(
        current.date(),
        time.min,
        tzinfo=current.tzinfo,
    )
    end = datetime.combine(
        current.date() + timedelta(days=1),
        time.min,
        tzinfo=current.tzinfo,
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _events_on_or_after(
    records: list[dict[str, Any]], start: datetime
) -> list[dict[str, Any]]:
    return [record for record in records if _observed_at(record) >= start]


def what_became_blocked_today(
    connection: sqlite3.Connection,
    *,
    baseline: str = "last_reviewed",
    checkpoint_name: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Return evidence-backed state changes to ``Blocked`` on the query date."""
    _validate_connection(connection)
    current = _as_of_upper_bound(as_of)
    start, end = _local_day_bounds(as_of)
    return [
        record
        for record in _portfolio_events(
            connection,
            baseline=baseline,
            checkpoint_name=checkpoint_name,
            as_of=as_of,
        )
        if start <= _observed_at(record) < end
        and _observed_at(record) <= current
        and (record["new_state"] or "").casefold() == "blocked"
        and (record["previous_state"] or "").casefold() != "blocked"
    ]


def changes_since_monday(
    connection: sqlite3.Connection,
    *,
    baseline: str = "last_reviewed",
    checkpoint_name: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Return all changes observed from Monday through the query date."""
    _validate_connection(connection)
    current = _as_of_upper_bound(as_of)
    start = _week_start(as_of)
    return [
        record
        for record in _portfolio_events(
            connection,
            baseline=baseline,
            checkpoint_name=checkpoint_name,
            as_of=as_of,
        )
        if start <= _observed_at(record) <= current
    ]


def completed_this_week(
    connection: sqlite3.Connection,
    *,
    baseline: str = "last_reviewed",
    checkpoint_name: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Return this week's completion events for tasks completed as of cutoff."""
    candidates = [
        record
        for record in changes_since_monday(
            connection,
            baseline=baseline,
            checkpoint_name=checkpoint_name,
            as_of=as_of,
        )
        if (record["new_state"] or "").casefold() == "completed"
        and record["evidence_availability"] == "available"
    ]
    if not candidates:
        return []

    effective_as_of = _as_of_upper_bound(as_of)
    project_ids = {candidate["project_id"] for candidate in candidates}
    placeholders = ", ".join("?" for _ in project_ids)
    timelines = [
        event
        for event in _event_records(
            connection,
            f"WHERE project_id IN ({placeholders})",
            tuple(project_ids),
        )
        if _observed_at(event) <= effective_as_of
    ]
    timelines_by_project: dict[str, list[dict[str, Any]]] = {}
    for event in timelines:
        timelines_by_project.setdefault(event["project_id"], []).append(event)
    conflicts_by_project = _open_conflicts_by_project(
        connection, project_ids, as_of=as_of
    )
    completed_tasks = {
        (project_id, state["workstream"], state["task_key"])
        for project_id, timeline in timelines_by_project.items()
        for state in _workstream_states(
            timeline, conflicts_by_project.get(project_id, {})
        )
        if state["current_state"] == "Completed"
    }
    return [
        record
        for record in candidates
        if (
            record["project_id"],
            record["workstream"],
            record["task_key"],
        ) in completed_tasks
    ]


def projects_waiting_on_me(
    connection: sqlite3.Connection,
    *,
    baseline: str = "last_reviewed",
    checkpoint_name: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Return registered projects whose current lifecycle is ``Waiting``."""
    _validate_connection(connection)
    normalized_baseline = baseline.casefold().replace("-", "_").replace(" ", "_")
    if (
        normalized_baseline != "last_reviewed"
        or checkpoint_name is not None
        or as_of is not None
    ):
        raise UnsupportedPortfolioSelectionError(
            "waiting_on_me reports current registered project lifecycle; "
            "baseline, checkpoint, and as_of selection are unsupported because "
            "project lifecycle history is not stored"
        )
    rows = connection.execute(
        "SELECT project_id, name, domain, lifecycle, context_path, "
        "update_horizon_minutes, created_at, updated_at FROM projects "
        "WHERE lifecycle = 'Waiting' ORDER BY domain, name, project_id"
    ).fetchall()
    return [{"kind": "project", **dict(row)} for row in rows]


def new_research_risks(
    connection: sqlite3.Connection,
    *,
    baseline: str = "last_reviewed",
    checkpoint_name: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Return newly recorded Research-risk events from the current week."""
    return [
        record
        for record in changes_since_monday(
            connection,
            baseline=baseline,
            checkpoint_name=checkpoint_name,
            as_of=as_of,
        )
        if (record["event_type"] or "").casefold() == "risk"
        and (record["risk_type"] or "").casefold() == "research"
    ]


def portfolio_query(
    connection: sqlite3.Connection,
    query: str,
    *,
    baseline: str = "last_reviewed",
    checkpoint_name: str | None = None,
    as_of: datetime | date | None = None,
) -> list[dict[str, Any]]:
    """Dispatch one of the named portfolio questions deterministically."""
    _validate_connection(connection)
    normalized = query.casefold().strip()
    normalized = _PORTFOLIO_QUERY_ALIASES.get(normalized, normalized)
    services = {
        "blocked_today": what_became_blocked_today,
        "changed_since_monday": changes_since_monday,
        "completed_this_week": completed_this_week,
        "waiting_on_me": projects_waiting_on_me,
        "new_research_risks": new_research_risks,
    }
    service = services.get(normalized)
    if service is None:
        raise ValueError(
            "unknown portfolio query; expected one of "
            + ", ".join(PORTFOLIO_QUERY_NAMES)
        )
    return service(
        connection,
        baseline=baseline,
        checkpoint_name=checkpoint_name,
        as_of=as_of,
    )


def _priority(record: dict[str, Any]) -> tuple[int, int]:
    return ATTENTION_PRIORITY.get(
        record["importance"], len(ATTENTION_PRIORITY)
    ), record["sequence"]


def needs_attention(
    connection: sqlite3.Connection,
    project_id: str | None = None,
    *,
    since_sequence: int | None = None,
) -> list[dict[str, Any]]:
    """Return unreviewed attention items in risk-first priority order."""
    _validate_connection(connection)
    changes = (
        _redact_conflicted_actions(
            connection,
            _changes_after(connection, since_sequence, project_id),
            project_id,
        )
        if since_sequence is not None
        else changes_since_last_review(connection, project_id)
    )
    attention = [
        change
        for change in changes
        if change["importance"] in ATTENTION_PRIORITY
        and change["importance"] != "Completed milestone"
        and change["importance"] != "Routine change"
    ]
    return sorted(attention, key=_priority)


def recently_completed(
    connection: sqlite3.Connection,
    project_id: str | None = None,
    *,
    since_sequence: int | None = None,
) -> list[dict[str, Any]]:
    """Return newly completed task events in stable sequence order."""
    _validate_connection(connection)
    changes = (
        _changes_after(connection, since_sequence, project_id)
        if since_sequence is not None
        else changes_since_last_review(connection, project_id)
    )
    candidates = [
        change
        for change in changes
        if change["new_state"] == "Completed"
        and change["evidence_availability"] == "available"
    ]
    if not candidates:
        return []

    completed_tasks = set()
    if project_id is not None:
        timeline = _project_timeline(connection, project_id)
        conflicts = _open_conflicts(connection, project_id)
        completed_tasks.update(
            (project_id, state["workstream"], state["task_key"])
            for state in _workstream_states(timeline, conflicts)
            if state["current_state"] == "Completed"
        )
    else:
        project_ids = {change["project_id"] for change in candidates}
        placeholders = ", ".join("?" for _ in project_ids)
        timelines = _event_records(
            connection,
            f"WHERE project_id IN ({placeholders})",
            tuple(project_ids),
        )
        timelines_by_project: dict[str, list[dict[str, Any]]] = {}
        for event in timelines:
            timelines_by_project.setdefault(event["project_id"], []).append(event)
        conflicts_by_project = _open_conflicts_by_project(connection, project_ids)
        for current_project_id, timeline in timelines_by_project.items():
            completed_tasks.update(
                (current_project_id, state["workstream"], state["task_key"])
                for state in _workstream_states(
                    timeline, conflicts_by_project.get(current_project_id, {})
                )
                if state["current_state"] == "Completed"
            )
    completed = [
        change
        for change in candidates
        if (
            change["project_id"],
            change["workstream"],
            change["task_key"],
        ) in completed_tasks
    ]
    return sorted(completed, key=lambda change: change["sequence"])


def project_timeline(
    connection: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """Return the complete append-only event timeline for a project."""
    _validate_connection(connection)
    return _redact_conflicted_actions(
        connection,
        _event_records(
            connection,
            "WHERE project_id = ?",
            (project_id,),
        ),
        project_id,
    )


def _project_evidence_availability(timeline: list[dict[str, Any]]) -> str:
    """Summarize evidence without treating an empty record as confirmed."""
    if not timeline:
        return "unknown"
    availability = {event["evidence_availability"] for event in timeline}
    if "unavailable" in availability:
        return "unavailable"
    if "unknown" in availability:
        return "unknown"
    return "available"


def _plan_progress(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return check-box progress with wording scoped to plan execution."""
    unavailable = {
        "status": "unavailable",
        "completed": None,
        "total": None,
        "label": "Plan execution progress: unavailable",
    }
    if plan is None:
        return unavailable
    try:
        content = Path(plan["path"]).read_text(encoding="utf-8")
    except (OSError, UnicodeError, TypeError):
        return unavailable
    tasks = re.findall(r"^\s*-\s*\[([ xX])\]\s+.+$", content, re.MULTILINE)
    if not tasks:
        return unavailable
    completed = sum(marker.casefold() == "x" for marker in tasks)
    total = len(tasks)
    return {
        "status": "available",
        "completed": completed,
        "total": total,
        "label": f"Plan execution progress: {completed} / {total} tasks",
    }


def _event_identity(event: dict[str, Any]) -> tuple[str, str] | tuple[str, str, str]:
    if event["task_key"] is None:
        return ("event", event["event_id"], "")
    return (event["workstream"], event["task_key"])


def _current_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, ...], dict[str, Any]] = {}
    for event in timeline:
        latest[_event_identity(event)] = event
    return sorted(latest.values(), key=_priority)


def _project_status(project: dict[str, Any], timeline: list[dict[str, Any]]) -> str:
    project_events = [
        event
        for event in timeline
        if event["task_key"] is None
        and event["workstream"] is None
        and event["new_state"] in _PROJECT_STATUS_VALUES
        and event["evidence_availability"] == "available"
    ]
    if project_events:
        return max(project_events, key=lambda event: event["sequence"])["new_state"]
    return project["lifecycle"]


def _latest_by_sequence(events):
    return max(events, key=lambda event: event["sequence"], default=None)


def _latest_confirmed_result(timeline):
    return _latest_by_sequence(
        [
            event
            for event in timeline
            if event["evidence_availability"] == "available"
            and event["importance"]
            in {"Important result change", "Completed milestone"}
        ]
    )


def _current_focus(workstream_states: list[dict[str, Any]]) -> dict[str, Any] | None:
    active_state_events = [
        state["state_event"]
        for state in workstream_states
        if state["current_state"] == "Active"
        and state["state_event"] is not None
        and state["state_event"]["evidence_availability"] == "available"
    ]
    return _latest_by_sequence(active_state_events)


def _compact_now(workstream_states: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        state
        for state in workstream_states
        if state["current_state"] in {"Active", "Waiting"}
        and state["state_event"] is not None
        and state["state_event"]["evidence_availability"] == "available"
    ]
    if not candidates:
        return None
    selected = min(
        candidates,
        key=lambda state: (
            0 if state["current_state"] == "Waiting" else 1,
            -state["state_event"]["sequence"],
        ),
    )
    return selected["state_event"]


def _current_decisions(current_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every current, task-attached decision in deterministic order."""
    return sorted(
        [
            event
            for event in current_events
            if event["importance"] == "Decision needed"
            and event["evidence_availability"] == "available"
            and isinstance(event["task_key"], str)
            and event["task_key"].strip()
        ],
        key=_priority,
    )


def _current_decision(current_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return _latest_by_sequence(_current_decisions(current_events))


def _inactive_risk_event_ids(
    connection: sqlite3.Connection,
    events: list[dict[str, Any]],
) -> set[str]:
    event_ids = [event["event_id"] for event in events]
    if not event_ids:
        return set()
    placeholders = ", ".join("?" for _ in event_ids)
    rows = connection.execute(
        "SELECT originating_event_id FROM risks "
        f"WHERE originating_event_id IN ({placeholders}) "
        "AND current_status IN ('Accepted', 'Resolved')",
        tuple(event_ids),
    ).fetchall()
    return {row["originating_event_id"] for row in rows}


def _current_blockers_and_risks(
    connection: sqlite3.Connection,
    current_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = [
        event
        for event in current_events
        if event["importance"]
        in {"Critical blocker", "Research risk", "Operational risk"}
        and event["evidence_availability"] == "available"
    ]
    inactive_event_ids = _inactive_risk_event_ids(connection, candidates)
    return sorted(
        [
            event
            for event in candidates
            if event["event_id"] not in inactive_event_ids
        ],
        key=_priority,
    )


def _freshness(
    project: dict[str, Any],
    timeline: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    evidence_backed = [
        event for event in timeline if event["evidence_availability"] == "available"
    ]
    if not evidence_backed:
        return {
            "stale": None,
            "last_material_update": None,
            "label": "No material update recorded",
        }
    last = max(_observed_at(event) for event in evidence_backed)
    horizon = project["update_horizon_minutes"]
    stale = None if horizon is None else now - last > timedelta(minutes=horizon)
    if stale is True:
        label = "Status may be stale"
    elif stale is False:
        label = "Current within configured horizon"
    else:
        label = "Freshness horizon not configured"
    return {"stale": stale, "last_material_update": last, "label": label}


def _immediate_next_action(
    decision_needed: dict[str, Any] | None,
    next_work: list[dict[str, Any]],
) -> str | None:
    if decision_needed is not None and decision_needed["next_action"]:
        return decision_needed["next_action"]
    supported_action = _latest_by_sequence(next_work)
    return supported_action["next_action"] if supported_action is not None else None


def _open_conflicts(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[tuple[str | None, str], dict[str, Any]]:
    rows = connection.execute(
        "SELECT conflict_id, project_id, workstream, task_key, left_event_id, "
        "right_event_id, resolved_by_event_id, created_at FROM state_conflicts "
        "WHERE project_id = ? AND resolved_by_event_id IS NULL "
        "ORDER BY created_at, conflict_id",
        (project_id,),
    ).fetchall()
    conflicts: dict[tuple[str | None, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["workstream"], row["task_key"])
        conflicts.setdefault(key, dict(row))
    return conflicts


def _open_conflicts_by_project(
    connection: sqlite3.Connection,
    project_ids: set[str],
    as_of: datetime | date | None = None,
) -> dict[str, dict[tuple[str | None, str], dict[str, Any]]]:
    if not project_ids:
        return {}
    placeholders = ", ".join("?" for _ in project_ids)
    if as_of is None:
        rows = connection.execute(
            "SELECT conflict_id, project_id, workstream, task_key, left_event_id, "
            "right_event_id, resolved_by_event_id, created_at FROM state_conflicts "
            f"WHERE project_id IN ({placeholders}) AND resolved_by_event_id IS NULL "
            "ORDER BY project_id, created_at, conflict_id",
            tuple(project_ids),
        ).fetchall()
    else:
        effective_as_of = _as_of_upper_bound(as_of)
        rows = [
            row
            for row in connection.execute(
                "SELECT conflict_id, project_id, workstream, task_key, "
                "left_event_id, right_event_id, resolved_by_event_id, created_at "
                "FROM state_conflicts "
                f"WHERE project_id IN ({placeholders}) "
                "ORDER BY project_id, created_at, conflict_id",
                tuple(project_ids),
            ).fetchall()
            if _conflict_visible_as_of(connection, row, effective_as_of)
        ]
    conflicts_by_project: dict[
        str, dict[tuple[str | None, str], dict[str, Any]]
    ] = {}
    for row in rows:
        project_conflicts = conflicts_by_project.setdefault(row["project_id"], {})
        key = (row["workstream"], row["task_key"])
        project_conflicts.setdefault(key, dict(row))
    return conflicts_by_project


def _conflict_visible_as_of(
    connection: sqlite3.Connection,
    conflict: sqlite3.Row,
    cutoff: datetime,
) -> bool:
    if _timestamp(conflict["created_at"]) > cutoff:
        return False
    endpoint_rows = connection.execute(
        "SELECT observed_at FROM events WHERE event_id IN (?, ?)",
        (conflict["left_event_id"], conflict["right_event_id"]),
    ).fetchall()
    if len(endpoint_rows) != 2 or any(
        _observed_at(dict(row)) > cutoff for row in endpoint_rows
    ):
        return False
    resolved_by_event_id = conflict["resolved_by_event_id"]
    if resolved_by_event_id is None:
        return True
    resolution = connection.execute(
        "SELECT observed_at FROM events WHERE event_id = ?",
        (resolved_by_event_id,),
    ).fetchone()
    return resolution is not None and _observed_at(dict(resolution)) > cutoff


def _normalize_conflicted_actions(
    timeline: list[dict[str, Any]],
    conflicts: dict[tuple[Any, ...], Any],
) -> list[dict[str, Any]]:
    """Remove unsupported actions from every event for an open conflict."""
    conflict_keys = set(conflicts)
    normalized = []
    for event in timeline:
        task_key = (event["workstream"], event["task_key"])
        project_task_key = (event["project_id"], *task_key)
        if (
            event["task_key"] is None
            or (task_key not in conflict_keys and project_task_key not in conflict_keys)
        ):
            normalized.append(event)
            continue
        normalized_event = event.copy()
        normalized_event["next_action"] = None
        normalized.append(normalized_event)
    return normalized


def _task_state_from_rows(
    project_id: str,
    task_key: str,
    workstream: str | None,
    rows: list[dict[str, Any]],
    conflict: dict[str, Any] | None,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda row: row["sequence"], reverse=True)
    latest_event = ordered_rows[0]
    state_events = [row for row in ordered_rows if row["new_state"] is not None]
    confirmed_state_events = [
        row for row in state_events if row["evidence_availability"] == "available"
    ]
    state_event = confirmed_state_events[0] if confirmed_state_events else None
    current_state = (
        STATE_CONFLICT
        if conflict is not None
        else (state_event["new_state"] if state_event is not None else None)
    )
    return {
        "project_id": project_id,
        "workstream": workstream,
        "task_key": task_key,
        "current_state": current_state,
        "state_event": state_event,
        "latest_event": latest_event,
        "epistemic_status": latest_event["epistemic_status"],
        "evidence_availability": latest_event["evidence_availability"],
        "conflict": conflict,
    }


def _workstream_states(
    timeline: list[dict[str, Any]],
    conflicts: dict[tuple[str | None, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    task_keys = {
        (event["workstream"], event["task_key"])
        for event in timeline
        if event["task_key"] is not None
    }
    events_by_task: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for event in timeline:
        if event["task_key"] is not None:
            events_by_task.setdefault(
                (event["workstream"], event["task_key"]), []
            ).append(event)
    states = []
    for workstream, task_key in sorted(
        task_keys, key=lambda item: (item[0] or "", item[1])
    ):
        state = _task_state_from_rows(
            timeline[0]["project_id"],
            task_key,
            workstream,
            events_by_task[(workstream, task_key)],
            conflicts.get((workstream, task_key)),
        )
        states.append(state)
    return states


def _completed_task_keys(
    workstream_states: list[dict[str, Any]],
) -> set[tuple[str | None, str]]:
    return {
        (state["workstream"], state["task_key"])
        for state in workstream_states
        if state["current_state"] == "Completed"
    }


_NON_ACTIVE_STATES = {
    None,
    "Completed",
    "Waiting",
    "Paused",
    "Archived",
    STATE_CONFLICT,
}


def _next_work(
    timeline: list[dict[str, Any]],
    workstream_states: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    states_by_task = {
        (state["workstream"], state["task_key"]): state
        for state in workstream_states
    }
    next_work: list[dict[str, Any]] = []
    uncertainty: list[dict[str, Any]] = []
    for event in sorted(
        _current_events(timeline), key=lambda event: event["sequence"]
    ):
        if not event["next_action"]:
            continue
        state = states_by_task.get((event["workstream"], event["task_key"]))
        if (
            state is None
            or state["state_event"] is None
            or state["current_state"] in _NON_ACTIVE_STATES
        ):
            continue
        if event["evidence_availability"] != "available":
            uncertainty.append(event)
            continue
        next_work.append(event)
    return next_work, uncertainty


def _evidence_records(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": event["sequence"],
            "task_key": event["task_key"],
            "what_changed": event["what_changed"],
            "epistemic_status": event["epistemic_status"],
            "evidence_availability": event["evidence_availability"],
            "evidence": event["evidence"],
        }
        for event in timeline
    ]


def _project_read_model(
    connection: sqlite3.Connection,
    project: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    project_id = project["project_id"]
    timeline = _project_timeline(connection, project_id)
    open_conflicts = _open_conflicts(connection, project_id)
    timeline = _normalize_conflicted_actions(timeline, open_conflicts)
    workstream_states = _workstream_states(timeline, open_conflicts)
    completed_task_keys = _completed_task_keys(workstream_states)
    completed = [
        event
        for event in timeline
        if event["new_state"] == "Completed"
        and event["evidence_availability"] == "available"
        and (event["workstream"], event["task_key"]) in completed_task_keys
    ]
    current_work = [
        state
        for state in workstream_states
        if state["current_state"]
        not in _NON_ACTIVE_STATES
        and state["state_event"] is not None
    ]
    next_work, next_work_uncertainty = _next_work(timeline, workstream_states)
    current_events = _current_events(timeline)
    decisions_needed = _current_decisions(current_events)
    decision_needed = _current_decision(current_events)
    current_blockers_and_risks = _current_blockers_and_risks(
        connection, current_events
    )
    attention = [
        event
        for event in current_events
        if event["importance"] in ATTENTION_PRIORITY
        and event["importance"] not in {"Completed milestone", "Routine change"}
        and event["evidence_availability"] == "available"
    ]
    blockers = [
        event
        for event in current_events
        if event["importance"] == "Critical blocker"
        and event["evidence_availability"] == "available"
    ]
    risks = [
        event
        for event in current_events
        if event["importance"] in {"Research risk", "Operational risk"}
        and event["evidence_availability"] == "available"
    ]
    governing_plan = get_active_governing_plan(connection, project_id)
    executions = [
        execution
        for execution in list_executions(connection)
        if execution["project_id"] == project_id
    ]
    latest_confirmed_result = _latest_confirmed_result(timeline)
    now_event = _compact_now(workstream_states)
    has_evidence_backed_content = (
        any(state["state_event"] is not None for state in workstream_states)
        or latest_confirmed_result is not None
        or decision_needed is not None
        or bool(current_blockers_and_risks)
    )
    now_label = (
        now_event["what_changed"]
        if now_event is not None
        else "Plan ready — execution not started"
        if governing_plan is not None and not has_evidence_backed_content
        else None
    )
    freshness = _freshness(project, timeline, now)
    planning = planning_summary(connection, project_id)
    return {
        "project": project,
        "status": _project_status(project, timeline),
        "current_focus": _current_focus(workstream_states),
        "now": now_event,
        "now_label": now_label,
        "brief_updated_at": (
            freshness["last_material_update"] or _timestamp(project["updated_at"])
        ),
        "latest_confirmed_result": latest_confirmed_result,
        "primary_blocker": (
            current_blockers_and_risks[0] if current_blockers_and_risks else None
        ),
        "decisions_needed": decisions_needed,
        "decision_needed": decision_needed,
        "immediate_next_action": _immediate_next_action(
            decision_needed, next_work
        ),
        "freshness": freshness,
        "planning_summary": {
            "roadmap_progress": planning["roadmap_progress"],
            "open_todo_count": planning["open_todo_count"],
        },
        "project_roots": list_project_roots(connection, project_id),
        "completed": completed,
        "current_work": current_work,
        "next_work": next_work,
        "next_work_uncertainty": next_work_uncertainty,
        "blockers": blockers,
        "risks": risks,
        "workstream_states": workstream_states,
        "timeline": timeline,
        "governing_plan": governing_plan,
        "context_coverage": project["context_status"],
        "evidence_availability": _project_evidence_availability(timeline),
        "evidence_records": _evidence_records(timeline),
        "executions": executions,
        "plan_progress": _plan_progress(governing_plan),
        "event_count": len(timeline),
        "open_conflict_count": len(open_conflicts),
        "open_risk_count": connection.execute(
            "SELECT COUNT(*) FROM risks WHERE project_id = ? "
            "AND lower(current_status) NOT IN ('resolved', 'closed', 'accepted')",
            (project_id,),
        ).fetchone()[0],
        "attention_count": len(attention),
        "recently_completed": completed,
    }


def project_read_model(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    now: datetime | date | None = None,
) -> dict[str, Any] | None:
    """Return the evidence-aware read model for a project drill-down page."""
    _validate_connection(connection)
    project = get_project(connection, project_id)
    if project is None:
        return None
    return _project_read_model(connection, project, _as_utc(now))


def _presentation_state(summary: dict[str, Any]) -> str:
    if summary["decision_needed"] is not None:
        return "Needs me"
    primary_blocker = summary["primary_blocker"]
    if (
        primary_blocker is not None
        and primary_blocker["importance"] == "Critical blocker"
    ):
        return "Blocked"
    return summary["status"]


def _portfolio_item_identity(event: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return event["project_id"], event["workstream"], event["task_key"]


def portfolio_read_model(
    connection: sqlite3.Connection,
    *,
    now: datetime | date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build deterministic project summaries from the registry and event store."""
    _validate_connection(connection)
    current_now = _as_utc(now)
    projects = [
        _project_read_model(connection, project, current_now)
        for project in list_projects(connection)
    ]

    needs_me = [
        decision
        for summary in projects
        for decision in summary["decisions_needed"]
    ]
    decision_identities = {
        _portfolio_item_identity(decision) for decision in needs_me
    }
    watch = []
    for summary in projects:
        current_events = _current_events(summary["timeline"])
        for event in _current_blockers_and_risks(connection, current_events):
            if _portfolio_item_identity(event) not in decision_identities:
                watch.append(event)

    projects.sort(
        key=lambda summary: (
            _PRESENTATION_RANK[_presentation_state(summary)],
            summary["project"]["name"],
            summary["project"]["project_id"],
        )
    )
    return {
        "projects": projects,
        "needs_me": sorted(needs_me, key=_priority),
        "watch": sorted(watch, key=_priority),
        "changes": changes_since_last_review(connection),
        "recently_completed": recently_completed(connection),
    }


def _open_conflict(
    connection: sqlite3.Connection,
    project_id: str,
    task_key: str,
    workstream: str | None,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT conflict_id, project_id, workstream, task_key, left_event_id, "
        "right_event_id, resolved_by_event_id, created_at FROM state_conflicts "
        "WHERE project_id = ? AND workstream IS ? AND task_key = ? "
        "AND resolved_by_event_id IS NULL ORDER BY created_at, conflict_id LIMIT 1",
        (project_id, workstream, task_key),
    ).fetchone()
    return dict(row) if row is not None else None


def current_task_state(
    connection: sqlite3.Connection,
    project_id: str,
    task_key: str,
    workstream: str | None = None,
) -> dict[str, Any] | None:
    """Return the current semantic state without hiding uncertainty."""
    _validate_connection(connection)
    rows = _event_records(
        connection,
        "WHERE project_id = ? AND workstream IS ? AND task_key = ?",
        (project_id, workstream, task_key),
        order_by="sequence DESC",
    )
    if not rows:
        return None

    latest_event = rows[0]
    state_events = [row for row in rows if row["new_state"] is not None]
    confirmed_state_events = [
        row for row in state_events if row["evidence_availability"] == "available"
    ]
    state_event = confirmed_state_events[0] if confirmed_state_events else None
    conflict = _open_conflict(connection, project_id, task_key, workstream)
    current_state = (
        STATE_CONFLICT
        if conflict is not None
        else (state_event["new_state"] if state_event is not None else None)
    )
    if current_state == STATE_CONFLICT:
        normalized_events = _normalize_conflicted_actions(
            [latest_event] + ([state_event] if state_event is not None else []),
            {(workstream, task_key): conflict},
        )
        latest_event = normalized_events[0]
        if state_event is not None:
            state_event = normalized_events[1]
    return {
        "project_id": project_id,
        "workstream": workstream,
        "task_key": task_key,
        "current_state": current_state,
        "state_event": state_event,
        "latest_event": latest_event,
        "epistemic_status": latest_event["epistemic_status"],
        "evidence_availability": latest_event["evidence_availability"],
        "conflict": conflict,
    }


def project_summary(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any] | None:
    """Return the project read model used by dashboard project views."""
    return project_read_model(connection, project_id)
