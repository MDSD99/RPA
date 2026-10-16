"""MultiRPA system endpoints; business rules belong to services."""

from fastapi import APIRouter

from app.core.exceptions import DatabaseOperationError
from app.services.flow_service import VISUAL_WORKFLOW
from app.web.dependencies import Services
from app.workflows.node_contract import node_catalog

router = APIRouter()


@router.get("/api/health")
def health(services: Services):
    if not services.management.healthy():
        raise DatabaseOperationError("Database schema is unavailable")
    return {
        "status": "ok",
        "database": "connected",
        "workers_enabled": services.workers_enabled,
    }


@router.get("/api/overview")
def overview(services: Services):
    return {
        "counts": services.management.counts(),
        "workers": services.worker_snapshot(),
    }


@router.get("/api/workers")
def workers(services: Services):
    return {"items": services.worker_snapshot()}


@router.get("/api/workflows")
def workflows(services: Services):
    enabled = services.workers_enabled
    items = [
        {
            "name": "infrastructure_demo",
            "title": "Altyapı demosu",
            "description": "Başarı, kontrollü tekrar ve kesinti sonrası kurtarma senaryoları. "
            "Dış sistemde işlem yapmaz.",
            "kind": "demo",
            "registered": True,
            "workers_enabled": enabled,
        }
    ]
    items.extend(
        {
            "name": definition.name,
            "title": definition.title,
            "description": definition.description,
            "kind": "visual" if definition.name == VISUAL_WORKFLOW else "automation",
            "registered": True,
            "workers_enabled": enabled,
        }
        for definition in services.workflow_registry.definitions()
    )
    return {"items": items, "workers_enabled": enabled, "node_catalog": node_catalog()}
