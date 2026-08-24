import sqlite3

import pytest

from research_dashboard.db import init_db
from research_dashboard.domain import ProjectInput
from research_dashboard.registry import (
    add_project,
    add_project_root,
    get_active_governing_plan,
    get_project,
    list_projects,
    resolve_project_for_path,
    set_governing_plan,
)
from research_dashboard.settings import Settings


@pytest.fixture
def connection(tmp_path):
    database = init_db(Settings(tmp_path / "runtime"))
    try:
        yield database
    finally:
        database.close()


def project(project_id="project-1", **overrides):
    values = {
        "project_id": project_id,
        "name": "Project",
        "domain": "Research",
        "lifecycle": "Active",
    }
    values.update(overrides)
    return ProjectInput(**values)


def test_add_project_and_retrieve_it(connection, tmp_path):
    context_path = tmp_path / "project" / "CONTEXT.md"
    add_project(connection, project(context_path=str(context_path)))

    stored = get_project(connection, "project-1")

    assert stored["project_id"] == "project-1"
    assert stored["name"] == "Project"
    assert stored["context_path"] == str(context_path.resolve())
    assert stored["context_status"] == "Context coverage incomplete"


def test_add_project_rejects_duplicate_id(connection):
    add_project(connection, project())

    with pytest.raises(sqlite3.IntegrityError):
        add_project(connection, project())


def test_project_accepts_arbitrary_domain(connection):
    project = add_project(
        connection,
        {
            "project_id": "climate-forecasting",
            "name": "Climate Forecasting",
            "domain": "Climate Science",
        },
    )
    assert project["domain"] == "Climate Science"


def test_registry_supports_more_than_four_projects(connection):
    for i in range(6):
        add_project(
            connection,
            {
                "project_id": f"project-{i}",
                "name": f"Project {i}",
                "domain": f"Domain {i}",
            },
        )
    assert len(list_projects(connection)) == 6


def test_add_project_supports_multiple_normalized_roots(connection, tmp_path):
    add_project(connection, project())
    first_root = tmp_path / "research" / ".." / "research"
    second_root = tmp_path / "other"

    add_project_root(connection, "project-1", first_root)
    add_project_root(connection, "project-1", second_root)

    roots = {
        row["root_path"]
        for row in connection.execute(
            "SELECT root_path FROM project_roots WHERE project_id = ?",
            ("project-1",),
        )
    }
    assert roots == {str(first_root.resolve()), str(second_root.resolve())}


def test_add_project_root_rejects_normalized_root_for_another_project(
    connection, tmp_path
):
    add_project(connection, project("project-1"))
    add_project(connection, project("project-2", name="Second"))
    root = tmp_path / "research"
    add_project_root(connection, "project-1", root)

    with pytest.raises(ValueError, match="already registered"):
        add_project_root(connection, "project-2", root / ".." / "research")

    owners = connection.execute(
        "SELECT project_id FROM project_roots WHERE root_path = ?",
        (str(root.resolve()),),
    ).fetchall()
    assert [row["project_id"] for row in owners] == ["project-1"]


def test_resolve_project_from_child_path(connection, tmp_path):
    add_project(connection, project())
    root = tmp_path / "research"
    add_project_root(connection, "project-1", root)

    resolved = resolve_project_for_path(connection, root / "src" / "analysis.R")

    assert resolved["project_id"] == "project-1"


def test_resolve_project_prefers_longest_matching_registered_root(
    connection, tmp_path
):
    add_project(connection, project("parent", name="Parent"))
    add_project(connection, project("nested", name="Nested"))
    parent_root = tmp_path / "research"
    nested_root = parent_root / "nested"
    add_project_root(connection, "parent", parent_root)
    add_project_root(connection, "nested", nested_root)

    resolved = resolve_project_for_path(
        connection, nested_root / "results" / "summary.csv"
    )

    assert resolved["project_id"] == "nested"


def test_set_governing_plan_returns_active_plan(connection, tmp_path):
    add_project(connection, project())
    plan_path = tmp_path / "plans" / "analysis.md"

    plan = set_governing_plan(
        connection,
        "project-1",
        plan_path,
        workstream="analysis",
    )

    assert plan["path"] == str(plan_path.resolve())
    assert plan["workstream"] == "analysis"
    assert plan["active"] == 1
    assert get_active_governing_plan(connection, "project-1", "analysis")[
        "plan_id"
    ] == plan["plan_id"]


def test_setting_governing_plan_replaces_only_same_workstream(
    connection, tmp_path
):
    add_project(connection, project())
    first = set_governing_plan(
        connection, "project-1", tmp_path / "first.md", workstream="analysis"
    )
    second = set_governing_plan(
        connection, "project-1", tmp_path / "second.md", workstream="analysis"
    )
    other = set_governing_plan(
        connection, "project-1", tmp_path / "other.md", workstream="writing"
    )

    active = connection.execute(
        "SELECT plan_id, workstream, active FROM governing_plans "
        "WHERE project_id = ? ORDER BY plan_id",
        ("project-1",),
    ).fetchall()

    assert first["active"] == 1
    assert second["active"] == 1
    assert other["active"] == 1
    analysis_active = {
        row["plan_id"]: row["active"]
        for row in active
        if row["workstream"] == "analysis"
    }
    assert analysis_active == {first["plan_id"]: 0, second["plan_id"]: 1}
    assert get_active_governing_plan(connection, "project-1", "analysis")["path"] == str(
        (tmp_path / "second.md").resolve()
    )


@pytest.mark.parametrize("workstream", [None, "analysis"])
def test_database_rejects_two_active_plans_for_same_project_workstream(
    connection, tmp_path, workstream
):
    add_project(connection, project())
    first = set_governing_plan(
        connection, "project-1", tmp_path / "first.md", workstream=workstream
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO governing_plans "
            "(plan_id, project_id, workstream, path, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (
                "duplicate-active-plan",
                "project-1",
                workstream,
                str((tmp_path / "duplicate.md").resolve()),
            ),
        )

    active = connection.execute(
        "SELECT plan_id FROM governing_plans "
        "WHERE project_id = ? AND active = 1",
        ("project-1",),
    ).fetchall()
    assert [row["plan_id"] for row in active] == [first["plan_id"]]


def test_set_governing_plan_rolls_back_when_insert_fails(connection, tmp_path):
    add_project(connection, project())
    first = set_governing_plan(connection, "project-1", tmp_path / "first.md")

    with pytest.raises(sqlite3.IntegrityError):
        set_governing_plan(
            connection,
            "project-1",
            tmp_path / "replacement.md",
            plan_id=first["plan_id"],
        )

    assert get_active_governing_plan(connection, "project-1")["plan_id"] == first[
        "plan_id"
    ]
    assert connection.execute(
        "SELECT active FROM governing_plans WHERE plan_id = ?",
        (first["plan_id"],),
    ).fetchone()["active"] == 1


def test_registry_rejects_connection_without_sqlite_row_factory():
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="sqlite3.Row"):
            get_project(connection, "project-1")
    finally:
        connection.close()


def test_registry_rejects_connection_without_foreign_keys():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        with pytest.raises(ValueError, match="foreign keys"):
            get_project(connection, "project-1")
    finally:
        connection.close()


def test_registry_has_no_scheduler_registry_contract():
    import research_dashboard.registry as registry

    assert not hasattr(registry, "add_cluster")


@pytest.mark.parametrize("context_path", [None, "/path/that/is/not/available/CONTEXT.md"])
def test_missing_or_unavailable_context_is_reported_without_blocking(
    connection, context_path
):
    add_project(connection, project(lifecycle="Active", context_path=context_path))

    stored = get_project(connection, "project-1")

    assert stored["context_status"] == "Context coverage incomplete"
    assert stored["lifecycle"] == "Active"


def test_list_projects_returns_all_registered_projects(connection):
    add_project(connection, project("project-1"))
    add_project(connection, project("project-2", name="Second"))

    projects = list_projects(connection)

    assert [item["project_id"] for item in projects] == ["project-1", "project-2"]
