"""Registered business handlers exercise real PostgreSQL leases and fencing."""

import time
from threading import Event
from uuid import uuid4

import pytest

from app.core.exceptions import DatabaseOperationError, OwnershipLostError, ValidationError
from app.core.states import JobStatus, TransactionStatus
from app.services.management_service import ManagementService
from app.workers.runtime import WorkflowWorkerPool
from app.workflows import SafeRetry, WorkflowDefinition, WorkflowFailure, WorkflowRegistry

pytestmark = pytest.mark.postgres


def _wait(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    pytest.fail("Registered workflow did not reach the expected state before the deadline.")


def _setup(service, session_factory, handler, *, count=1, max_attempts=3, **options):
    name = f"registered_{uuid4().hex}"
    registry = WorkflowRegistry([WorkflowDefinition(name, "Registered workflow", "Test", handler)])
    job = service.create_job(f"registered-job-{uuid4().hex}", name)
    transactions = [
        service.add_transaction(
            job.id,
            f"registered-record-{uuid4().hex}",
            f"registered-key-{uuid4().hex}",
            max_attempts=max_attempts,
            external_reference="approved:test-reference",
        )
        for _ in range(count)
    ]
    service.start_job(job.id)
    pool = WorkflowWorkerPool(
        service,
        ManagementService(session_factory),
        registry,
        **({"poll_seconds": 0.02} | options),
    )
    return pool, job, transactions


def _cleanup(pool, service, job):
    pool.stop()
    if service.get_job(job.id).status == JobStatus.RUNNING:
        service.cancel_job(job.id)


def test_registered_handler_executes_once_with_transaction_and_checkpoint_context(
    service, session_factory
):
    seen = []

    def handler(context):
        assert context.transaction.id == context.claim.transaction_id
        assert context.transaction.external_reference == "approved:test-reference"
        assert context.get_checkpoint("progress") is None
        assert context.stop_requested is False
        context.check_active()
        context.checkpoint("progress", {"step": "verified"})
        assert context.get_checkpoint("progress") == {"step": "verified"}
        seen.append((context.claim.transaction_id, context.claim.idempotency_key))
        return {"operation": "completed", "reference_checked": True}

    pool, job, transactions = _setup(service, session_factory, handler, count=4)
    try:
        pool.start()
        _wait(lambda: service.get_job(job.id).status == JobStatus.COMPLETED)
        assert len(seen) == len(set(seen)) == 4
        assert {item[0] for item in seen} == {item.id for item in transactions}
        for transaction in transactions:
            saved = service.get_transaction(transaction.id)
            assert saved.status == TransactionStatus.SUCCESS
            assert saved.attempt_count == 1
            assert saved.result_data == {"operation": "completed", "reference_checked": True}
            assert service.get_checkpoints(saved.id)[0].checkpoint_value == {"step": "verified"}
        snapshots = pool.snapshot()
        assert sum(entry["processed_count"] for entry in snapshots) == 4
        assert all(entry["workflow_names"] == [job.workflow_name] for entry in snapshots)
        snapshots[0]["workflow_names"].clear()
        assert pool.snapshot()[0]["workflow_names"] == [job.workflow_name]
    finally:
        _cleanup(pool, service, job)


def test_safe_retry_resumes_checkpoint_and_none_is_explicit_empty_success(service, session_factory):
    def handler(context):
        if context.claim.attempt_count == 1:
            context.checkpoint("verified", {"external_action_applied": False})
            raise SafeRetry("TARGET_BUSY")
        assert context.get_checkpoint("verified") == {"external_action_applied": False}
        return None

    pool, job, transactions = _setup(service, session_factory, handler)
    try:
        pool.start()
        _wait(lambda: service.get_job(job.id).status == JobStatus.COMPLETED)
        saved = service.get_transaction(transactions[0].id)
        assert saved.attempt_count == 2
        assert saved.result_data == {}
        assert saved.reconciliation_required is False
    finally:
        _cleanup(pool, service, job)


@pytest.mark.parametrize(("exception_type", "attempts"), [(SafeRetry, 3), (WorkflowFailure, 1)])
def test_explicit_handler_failures_respect_retry_budget(
    service, session_factory, exception_type, attempts
):
    def handler(context):
        raise exception_type("TARGET_VALIDATION_FAILED")

    pool, job, transactions = _setup(service, session_factory, handler, max_attempts=3)
    try:
        pool.start()
        _wait(lambda: service.get_job(job.id).status == JobStatus.FAILED)
        saved = service.get_transaction(transactions[0].id)
        assert saved.attempt_count == attempts
        assert saved.status == TransactionStatus.FAILED
        assert saved.reconciliation_required is False
        assert saved.last_error == "TARGET_VALIDATION_FAILED"
        assert service.get_job(job.id).failed_count == 1
    finally:
        _cleanup(pool, service, job)


@pytest.mark.parametrize("scenario", ["exception", "bad_result", "sensitive_result", "bad_code"])
def test_unknown_or_invalid_handler_outcomes_are_quarantined_without_replay(
    service, session_factory, scenario, caplog
):
    calls = []

    def handler(context):
        calls.append(context.claim.attempt_count)
        if scenario == "exception":
            raise RuntimeError("private-handler-value")
        if scenario == "bad_result":
            return ["private-handler-value"]
        if scenario == "sensitive_result":
            return {"password": "private-handler-value"}
        raise SafeRetry("private-handler-value")

    pool, job, transactions = _setup(service, session_factory, handler, max_attempts=1)
    try:
        pool.start()
        _wait(lambda: service.get_transaction(transactions[0].id).reconciliation_required)
        heartbeat = pool.snapshot()[0]["last_heartbeat"]
        _wait(lambda: pool.snapshot()[0]["last_heartbeat"] != heartbeat)
        saved = service.get_transaction(transactions[0].id)
        assert saved.status == TransactionStatus.RETRY
        assert saved.attempt_count == 1
        assert saved.owner_id is None
        assert calls == [1]
        expected_code = {
            "exception": "WORKFLOW_OUTCOME_UNCERTAIN",
            "bad_result": "WORKFLOW_RESULT_INVALID",
            "sensitive_result": "WORKFLOW_RESULT_INVALID",
            "bad_code": "WORKFLOW_ERROR_CODE_INVALID",
        }[scenario]
        assert saved.last_error == expected_code
        details = ManagementService(session_factory).transaction_detail(service, saved.id)
        assert "private-handler-value" not in str(details)
        assert "private-handler-value" not in caplog.text
        assert pool.management.running_workflow_job_ids(pool.registry.names()) == []
    finally:
        _cleanup(pool, service, job)


def test_automatic_heartbeat_keeps_long_handler_alive_beyond_initial_lease(
    service, session_factory
):
    entered = Event()
    release = Event()
    claims = []

    def handler(context):
        claims.append(context.claim)
        entered.set()
        assert release.wait(6)
        return {"long_operation": "completed"}

    pool, job, transactions = _setup(
        service, session_factory, handler, lease_seconds=0.4, heartbeat_seconds=0.05
    )
    try:
        pool.start()
        assert entered.wait(4)
        time.sleep(0.85)
        active = service.get_transaction(transactions[0].id)
        assert active.status == TransactionStatus.PROCESSING
        assert active.lease_expires_at > claims[0].lease_expires_at
        assert service.recover_stale(job.id) == []
        release.set()
        _wait(lambda: service.get_job(job.id).status == JobStatus.COMPLETED)
        assert service.get_transaction(transactions[0].id).attempt_count == 1
        assert len(claims) == 1
    finally:
        release.set()
        _cleanup(pool, service, job)


def test_cancel_during_handler_fences_late_checkpoint_and_success(service, session_factory):
    entered = Event()
    release = Event()
    rejected = Event()
    contexts = []

    def handler(context):
        contexts.append(context)
        context.checkpoint("before_cancel", {"step": 1})
        entered.set()
        assert release.wait(6)
        try:
            context.checkpoint("after_cancel", {"step": 2})
        except OwnershipLostError:
            rejected.set()
        return {"must_not_succeed": True}

    pool, job, transactions = _setup(service, session_factory, handler)
    try:
        pool.start()
        assert entered.wait(4)
        service.cancel_job(job.id)
        release.set()
        assert rejected.wait(4)
        _wait(lambda: all(entry["transaction_id"] is None for entry in pool.snapshot()))
        saved = service.get_transaction(transactions[0].id)
        assert saved.status == TransactionStatus.RETRY
        assert saved.reconciliation_required is True
        assert saved.result_data == {}
        assert saved.attempt_count == 1
        assert service.get_job(job.id).success_count == 0
        assert [item.checkpoint_key for item in service.get_checkpoints(saved.id)] == [
            "before_cancel"
        ]
        with pytest.raises(OwnershipLostError):
            service.succeed(contexts[0].claim)
    finally:
        release.set()
        _cleanup(pool, service, job)


def test_unregistered_workflow_is_not_claimed_even_if_management_returns_it(
    service, session_factory
):
    pool, job, transactions = _setup(service, session_factory, lambda context: {})
    unknown = service.create_job(f"unknown-{uuid4().hex}", f"unregistered_{uuid4().hex}")
    unknown_transaction = service.add_transaction(
        unknown.id, f"unknown-record-{uuid4().hex}", f"unknown-key-{uuid4().hex}"
    )
    service.start_job(unknown.id)
    original = pool.management.running_workflow_job_ids
    pool.management.running_workflow_job_ids = lambda names: [unknown.id, *original(names)]
    try:
        pool.start()
        _wait(lambda: service.get_job(job.id).status == JobStatus.COMPLETED)
        saved = service.get_transaction(unknown_transaction.id)
        assert saved.status == TransactionStatus.PENDING
        assert saved.attempt_count == 0
        assert saved.owner_id is None
        assert service.get_transaction(transactions[0].id).status == TransactionStatus.SUCCESS
    finally:
        _cleanup(pool, service, job)
        service.cancel_job(unknown.id)


def test_safe_stop_reports_live_handler_and_stops_heartbeats_without_claiming_more(
    service, session_factory
):
    entered = Event()
    release = Event()
    contexts = []

    def handler(context):
        contexts.append(context)
        entered.set()
        assert release.wait(6)
        return {}

    pool, job, transactions = _setup(
        service,
        session_factory,
        handler,
        count=2,
        worker_count=1,
        lease_seconds=0.4,
        heartbeat_seconds=0.05,
    )
    try:
        pool.start()
        assert entered.wait(4)
        before = time.monotonic()
        pool.stop(timeout=0.05)
        assert time.monotonic() - before < 1
        snapshot = pool.snapshot()[0]
        assert snapshot["state"] == "stopping"
        assert snapshot["transaction_id"] == str(contexts[0].claim.transaction_id)
        assert contexts[0].stop_requested is True
        _wait(lambda: not contexts[0]._heartbeat.is_alive())
        pool.start()
        assert contexts[0].stop_requested is True  # An active stop cannot be undone.
        assert len(pool._threads) == 1
        release.set()
        _wait(lambda: pool.snapshot()[0]["state"] == "stopped")
        saved = [service.get_transaction(item.id) for item in transactions]
        assert sorted(item.attempt_count for item in saved) == [0, 1]
        assert all(item.status != TransactionStatus.SUCCESS for item in saved)
    finally:
        release.set()
        _cleanup(pool, service, job)


class _ExecutionProxy:
    def __init__(self, service):
        self.service = service

    def __getattr__(self, name):
        return getattr(self.service, name)


def test_lost_heartbeat_prevents_handler_success_and_requires_reconciliation(
    service, session_factory
):
    entered = Event()
    release = Event()
    lose_connection = Event()
    contexts = []

    class Disconnected(_ExecutionProxy):
        def heartbeat(self, claim, lease_seconds):
            if lose_connection.is_set():
                raise DatabaseOperationError("Database operation failed")
            return self.service.heartbeat(claim, lease_seconds)

    def handler(context):
        contexts.append(context)
        entered.set()
        assert release.wait(6)
        return {"must_not_succeed": True}

    pool, job, transactions = _setup(
        service, session_factory, handler, lease_seconds=0.4, heartbeat_seconds=0.05
    )
    pool.execution = Disconnected(service)
    try:
        pool.start()
        assert entered.wait(4)
        lose_connection.set()
        _wait(lambda: contexts[0].stop_requested)
        release.set()
        _wait(lambda: service.get_transaction(transactions[0].id).reconciliation_required)
        saved = service.get_transaction(transactions[0].id)
        assert saved.status == TransactionStatus.RETRY
        assert saved.attempt_count == 1
        assert saved.result_data == {}
        assert saved.last_error == "LEASE_EXPIRED"
        assert len(contexts) == 1
    finally:
        release.set()
        _cleanup(pool, service, job)


@pytest.mark.parametrize("committed", [False, True])
def test_ambiguous_success_acknowledgement_never_replays_handler(
    service, session_factory, committed
):
    calls = []

    class AmbiguousAcknowledgement(_ExecutionProxy):
        def succeed(self, claim, result_data):
            if committed:
                self.service.succeed(claim, result_data)
            raise DatabaseOperationError("Database acknowledgement unavailable")

    def handler(context):
        calls.append(context.claim.transaction_id)
        return {"applied": True}

    pool, job, transactions = _setup(
        service, session_factory, handler, lease_seconds=0.4, heartbeat_seconds=0.05
    )
    pool.execution = AmbiguousAcknowledgement(service)
    try:
        pool.start()
        if committed:
            _wait(lambda: service.get_job(job.id).status == JobStatus.COMPLETED)
        else:
            _wait(lambda: service.get_transaction(transactions[0].id).reconciliation_required)
        saved = service.get_transaction(transactions[0].id)
        assert saved.attempt_count == 1
        assert calls == [saved.id]
        assert saved.status == (TransactionStatus.SUCCESS if committed else TransactionStatus.RETRY)
        assert saved.reconciliation_required is not committed
    finally:
        _cleanup(pool, service, job)


def test_worker_instances_have_unique_owners_and_validate_timing(service, session_factory):
    registry = WorkflowRegistry()
    management = ManagementService(session_factory)
    first = WorkflowWorkerPool(service, management, registry)
    second = WorkflowWorkerPool(service, management, registry)
    assert {item["worker_id"] for item in first.snapshot()}.isdisjoint(
        item["worker_id"] for item in second.snapshot()
    )
    for invalid in (
        {"worker_count": True},
        {"poll_seconds": float("nan")},
        {"lease_seconds": 0},
        {"heartbeat_seconds": 30},
    ):
        with pytest.raises(ValidationError):
            WorkflowWorkerPool(service, management, registry, **invalid)
