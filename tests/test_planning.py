from datetime import date

import pytest
from pydantic import ValidationError

from research_dashboard import planning
from research_dashboard.db import init_db
from research_dashboard.domain import RoadmapItemInput, TodoInput
from research_dashboard.planning import (
    create_roadmap_item,
    delete_roadmap_item,
    list_roadmap_items,
    reorder_roadmap_items,
    roadmap_progress,
    update_roadmap_item,
)
from research_dashboard.registry import add_project
from research_dashboard.settings import Settings


PROJECT_ID = "project-1"


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


def roadmap_item(title, **overrides):
    value = {"project_id": PROJECT_ID, "title": title}
    value.update(overrides)
    return value


def todo(title, **overrides):
    value = {"project_id": PROJECT_ID, "title": title}
    value.update(overrides)
    return value


def records_by_id(connection):
    return {
        item["roadmap_item_id"]: item
        for item in list_roadmap_items(connection, PROJECT_ID)
    }


def test_roadmap_item_input_defaults_to_not_started_user_actor():
    item = RoadmapItemInput(project_id="project-1", title="Draft protocol")

    assert item.model_dump() == {
        "roadmap_item_id": None,
        "project_id": "project-1",
        "parent_item_id": None,
        "title": "Draft protocol",
        "status": "Not started",
        "note": None,
        "position": None,
        "source_plan_path": None,
        "source_key": None,
        "actor": "user",
    }


def test_roadmap_item_input_rejects_unknown_status_and_negative_position():
    with pytest.raises(ValidationError, match="status"):
        RoadmapItemInput(
            project_id="project-1", title="Draft protocol", status="Unknown"
        )

    with pytest.raises(ValidationError, match="position"):
        RoadmapItemInput(
            project_id="project-1", title="Draft protocol", position=-1
        )


def test_todo_input_accepts_date_and_defaults_to_open_normal_user_actor():
    todo = TodoInput(
        project_id="project-1",
        title="Review draft",
        due_date="2026-08-31",
    )

    assert todo.status == "Open"
    assert todo.priority == "Normal"
    assert todo.actor == "user"
    assert todo.due_date == date(2026, 8, 31)


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "Unknown"), ("priority", "Urgent"), ("actor", "system")],
)
def test_todo_input_rejects_unknown_enums(field, value):
    with pytest.raises(ValidationError, match=field):
        TodoInput(project_id="project-1", title="Review draft", **{field: value})


def test_roadmap_progress_counts_done_non_skipped_leaf_items(connection):
    for status in ("Done", "Done", "In progress", "Skipped"):
        create_roadmap_item(connection, roadmap_item(f"{status} item", status=status))

    assert roadmap_progress(list_roadmap_items(connection, PROJECT_ID)) == {
        "completed": 2,
        "total": 3,
    }


def test_create_roadmap_item_rejects_a_third_hierarchy_level(connection):
    parent = create_roadmap_item(connection, roadmap_item("Parent"))
    child = create_roadmap_item(
        connection,
        roadmap_item("Child", parent_item_id=parent["roadmap_item_id"]),
    )

    with pytest.raises(
        ValueError, match="roadmap hierarchy is limited to two levels"
    ):
        create_roadmap_item(
            connection,
            roadmap_item("Grandchild", parent_item_id=child["roadmap_item_id"]),
        )


def test_delete_roadmap_item_rejects_a_parent_without_deleting_rows(connection):
    parent = create_roadmap_item(connection, roadmap_item("Parent"))
    child = create_roadmap_item(
        connection,
        roadmap_item("Child", parent_item_id=parent["roadmap_item_id"]),
    )

    with pytest.raises(ValueError, match="cannot delete a roadmap item with children"):
        delete_roadmap_item(connection, PROJECT_ID, parent["roadmap_item_id"])

    assert records_by_id(connection) == {
        parent["roadmap_item_id"]: parent,
        child["roadmap_item_id"]: child,
    }


def test_update_rejects_reparenting_an_item_that_has_children(connection):
    parent = create_roadmap_item(connection, roadmap_item("Parent"))
    create_roadmap_item(
        connection,
        roadmap_item("Child", parent_item_id=parent["roadmap_item_id"]),
    )
    destination = create_roadmap_item(connection, roadmap_item("Destination"))

    with pytest.raises(
        ValueError, match="roadmap hierarchy is limited to two levels"
    ):
        update_roadmap_item(
            connection,
            parent["roadmap_item_id"],
            parent_item_id=destination["roadmap_item_id"],
        )

    assert records_by_id(connection)[parent["roadmap_item_id"]]["parent_item_id"] is None


def test_update_rejects_self_and_descendant_parenting_without_changing_hierarchy(
    connection,
):
    parent = create_roadmap_item(connection, roadmap_item("Parent"))
    child = create_roadmap_item(
        connection,
        roadmap_item("Child", parent_item_id=parent["roadmap_item_id"]),
    )

    with pytest.raises(ValueError):
        update_roadmap_item(
            connection,
            parent["roadmap_item_id"],
            parent_item_id=parent["roadmap_item_id"],
        )
    with pytest.raises(ValueError):
        update_roadmap_item(
            connection,
            parent["roadmap_item_id"],
            parent_item_id=child["roadmap_item_id"],
        )

    stored = records_by_id(connection)
    assert stored[parent["roadmap_item_id"]]["parent_item_id"] is None
    assert stored[child["roadmap_item_id"]]["parent_item_id"] == parent[
        "roadmap_item_id"
    ]


def test_update_roadmap_status_tracks_completed_at(connection):
    item = create_roadmap_item(
        connection,
        roadmap_item("Draft", status="In progress"),
    )

    done = update_roadmap_item(
        connection,
        item["roadmap_item_id"],
        status="Done",
    )
    reopened = update_roadmap_item(
        connection,
        item["roadmap_item_id"],
        status="In progress",
    )

    assert done["completed_at"] is not None
    assert reopened["completed_at"] is None


def test_reorder_roadmap_items_assigns_requested_sibling_positions(connection):
    first = create_roadmap_item(connection, roadmap_item("First"))
    second = create_roadmap_item(connection, roadmap_item("Second"))
    third = create_roadmap_item(connection, roadmap_item("Third"))

    reordered = reorder_roadmap_items(
        connection,
        PROJECT_ID,
        parent_item_id=None,
        ordered_item_ids=[
            third["roadmap_item_id"],
            first["roadmap_item_id"],
            second["roadmap_item_id"],
        ],
    )

    assert [item["roadmap_item_id"] for item in reordered] == [
        third["roadmap_item_id"],
        first["roadmap_item_id"],
        second["roadmap_item_id"],
    ]
    assert [item["position"] for item in reordered] == [0, 1, 2]


def test_reorder_roadmap_items_rejects_an_incomplete_sibling_list_without_changes(
    connection,
):
    first = create_roadmap_item(connection, roadmap_item("First"))
    second = create_roadmap_item(connection, roadmap_item("Second"))
    third = create_roadmap_item(connection, roadmap_item("Third"))
    before = records_by_id(connection)

    with pytest.raises(ValueError, match="exactly match the current siblings"):
        reorder_roadmap_items(
            connection,
            PROJECT_ID,
            parent_item_id=None,
            ordered_item_ids=[third["roadmap_item_id"], first["roadmap_item_id"]],
        )

    assert records_by_id(connection) == before


def test_create_todo_defaults_to_open_normal_without_due_date(connection):
    created = planning.create_todo(connection, todo("Review draft"))

    assert created["due_date"] is None
    assert created["status"] == "Open"
    assert created["priority"] == "Normal"


def test_create_todo_rejects_cross_project_roadmap_attachment_without_storing(
    connection,
):
    other_project_id = "project-2"
    add_project(
        connection,
        {"project_id": other_project_id, "name": "Other", "domain": "Research"},
    )
    roadmap = create_roadmap_item(connection, roadmap_item("Project A roadmap"))

    with pytest.raises(ValueError, match="roadmap attachment"):
        planning.create_todo(
            connection,
            {
                "project_id": other_project_id,
                "title": "Project B TODO",
                "roadmap_item_id": roadmap["roadmap_item_id"],
            },
        )

    assert planning.list_todos(connection, other_project_id) == []


def test_update_todo_to_done_moves_it_to_completed_summary(connection):
    created = planning.create_todo(connection, todo("Review draft"))

    completed = planning.update_todo(
        connection,
        created["todo_id"],
        status="Done",
    )
    summary = planning.planning_summary(connection, PROJECT_ID)

    assert completed["completed_at"] is not None
    assert summary["open_todos"] == []
    assert [item["todo_id"] for item in summary["completed_todos"]] == [
        created["todo_id"]
    ]


def test_suggest_todo_attachment_is_view_only(connection):
    roadmap = create_roadmap_item(
        connection,
        roadmap_item("Run X1-X2 sensitivity calibration"),
    )
    created = planning.create_todo(
        connection,
        todo("Review X1-X2 sensitivity calibration"),
    )

    suggestion = planning.suggest_todo_attachment(
        created,
        list_roadmap_items(connection, PROJECT_ID),
    )
    summary = planning.planning_summary(connection, PROJECT_ID)

    assert suggestion == roadmap
    assert created["roadmap_item_id"] is None
    assert planning.list_todos(connection, PROJECT_ID)[0]["roadmap_item_id"] is None
    assert summary["open_todos"][0]["suggested_roadmap_item"] == roadmap


def test_suggest_todo_attachment_returns_none_for_unrelated_title_at_cutoff(connection):
    create_roadmap_item(
        connection,
        roadmap_item("Run X1-X2 sensitivity calibration"),
    )
    created = planning.create_todo(connection, todo("Email advisor"))

    assert (
        planning.suggest_todo_attachment(
            created,
            list_roadmap_items(connection, PROJECT_ID),
            cutoff=0.60,
        )
        is None
    )


def test_planning_summary_reports_roadmap_progress_and_open_todo_count(connection):
    create_roadmap_item(connection, roadmap_item("Completed leaf", status="Done"))
    create_roadmap_item(
        connection,
        roadmap_item("Active leaf", status="In progress"),
    )
    for index in range(3):
        planning.create_todo(connection, todo(f"Open TODO {index}"))

    summary = planning.planning_summary(connection, PROJECT_ID)

    assert summary["roadmap_progress"] == {"completed": 1, "total": 2}
    assert summary["open_todo_count"] == 3


def test_list_and_reorder_todos_use_stored_open_display_order(connection):
    first = planning.create_todo(connection, todo("First"))
    second = planning.create_todo(connection, todo("Second"))
    third = planning.create_todo(connection, todo("Third"))

    reordered = planning.reorder_todos(
        connection,
        PROJECT_ID,
        [third["todo_id"], first["todo_id"], second["todo_id"]],
    )

    assert [item["todo_id"] for item in reordered] == [
        third["todo_id"],
        first["todo_id"],
        second["todo_id"],
    ]
    assert [item["position"] for item in planning.list_todos(connection, PROJECT_ID)] == [
        0,
        1,
        2,
    ]


def test_update_todo_serializes_and_clears_due_date(connection):
    created = planning.create_todo(connection, todo("Review draft"))

    scheduled = planning.update_todo(
        connection,
        created["todo_id"],
        due_date=date(2026, 8, 31),
    )
    cleared = planning.update_todo(
        connection,
        created["todo_id"],
        due_date=None,
    )

    assert scheduled["due_date"] == "2026-08-31"
    assert cleared["due_date"] is None


def test_update_roadmap_note_distinguishes_omission_from_explicit_null(connection):
    created = create_roadmap_item(
        connection,
        roadmap_item("Draft analysis", note="Keep this note"),
    )

    unchanged = update_roadmap_item(
        connection,
        created["roadmap_item_id"],
        status="In progress",
    )
    cleared = update_roadmap_item(
        connection,
        created["roadmap_item_id"],
        note=None,
    )

    assert unchanged["note"] == "Keep this note"
    assert cleared["note"] is None


def test_update_todo_note_distinguishes_omission_from_explicit_null(connection):
    created = planning.create_todo(
        connection,
        todo("Review analysis", note="Keep this note"),
    )

    unchanged = planning.update_todo(
        connection,
        created["todo_id"],
        priority="High",
    )
    cleared = planning.update_todo(
        connection,
        created["todo_id"],
        note=None,
    )

    assert unchanged["note"] == "Keep this note"
    assert cleared["note"] is None


def test_delete_todo_removes_the_project_todo(connection):
    created = planning.create_todo(connection, todo("Review draft"))

    planning.delete_todo(connection, PROJECT_ID, created["todo_id"])

    assert planning.list_todos(connection, PROJECT_ID) == []
