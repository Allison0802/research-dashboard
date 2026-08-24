"""Read-only FastAPI presentation layer for the research dashboard."""

from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import connect_db, init_db
from .plan_sync import list_pending_proposal_batches, plan_sync_status
from .planning import planning_summary
from .provenance import path_uri
from .settings import Settings, load_settings
from .state import portfolio_query, portfolio_read_model, project_read_model


TEMPLATE_ROOT = Path(__file__).with_name("templates")
STATIC_ROOT = Path(__file__).with_name("static")


def context_actions(
    item: dict[str, Any], *, baseline: str = "last_reviewed"
) -> list[str]:
    """Return navigation actions supported by one event or project context."""
    del baseline
    return ["Open project"] if item.get("project_id") else []


def _dashboard_context(connection: Any) -> dict[str, Any]:
    return portfolio_read_model(connection)


def _project_page_context(connection: Any, project_id: str) -> dict[str, Any] | None:
    project = project_read_model(connection, project_id)
    if project is None:
        return None
    return {
        "project": project,
        "planning": planning_summary(connection, project_id),
        "plan_sync": plan_sync_status(connection, project_id),
        "pending_proposal_batches": list_pending_proposal_batches(
            connection, project_id
        ),
    }


def _unsupported_response(resource: str) -> JSONResponse:
    return JSONResponse(
        {
            "status": "unavailable",
            "resource": resource,
            "message": "This dashboard action is reserved for a later task.",
        },
        status_code=501,
    )


def _with_connection(settings: Settings, callback: Callable[[Any], Any]) -> Any:
    connection = connect_db(settings)
    try:
        return callback(connection)
    finally:
        connection.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    initialized = init_db(settings)
    initialized.close()

    application = FastAPI(title="Research Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATE_ROOT))
    templates.env.filters["registered_path_uri"] = path_uri
    application.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")
    application.state.settings = settings

    @application.get("/", name="index")
    def index(request: Request):
        context = _with_connection(settings, _dashboard_context)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context=context,
        )

    @application.get("/portfolio/queries/{query_name}", name="portfolio_query")
    def portfolio_query_route(
        query_name: str,
        baseline: str = "last_reviewed",
        checkpoint: str | None = None,
        as_of: str | None = None,
    ):
        try:
            if as_of is None:
                query_time = None
            else:
                try:
                    query_time = date.fromisoformat(as_of)
                except ValueError:
                    query_time = datetime.fromisoformat(as_of)
                    if query_time.tzinfo is None:
                        raise ValueError("as_of must include a timezone")
            results = _with_connection(
                settings,
                lambda connection: portfolio_query(
                    connection,
                    query_name,
                    baseline=baseline,
                    checkpoint_name=checkpoint,
                    as_of=query_time,
                ),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "query": query_name,
            "baseline": baseline,
            "checkpoint": checkpoint,
            "results": [
                {
                    **item,
                    "actions": context_actions(item, baseline=baseline),
                }
                for item in results
            ],
        }

    @application.get("/projects/{project_id}", name="project")
    def project(request: Request, project_id: str):
        context = _with_connection(
            settings, lambda connection: _project_page_context(connection, project_id)
        )
        if context is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return templates.TemplateResponse(
            request=request,
            name="project.html",
            context=context,
        )

    @application.get("/risks/{risk_id}", name="risk")
    def risk(risk_id: str):
        return _unsupported_response(f"risk:{risk_id}")

    return application
