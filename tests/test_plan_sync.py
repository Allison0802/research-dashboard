from hashlib import sha256
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from research_dashboard import plan_sync
from research_dashboard.db import connect_db, init_db
from research_dashboard.domain import RoadmapProposalBatchInput
from research_dashboard.plan_sync import (
    bootstrap_roadmap_from_governing_plan,
    create_proposal_batch,
    list_pending_proposal_batches,
    parse_structured_plan,
    plan_sync_status,
    review_proposal_batch,
)
from research_dashboard.planning import create_roadmap_item, list_roadmap_items
from research_dashboard.registry import add_project, set_governing_plan
from research_dashboard.settings import Settings


PROJECT_ID = "project-1"

TASK_PLAN = """\
### Task 1: Prepare data
- [x] **Step 1:** Read source
- [x] **Step 2:** Validate source

### Task 2: Run model
- [ ] **Step 1:** Execute model
- [ ] **Step 2:** Validate model
"""


@pytest.fixture
def connection(tmp_path):
    database = init_db(Settings(tmp_path / "runtime"))
    add_project(
        database,
        {"project_id": PROJECT_ID, "name": "Project One", "domain": "Research"},
    )
    try:
        yield database
    finally:
        database.close()


def write_plan(tmp_path, content=TASK_PLAN):
    path = tmp_path / "governing-plan.md"
    path.write_text(content, encoding="utf-8")
    return path


def plan_sha256(path):
    return sha256(path.read_bytes()).hexdigest()


def proposal_input(path, proposals):
    return {
        "project_id": PROJECT_ID,
        "source_plan_path": str(path),
        "source_plan_sha256": plan_sha256(path),
        "proposals": proposals,
    }


def test_parse_structured_plan_imports_task_headings_without_step_rows(tmp_path):
    path = write_plan(tmp_path)

    assert parse_structured_plan(path) == [
        {"title": "Prepare data", "status": "Done"},
        {"title": "Run model", "status": "Not started"},
    ]


def test_parse_structured_plan_uses_mixed_steps_and_ignores_fenced_headings(
    tmp_path,
):
    path = write_plan(
        tmp_path,
        """\
```markdown
### Task 99: Example only
- [x] Not a real roadmap item
```

### Task 3: Reconcile outcomes
- [x] Inspect source files
- [ ] Confirm coverage
""",
    )

    assert parse_structured_plan(path) == [
        {"title": "Reconcile outcomes", "status": "In progress"}
    ]


def test_parse_structured_plan_imports_only_top_level_checklist_items(tmp_path):
    path = write_plan(
        tmp_path,
        """\
- [x] Prepare analysis environment
  - [ ] Do not import nested detail
- [ ] Run the primary model
- [X] Write the report
""",
    )

    assert parse_structured_plan(path) == [
        {"title": "Prepare analysis environment", "status": "Done"},
        {"title": "Run the primary model", "status": "Not started"},
        {"title": "Write the report", "status": "Done"},
    ]


def test_parse_structured_plan_returns_none_for_prose_only_plan(tmp_path):
    path = write_plan(
        tmp_path,
        "# Governing plan\n\nInspect the source data before deciding the next action.\n",
    )

    assert parse_structured_plan(path) is None


def test_bootstrap_initializes_only_an_empty_project_roadmap(connection, tmp_path):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)

    result = bootstrap_roadmap_from_governing_plan(connection, PROJECT_ID)

    assert result["status"] == "current"
    assert result["plan_path"] == str(path)
    assert result["imported_count"] == 2
    assert [
        {"title": item["title"], "status": item["status"]}
        for item in list_roadmap_items(connection, PROJECT_ID)
    ] == [
        {"title": "Prepare data", "status": "Done"},
        {"title": "Run model", "status": "Not started"},
    ]
    for item, source_key in zip(
        list_roadmap_items(connection, PROJECT_ID),
        ["prepare-data-1", "run-model-2"],
        strict=True,
    ):
        assert item["created_by"] == "agent"
        assert item["source_plan_path"] == str(path)
        assert item["source_key"] == source_key
    assert plan_sync_status(connection, PROJECT_ID) == {
        "status": "current",
        "plan_path": str(path),
        "current_sha256": plan_sha256(path),
        "acknowledged_sha256": plan_sha256(path),
        "pending_proposal_count": 0,
    }


def test_bootstrap_imports_and_acknowledges_the_same_plan_byte_snapshot(
    connection, tmp_path, monkeypatch
):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    replacement = TASK_PLAN.replace("Prepare data", "Changed between reads")
    original_read_text = type(path).read_text

    def replace_before_text_read(candidate, *args, **kwargs):
        if candidate == path:
            candidate.write_text(replacement, encoding="utf-8")
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", replace_before_text_read)

    result = bootstrap_roadmap_from_governing_plan(connection, PROJECT_ID)

    assert result["imported_count"] == 2
    assert [item["title"] for item in list_roadmap_items(connection, PROJECT_ID)] == [
        "Prepare data",
        "Run model",
    ]
    acknowledged_sha256 = connection.execute(
        "SELECT acknowledged_sha256 FROM roadmap_sync_state WHERE project_id = ?",
        (PROJECT_ID,),
    ).fetchone()["acknowledged_sha256"]
    assert acknowledged_sha256 == plan_sha256(path)


def test_bootstrap_serializes_concurrent_empty_roadmap_imports(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "runtime")
    setup_connection = init_db(settings)
    path = write_plan(tmp_path)
    add_project(
        setup_connection,
        {"project_id": PROJECT_ID, "name": "Project One", "domain": "Research"},
    )
    set_governing_plan(setup_connection, PROJECT_ID, path)
    setup_connection.close()

    barrier = Barrier(2)
    original_list_roadmap_items = plan_sync.list_roadmap_items

    def synchronize_pretransaction_empty_check(connection, project_id):
        items = original_list_roadmap_items(connection, project_id)
        if not connection.in_transaction:
            barrier.wait(timeout=5)
        return items

    monkeypatch.setattr(
        plan_sync,
        "list_roadmap_items",
        synchronize_pretransaction_empty_check,
    )

    def bootstrap_in_separate_connection():
        worker_connection = connect_db(settings)
        try:
            return bootstrap_roadmap_from_governing_plan(worker_connection, PROJECT_ID)
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: bootstrap_in_separate_connection(), range(2)))

    verification_connection = connect_db(settings)
    try:
        assert sorted(result["imported_count"] for result in results) == [0, 2]
        assert [item["title"] for item in list_roadmap_items(verification_connection, PROJECT_ID)] == [
            "Prepare data",
            "Run model",
        ]
    finally:
        verification_connection.close()


def test_bootstrap_leaves_ambiguous_and_established_roadmaps_unchanged(
    connection, tmp_path
):
    prose_path = write_plan(tmp_path, "# Plan\n\nUse the available evidence.\n")
    set_governing_plan(connection, PROJECT_ID, prose_path)

    ambiguous = bootstrap_roadmap_from_governing_plan(connection, PROJECT_ID)

    assert ambiguous == {
        "status": "ambiguous",
        "plan_path": str(prose_path),
        "imported_count": 0,
    }
    assert list_roadmap_items(connection, PROJECT_ID) == []

    structured_path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, structured_path)
    bootstrap_roadmap_from_governing_plan(connection, PROJECT_ID)
    original_items = list_roadmap_items(connection, PROJECT_ID)
    structured_path.write_text(
        TASK_PLAN.replace("Run model", "Fit model"), encoding="utf-8"
    )

    result = bootstrap_roadmap_from_governing_plan(connection, PROJECT_ID)

    assert result["imported_count"] == 0
    assert list_roadmap_items(connection, PROJECT_ID) == original_items
    assert plan_sync_status(connection, PROJECT_ID)["status"] == "changed"


def test_bootstrap_ignores_workstream_only_governing_plans(connection, tmp_path):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path, workstream="secondary")

    result = bootstrap_roadmap_from_governing_plan(connection, PROJECT_ID)

    assert result["status"] == "uninitialized"
    assert result["imported_count"] == 0
    assert list_roadmap_items(connection, PROJECT_ID) == []


def test_plan_sync_status_reports_unavailable_registered_plan(connection, tmp_path):
    path = tmp_path / "missing-plan.md"
    set_governing_plan(connection, PROJECT_ID, path)

    assert plan_sync_status(connection, PROJECT_ID) == {
        "status": "unavailable",
        "plan_path": str(path),
        "current_sha256": None,
        "acknowledged_sha256": None,
        "pending_proposal_count": 0,
    }


def test_create_proposal_batch_validates_registered_current_plan_and_stamps_agent(
    connection, tmp_path
):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    value = proposal_input(
        path,
        [
            {
                "operation": "add",
                "title": "Review changed model",
                "status": "In progress",
            }
        ],
    )

    with pytest.raises(ValidationError):
        RoadmapProposalBatchInput.model_validate({**value, "created_by": "user"})
    with pytest.raises(ValueError, match="current plan hash"):
        create_proposal_batch(
            connection, {**value, "source_plan_sha256": "f" * 64}
        )

    created = create_proposal_batch(connection, value)

    assert created["created_by"] == "agent"
    assert created["status"] == "Pending"
    stored_json = connection.execute(
        "SELECT proposals_json FROM roadmap_proposal_batches WHERE batch_id = ?",
        (created["batch_id"],),
    ).fetchone()["proposals_json"]
    expected_proposals = RoadmapProposalBatchInput.model_validate(value).model_dump()[
        "proposals"
    ]
    assert json.loads(stored_json) == expected_proposals
    assert list_pending_proposal_batches(connection, PROJECT_ID) == [
        {**created, "proposals": expected_proposals}
    ]


def test_review_proposal_batch_applies_only_selected_operations_and_acknowledges(
    connection, tmp_path
):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    batch = create_proposal_batch(
        connection,
        proposal_input(
            path,
            [
                {"operation": "add", "title": "Do not apply"},
                {"operation": "add", "title": "Apply this update"},
            ],
        ),
    )

    reviewed = review_proposal_batch(
        connection, batch["batch_id"], accepted_indices=[1]
    )

    assert reviewed["status"] == "Reviewed"
    assert [item["title"] for item in list_roadmap_items(connection, PROJECT_ID)] == [
        "Apply this update"
    ]
    assert list_pending_proposal_batches(connection, PROJECT_ID) == []
    assert plan_sync_status(connection, PROJECT_ID) == {
        "status": "current",
        "plan_path": str(path),
        "current_sha256": plan_sha256(path),
        "acknowledged_sha256": plan_sha256(path),
        "pending_proposal_count": 0,
    }


def test_reviewing_ambiguous_plan_acknowledges_that_exact_version(connection, tmp_path):
    path = write_plan(tmp_path, "# Plan\n\nUse the available evidence.\n")
    set_governing_plan(connection, PROJECT_ID, path)
    assert plan_sync_status(connection, PROJECT_ID)["status"] == "ambiguous"
    batch = create_proposal_batch(
        connection,
        proposal_input(path, [{"operation": "add", "title": "Review evidence"}]),
    )

    review_proposal_batch(connection, batch["batch_id"], accepted_indices=[0])

    status = plan_sync_status(connection, PROJECT_ID)
    assert status["status"] == "current"
    assert status["current_sha256"] == plan_sha256(path)
    assert status["acknowledged_sha256"] == plan_sha256(path)
    assert status["pending_proposal_count"] == 0


def test_stale_proposal_batch_cannot_be_listed_or_reviewed(connection, tmp_path):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    batch = create_proposal_batch(
        connection,
        proposal_input(path, [{"operation": "add", "title": "Old plan update"}]),
    )
    path.write_text(TASK_PLAN.replace("Run model", "Fit changed model"), encoding="utf-8")

    assert list_pending_proposal_batches(connection, PROJECT_ID) == []
    assert plan_sync_status(connection, PROJECT_ID)["pending_proposal_count"] == 0
    with pytest.raises(ValueError, match="current plan version"):
        review_proposal_batch(connection, batch["batch_id"], accepted_indices=[0])

    assert list_roadmap_items(connection, PROJECT_ID) == []
    stored = connection.execute(
        "SELECT status FROM roadmap_proposal_batches WHERE batch_id = ?",
        (batch["batch_id"],),
    ).fetchone()
    assert stored["status"] == "Pending"
    assert plan_sync_status(connection, PROJECT_ID)["acknowledged_sha256"] is None


def test_same_plan_version_cannot_receive_repeated_proposal_batches(connection, tmp_path):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    first = create_proposal_batch(
        connection,
        proposal_input(path, [{"operation": "add", "title": "First proposal"}]),
    )

    with pytest.raises(ValueError, match="already has a pending proposal"):
        create_proposal_batch(
            connection,
            proposal_input(path, [{"operation": "add", "title": "Duplicate proposal"}]),
        )

    review_proposal_batch(connection, first["batch_id"], accepted_indices=[])
    with pytest.raises(ValueError, match="already acknowledged"):
        create_proposal_batch(
            connection,
            proposal_input(path, [{"operation": "add", "title": "Repeated proposal"}]),
        )


def test_review_proposal_batch_validates_indices_and_rolls_back_all_changes(
    connection, tmp_path
):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    parent = create_roadmap_item(
        connection, {"project_id": PROJECT_ID, "title": "Protected parent"}
    )
    create_roadmap_item(
        connection,
        {
            "project_id": PROJECT_ID,
            "parent_item_id": parent["roadmap_item_id"],
            "title": "Existing child",
        },
    )
    batch = create_proposal_batch(
        connection,
        proposal_input(
            path,
            [
                {"operation": "add", "title": "Must roll back"},
                {
                    "operation": "remove",
                    "roadmap_item_id": parent["roadmap_item_id"],
                },
            ],
        ),
    )

    with pytest.raises(ValueError, match="accepted indices"):
        review_proposal_batch(connection, batch["batch_id"], accepted_indices=[-1])
    with pytest.raises(ValueError, match="children"):
        review_proposal_batch(connection, batch["batch_id"], accepted_indices=[0, 1])

    assert [item["title"] for item in list_roadmap_items(connection, PROJECT_ID)] == [
        "Protected parent",
        "Existing child",
    ]
    assert list_pending_proposal_batches(connection, PROJECT_ID)[0]["batch_id"] == batch[
        "batch_id"
    ]
    assert plan_sync_status(connection, PROJECT_ID)["acknowledged_sha256"] is None


def test_review_revalidates_stored_proposal_json_before_application(connection, tmp_path):
    path = write_plan(tmp_path)
    set_governing_plan(connection, PROJECT_ID, path)
    batch = create_proposal_batch(
        connection,
        proposal_input(path, [{"operation": "add", "title": "Valid proposal"}]),
    )
    connection.execute(
        "UPDATE roadmap_proposal_batches SET proposals_json = ? WHERE batch_id = ?",
        ('[{"operation": "unknown"}]', batch["batch_id"]),
    )
    connection.commit()

    with pytest.raises(ValidationError):
        review_proposal_batch(connection, batch["batch_id"], accepted_indices=[0])
