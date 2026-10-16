"""MultiRPA application composition; route adapters live in app.web.routes."""

import os
import re
from html import escape
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from app.core.branding import PRODUCT_NAME
from app.core.config import ConfigurationError
from app.database.connection import create_db_engine
from app.web.errors import register_error_handlers
from app.web.middleware import LocalRequestBoundary
from app.web.routes import flows, operations, schedules, system
from app.web.runtime import build_lifespan
from app.workflows.registered import build_registry
from app.workflows.registry import WorkflowRegistry

_WEB = Path(__file__).resolve().parent


def _allowed_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "testserver"}
    for host in os.environ.get("RPA_ALLOWED_HOSTS", "").split(","):
        host = host.strip().lower()
        if not host:
            continue
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ConfigurationError("RPA_ALLOWED_HOSTS contains an invalid hostname")
        hosts.add(host)
    return hosts


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    start_workers: bool = True,
    workflow_registry: WorkflowRegistry | None = None,
) -> FastAPI:
    registry = build_registry() if workflow_registry is None else workflow_registry
    if not isinstance(registry, WorkflowRegistry):
        raise ConfigurationError("Workflow registry is invalid")
    application = FastAPI(
        title=PRODUCT_NAME,
        lifespan=build_lifespan(
            session_factory=session_factory,
            start_workers=start_workers,
            registry=registry,
            custom_registry=workflow_registry is not None,
            engine_factory=create_db_engine,
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(LocalRequestBoundary, allowed_hosts=_allowed_hosts())
    register_error_handlers(application)
    for router in (system.router, flows.router, schedules.router, operations.router):
        application.include_router(router)

    @application.get("/", include_in_schema=False, response_class=HTMLResponse)
    def index() -> HTMLResponse:
        template = (_WEB / "templates" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            template.replace("__PRODUCT_NAME__", escape(PRODUCT_NAME)),
            headers={"Cache-Control": "no-store"},
        )

    application.mount(
        "/static", StaticFiles(directory=_WEB / "static", check_dir=False), name="static"
    )
    return application
