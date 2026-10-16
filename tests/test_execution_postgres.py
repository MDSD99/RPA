"""Execution invariants against the explicitly isolated PostgreSQL test database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.core.exceptions import (
    IdempotencyConflictError,
    InvalidTransitionError,
    OwnershipLostError,
)
from app.core.states import JobStatus, TransactionStatus
from app.database.models import AuditLog, Error, Transaction
from app.database.repositories.audit_repository import AuditRepository
from app.services.execution_service import ExecutionService

pytestmark = pytest.mark.postgres


def _add(service, job, *, max_attempts=3, record_id=None, key=None):
    return service.add_transaction(
        job.id,
        record_id or f"record-{uuid4()}",
        key or f"operation-{uuid4()}",
        max_attempts=max_attempts,
    )


def _expire(db_engine, *transaction_ids):
    with db_engine.begin() as connection:
        connection.execute(
            update(Transaction)
            .where(Transaction.id.in_(transaction_ids))
            .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )


def _make_available(db_engine, transaction_id):
    with db_engine.begin() as connection:
        connection.execute(
            update(Transaction)
            .where(Transaction.id == transaction_id)
            .values(available_at=func.clock_timestamp() - timedelta(seconds=1))
        )


def test_twelve_workers_claim_thirty_six_records_once(service, job):
    transactions = [_add(service, job) for _ in range(36)]
    service.start_job(job.id)
    start_barrier = Barrier(12, timeout=30)
    processing_barrier = Barrier(12, timeout=30)

    def worker(number):
        start_barrier.wait()
        claimed = []
        claim = service.claim(job.id, f"worker-{number}")
        assert claim is not None
        # All twelve leases coexist before any worker reports completion.
        processing_barrier.wait()
        while claim is not None:
            claimed.append(claim.transaction_id)
            assert claim.attempt_count == 1
            service.succeed(claim, {"processed": True})
            claim = service.claim(job.id, f"worker-{number}")
        return claimed

    with ThreadPoolExecutor(max_workers=12) as executor:
        claimed_ids = [item for result in executor.map(worker, range(12)) for item in result]

    assert len(claimed_ids) == len(set(claimed_ids)) == 36
    assert set(claimed_ids) == {transaction.id for transaction in transactions}
    completed = service.finalize_job(job.id)
    assert completed.status == JobStatus.COMPLETED
    assert (completed.total_records, completed.success_count) == (36, 36)
    assert (completed.failed_count, completed.skipped_count) == (0, 0)


def test_concurrent_global_idempotency_returns_one_canonical_transaction(service):
    jobs = [service.create_job(f"job-{uuid4()}", "test-workflow") for _ in range(12)]
    key = f"shared-operation-{uuid4()}"
    record_id = f"shared-record-{uuid4()}"
    barrier = Barrier(12, timeout=30)

    def reserve(job):
        barrier.wait()
        return service.add_transaction(job.id, record_id, key)

    with ThreadPoolExecutor(max_workers=12) as executor:
        reservations = list(executor.map(reserve, jobs))

    assert len({transaction.id for transaction in reservations}) == 1
    canonical = reservations[0]
    counts = {job.id: service.get_job(job.id).total_records for job in jobs}
    assert counts[canonical.job_id] == 1
    assert sum(counts.values()) == 1


def test_one_job_record_cannot_be_reserved_under_a_second_key(service, job, transaction):
    with pytest.raises(IdempotencyConflictError):
        service.add_transaction(job.id, transaction.record_id, f"different-key-{uuid4()}")
    assert service.get_job(job.id).total_records == 1
    assert service.get_transaction(transaction.id).idempotency_key == transaction.idempotency_key


@pytest.mark.parametrize("mismatch", ["record", "reference", "workflow"])
def test_idempotency_key_rejects_different_business_identity(service, job, transaction, mismatch):
    destination = job
    record_id = transaction.record_id
    reference = transaction.external_reference
    if mismatch == "record":
        record_id = f"different-record-{uuid4()}"
    elif mismatch == "reference":
        reference = "different-operation-reference"
    else:
        destination = service.create_job(f"other-job-{uuid4()}", "different-workflow")

    with pytest.raises(IdempotencyConflictError):
        service.add_transaction(
            destination.id,
            record_id,
            transaction.idempotency_key,
            external_reference=reference,
        )
    assert service.get_job(job.id).total_records == 1


def test_successful_idempotency_key_is_not_reclaimed_in_another_job(service, job, transaction):
    service.start_job(job.id)
    claim = service.claim(job.id, "worker")
    service.succeed(claim, {"confirmed": True})
    service.finalize_job(job.id)

    # Repeating a request after completion also recovers an uncertain COMMIT response.
    repeated = service.add_transaction(job.id, transaction.record_id, transaction.idempotency_key)
    assert repeated.id == transaction.id
    other = service.create_job(f"other-job-{uuid4()}", job.workflow_name)
    reserved = service.add_transaction(other.id, transaction.record_id, transaction.idempotency_key)
    service.start_job(other.id)
    assert reserved.id == transaction.id
    assert reserved.status == TransactionStatus.SUCCESS
    assert service.claim(other.id, "other-worker") is None
    assert service.get_job(other.id).total_records == 0


def test_retry_budget_is_finite_and_counts_only_the_terminal_attempt(service, job, transaction):
    service.start_job(job.id)
    fences = []
    for attempt in range(1, 4):
        claim = service.claim(job.id, "retry-worker")
        assert claim.attempt_count == attempt
        fences.append(claim.fencing_token)
        result = service.fail(claim, "TARGET_TEMPORARY_ERROR", retryable=True)
        expected = TransactionStatus.RETRY if attempt < 3 else TransactionStatus.FAILED
        assert result.status == expected
        assert result.owner_id is None
        assert service.get_job(job.id).failed_count == (1 if attempt == 3 else 0)
    assert fences == sorted(set(fences))
    assert service.claim(job.id, "retry-worker") is None
    assert service.get_transaction(transaction.id).attempt_count == 3
    assert service.finalize_job(job.id).status == JobStatus.FAILED


def test_nonretryable_failure_does_not_consume_remaining_attempts(service, job, transaction):
    service.start_job(job.id)
    claim = service.claim(job.id, "worker")
    failed = service.fail(claim, "INVALID_TARGET_STATE")
    assert failed.status == TransactionStatus.FAILED
    assert failed.attempt_count == 1
    assert service.claim(job.id, "worker") is None


def test_retry_backoff_gates_claims_and_caps_delay(session_factory, db_engine, job):
    delayed = ExecutionService(session_factory, retry_base_seconds=30, retry_max_seconds=60)
    transaction = _add(delayed, job, max_attempts=4)
    delayed.start_job(job.id)

    for expected_delay in [30, 60, 60]:
        claim = delayed.claim(job.id, "backoff-worker")
        result = delayed.fail(claim, "TARGET_BUSY", retryable=True)
        assert (result.available_at - result.updated_at).total_seconds() == expected_delay
        assert delayed.claim(job.id, "backoff-worker") is None
        _make_available(db_engine, transaction.id)

    final_claim = delayed.claim(job.id, "backoff-worker")
    assert final_claim.attempt_count == 4
    failed = delayed.fail(final_claim, "TARGET_BUSY", retryable=True)
    assert failed.status == TransactionStatus.FAILED


@pytest.mark.parametrize("operation", ["heartbeat", "checkpoint", "succeed", "fail", "skip"])
def test_expired_lease_rejects_every_worker_write_before_recovery(
    service, db_engine, job, transaction, operation
):
    service.start_job(job.id)
    claim = service.claim(job.id, "expired-worker")
    _expire(db_engine, transaction.id)

    with pytest.raises(OwnershipLostError):
        if operation == "checkpoint":
            service.checkpoint(claim, "progress", {"step": 1})
        elif operation == "fail":
            service.fail(claim, "TARGET_ERROR", retryable=True)
        else:
            getattr(service, operation)(claim)

    state = service.get_transaction(transaction.id)
    assert state.status == TransactionStatus.PROCESSING
    assert state.fencing_token == claim.fencing_token
    assert service.get_job(job.id).success_count == 0
    assert service.get_job(job.id).failed_count == 0
    assert service.get_checkpoints(transaction.id) == []


def test_recovery_quarantines_then_fences_old_claim_even_with_same_worker_id(
    service, db_engine, job, transaction
):
    service.start_job(job.id)
    old_claim = service.claim(job.id, "restarted-worker")
    _expire(db_engine, transaction.id)
    recovered = service.recover_stale(job.id)
    assert len(recovered) == 1
    uncertain = recovered[0]
    assert uncertain.id == transaction.id
    assert uncertain.status == TransactionStatus.RETRY
    assert uncertain.reconciliation_required is True
    assert uncertain.owner_id is None
    assert uncertain.lease_expires_at is None
    assert uncertain.fencing_token > old_claim.fencing_token
    assert service.claim(job.id, "restarted-worker") is None
    with pytest.raises(InvalidTransitionError):
        service.finalize_job(job.id)

    safe_retry = service.reconcile(uncertain.id, uncertain.fencing_token, "not_applied")
    assert safe_retry.reconciliation_required is False
    new_claim = service.claim(job.id, "restarted-worker")
    assert new_claim.transaction_id == old_claim.transaction_id
    assert new_claim.owner_id == old_claim.owner_id
    assert new_claim.fencing_token > old_claim.fencing_token
    assert new_claim.attempt_count == 2
    for operation in ["heartbeat", "succeed", "skip"]:
        with pytest.raises(OwnershipLostError):
            getattr(service, operation)(old_claim)
    with pytest.raises(OwnershipLostError):
        service.checkpoint(old_claim, "progress", {"step": 9})
    with pytest.raises(OwnershipLostError):
        service.fail(old_claim, "OLD_WORKER_ERROR", retryable=True)
    service.succeed(new_claim)
    assert service.get_job(job.id).success_count == 1


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("succeeded", TransactionStatus.SUCCESS), ("not_applied", TransactionStatus.FAILED)],
)
def test_exhausted_crash_requires_reconciliation_before_terminal_state(
    service, db_engine, job, outcome, expected
):
    transaction = _add(service, job, max_attempts=1)
    service.start_job(job.id)
    service.claim(job.id, "crashed-worker")
    _expire(db_engine, transaction.id)
    recovered = service.recover_stale(job.id)[0]
    assert recovered.reconciliation_required is True
    assert recovered.status == TransactionStatus.RETRY
    assert service.claim(job.id, "replacement") is None
    result = service.reconcile(recovered.id, recovered.fencing_token, outcome, {"verified": True})
    assert result.status == expected
    assert result.attempt_count == 1
    with pytest.raises(OwnershipLostError):
        service.reconcile(recovered.id, recovered.fencing_token, outcome)
    if outcome == "succeeded":
        assert result.result_data == {"verified": True}
        assert service.get_job(job.id).success_count == 1
    else:
        assert service.get_job(job.id).failed_count == 1


def test_uncertain_report_overrides_retry_flag_and_needs_single_reconciliation(
    service, job, transaction
):
    service.start_job(job.id)
    claim = service.claim(job.id, "worker")
    uncertain = service.fail(claim, "TARGET_RESPONSE_LOST", retryable=True, outcome_uncertain=True)
    assert uncertain.reconciliation_required is True
    assert service.claim(job.id, "replacement") is None
    released = service.reconcile(uncertain.id, uncertain.fencing_token, "not_applied")
    assert released.status == TransactionStatus.RETRY
    assert released.fencing_token > uncertain.fencing_token
    with pytest.raises(OwnershipLostError):
        service.reconcile(uncertain.id, uncertain.fencing_token, "succeeded")
    assert service.get_job(job.id).success_count == 0


def test_checkpoint_upsert_survives_recovery_and_new_owner_resumes(
    service, db_engine, job, transaction
):
    service.start_job(job.id)
    old_claim = service.claim(job.id, "worker-one")
    first = service.checkpoint(old_claim, "cursor", {"page": 1})
    second = service.checkpoint(old_claim, "cursor", {"page": 2})
    assert first.id == second.id
    assert second.checkpoint_value == {"page": 2}
    _expire(db_engine, transaction.id)
    recovered = service.recover_stale(job.id)[0]
    service.reconcile(recovered.id, recovered.fencing_token, "not_applied")
    new_claim = service.claim(job.id, "worker-two")
    checkpoints = service.get_checkpoints(transaction.id)
    assert len(checkpoints) == 1
    assert checkpoints[0].checkpoint_value == {"page": 2}
    assert checkpoints[0].fencing_token == old_claim.fencing_token
    resumed = service.checkpoint(new_claim, "cursor", {"page": 3})
    assert resumed.id == first.id
    assert resumed.fencing_token == new_claim.fencing_token
    with pytest.raises(OwnershipLostError):
        service.checkpoint(old_claim, "cursor", {"page": 0})
    assert service.get_checkpoints(transaction.id)[0].checkpoint_value == {"page": 3}


def test_heartbeat_renews_lease_using_database_time(service, job, transaction):
    service.start_job(job.id)
    claim = service.claim(job.id, "worker", lease_seconds=30)
    renewed = service.heartbeat(claim, lease_seconds=90)
    assert renewed.lease_expires_at > claim.lease_expires_at
    assert renewed.fencing_token == claim.fencing_token
    assert renewed.attempt_count == 1
    state = service.get_transaction(transaction.id)
    assert (state.lease_expires_at - state.updated_at).total_seconds() == 90
    assert service.recover_stale(job.id) == []


def test_recovery_limit_leaves_other_expired_records_for_next_batch(service, db_engine, job):
    transactions = [_add(service, job) for _ in range(3)]
    service.start_job(job.id)
    claims = [service.claim(job.id, f"worker-{index}") for index in range(3)]
    _expire(db_engine, *(claim.transaction_id for claim in claims))
    first = service.recover_stale(job.id, limit=2)
    second = service.recover_stale(job.id, limit=2)
    assert len(first) == 2
    assert len(second) == 1
    assert {item.id for item in first + second} == {item.id for item in transactions}
    assert service.recover_stale(job.id, limit=2) == []


@pytest.mark.parametrize("outcome", ["succeeded", "not_applied"])
def test_cancel_fences_active_worker_and_preserves_uncertain_external_outcome(
    service, job, outcome
):
    active = _add(service, job)
    safe_pending = _add(service, job)
    service.start_job(job.id)
    claim = service.claim(job.id, "active-worker")
    assert claim.transaction_id == active.id
    cancelled = service.cancel_job(job.id)
    assert cancelled.status == JobStatus.CANCELLED
    assert cancelled.skipped_count == 1
    assert service.get_transaction(safe_pending.id).status == TransactionStatus.SKIPPED
    uncertain = service.get_transaction(active.id)
    assert uncertain.status == TransactionStatus.RETRY
    assert uncertain.reconciliation_required is True
    assert uncertain.fencing_token > claim.fencing_token
    with pytest.raises(OwnershipLostError):
        service.succeed(claim)
    with pytest.raises(InvalidTransitionError):
        service.claim(job.id, "new-worker")

    result = service.reconcile(uncertain.id, uncertain.fencing_token, outcome)
    expected = TransactionStatus.SUCCESS if outcome == "succeeded" else TransactionStatus.SKIPPED
    assert result.status == expected
    final = service.get_job(job.id)
    assert final.status == JobStatus.CANCELLED
    assert final.success_count + final.skipped_count == final.total_records == 2
    assert service.cancel_job(job.id).status == JobStatus.CANCELLED


@pytest.mark.parametrize(
    ("actions", "status", "counts"),
    [
        ([], JobStatus.COMPLETED, (0, 0, 0)),
        (["succeed", "skip"], JobStatus.COMPLETED, (1, 0, 1)),
        (["fail", "fail"], JobStatus.FAILED, (0, 2, 0)),
        (["succeed", "fail", "skip"], JobStatus.PARTIALLY_COMPLETED, (1, 1, 1)),
    ],
)
def test_finalization_aggregates_actual_terminal_records(service, job, actions, status, counts):
    for _ in actions:
        _add(service, job)
    service.start_job(job.id)
    for action in actions:
        claim = service.claim(job.id, "worker")
        if action == "fail":
            service.fail(claim, "BUSINESS_REJECTED")
        else:
            getattr(service, action)(claim)
    finalized = service.finalize_job(job.id)
    assert finalized.status == status
    assert (finalized.success_count, finalized.failed_count, finalized.skipped_count) == counts
    assert finalized.total_records == len(actions)
    assert finalized.finished_at is not None
    assert service.finalize_job(job.id).status == status
    with pytest.raises(InvalidTransitionError):
        service.start_job(job.id)
    with pytest.raises(InvalidTransitionError):
        _add(service, job)


def test_invalid_transitions_preserve_state_and_counts(service, job, transaction):
    with pytest.raises(InvalidTransitionError):
        service.claim(job.id, "worker")
    with pytest.raises(InvalidTransitionError):
        service.finalize_job(job.id)
    with pytest.raises(InvalidTransitionError):
        service.recover_stale(job.id)
    service.start_job(job.id)
    with pytest.raises(InvalidTransitionError):
        service.finalize_job(job.id)
    claim = service.claim(job.id, "worker")
    with pytest.raises(InvalidTransitionError):
        service.finalize_job(job.id)
    succeeded = service.succeed(claim)
    with pytest.raises(OwnershipLostError):
        service.succeed(claim)
    with pytest.raises(InvalidTransitionError):
        service.reconcile(succeeded.id, succeeded.fencing_token, "succeeded")
    assert service.get_job(job.id).success_count == 1


def test_claim_skips_a_row_locked_by_an_independent_postgres_transaction(service, db_engine, job):
    locked = _add(service, job)
    other = _add(service, job)
    service.start_job(job.id)
    _make_available(db_engine, locked.id)

    with db_engine.begin() as independent:
        independent.execute(
            select(Transaction.id).where(Transaction.id == locked.id).with_for_update()
        ).scalar_one()
        claim = service.claim(job.id, "available-worker")
        assert claim.transaction_id == other.id
        # A locked candidate is skipped instead of stalling until lock_timeout.
        assert service.claim(job.id, "empty-worker") is None

    later_claim = service.claim(job.id, "later-worker")
    assert later_claim.transaction_id == locked.id


@pytest.mark.parametrize("operation", ["succeed", "fail"])
def test_audit_failure_rolls_back_flushed_state_counts_and_error_history(
    service, db_engine, monkeypatch, job, transaction, operation
):
    service.start_job(job.id)
    claim = service.claim(job.id, "worker")
    with db_engine.connect() as connection:
        audit_count = connection.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.job_id == job.id)
        )

    def failed_audit(repository, *args, **kwargs):
        # Force the writes into PostgreSQL before simulating a later unit-of-work failure.
        repository.session.flush()
        raise RuntimeError("INJECTED_AUDIT_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(AuditRepository, "record", failed_audit)
        with pytest.raises(RuntimeError, match="INJECTED_AUDIT_FAILURE"):
            if operation == "fail":
                service.fail(claim, "INJECTED_TARGET_ERROR")
            else:
                service.succeed(claim, {"approved": True})

    state = service.get_transaction(transaction.id)
    assert state.status == TransactionStatus.PROCESSING
    assert state.fencing_token == claim.fencing_token
    assert state.owner_id == claim.owner_id
    assert state.result_data == {}
    assert state.last_error is None
    summary = service.get_job(job.id)
    assert (summary.success_count, summary.failed_count, summary.skipped_count) == (0, 0, 0)
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.job_id == job.id)
            )
            == audit_count
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(Error)
                .where(Error.transaction_id == transaction.id)
            )
            == 0
        )
    service.succeed(claim)
    assert service.get_job(job.id).success_count == 1
