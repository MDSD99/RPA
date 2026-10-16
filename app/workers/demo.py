"""Two local demonstration workers exercising the real execution infrastructure.

Only the reserved infrastructure_demo workflow is claimed. These workers never
perform external business actions; recovery simulates a lost worker response.
"""

from datetime import UTC, datetime
from threading import Event, Lock, Thread

from app.core.exceptions import InvalidTransitionError, OwnershipLostError


class DemoWorkerPool:
    def __init__(self, execution_service, management_service, worker_count=2, poll_seconds=0.25):
        if type(worker_count) is not int or not 1 <= worker_count <= 8:
            raise ValueError("Worker count must be between 1 and 8")
        if not 0.01 <= poll_seconds <= 5:
            raise ValueError("Worker polling interval is outside the supported range")
        self.execution = execution_service
        self.management = management_service
        self.poll_seconds = poll_seconds
        self._stop = Event()
        self._lock = Lock()
        self._lifecycle_lock = Lock()
        self._threads = []
        self._state = {
            f"demo-worker-{index}": {
                "worker_id": f"demo-worker-{index}",
                "state": "stopped",
                "last_heartbeat": None,
                "processed_count": 0,
                "error_count": 0,
                "transaction_id": None,
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
                self._stop.set()
                raise

    def stop(self):
        with self._lifecycle_lock:
            self._stop.set()
            for thread in self._threads:
                thread.join(timeout=6)
            # A blocked database call can exceed the join budget; report its real
            # thread state instead of falsely claiming the worker already stopped.
            self._threads = [thread for thread in self._threads if thread.is_alive()]

    def snapshot(self):
        with self._lock:
            return [dict(value) for value in self._state.values()]

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
                    for job_id in self.management.running_demo_job_ids():
                        if self._stop.is_set():
                            break
                        self._update(worker_id, state="recovering")
                        try:
                            self.execution.recover_stale(job_id)
                            # Short leases are intentional here: all simulated
                            # actions finish below one second and are local only.
                            claim = self.execution.claim(job_id, worker_id, lease_seconds=2)
                        except InvalidTransitionError:
                            continue  # Another worker completed/cancelled the job.
                        if claim is not None:
                            self._process(worker_id, claim)
                        try:
                            self.execution.finalize_job(job_id)
                        except InvalidTransitionError:
                            pass  # Queue, processing lease or reconciliation remains.
                except (InvalidTransitionError, OwnershipLostError):
                    pass
                except Exception:
                    # A driver or user payload must never enter worker logs.
                    self._update(worker_id, state="error", errors=1)
                self._stop.wait(self.poll_seconds)
        finally:
            self._update(worker_id, state="stopped")

    def _process(self, worker_id, claim):
        self._update(worker_id, state="processing", transaction_id=claim.transaction_id)
        transaction = self.execution.get_transaction(claim.transaction_id)
        if transaction.external_reference not in {
            "demo:success",
            "demo:retry_once",
            "demo:recovery",
        }:
            self.execution.fail(claim, "DEMO_CONFIGURATION_INVALID")
            self._update(worker_id, state="idle", errors=1)
            return
        self.execution.checkpoint(claim, "demo_progress", {"step": "prepared", "demo": True})
        self._stop.wait(0.35)
        if transaction.external_reference == "demo:recovery" and claim.attempt_count == 1:
            # Deliberately leave PROCESSING: the sweeper must invalidate this
            # lease and quarantine it, not automatically perform a second attempt.
            self._update(worker_id, state="idle", errors=1)
            return
        if transaction.external_reference == "demo:retry_once" and claim.attempt_count == 1:
            self.execution.fail(claim, "DEMO_SAFE_RETRY", retryable=True)
            self._update(worker_id, state="idle", errors=1)
            return
        self.execution.checkpoint(claim, "demo_progress", {"step": "completed", "demo": True})
        self.execution.succeed(claim, {"demo": True, "external_action_performed": False})
        self._update(worker_id, state="idle", processed=1)
