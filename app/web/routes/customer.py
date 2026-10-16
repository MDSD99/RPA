"""Authenticated customer surface, excluding unrestricted job creation and demos."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.web.dependencies import Services
from app.web.portal import Portal, check_csrf, request_throttle, session_token
from app.web.routes import operations
from app.web.runtime import WebServices
from app.workflows.node_contract import node_catalog


def customer_services(request: Request, portal: Portal) -> WebServices:
    token = (
        session_token(request, portal, "customer")
        if request.method in {"GET", "HEAD", "OPTIONS"}
        else check_csrf(request, portal, "customer")
    )
    account = portal.identity.require_approved(token)
    return request.app.state.customer_runtime.scoped(UUID(account.tenant_id))


Customer = Annotated[WebServices, Depends(customer_services)]
router = APIRouter(dependencies=[Depends(request_throttle)])

# These operations already use the tenant-scoped dependency and enforce durable
# execution transitions. A customer cannot submit an arbitrary workflow/job.
for path, method, endpoint in (
    ("/api/jobs", "GET", operations.jobs),
    ("/api/jobs/{job_id}", "GET", operations.get_job),
    ("/api/jobs/{job_id}/transactions", "GET", operations.job_transactions),
    ("/api/jobs/{job_id}/cancel", "POST", operations.cancel_job),
    ("/api/jobs/{job_id}/finalize", "POST", operations.finalize_job),
    ("/api/jobs/{job_id}/recover", "POST", operations.recover_job),
    ("/api/transactions", "GET", operations.transactions),
    ("/api/transactions/{transaction_id}", "GET", operations.get_transaction),
    ("/api/transactions/{transaction_id}/reconcile", "POST", operations.reconcile_transaction),
):
    router.add_api_route(path, endpoint, methods=[method])


@router.get("/api/overview")
def overview(services: Services) -> dict[str, object]:
    return {"counts": services.management.counts(), "workers": []}


@router.get("/api/workflows")
def workflows(services: Services) -> dict[str, object]:
    return {
        "node_catalog": node_catalog(),
        "items": [
            {
                "name": definition.name,
                "title": definition.title,
                "description": definition.description,
                "kind": "visual",
                "registered": True,
                "workers_enabled": services.workers_enabled,
            }
            for definition in services.workflow_registry.definitions()
        ],
        "workers_enabled": services.workers_enabled,
    }


@router.get("/api/health")
def health(services: Services) -> dict[str, object]:
    return {"status": "ok" if services.management.healthy() else "unavailable"}
