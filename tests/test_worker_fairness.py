"""Real PostgreSQL scheduler fairness with blocked, delayed and finalizable jobs."""

import time
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.core.states import JobStatus, TransactionStatus
from app.database.models import Transaction
from app.services.management_service import ManagementService
from app.workers.demo import DemoWorkerPool

pytestmark = pytest.mark.postgres


def _running_demo(service):
    key = uuid4().hex
    job = service.create_job(f"fairness-{key}", "infrastructure_demo")
    transaction = service.add_transaction(
        job.id, f"record-{key}", f"fairness-{key}", external_reference="demo:success"
    )
    service.start_job(job.id)
    return job, transaction


def _wait_for_completion(service, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if service.get_job(job_id).status == JobStatus.COMPLETED:
            return
        time.sleep(0.05)
    pytest.fail("An eligible demo job was not completed by the worker before the deadline.")


def test_one_hundred_quarantined_jobs_do_not_starve_later_demo(service, session_factory, db_engine):
    blocked_ids = []
    pool = DemoWorkerPool(service, ManagementService(session_factory), poll_seconds=0.05)
    try:
        for _ in range(100):
            job, _ = _running_demo(service)
            blocked_ids.append(job.id)
            claim = service.claim(job.id, "quarantined-fairness-worker")
            service.fail(claim, "UNCERTAIN_DEMO_RESULT", outcome_uncertain=True)

        ready_job, ready_transaction = _running_demo(service)
        # The old scheduler selected exactly these first 100 blocked jobs.
        # Verify that the actual worker now makes progress on the later job.
        pool.start()
        _wait_for_completion(service, ready_job.id)
        completed = service.get_transaction(ready_transaction.id)
        assert completed.status == TransactionStatus.SUCCESS
        assert completed.attempt_count == 1
        with db_engine.connect() as connection:
            still_quarantined = connection.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(
                    Transaction.job_id.in_(blocked_ids),
                    Transaction.reconciliation_required.is_(True),
                    Transaction.status == TransactionStatus.RETRY,
                    Transaction.attempt_count == 1,
                )
            )
        assert still_quarantined == 100
    finally:
        pool.stop()
        for job_id in blocked_ids:
            service.cancel_job(job_id)


def test_scheduler_rechecks_database_time_and_keeps_recovery_and_finalization(
    service, session_factory, db_engine
):
    management = ManagementService(session_factory)
    delayed, delayed_transaction = _running_demo(service)
    claim = service.claim(delayed.id, "delayed-fairness-worker")
    service.fail(claim, "SAFE_RETRY", retryable=True)
    leased, leased_transaction = _running_demo(service)
    service.claim(leased.id, "active-fairness-worker", lease_seconds=60)
    finished, _ = _running_demo(service)
    service.succeed(service.claim(finished.id, "completed-fairness-worker"))
    empty = service.create_job(f"empty-{uuid4().hex}", "infrastructure_demo")
    service.start_job(empty.id)
    with db_engine.begin() as connection:
        connection.execute(
            update(Transaction)
            .where(Transaction.id == delayed_transaction.id)
            .values(available_at=func.clock_timestamp() + timedelta(hours=1))
        )
    candidates = set(management.running_demo_job_ids())
    assert delayed.id not in candidates
    assert leased.id not in candidates
    assert {finished.id, empty.id} <= candidates

    with db_engine.begin() as connection:
        connection.execute(
            update(Transaction)
            .where(Transaction.id == delayed_transaction.id)
            .values(available_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        connection.execute(
            update(Transaction)
            .where(Transaction.id == leased_transaction.id)
            .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
    candidates = set(management.running_demo_job_ids())
    assert {delayed.id, leased.id, finished.id, empty.id} <= candidates

    pool = DemoWorkerPool(service, management, poll_seconds=0.05)
    try:
        pool.start()
        for job_id in (delayed.id, finished.id, empty.id):
            _wait_for_completion(service, job_id)
        deadline = time.monotonic() + 10
        while not service.get_transaction(leased_transaction.id).reconciliation_required:
            assert time.monotonic() < deadline, "Expired lease did not enter reconciliation."
            time.sleep(0.05)
        assert service.get_transaction(delayed_transaction.id).attempt_count == 2
        assert leased.id not in management.running_demo_job_ids()
    finally:
        pool.stop()
        for job_id in (delayed.id, leased.id, finished.id, empty.id):
            if service.get_job(job_id).status == JobStatus.RUNNING:
                service.cancel_job(job_id)
