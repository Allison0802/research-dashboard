PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    domain TEXT NOT NULL,
    context_path TEXT,
    lifecycle TEXT NOT NULL
        CHECK(lifecycle IN (
            'Active', 'Waiting', 'Paused', 'Completed',
            'Archived', 'Needs classification'
        )),
    update_horizon_minutes INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_roots (
    project_id TEXT NOT NULL,
    root_path TEXT NOT NULL,
    PRIMARY KEY(project_id, root_path),
    FOREIGN KEY(project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS governing_plans (
    plan_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    workstream TEXT,
    path TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    FOREIGN KEY(project_id) REFERENCES projects(project_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS governing_plans_one_active_per_project_workstream
ON governing_plans (
    project_id,
    CASE WHEN workstream IS NULL THEN 0 ELSE 1 END,
    CASE WHEN workstream IS NULL THEN '' ELSE workstream END
)
WHERE active = 1;

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    workstream TEXT,
    task_key TEXT,
    event_type TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    importance TEXT NOT NULL,
    risk_type TEXT,
    risk_severity TEXT,
    epistemic_status TEXT NOT NULL
        CHECK(epistemic_status IN ('Observed', 'Derived', 'Inferred')),
    context TEXT NOT NULL,
    what_changed TEXT NOT NULL,
    cause TEXT,
    impact TEXT,
    next_action TEXT,
    confidence TEXT
        CHECK(confidence IN ('High', 'Medium', 'Low') OR confidence IS NULL),
    governing_plan_path TEXT,
    source_agent TEXT,
    source_session TEXT,
    observed_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    corrects_event_id TEXT,
    UNIQUE(project_id, event_id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(corrects_event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS event_evidence (
    evidence_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    locator TEXT NOT NULL,
    authority INTEGER NOT NULL,
    observed_at TEXT,
    availability TEXT NOT NULL DEFAULT 'available'
        CHECK(availability IN ('available', 'unavailable', 'unknown')),
    FOREIGN KEY(event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS state_conflicts (
    conflict_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    workstream TEXT,
    task_key TEXT NOT NULL,
    left_event_id TEXT NOT NULL,
    right_event_id TEXT NOT NULL,
    resolved_by_event_id TEXT,
    created_at TEXT NOT NULL,
    CHECK(length(trim(task_key, ' ' || char(9) || char(10) || char(11) || char(12) || char(13))) > 0),
    CHECK(left_event_id <> right_event_id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(project_id, left_event_id) REFERENCES events(project_id, event_id),
    FOREIGN KEY(project_id, right_event_id) REFERENCES events(project_id, event_id),
    FOREIGN KEY(project_id, resolved_by_event_id) REFERENCES events(project_id, event_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS state_conflicts_event_pair
ON state_conflicts (
    project_id,
    COALESCE(workstream, ''),
    task_key,
    left_event_id,
    right_event_id
);

CREATE TABLE IF NOT EXISTS risks (
    risk_id TEXT PRIMARY KEY,
    risk_key TEXT NOT NULL,
    project_id TEXT NOT NULL,
    workstream TEXT,
    task_key TEXT,
    risk_type TEXT NOT NULL
        CHECK(risk_type IN ('Research', 'Operational')),
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    current_status TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    originating_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, risk_key),
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(originating_event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS risk_transitions (
    transition_id TEXT PRIMARY KEY,
    risk_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    note TEXT,
    event_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(risk_id) REFERENCES risks(risk_id),
    FOREIGN KEY(event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS review_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    reviewed_through_sequence INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT
);

INSERT OR IGNORE INTO review_state(
    singleton,
    reviewed_through_sequence
)
VALUES (1, 0);

CREATE TABLE IF NOT EXISTS named_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    through_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    external_id TEXT NOT NULL,
    current_state TEXT NOT NULL CHECK(current_state IN (
        'SUBMITTED', 'QUEUED', 'RUNNING', 'COMPLETED',
        'FAILED', 'CANCELLED', 'UNKNOWN'
    )),
    project_id TEXT,
    workstream TEXT,
    task_key TEXT,
    created_at TEXT NOT NULL,
    last_observed_at TEXT,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    UNIQUE(backend, external_id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS execution_observations (
    observation_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'SUBMITTED', 'QUEUED', 'RUNNING', 'COMPLETED',
        'FAILED', 'CANCELLED', 'UNKNOWN'
    )),
    observed_at TEXT NOT NULL,
    raw_record TEXT NOT NULL,
    UNIQUE(execution_id, state, raw_record),
    FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
);

CREATE TABLE IF NOT EXISTS roadmap_items (
    roadmap_item_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_item_id TEXT,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    status TEXT NOT NULL CHECK(status IN (
        'Not started', 'In progress', 'Waiting',
        'Blocked', 'Done', 'Skipped'
    )),
    note TEXT,
    position INTEGER NOT NULL CHECK(position >= 0),
    source_plan_path TEXT,
    source_key TEXT,
    created_by TEXT NOT NULL CHECK(created_by IN ('user', 'agent')),
    updated_by TEXT NOT NULL CHECK(updated_by IN ('user', 'agent')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(parent_item_id) REFERENCES roadmap_items(roadmap_item_id)
);

CREATE INDEX IF NOT EXISTS roadmap_items_project_order
ON roadmap_items(project_id, parent_item_id, position, roadmap_item_id);

CREATE TABLE IF NOT EXISTS todos (
    todo_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    roadmap_item_id TEXT,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    status TEXT NOT NULL CHECK(status IN ('Open', 'Done')),
    priority TEXT NOT NULL DEFAULT 'Normal'
        CHECK(priority IN ('High', 'Normal', 'Low')),
    due_date TEXT,
    note TEXT,
    position INTEGER NOT NULL CHECK(position >= 0),
    created_by TEXT NOT NULL CHECK(created_by IN ('user', 'agent')),
    updated_by TEXT NOT NULL CHECK(updated_by IN ('user', 'agent')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(project_id),
    FOREIGN KEY(roadmap_item_id) REFERENCES roadmap_items(roadmap_item_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS todos_project_order
ON todos(project_id, status, position, todo_id);

CREATE TABLE IF NOT EXISTS roadmap_sync_state (
    project_id TEXT PRIMARY KEY,
    plan_path TEXT NOT NULL,
    acknowledged_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS roadmap_proposal_batches (
    batch_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_plan_path TEXT NOT NULL,
    source_plan_sha256 TEXT NOT NULL,
    proposals_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Pending', 'Reviewed')),
    created_by TEXT NOT NULL CHECK(created_by IN ('agent')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(project_id)
);

CREATE INDEX IF NOT EXISTS roadmap_proposal_batches_pending
ON roadmap_proposal_batches(project_id, status, created_at);
