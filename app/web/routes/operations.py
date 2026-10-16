"""MultiRPA operations endpoints; business rules belong to services."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.exceptions import ValidationError
from app.core.states import JobStatus, TransactionStatus
from app.services.flow_service import VISUAL_WORKFLOW
from app.services.management_service import job_data, transaction_data
from app.web.dependencies import Services
from app.web.schemas import CreateJob, CreateTransaction, DemoJob, EmptyBody, Reconcile

router = APIRouter()


@router.get("/api/jobs")
def jobs(
    services: Services,
    status: JobStatus | None = None,
    q: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return services.management.jobs(status=status, q=q, limit=limit, offset=offset)


@router.post("/api/jobs", status_code=201)
def create_job(body: CreateJob, services: Services):
    if body.workflow_name == VISUAL_WORKFLOW or body.job_id.startswith("flow:"):
        raise ValidationError("Visual jobs must be created from a saved flow version")
    return {"job": job_data(services.execution.create_job(**body.model_dump()))}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: UUID, services: Services):
    return {"job": job_data(services.execution.get_job(job_id))}


@router.get("/api/transactions")
def transactions(
    services: Services,
    status: TransactionStatus | None = None,
    reconciliation_required: bool | None = None,
    q: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return services.management.transactions(
        status=status,
        reconciliation_required=reconciliation_required,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.get("/api/jobs/{job_id}/transactions")
def job_transactions(
    job_id: UUID,
    services: Services,
    status: TransactionStatus | None = None,
    reconciliation_required: bool | None = None,
    q: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    services.execution.get_job(job_id)
    return services.management.transactions(
        job_id=job_id,
        status=status,
        reconciliation_required=reconciliation_required,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.post("/api/jobs/{job_id}/transactions", status_code=201)
def add_transaction(job_id: UUID, body: CreateTransaction, services: Services):
    job = services.execution.get_job(job_id)
    if job.workflow_name == VISUAL_WORKFLOW:
        raise ValidationError("Visual flow runs have one immutable input transaction")
    result = services.execution.add_transaction(job_id, **body.model_dump())
    return {"transaction": transaction_data(result)}


@router.post("/api/jobs/{job_id}/start")
def start_job(job_id: UUID, body: EmptyBody, services: Services):
    return {"job": job_data(services.execution.start_job(job_id))}


@router.post("/api/jobs/{job_id}/finalize")
def finalize_job(job_id: UUID, body: EmptyBody, services: Services):
    return {"job": job_data(services.execution.finalize_job(job_id))}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: UUID, body: EmptyBody, services: Services):
    return {"job": job_data(services.execution.cancel_job(job_id))}


@router.post("/api/jobs/{job_id}/recover")
def recover_job(job_id: UUID, body: EmptyBody, services: Services):
    execution = services.execution
    recovered = execution.recover_stale(job_id)
    return {
        "recovered": [transaction_data(item) for item in recovered],
        "count": len(recovered),
        "job": job_data(execution.get_job(job_id)),
    }


@router.get("/api/transactions/{transaction_id}")
def get_transaction(transaction_id: UUID, services: Services):
    return services.management.transaction_detail(services.execution, transaction_id)


@router.post("/api/transactions/{transaction_id}/reconcile")
def reconcile_transaction(transaction_id: UUID, body: Reconcile, services: Services):
    result = services.execution.reconcile(transaction_id, **body.model_dump())
    return {"transaction": transaction_data(result)}


@router.post("/api/demo-jobs", status_code=201)
def create_demo(body: DemoJob, services: Services):
    return services.management.create_demo(services.execution, **body.model_dump())
