"""Real PostgreSQL ownership across independent spawned Python worker processes."""

from __future__ import annotations

import multiprocessing
import os
import queue
import time

import pytest
from sqlalchemy import text

from app.core.config import DatabaseSettings
from app.core.exceptions import OwnershipLostError
from app.core.states import TransactionStatus
from app.database.connection import create_db_engine, create_session_factory
from app.services.execution_service import ExecutionService

pytestmark = pytest.mark.postgres


def _worker_service():
    # spawn ensures no pooled parent connection is inherited. Guard before connect.
    settings = DatabaseSettings.from_env()
    if settings.app_env != "test":
        raise RuntimeError("Child worker requires an explicitly isolated test database")
    engine = create_db_engine(settings)
    service = ExecutionService(
        create_session_factory(engine), retry_base_seconds=0, retry_max_seconds=0
    )
    return engine, service


def _dispose_safely(engine):
    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            # The process is exiting; never expose a raw driver disposal error.
            pass


def _claim_together(job_id, ready, start, results):
    engine = None
    try:
        engine, service = _worker_service()
        ready.put("ready")
        if not start.wait(timeout=30):
            results.put({"kind": "error", "code": "START_TIMEOUT"})
            return
        claim = service.claim(job_id, worker_id=f"spawn-worker-{os.getpid()}", lease_seconds=120)
        results.put({"kind": "claimed", "pid": os.getpid(), "claim": claim})
    except Exception:
        # Do not serialize exception/driver text, environment, URLs or locals.
        results.put({"kind": "error", "code": "CHILD_CLAIM_FAILED"})
    finally:
        _dispose_safely(engine)


def _checkpoint_and_wait_for_crash(job_id, committed):
    engine = None
    try:
        engine, service = _worker_service()
        claim = service.claim(job_id, worker_id=f"crash-worker-{os.getpid()}", lease_seconds=120)
        if claim is None:
            committed.put({"kind": "error", "code": "NO_CLAIM"})
            return
        service.checkpoint(claim, "resume_cursor", {"offset": 7})
        # Both service calls committed before the parent receives this capability.
        committed.put({"kind": "committed", "pid": os.getpid(), "claim": claim})
        # No shared Event/Condition is held when SIGKILL interrupts this process.
        # Waking a killed semaphore waiter can otherwise deadlock parent cleanup.
        time.sleep(45)
    except Exception:
        committed.put({"kind": "error", "code": "CHILD_CHECKPOINT_FAILED"})
    finally:
        _dispose_safely(engine)


def _resume_after_reconciliation(job_id, results):
    engine = None
    try:
        engine, service = _worker_service()
        claim = service.claim(job_id, worker_id=f"resume-worker-{os.getpid()}", lease_seconds=120)
        if claim is None:
            results.put({"kind": "error", "code": "NO_RESUME_CLAIM"})
            return
        checkpoints = service.get_checkpoints(claim.transaction_id)
        cursor = next(item for item in checkpoints if item.checkpoint_key == "resume_cursor")
        previous_value = cursor.checkpoint_value
        checkpoint = service.checkpoint(claim, "resume_cursor", {"offset": 8})
        service.succeed(claim, result_data={"resumed_from": previous_value["offset"]})
        results.put(
            {
                "kind": "resumed",
                "pid": os.getpid(),
                "claim": claim,
                "previous_value": previous_value,
                "checkpoint_fence": checkpoint.fencing_token,
            }
        )
    except Exception:
        results.put({"kind": "error", "code": "CHILD_RESUME_FAILED"})
    finally:
        _dispose_safely(engine)


def _messages(channel, count, timeout=30):
    deadline = time.monotonic() + timeout
    messages = []
    for _ in range(count):
        try:
            messages.append(channel.get(timeout=max(0.001, deadline - time.monotonic())))
        except queue.Empty:
            pytest.fail("A spawned test worker did not report before the timeout")
    return messages


def _cleanup(processes, channels):
    # Only children created by this test are touched; never server services.
    for process in processes:
        if process.pid is None:
            continue
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        process.close()
    for channel in channels:
        channel.cancel_join_thread()
        channel.close()


def test_four_spawned_worker_processes_claim_distinct_records(service, job):
    records = [
        service.add_transaction(
            job.id,
            record_id=f"process-record-{number}",
            idempotency_key=f"spawn-claim:{job.id}:{number}",
        )
        for number in range(4)
    ]
    service.start_job(job.id)
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    start = context.Event()
    workers = [
        context.Process(target=_claim_together, args=(job.id, ready, start, results))
        for _ in range(4)
    ]
    try:
        for worker in workers:
            worker.start()
        assert _messages(ready, 4) == ["ready"] * 4
        start.set()
        messages = _messages(results, 4)
        assert all(message["kind"] == "claimed" for message in messages)
        assert len({message["pid"] for message in messages}) == 4
        claims = [message["claim"] for message in messages]
        assert all(claim is not None for claim in claims)
        assert {claim.transaction_id for claim in claims} == {record.id for record in records}
        assert len({claim.owner_id for claim in claims}) == 4
        assert all(claim.attempt_count == 1 for claim in claims)
        assert service.claim(job.id, worker_id="parent-observer") is None
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        _cleanup(workers, [ready, results])


def test_killed_worker_checkpoint_recovers_in_new_process_with_new_fence(
    db_engine, service, job, transaction
):
    service.start_job(job.id)
    context = multiprocessing.get_context("spawn")
    committed = context.Queue()
    resumed = context.Queue()
    crashed_worker = context.Process(
        target=_checkpoint_and_wait_for_crash, args=(job.id, committed)
    )
    replacement = context.Process(target=_resume_after_reconciliation, args=(job.id, resumed))
    processes = [crashed_worker, replacement]
    try:
        crashed_worker.start()
        message = _messages(committed, 1)[0]
        assert message["kind"] == "committed"
        old_claim = message["claim"]
        assert old_claim.transaction_id == transaction.id
        assert crashed_worker.is_alive()
        # Simulate a real process crash, bypassing any application cleanup.
        crashed_worker.kill()
        crashed_worker.join(timeout=10)
        assert not crashed_worker.is_alive()
        assert crashed_worker.exitcode is not None and crashed_worker.exitcode < 0

        persisted = service.get_transaction(transaction.id)
        assert persisted.status == TransactionStatus.PROCESSING
        assert service.get_checkpoints(transaction.id)[0].checkpoint_value == {"offset": 7}
        # Only this explicitly guarded disposable test database is modified.
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE rpa_transactions "
                    "SET lease_expires_at = clock_timestamp() - INTERVAL '1 second' "
                    "WHERE id = :transaction_id"
                ),
                {"transaction_id": transaction.id},
            )
        recovered = service.recover_stale(job.id)
        assert len(recovered) == 1
        assert recovered[0].reconciliation_required is True
        assert recovered[0].fencing_token > old_claim.fencing_token
        assert service.claim(job.id, worker_id="unreconciled-observer") is None
        for operation in (
            lambda: service.heartbeat(old_claim),
            lambda: service.checkpoint(old_claim, "resume_cursor", {"offset": 999}),
            lambda: service.succeed(old_claim, result_data={"stale": True}),
        ):
            with pytest.raises(OwnershipLostError):
                operation()

        service.reconcile(
            transaction.id,
            expected_fencing_token=recovered[0].fencing_token,
            outcome="not_applied",
        )
        replacement.start()
        result = _messages(resumed, 1)[0]
        assert result["kind"] == "resumed"
        fresh_claim = result["claim"]
        assert fresh_claim.transaction_id == old_claim.transaction_id
        assert fresh_claim.owner_id != old_claim.owner_id
        assert fresh_claim.fencing_token > old_claim.fencing_token
        assert fresh_claim.attempt_count == 2
        assert result["previous_value"] == {"offset": 7}
        assert result["checkpoint_fence"] == fresh_claim.fencing_token
        replacement.join(timeout=10)
        assert replacement.exitcode == 0

        final = service.get_transaction(transaction.id)
        assert final.status == TransactionStatus.SUCCESS
        assert final.result_data == {"resumed_from": 7}
        assert final.attempt_count == 2
        assert service.get_checkpoints(transaction.id)[0].checkpoint_value == {"offset": 8}
        with pytest.raises(OwnershipLostError):
            service.succeed(old_claim, result_data={"stale": True})
    finally:
        _cleanup(processes, [committed, resumed])
