"""Atomic execution state operations for workflow-neutral RPA workers.

Each call opens one short database transaction. External automation must run
*between* calls, never while a database lock is held. A database reservation does
not make external side effects exactly-once: pass the idempotency key to the
target where supported and reconcile uncertain outcomes before another attempt.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import (
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    OwnershipLostError,
    ValidationError,
)
from app.core.states import TRANSACTION_TRANSITIONS, JobStatus, TransactionStatus
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.core.validation import validate_json, validate_number, validate_text, validate_uuid
from app.database.models import Checkpoint, Job, Transaction
from app.database.repositories.audit_repository import AuditRepository
from app.database.repositories.checkpoint_repository import CheckpointRepository
from app.database.repositories.error_repository import ErrorRepository
from app.database.repositories.job_repository import JobRepository
from app.database.repositories.transaction_repository import TransactionRepository
from app.database.unit_of_work import DatabaseUnitOfWork
from app.entitlements.access import assert_access
from app.entitlements.domain import EntitlementError
from app.services.job_lifecycle import transition_job

# Transitional compatibility for existing private imports; new callers use core.validation.
_json_value = validate_json
_number = validate_number
_text = validate_text
_uuid = validate_uuid


@dataclass(frozen=True, slots=True)
class Claim:
    """Worker ownership capability. Keep it in memory, never in log payloads."""

    job_id: UUID
    transaction_id: UUID
    owner_id: str
    fencing_token: int
    lease_expires_at: datetime
    attempt_count: int
    idempotency_key: str


_TERMINAL = frozenset(
    {TransactionStatus.SUCCESS, TransactionStatus.FAILED, TransactionStatus.SKIPPED}
)


class ExecutionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        tenant_id: UUID = LEGACY_TENANT_ID,
        system: bool = False,
        retry_base_seconds: float = 1,
        retry_max_seconds: float = 300,
    ) -> None:
        self.scope = TenantScope(tenant_id, system=system)
        self.unit_of_work = DatabaseUnitOfWork(session_factory)
        self.retry_max_seconds = validate_number(
            retry_max_seconds, "retry_max_seconds", minimum=0, maximum=86_400
        )
        self.retry_base_seconds = validate_number(
            retry_base_seconds, "retry_base_seconds", minimum=0, maximum=self.retry_max_seconds
        )

    def _job(self, session: Session, job_id: UUID | str, *, locked: bool = True) -> Job:
        job = JobRepository(session, self.scope.tenant_id, system=self.scope.system).get(
            validate_uuid(job_id), for_update=locked
        )
        if job is None:
            raise NotFoundError("Job not found")
        return job

    def _transaction(
        self, session: Session, transaction_id: UUID | str, *, locked: bool = True
    ) -> Transaction:
        transaction = TransactionRepository(
            session, self.scope.tenant_id, system=self.scope.system
        ).get(validate_uuid(transaction_id), for_update=locked)
        if transaction is None:
            raise NotFoundError("Transaction not found")
        return transaction

    @staticmethod
    def _state(transaction: Transaction, new_status: TransactionStatus, now: datetime) -> None:
        if new_status not in TRANSACTION_TRANSITIONS[transaction.status]:
            raise InvalidTransitionError("Transaction state transition is not allowed")
        transaction.status = new_status
        transaction.updated_at = now

    @staticmethod
    def _clear_ownership(transaction: Transaction) -> None:
        transaction.owner_id = None
        transaction.lease_expires_at = None
        transaction.processing_started_at = None

    def _owned(self, session: Session, claim: Claim) -> tuple[Job, Transaction, datetime]:
        if not isinstance(claim, Claim):
            raise ValidationError("A Claim is required")
        # Always parent before child. This also serializes finalization and
        # cancellation against every state/checkpoint/heartbeat mutation.
        job = self._job(session, claim.job_id)
        transaction = self._transaction(session, claim.transaction_id)
        now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
        if (
            job.status != JobStatus.RUNNING
            or transaction.job_id != job.id
            or transaction.status != TransactionStatus.PROCESSING
            or transaction.owner_id != claim.owner_id
            or transaction.fencing_token != claim.fencing_token
            or transaction.lease_expires_at is None
            or transaction.lease_expires_at <= now
        ):
            raise OwnershipLostError("Worker ownership is no longer valid")
        return job, transaction, now

    @staticmethod
    def _claim_value(transaction: Transaction) -> Claim:
        return Claim(
            job_id=transaction.job_id,
            transaction_id=transaction.id,
            owner_id=transaction.owner_id,
            fencing_token=transaction.fencing_token,
            lease_expires_at=transaction.lease_expires_at,
            attempt_count=transaction.attempt_count,
            idempotency_key=transaction.idempotency_key,
        )

    def _backoff(self, transaction: Transaction) -> timedelta:
        seconds = min(
            self.retry_max_seconds,
            self.retry_base_seconds * 2 ** max(0, transaction.attempt_count - 1),
        )
        return timedelta(seconds=seconds)

    def _finish(
        self, job: Job, transaction: Transaction, status: TransactionStatus, now: datetime
    ) -> None:
        self._state(transaction, status, now)
        self._clear_ownership(transaction)
        transaction.reconciliation_required = False
        transaction.finished_at = now
        # A completed ownership capability can never become live again.
        transaction.fencing_token += 1
        job.updated_at = now
        if status == TransactionStatus.SUCCESS:
            job.success_count += 1
        elif status == TransactionStatus.FAILED:
            job.failed_count += 1
        elif status == TransactionStatus.SKIPPED:
            job.skipped_count += 1

    def _uncertain(self, transaction: Transaction, now: datetime) -> None:
        self._state(transaction, TransactionStatus.RETRY, now)
        self._clear_ownership(transaction)
        transaction.fencing_token += 1
        transaction.reconciliation_required = True
        transaction.available_at = now

    def create_job(self, job_id: str, workflow_name: str) -> Job:
        job_id = validate_text(job_id, "job_id", 128)
        workflow_name = validate_text(workflow_name, "workflow_name", 128)
        with self.unit_of_work.transaction() as session:
            assert_access(session, self.scope.tenant_id)
            job, created = JobRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).create_once(job_id, workflow_name)
            if job.workflow_name != workflow_name:
                raise IdempotencyConflictError("Job identifier belongs to another workflow")
            if created:
                AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                    job, "JOB_CREATED", new_status=job.status.value
                )
            return job

    def add_transaction(
        self,
        job_id: UUID | str,
        record_id: str,
        idempotency_key: str,
        max_attempts: int = 3,
        external_reference: str | None = None,
    ) -> Transaction:
        record_id = validate_text(record_id, "record_id", 256)
        idempotency_key = validate_text(idempotency_key, "idempotency_key", 256)
        if external_reference is not None:
            external_reference = validate_text(external_reference, "external_reference", 256)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValidationError("max_attempts must be an integer between 1 and 100")
        with self.unit_of_work.transaction() as session:
            job = self._job(session, job_id)
            assert_access(session, job.tenant_id)
            repository = TransactionRepository(session, job.tenant_id)
            # Identical requests can recover an uncertain commit, including
            # after the original job has reached a terminal state.
            existing = repository.by_idempotency_key(idempotency_key)
            if existing is not None:
                self._same_identity(existing, job, record_id, external_reference)
                return existing
            if job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
                raise InvalidTransitionError("Cannot add records to a terminal job")
            transaction, created = repository.create_once(
                job_id=job.id,
                workflow_name=job.workflow_name,
                record_id=record_id,
                idempotency_key=idempotency_key,
                external_reference=external_reference,
                max_attempts=max_attempts,
            )
            if transaction is None:
                raise IdempotencyConflictError(
                    "Business record is already reserved under a different key"
                )
            self._same_identity(transaction, job, record_id, external_reference)
            if created:
                job.total_records += 1
                job.updated_at = JobRepository(
                    session, self.scope.tenant_id, system=self.scope.system
                ).now()
                AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                    job,
                    "TRANSACTION_CREATED",
                    transaction=transaction,
                    new_status=transaction.status.value,
                )
            return transaction

    @staticmethod
    def _same_identity(
        transaction: Transaction, job: Job, record_id: str, external_reference: str | None
    ) -> None:
        if (
            transaction.workflow_name != job.workflow_name
            or transaction.record_id != record_id
            or transaction.external_reference != external_reference
        ):
            raise IdempotencyConflictError(
                "Idempotency key belongs to a different business operation"
            )

    def start_job(self, job_id: UUID | str) -> Job:
        with self.unit_of_work.transaction() as session:
            job = self._job(session, job_id)
            assert_access(session, job.tenant_id)
            if job.status == JobStatus.RUNNING:
                return job
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            old_status = job.status.value
            transition_job(job, JobStatus.RUNNING, now)
            job.started_at = now
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job, "JOB_STARTED", old_status=old_status, new_status=job.status.value
            )
            return job

    def claim(self, job_id: UUID | str, worker_id: str, lease_seconds: float = 60) -> Claim | None:
        worker_id = validate_text(worker_id, "worker_id", 128)
        lease_seconds = validate_number(
            lease_seconds, "lease_seconds", minimum=0.001, maximum=86_400
        )
        with self.unit_of_work.transaction() as session:
            job = self._job(session, job_id)
            if job.status != JobStatus.RUNNING:
                raise InvalidTransitionError("Only a running job can be claimed")
            try:
                assert_access(session, job.tenant_id)
            except EntitlementError:
                return None
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            transaction = TransactionRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).claim_candidate(job.id, now)
            if transaction is None:
                return None
            old_status = transaction.status.value
            self._state(transaction, TransactionStatus.PROCESSING, now)
            transaction.attempt_count += 1
            transaction.fencing_token += 1
            transaction.owner_id = worker_id
            transaction.started_at = transaction.started_at or now
            transaction.processing_started_at = now
            transaction.lease_expires_at = now + timedelta(seconds=lease_seconds)
            transaction.finished_at = None
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job,
                "TRANSACTION_CLAIMED",
                transaction=transaction,
                old_status=old_status,
                new_status=transaction.status.value,
            )
            return self._claim_value(transaction)

    def heartbeat(self, claim: Claim, lease_seconds: float = 60) -> Claim:
        lease_seconds = validate_number(
            lease_seconds, "lease_seconds", minimum=0.001, maximum=86_400
        )
        with self.unit_of_work.transaction() as session:
            job, transaction, now = self._owned(session, claim)
            assert_access(session, job.tenant_id)
            transaction.lease_expires_at = now + timedelta(seconds=lease_seconds)
            transaction.updated_at = now
            return self._claim_value(transaction)

    def checkpoint(self, claim: Claim, key: str, value) -> Checkpoint:
        key = validate_text(key, "checkpoint key", 128)
        value = validate_json(value)
        with self.unit_of_work.transaction() as session:
            job, transaction, now = self._owned(session, claim)
            assert_access(session, job.tenant_id)
            return CheckpointRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).save(transaction, key, value, now)

    def succeed(self, claim: Claim, result_data=None) -> Transaction:
        result_data = validate_json({} if result_data is None else result_data)
        with self.unit_of_work.transaction() as session:
            job, transaction, now = self._owned(session, claim)
            old_status = transaction.status.value
            self._finish(job, transaction, TransactionStatus.SUCCESS, now)
            transaction.result_data = result_data
            transaction.last_error = None
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job,
                "TRANSACTION_SUCCEEDED",
                transaction=transaction,
                old_status=old_status,
                new_status=transaction.status.value,
            )
            return transaction

    def fail(
        self,
        claim: Claim,
        error_code: str,
        retryable: bool = False,
        outcome_uncertain: bool = False,
    ) -> Transaction:
        if not isinstance(error_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code):
            raise ValidationError("error_code must be an uppercase code of at most 64 characters")
        if type(retryable) is not bool or type(outcome_uncertain) is not bool:
            raise ValidationError("Retry and uncertainty flags must be boolean")
        with self.unit_of_work.transaction() as session:
            job, transaction, now = self._owned(session, claim)
            old_status = transaction.status.value
            transaction.last_error = error_code
            if outcome_uncertain:
                self._uncertain(transaction, now)
            elif retryable and transaction.attempt_count < transaction.max_attempts:
                self._state(transaction, TransactionStatus.RETRY, now)
                self._clear_ownership(transaction)
                transaction.fencing_token += 1
                transaction.available_at = now + self._backoff(transaction)
            else:
                self._finish(job, transaction, TransactionStatus.FAILED, now)
            ErrorRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                transaction, error_code
            )
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job,
                "TRANSACTION_ERROR",
                transaction=transaction,
                old_status=old_status,
                new_status=transaction.status.value,
            )
            return transaction

    def skip(self, claim: Claim) -> Transaction:
        with self.unit_of_work.transaction() as session:
            job, transaction, now = self._owned(session, claim)
            old_status = transaction.status.value
            self._finish(job, transaction, TransactionStatus.SKIPPED, now)
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job,
                "TRANSACTION_SKIPPED",
                transaction=transaction,
                old_status=old_status,
                new_status=transaction.status.value,
            )
            return transaction

    def recover_stale(self, job_id: UUID | str, limit: int = 100) -> list[Transaction]:
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValidationError("Recovery limit must be between 1 and 10000")
        with self.unit_of_work.transaction() as session:
            job = self._job(session, job_id)
            if job.status != JobStatus.RUNNING:
                raise InvalidTransitionError("Stale recovery requires a running job")
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            transactions = TransactionRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).expired(job.id, now, limit)
            for transaction in transactions:
                self._uncertain(transaction, now)
                transaction.last_error = "LEASE_EXPIRED"
                ErrorRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                    transaction, "LEASE_EXPIRED"
                )
                AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                    job,
                    "TRANSACTION_RECOVERY_REQUIRED",
                    transaction=transaction,
                    old_status=TransactionStatus.PROCESSING.value,
                    new_status=transaction.status.value,
                )
            return transactions

    def reconcile(
        self,
        transaction_id: UUID | str,
        expected_fencing_token: int,
        outcome: Literal["succeeded", "not_applied"],
        result_data=None,
    ) -> Transaction:
        transaction_id = validate_uuid(transaction_id)
        if type(expected_fencing_token) is not int or expected_fencing_token < 0:
            raise ValidationError("Expected fencing token must be a nonnegative integer")
        if not isinstance(outcome, str) or outcome not in {"succeeded", "not_applied"}:
            raise ValidationError("Reconciliation outcome must be succeeded or not_applied")
        result_data = validate_json({} if result_data is None else result_data)
        with self.unit_of_work.transaction() as session:
            parent_id = TransactionRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).parent_id(transaction_id)
            if parent_id is None:
                raise NotFoundError("Transaction not found")
            job = self._job(session, parent_id)
            transaction = self._transaction(session, transaction_id)
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            if transaction.fencing_token != expected_fencing_token:
                raise OwnershipLostError("Reconciliation token is no longer valid")
            if (
                job.status not in {JobStatus.RUNNING, JobStatus.CANCELLED}
                or transaction.status != TransactionStatus.RETRY
                or not transaction.reconciliation_required
            ):
                raise InvalidTransitionError("Transaction does not await reconciliation")
            old_status = transaction.status.value
            if outcome == "succeeded":
                self._finish(job, transaction, TransactionStatus.SUCCESS, now)
                transaction.result_data = result_data
                transaction.last_error = None
            elif job.status == JobStatus.CANCELLED:
                self._finish(job, transaction, TransactionStatus.SKIPPED, now)
            elif transaction.attempt_count >= transaction.max_attempts:
                self._finish(job, transaction, TransactionStatus.FAILED, now)
            else:
                # Remains RETRY, but the reconciler releases its quarantine.
                transaction.reconciliation_required = False
                transaction.fencing_token += 1
                transaction.available_at = now + self._backoff(transaction)
                transaction.updated_at = now
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job,
                "TRANSACTION_RECONCILED_" + outcome.upper(),
                transaction=transaction,
                old_status=old_status,
                new_status=transaction.status.value,
            )
            return transaction

    def finalize_job(self, job_id: UUID | str) -> Job:
        with self.unit_of_work.transaction() as session:
            job = self._job(session, job_id)
            if job.status in {JobStatus.COMPLETED, JobStatus.PARTIALLY_COMPLETED, JobStatus.FAILED}:
                return job
            if job.status != JobStatus.RUNNING:
                raise InvalidTransitionError("Only a running job can be finalized")
            counts = TransactionRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).status_counts(job.id)
            if any(count for state, count in counts.items() if state not in _TERMINAL):
                raise InvalidTransitionError("Job still has unfinished transactions")
            job.total_records = sum(counts.values())
            job.success_count = counts.get(TransactionStatus.SUCCESS, 0)
            job.failed_count = counts.get(TransactionStatus.FAILED, 0)
            job.skipped_count = counts.get(TransactionStatus.SKIPPED, 0)
            if not job.failed_count:
                status = JobStatus.COMPLETED
            elif job.failed_count == job.total_records:
                status = JobStatus.FAILED
            else:
                status = JobStatus.PARTIALLY_COMPLETED
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            old_status = job.status.value
            transition_job(job, status, now)
            job.finished_at = now
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job, "JOB_FINALIZED", old_status=old_status, new_status=job.status.value
            )
            return job

    def cancel_job(self, job_id: UUID | str) -> Job:
        with self.unit_of_work.transaction() as session:
            job = self._job(session, job_id)
            if job.status == JobStatus.CANCELLED:
                return job
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            old_job_status = job.status.value
            transition_job(job, JobStatus.CANCELLED, now)
            transactions = TransactionRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).unfinished(job.id)
            for transaction in transactions:
                old_status = transaction.status.value
                if transaction.status == TransactionStatus.PROCESSING:
                    self._uncertain(transaction, now)
                    transaction.last_error = "CANCELLED_WHILE_PROCESSING"
                    ErrorRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                        transaction, transaction.last_error
                    )
                elif transaction.reconciliation_required:
                    # Revoke an outstanding reconciler's stale view as well.
                    transaction.fencing_token += 1
                    transaction.updated_at = now
                else:
                    self._finish(job, transaction, TransactionStatus.SKIPPED, now)
                AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                    job,
                    "TRANSACTION_JOB_CANCELLED",
                    transaction=transaction,
                    old_status=old_status,
                    new_status=transaction.status.value,
                )
            job.finished_at = now
            AuditRepository(session, self.scope.tenant_id, system=self.scope.system).record(
                job, "JOB_CANCELLED", old_status=old_job_status, new_status=job.status.value
            )
            return job

    def get_job(self, job_id: UUID | str) -> Job:
        with self.unit_of_work.transaction() as session:
            return self._job(session, job_id, locked=False)

    def get_transaction(self, transaction_id: UUID | str) -> Transaction:
        with self.unit_of_work.transaction() as session:
            return self._transaction(session, transaction_id, locked=False)

    def get_checkpoints(self, transaction_id: UUID | str) -> list[Checkpoint]:
        with self.unit_of_work.transaction() as session:
            transaction = self._transaction(session, transaction_id, locked=False)
            return CheckpointRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).list_for_transaction(transaction.id)
