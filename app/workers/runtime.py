"""Leased worker execution for explicitly registered business workflows.

Automatic heartbeats protect long synchronous handlers. Python cannot forcibly
cancel a handler: it must cooperate through context.stop_requested/check_active.
Every persisted write is still fenced by PostgreSQL. A stopped or disconnected
worker never reports success, and ambiguous acknowledgements are never replayed.
"""

import os
import time
from copy import deepcopy
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from uuid import uuid4

from app.core.exceptions import (
    DatabaseOperationError,
    InvalidTransitionError,
    OwnershipLostError,
    ValidationError,
)
from app.core.validation import validate_number
from app.workflows.registry import SafeRetry, WorkflowFailure, WorkflowRegistry


class WorkflowContext:
    """A transaction snapshot and the current worker ownership capability.

    checkpoint() and get_checkpoint() use the execution service, without exposing
    database sessions. check_active() renews and validates ownership against the DB;
    call it immediately before each external action. A claim alone cannot fence an
    external application, so handlers must also use target-side idempotency.
    """

    def __init__(self, pool, worker_id, claim, transaction):
        self._pool = pool
        self._worker_id = worker_id
        self._claim = claim
        self.transaction = transaction
        self._operation_lock = Lock()
        self._lost = Event()
        self._heartbeat_stop = Event()
        self._heartbeat = Thread(
            target=self._keep_alive, name=f"{worker_id}-heartbeat", daemon=True
        )

    @property
    def claim(self):
        return self._claim

    @property
    def stop_requested(self) -> bool:
        return self._pool._stop.is_set() or self._lost.is_set()

    def _assert_active(self):
        if self.stop_requested:
            raise OwnershipLostError("Workflow execution is no longer active")

    def _renew(self):
        self._assert_active()
        try:
            self._claim = self._pool.execution.heartbeat(
                self._claim, lease_seconds=self._pool.lease_seconds
            )
        except Exception:
            self._lost.set()
            raise
        # The stop signal may arrive while the database call is in progress.
        self._assert_active()
        self._pool._update(
            self._worker_id, state="processing", transaction_id=self._claim.transaction_id
        )
        return self._claim

    def check_active(self):
        with self._operation_lock:
            return self._renew()

    def checkpoint(self, key: str, value: dict):
        with self._operation_lock:
            self._renew()
            try:
                return self._pool.execution.checkpoint(self._claim, key, value)
            except (DatabaseOperationError, OwnershipLostError):
                self._lost.set()
                raise

    def get_checkpoint(self, key: str) -> dict | None:
        with self._operation_lock:
            self._renew()
            try:
                checkpoints = self._pool.execution.get_checkpoints(self._claim.transaction_id)
            except (DatabaseOperationError, OwnershipLostError):
                self._lost.set()
                raise
            self._assert_active()
            for checkpoint in checkpoints:
                if checkpoint.checkpoint_key == key:
                    return deepcopy(checkpoint.checkpoint_value)
            return None

    def _keep_alive(self):
        while not self._heartbeat_stop.wait(self._pool.heartbeat_seconds):
            if self._pool._stop.is_set():
                return
            try:
                with self._operation_lock:
                    if self._heartbeat_stop.is_set():
                        return
                    self._renew()
            except Exception:
                # Neither driver details nor handler data enter logs or state.
                self._lost.set()
                return

    def _close(self):
        self._heartbeat_stop.set()
        # Wait in the worker, never in the stop caller without its join budget.
        # A blocked DB call must remain visible as a live worker until it ends.
        if self._heartbeat.ident is not None:
            self._heartbeat.join()


class WorkflowWorkerPool:
    def __init__(
        self,
        execution_service,
        management_service,
        registry,
        worker_count=2,
        poll_seconds=0.25,
        lease_seconds=30,
        heartbeat_seconds=None,
    ):
        if not isinstance(registry, WorkflowRegistry):
            raise ValidationError("A workflow registry is required")
        if type(worker_count) is not int or not 1 <= worker_count <= 8:
            raise ValidationError("Worker count must be between 1 and 8")
        self.poll_seconds = validate_number(poll_seconds, "poll_seconds", minimum=0.01, maximum=5)
        self.lease_seconds = validate_number(
            lease_seconds, "lease_seconds", minimum=0.03, maximum=86_400
        )
        self.heartbeat_seconds = validate_number(
            self.lease_seconds / 3 if heartbeat_seconds is None else heartbeat_seconds,
            "heartbeat_seconds",
            minimum=0.001,
            maximum=self.lease_seconds / 2,
        )
        self.execution = execution_service
        self.management = management_service
        self.registry = registry
        self._stop = Event()
        self._lock = Lock()
        self._lifecycle_lock = Lock()
        self._threads = []
        self._contexts = {}
        instance_id = f"{os.getpid()}-{uuid4().hex}"
        self._state = {
            f"workflow-worker-{instance_id}-{index}": {
                "worker_id": f"workflow-worker-{instance_id}-{index}",
                "state": "stopped",
                "last_heartbeat": None,
                "processed_count": 0,
                "error_count": 0,
                "transaction_id": None,
                "workflow_names": list(registry.names()),
            }
            for index in range(1, worker_count + 1)
        }

    def start(self):
        with self._lifecycle_lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            if self._threads:
                return
            self._stop.clear()
            try:
                for worker_id in self._state:
                    thread = Thread(
                        target=self._run, args=(worker_id,), name=worker_id, daemon=True
                    )
                    thread.start()
                    self._threads.append(thread)
            except BaseException:
                # A partial start must stop earlier workers and leave only
                # successfully started threads for stop() to join.
                self._stop.set()
                raise

    def stop(self, timeout=6):
        timeout = validate_number(timeout, "stop timeout", minimum=0, maximum=60)
        with self._lifecycle_lock:
            self._stop.set()
            with self._lock:
                for context in self._contexts.values():
                    context._heartbeat_stop.set()
                for thread in self._threads:
                    if thread.is_alive():
                        self._state[thread.name]["state"] = "stopping"
            deadline = time.monotonic() + timeout
            for thread in self._threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            self._threads = [thread for thread in self._threads if thread.is_alive()]

    def snapshot(self):
        with self._lock:
            return [deepcopy(entry) for entry in self._state.values()]

    def _update(self, worker_id, *, state=None, transaction_id=None, processed=0, errors=0):
        with self._lock:
            entry = self._state[worker_id]
            entry["last_heartbeat"] = datetime.now(UTC).isoformat()
            if state is not None:
                entry["state"] = state
            entry["transaction_id"] = str(transaction_id) if transaction_id else None
            entry["processed_count"] += processed
            entry["error_count"] += errors

    def _run(self, worker_id):
        try:
            while not self._stop.is_set():
                self._update(worker_id, state="idle")
                try:
                    job_ids = self.management.running_workflow_job_ids(self.registry.names())
                    for job_id in job_ids:
                        if self._stop.is_set():
                            break
                        try:
                            job = self.execution.get_job(job_id)
                            definition = self.registry.get(job.workflow_name)
                            if definition is None:
                                continue
                            self._update(worker_id, state="recovering")
                            self.execution.recover_stale(job_id)
                            if self._stop.is_set():
                                break
                            claim = self.execution.claim(
                                job_id, worker_id, lease_seconds=self.lease_seconds
                            )
                            if claim is not None:
                                self._process(worker_id, claim, definition)
                            try:
                                self.execution.finalize_job(job_id)
                            except InvalidTransitionError:
                                pass
                        except (InvalidTransitionError, OwnershipLostError):
                            continue
                        except Exception:
                            self._update(worker_id, state="error", errors=1)
                except Exception:
                    self._update(worker_id, state="error", errors=1)
                self._stop.wait(self.poll_seconds)
        finally:
            self._update(worker_id, state="stopped")

    def _fail(self, context, error_code, *, retryable=False, uncertain=False):
        with context._operation_lock:
            context._assert_active()
            try:
                self.execution.fail(
                    context.claim, error_code, retryable=retryable, outcome_uncertain=uncertain
                )
            except ValidationError:
                # Error-code validation happened before any database operation.
                self.execution.fail(
                    context.claim, "WORKFLOW_ERROR_CODE_INVALID", outcome_uncertain=True
                )

    def _process(self, worker_id, claim, definition):
        self._update(worker_id, state="processing", transaction_id=claim.transaction_id)
        context = None
        succeeded = False
        try:
            transaction = self.execution.get_transaction(claim.transaction_id)
            context = WorkflowContext(self, worker_id, claim, transaction)
            with self._lock:
                self._contexts[worker_id] = context
            context._heartbeat.start()
            context.check_active()
            try:
                result = definition.execute(context)
            except SafeRetry as error:
                context._heartbeat_stop.set()
                self._fail(context, error.error_code, retryable=True)
            except WorkflowFailure as error:
                context._heartbeat_stop.set()
                self._fail(context, error.error_code)
            except (DatabaseOperationError, OwnershipLostError):
                # A persistence operation might have committed without an ACK.
                # Stop renewing and let recovery inspect/fence the stored state.
                context._lost.set()
            except Exception:
                context._heartbeat_stop.set()
                self._fail(context, "WORKFLOW_OUTCOME_UNCERTAIN", uncertain=True)
            else:
                context._heartbeat_stop.set()
                if result is not None and not isinstance(result, dict):
                    self._fail(context, "WORKFLOW_RESULT_INVALID", uncertain=True)
                else:
                    try:
                        with context._operation_lock:
                            context._assert_active()
                            self.execution.succeed(context.claim, {} if result is None else result)
                        succeeded = True
                    except ValidationError:
                        self._fail(context, "WORKFLOW_RESULT_INVALID", uncertain=True)
        except Exception:
            # Never replay failed/uncertain writes or persist arbitrary text.
            # The lease sweeper quarantines any unresolved PROCESSING record.
            if context is not None:
                context._lost.set()
        finally:
            if context is not None:
                context._close()
                with self._lock:
                    self._contexts.pop(worker_id, None)
            self._update(
                worker_id, state="idle", processed=int(succeeded), errors=int(not succeeded)
            )
