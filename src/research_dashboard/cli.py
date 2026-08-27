"""Command-line interface for the local research dashboard."""

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any, Sequence
from uuid import uuid4

from .db import connect_db, create_snapshot, init_db, transaction
from .domain import (
    PlanningMutationBatch,
    ProposalBatchOperation,
    RoadmapAddOperation,
    RoadmapUpdateOperation,
    SemanticEventInput,
    TodoAddOperation,
    TodoUpdateOperation,
)
from .executions import list_executions, register_execution
from .plan_sync import bootstrap_roadmap_from_governing_plan, create_proposal_batch
from .planning import (
    UNSET,
    create_roadmap_item,
    create_todo,
    list_roadmap_items,
    list_todos,
    update_roadmap_item,
    update_todo,
)
from .registry import (
    add_project,
    add_project_root,
    list_projects,
    resolve_project_for_path,
    set_governing_plan,
)
from .reviews import create_named_checkpoint, mark_reviewed
from .risks import accept_risk, resolve_risk, transition_risk
from .settings import load_settings
from .state import portfolio_query
from .writer import DashboardWriteError, submit_event


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _parse_query_time(value: str) -> datetime | date:
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError as error:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise argparse.ArgumentTypeError(
                "--as-of must be an ISO-8601 date or datetime"
            ) from error
        if parsed.tzinfo is None:
            raise argparse.ArgumentTypeError("--as-of must include a timezone")
        return parsed
    else:
        return parsed_date


def _add_project_parser(subparsers: argparse._SubParsersAction) -> None:
    project = subparsers.add_parser("project")
    commands = project.add_subparsers(dest="project_command", required=True)

    add = commands.add_parser("add")
    add.add_argument("--project-id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--domain", required=True)
    add.add_argument("--context-path")
    add.add_argument("--lifecycle", default="Needs classification")
    add.add_argument("--update-horizon-minutes", type=int)

    root_add = commands.add_parser("root-add")
    root_add.add_argument("--project-id", required=True)
    root_add.add_argument("--path", required=True)

    resolve_path = commands.add_parser("resolve-path")
    resolve_path.add_argument("--path", required=True)

    plan_set = commands.add_parser("plan-set")
    plan_set.add_argument("--project-id", required=True)
    plan_set.add_argument("--path", required=True)
    plan_set.add_argument("--workstream")
    plan_set.add_argument("--plan-id")

    commands.add_parser("list")


def _add_risk_parser(subparsers: argparse._SubParsersAction) -> None:
    risk = subparsers.add_parser("risk")
    commands = risk.add_subparsers(dest="risk_command", required=True)
    for command in ("accept", "resolve", "waiting"):
        action = commands.add_parser(command)
        action.add_argument("risk_id")


def _add_execution_parser(subparsers: argparse._SubParsersAction) -> None:
    execution = subparsers.add_parser("execution")
    commands = execution.add_subparsers(dest="execution_command", required=True)
    list_command = commands.add_parser("list")
    list_command.add_argument("--active-only", action="store_true")


def _add_slurm_parser(subparsers: argparse._SubParsersAction) -> None:
    slurm = subparsers.add_parser("slurm")
    commands = slurm.add_subparsers(dest="slurm_command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--execution-id", required=True)
    register.add_argument("--job-id", required=True)
    register.add_argument("--project-id")
    register.add_argument("--workstream")
    register.add_argument("--task-key")
    poll = commands.add_parser("poll")
    poll.add_argument("--execution-id", required=True)
    poll.add_argument("--ssh-target", required=True)


def _add_portfolio_parser(subparsers: argparse._SubParsersAction) -> None:
    portfolio = subparsers.add_parser("portfolio")
    commands = portfolio.add_subparsers(dest="portfolio_command", required=True)
    query = commands.add_parser("query")
    query.add_argument("query_name")
    query.add_argument(
        "--baseline",
        choices=("last-reviewed", "named-checkpoint"),
        default="last-reviewed",
    )
    query.add_argument("--checkpoint")
    query.add_argument("--as-of", type=_parse_query_time)


def _add_planning_parser(subparsers: argparse._SubParsersAction) -> None:
    planning = subparsers.add_parser("planning")
    commands = planning.add_subparsers(dest="planning_command", required=True)
    sync_plan = commands.add_parser("sync-plan")
    sync_plan.add_argument("--project-id", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--input", required=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-dashboard")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    snapshot = commands.add_parser("snapshot")
    snapshot_commands = snapshot.add_subparsers(
        dest="snapshot_command", required=True
    )
    create = snapshot_commands.add_parser("create")
    create.add_argument("--destination", required=True)
    _add_project_parser(commands)
    _add_risk_parser(commands)
    _add_execution_parser(commands)
    _add_slurm_parser(commands)
    _add_portfolio_parser(commands)
    _add_planning_parser(commands)

    event = commands.add_parser("event")
    event_commands = event.add_subparsers(dest="event_command", required=True)
    event_add = event_commands.add_parser("add")
    event_add.add_argument("--input", required=True)

    review = commands.add_parser("review")
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_mark = review_commands.add_parser("mark")
    review_mark.add_argument("--through-sequence", type=int)

    checkpoint = commands.add_parser("checkpoint")
    checkpoint_commands = checkpoint.add_subparsers(
        dest="checkpoint_command", required=True
    )
    checkpoint_add = checkpoint_commands.add_parser("add")
    checkpoint_add.add_argument("--name", required=True)
    checkpoint_add.add_argument("--through-sequence", type=int)
    checkpoint_add.add_argument("--checkpoint-id")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def _read_event_input(source: str) -> SemanticEventInput:
    if source == "-":
        payload = json.load(sys.stdin)
    else:
        with Path(source).open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("event input must be a JSON object")
    return SemanticEventInput.model_validate(payload)


def _read_planning_mutation_batch(source: str) -> PlanningMutationBatch:
    if source == "-":
        payload = json.load(sys.stdin)
    else:
        with Path(source).open(encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("planning input must be a JSON object")
    return PlanningMutationBatch.model_validate(payload)


def _project_roadmap_item_ids(connection: Any, project_id: str) -> set[str]:
    return {
        item["roadmap_item_id"]
        for item in list_roadmap_items(connection, project_id)
    }


def _project_todo_ids(connection: Any, project_id: str) -> set[str]:
    return {todo["todo_id"] for todo in list_todos(connection, project_id)}


def _apply_planning_mutation_batch(
    connection: Any,
    batch: PlanningMutationBatch,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with transaction(connection):
        for operation in batch.operations:
            if isinstance(operation, RoadmapAddOperation):
                result = create_roadmap_item(
                    connection,
                    {
                        "project_id": batch.project_id,
                        "title": operation.title,
                        "status": operation.status,
                        "note": operation.note,
                        "parent_item_id": operation.parent_item_id,
                        "actor": "agent",
                    },
                )
            elif isinstance(operation, RoadmapUpdateOperation):
                if operation.roadmap_item_id not in _project_roadmap_item_ids(
                    connection, batch.project_id
                ):
                    raise ValueError("roadmap item does not exist in the project")
                result = update_roadmap_item(
                    connection,
                    operation.roadmap_item_id,
                    title=(
                        operation.title
                        if "title" in operation.model_fields_set
                        else None
                    ),
                    status=(
                        operation.status
                        if "status" in operation.model_fields_set
                        else None
                    ),
                    note=(
                        operation.note
                        if "note" in operation.model_fields_set
                        else UNSET
                    ),
                    actor="agent",
                )
            elif isinstance(operation, TodoAddOperation):
                result = create_todo(
                    connection,
                    {
                        "project_id": batch.project_id,
                        "title": operation.title,
                        "priority": operation.priority,
                        "due_date": operation.due_date,
                        "note": operation.note,
                        "roadmap_item_id": operation.roadmap_item_id,
                        "actor": "agent",
                    },
                )
            elif isinstance(operation, TodoUpdateOperation):
                if operation.todo_id not in _project_todo_ids(
                    connection, batch.project_id
                ):
                    raise ValueError("todo does not exist in the project")
                result = update_todo(
                    connection,
                    operation.todo_id,
                    status=(
                        operation.status
                        if "status" in operation.model_fields_set
                        else None
                    ),
                    priority=(
                        operation.priority
                        if "priority" in operation.model_fields_set
                        else None
                    ),
                    due_date=(
                        operation.due_date
                        if "due_date" in operation.model_fields_set
                        else UNSET
                    ),
                    note=(
                        operation.note
                        if "note" in operation.model_fields_set
                        else UNSET
                    ),
                    roadmap_item_id=(
                        operation.roadmap_item_id
                        if "roadmap_item_id" in operation.model_fields_set
                        else UNSET
                    ),
                    actor="agent",
                )
            else:
                assert isinstance(operation, ProposalBatchOperation)
                result = create_proposal_batch(
                    connection,
                    {
                        "project_id": batch.project_id,
                        "source_plan_path": operation.source_plan_path,
                        "source_plan_sha256": operation.source_plan_sha256,
                        "proposals": [
                            proposal.model_dump(mode="json")
                            for proposal in operation.proposals
                        ],
                    },
                )
            results.append(result)
    return results


def _serve(args: argparse.Namespace) -> None:
    from importlib import import_module
    import uvicorn

    web = import_module("research_dashboard.web")
    application = web.create_app()
    uvicorn.run(application, host=args.host, port=args.port)


def _run_command(args: argparse.Namespace) -> None:
    settings = load_settings()

    if args.command == "init":
        connection = init_db(settings)
        try:
            _json({"initialized": True, "database_path": str(settings.database_path)})
        finally:
            connection.close()
        return

    if args.command == "serve":
        _serve(args)
        return

    if args.command == "snapshot":
        _json(str(create_snapshot(args.destination, settings)))
        return

    if args.command == "event":
        event = _read_event_input(args.input)
        _json(submit_event(event))
        return

    read_only = args.command == "portfolio"
    connection = connect_db(settings, read_only=read_only)
    try:
        if args.command == "project":
            if args.project_command == "add":
                _json(
                    add_project(
                        connection,
                        {
                            "project_id": args.project_id,
                            "name": args.name,
                            "domain": args.domain,
                            "context_path": args.context_path,
                            "lifecycle": args.lifecycle,
                            "update_horizon_minutes": args.update_horizon_minutes,
                        },
                    )
                )
            elif args.project_command == "root-add":
                _json(add_project_root(connection, args.project_id, args.path))
            elif args.project_command == "resolve-path":
                _json(resolve_project_for_path(connection, args.path))
            elif args.project_command == "plan-set":
                plan_record = set_governing_plan(
                    connection,
                    args.project_id,
                    args.path,
                    workstream=args.workstream,
                    plan_id=args.plan_id,
                )
                if args.workstream is None:
                    _json(
                        {
                            "governing_plan": plan_record,
                            "roadmap_sync": bootstrap_roadmap_from_governing_plan(
                                connection, args.project_id
                            ),
                        }
                    )
                else:
                    _json(plan_record)
            else:
                _json(list_projects(connection))
        elif args.command == "execution":
            _json(list_executions(connection, active_only=args.active_only))
        elif args.command == "slurm":
            from .adapters.slurm import (
                poll_slurm_execution,
                validate_slurm_job_id,
            )

            if args.slurm_command == "register":
                args.job_id = validate_slurm_job_id(args.job_id)
                _json(
                    register_execution(
                        connection,
                        {
                            "execution_id": args.execution_id,
                            "backend": "slurm",
                            "external_id": args.job_id,
                            "current_state": "SUBMITTED",
                            "project_id": args.project_id,
                            "workstream": args.workstream,
                            "task_key": args.task_key,
                        },
                    )
                )
            else:
                _json(
                    poll_slurm_execution(
                        connection,
                        execution_id=args.execution_id,
                        ssh_target=args.ssh_target,
                    )
                )
        elif args.command == "portfolio":
            from .web import context_actions

            baseline = args.baseline.replace("-", "_")
            if baseline == "last_reviewed" and args.checkpoint is not None:
                raise ValueError(
                    "--checkpoint is only valid with --baseline named-checkpoint"
                )
            results = portfolio_query(
                connection,
                args.query_name,
                baseline=baseline,
                checkpoint_name=args.checkpoint,
                as_of=args.as_of,
            )
            _json(
                {
                    "query": args.query_name,
                    "baseline": args.baseline,
                    "checkpoint": args.checkpoint,
                    "results": [
                        {
                            **item,
                            "actions": context_actions(item, baseline=baseline),
                        }
                        for item in results
                    ],
                }
            )
        elif args.command == "review":
            _json(mark_reviewed(connection, args.through_sequence))
        elif args.command == "risk":
            if args.risk_command == "accept":
                result = accept_risk(connection, args.risk_id)
            elif args.risk_command == "resolve":
                result = resolve_risk(connection, args.risk_id)
            else:
                result = transition_risk(connection, args.risk_id, "Waiting")
            _json(result)
        elif args.command == "planning":
            if args.planning_command == "sync-plan":
                _json(
                    bootstrap_roadmap_from_governing_plan(
                        connection,
                        args.project_id,
                        actor="agent",
                    )
                )
            else:
                _json(
                    _apply_planning_mutation_batch(
                        connection,
                        _read_planning_mutation_batch(args.input),
                    )
                )
        else:
            _json(
                create_named_checkpoint(
                    connection,
                    args.name,
                    args.through_sequence,
                    args.checkpoint_id,
                )
            )
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dashboard CLI and return a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        _run_command(args)
    except DashboardWriteError as error:
        print(
            json.dumps(
                {
                    "error": {
                        "code": error.code,
                        "transient": error.transient,
                        "message": str(error),
                    }
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0
