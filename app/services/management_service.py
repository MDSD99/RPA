"""Safe read models for the local management UI, with explicit field allowlists."""

from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import DatabaseOperationError, IdempotencyConflictError, ValidationError
from app.core.states import JobStatus
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.repositories.management_repository import ManagementRepository

JOB_FIELDS = (
    "id",
    "job_id",
    "workflow_name",
    "status",
    "started_at",
    "finished_at",
    "total_records",
    "success_count",
    "failed_count",
    "skipped_count",
    "created_at",
    "updated_at",
)
TRANSACTION_FIELDS = (
    "id",
    "job_id",
    "workflow_name",
    "record_id",
    "idempotency_key",
    "external_reference",
    "status",
    "attempt_count",
    "max_attempts",
    "available_at",
    "processing_started_at",
    "started_at",
    "finished_at",
    "owner_id",
    "lease_expires_at",
    "fencing_token",
    "reconciliation_required",
    "last_error",
    "result_data",
    "created_at",
    "updated_at",
)
CHECKPOINT_FIELDS = (
    "id",
    "transaction_id",
    "checkpoint_key",
    "checkpoint_value",
    "fencing_token",
    "created_at",
    "updated_at",
)
ERROR_FIELDS = ("id", "transaction_id", "error_type", "error_message", "step", "created_at")
AUDIT_FIELDS = ("id", "transaction_id", "action", "old_value", "new_value", "created_at")


def serialize(model, fields) -> dict:
    result = {}
    for field in fields:
        value = getattr(model, field)
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, Enum):
            value = value.value
        result[field] = value
    return result


def job_data(job) -> dict:
    return serialize(job, JOB_FIELDS)


def transaction_data(transaction) -> dict:
    return serialize(transaction, TRANSACTION_FIELDS)


class ManagementService:
    def __init__(
        self, session_factory, *, tenant_id: UUID = LEGACY_TENANT_ID, system: bool = False
    ):
        self.scope = TenantScope(tenant_id, system=system)
        self.session_factory = session_factory

    @contextmanager
    def _read(self):
        try:
            with self.session_factory() as session:
                yield ManagementRepository(session, self.scope.tenant_id, system=self.scope.system)
        except SQLAlchemyError:
            raise DatabaseOperationError("Database read could not be completed") from None

    def healthy(self) -> bool:
        with self._read() as repository:
            return repository.healthy()

    def counts(self):
        with self._read() as repository:
            return repository.counts()

    def jobs(self, **filters):
        with self._read() as repository:
            items, total = repository.jobs(**filters)
            return {
                "items": [job_data(item) for item in items],
                "total": total,
                "limit": filters.get("limit", 50),
                "offset": filters.get("offset", 0),
            }

    def transactions(self, **filters):
        with self._read() as repository:
            items, total = repository.transactions(**filters)
            return {
                "items": [transaction_data(item) for item in items],
                "total": total,
                "limit": filters.get("limit", 50),
                "offset": filters.get("offset", 0),
            }

    def transaction_detail(self, execution, transaction_id):
        transaction = execution.get_transaction(transaction_id)
        self.scope.require_owner(transaction)
        with self._read() as repository:
            checkpoints, errors, audit = repository.history(transaction.id)
            return {
                "transaction": transaction_data(transaction),
                "checkpoints": [serialize(item, CHECKPOINT_FIELDS) for item in checkpoints],
                "errors": [serialize(item, ERROR_FIELDS) for item in errors],
                "audit": [serialize(item, AUDIT_FIELDS) for item in audit],
            }

    def running_demo_job_ids(self):
        return self.running_workflow_job_ids(("infrastructure_demo",))

    def running_workflow_job_ids(self, workflow_names, *, limit=100):
        if (
            not isinstance(workflow_names, (tuple, list))
            or len(workflow_names) > 1000
            or any(
                not isinstance(name, str) or not 1 <= len(name) <= 128 for name in workflow_names
            )
            or type(limit) is not int
            or not 1 <= limit <= 500
        ):
            raise ValidationError("Workflow scheduler parameters are invalid")
        if not workflow_names:
            return []
        with self._read() as repository:
            return repository.running_workflow_job_ids(tuple(workflow_names), limit=limit)

    def create_demo(self, execution, *, scenario, record_count, request_id=None):
        request_id = request_id or uuid4().hex
        job = execution.create_job(f"demo-{request_id}", "infrastructure_demo")
        reference = {
            "success": "demo:success",
            "retry": "demo:retry_once",
            "recovery": "demo:recovery",
        }[scenario]
        existing = self.transactions(job_id=job.id, limit=500)["items"]
        expected_records = {
            f"demo:{scenario}:{record_count}:{index}" for index in range(1, record_count + 1)
        }
        if any(
            item["record_id"] not in expected_records or item["external_reference"] != reference
            for item in existing
        ):
            raise IdempotencyConflictError("Demo request configuration differs")
        for index in range(1, record_count + 1):
            execution.add_transaction(
                job.id,
                f"demo:{scenario}:{record_count}:{index}",
                f"demo:{request_id}:{index}",
                max_attempts=3,
                external_reference=reference,
            )
        job = execution.get_job(job.id)
        if job.status == JobStatus.PENDING:
            job = execution.start_job(job.id)
        return {"job": job_data(job), "scenario": scenario, "record_count": record_count}
