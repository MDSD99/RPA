"""MultiRPA flows endpoints; business rules belong to services."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.exceptions import InvalidTransitionError
from app.services.flow_service import VISUAL_WORKFLOW
from app.web.dependencies import Services
from app.web.schemas import CreateFlow, CreateFlowRun, ImportFlow, UpdateFlow

router = APIRouter()


@router.get("/api/flows")
def list_flows(
    services: Services,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return services.flows.list_flows(limit=limit, offset=offset)


@router.post("/api/flows", status_code=201)
def create_flow(body: CreateFlow, services: Services):
    return {"flow": services.flows.create_flow(**body.model_dump())}


@router.post("/api/flows/import", status_code=201)
def import_flow(body: ImportFlow, services: Services) -> dict[str, object]:
    return {"flow": services.flows.import_flow(body.document)}


@router.get("/api/flows/runs/by-job/{job_id}")
def flow_run_spec(job_id: UUID, services: Services):
    return services.flows.execution_spec(job_id)


@router.get("/api/flows/{flow_id}")
def get_flow(flow_id: UUID, services: Services):
    return {"flow": services.flows.get_flow(flow_id)}


@router.get("/api/flows/{flow_id}/export")
def export_flow(flow_id: UUID, services: Services) -> dict[str, object]:
    return services.flows.export_flow(flow_id)


@router.put("/api/flows/{flow_id}")
def update_flow(flow_id: UUID, body: UpdateFlow, services: Services):
    return {"flow": services.flows.update_flow(flow_id, **body.model_dump())}


@router.post("/api/flows/{flow_id}/runs", status_code=201)
def create_flow_run(flow_id: UUID, body: CreateFlowRun, services: Services):
    if VISUAL_WORKFLOW not in services.workflow_registry.names():
        raise InvalidTransitionError("Visual flow execution is not registered")
    return services.flows.start_run(flow_id, **body.model_dump())


@router.get("/api/flows/{flow_id}/runs")
def list_flow_runs(
    flow_id: UUID,
    services: Services,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return services.flows.list_runs(flow_id, limit=limit, offset=offset)
