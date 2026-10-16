"""Publish a flow run inside a caller-owned atomic database transaction."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import IdempotencyConflictError, NotFoundError
from app.core.states import JobStatus
from app.core.tenancy import LEGACY_TENANT_ID
from app.database.repositories.audit_repository import AuditRepository
from app.database.repositories.flow_repository import FlowRepository
from app.database.repositories.job_repository import JobRepository
from app.database.repositories.transaction_repository import TransactionRepository
from app.entitlements.access import assert_access
from app.services.job_lifecycle import transition_job
from app.services.management_service import job_data
from app.workflows.flow_contract import VISUAL_WORKFLOW
from app.workflows.flow_engine import validate_graph


def publish_flow_run(
    session: Session,
    *,
    tenant_id: UUID = LEGACY_TENANT_ID,
    flow_id: UUID,
    request_id: UUID,
    version: int,
    inputs: dict,
    inputs_digest: str,
    max_attempts: int,
) -> dict:
    """Publish validated inputs without opening, committing, or closing a session.

    The scheduler advances its due slot in this same transaction. A crash can
    publish both changes or neither, and never an untracked partially ready job.
    """
    assert_access(session, tenant_id)
    repository = FlowRepository(session, tenant_id)
    revision = repository.version(flow_id, version)
    if revision is None:
        raise NotFoundError("Flow revision not found")
    # The unique job key serializes concurrent requests, including requests
    # racing across different flows. Nothing is visible until this commit.
    job, created = JobRepository(session, tenant_id).create_once(
        f"flow:{request_id}", VISUAL_WORKFLOW
    )
    if not created:
        run = repository.run(job.id)
        if (
            run is None
            or job.workflow_name != VISUAL_WORKFLOW
            or run.request_id != request_id
            or run.flow_id != flow_id
            or run.version != version
            or run.inputs_digest != inputs_digest
            or run.max_attempts != max_attempts
        ):
            raise IdempotencyConflictError("Run request configuration differs")
        return {"job": job_data(job), "flow_id": str(flow_id), "version": version}
    # A revision may have been stored by a newer producer. Validate the pinned
    # definition before publishing a new run; failure rolls back create_once.
    # Existing idempotent requests still return their original job unchanged.
    validate_graph(revision.graph)
    audit = AuditRepository(session, tenant_id)
    audit.record(job, "JOB_CREATED", new_status=job.status.value)
    transaction, transaction_created = TransactionRepository(session, tenant_id).create_once(
        job_id=job.id,
        workflow_name=VISUAL_WORKFLOW,
        record_id=f"flow-run:{request_id}",
        idempotency_key=f"flow-run:{request_id}",
        external_reference=str(flow_id),
        max_attempts=max_attempts,
    )
    if not transaction_created:
        raise IdempotencyConflictError("Run transaction is already reserved")
    job.total_records = 1
    audit.record(
        job,
        "TRANSACTION_CREATED",
        transaction=transaction,
        new_status=transaction.status.value,
    )
    repository.add_run(
        job_id=job.id,
        request_id=request_id,
        flow_id=flow_id,
        version=version,
        inputs=inputs,
        inputs_digest=inputs_digest,
        max_attempts=max_attempts,
    )
    now = JobRepository(session, tenant_id).now()
    transition_job(job, JobStatus.RUNNING, now)
    job.started_at = now
    audit.record(
        job, "JOB_STARTED", old_status=JobStatus.PENDING.value, new_status=job.status.value
    )
    return {"job": job_data(job), "flow_id": str(flow_id), "version": version}
