"""Bounded scheduler loop; PostgreSQL owns dispatch identity and concurrency."""

import math
from datetime import UTC, datetime
from threading import Event, Lock, Thread

from app.core.config import ConfigurationError


def poll_interval(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.01 <= value <= 60
    ):
        raise ConfigurationError("Scheduler polling must be between 0.01 and 60 seconds")
    return float(value)


class SchedulerRunner:
    """Dispatch committed schedules without storing payloads or exception details."""

    def __init__(self, schedule_service, *, enabled=True, poll_seconds=1):
        self.service = schedule_service
        self.poll_seconds = poll_interval(poll_seconds)
        if type(enabled) is not bool:
            raise ConfigurationError("Scheduler enabled flag must be boolean")
        self.enabled = enabled
        self._stop = Event()
        self._lock = Lock()
        self._lifecycle_lock = Lock()
        self._thread = None
        self._state = {
            "enabled": enabled,
            "state": "stopped",
            "last_tick": None,
            "last_dispatched_count": 0,
            "total_dispatched": 0,
            "error_count": 0,
        }

    def start(self):
        with self._lifecycle_lock:
            if not self.enabled or (self._thread is not None and self._thread.is_alive()):
                return
            self._stop.clear()
            self._thread = None
            try:
                thread = Thread(target=self._run, name="rpa-scheduler", daemon=True)
                thread.start()
                self._thread = thread
            except BaseException:
                self._stop.set()
                raise

    def stop(self, timeout=6):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 <= timeout <= 60
        ):
            raise ConfigurationError("Scheduler stop timeout must be between 0 and 60 seconds")
        with self._lifecycle_lock:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=timeout)

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _run(self):
        try:
            while not self._stop.is_set():
                with self._lock:
                    self._state.update(
                        state="dispatching",
                        last_tick=datetime.now(UTC).isoformat(),
                        last_dispatched_count=0,
                    )
                if self._stop.is_set():
                    break
                try:
                    dispatched = self.service.dispatch_due(limit=20)
                    with self._lock:
                        self._state["last_dispatched_count"] = len(dispatched)
                        self._state["total_dispatched"] += len(dispatched)
                        self._state["state"] = "idle"
                except Exception:
                    # A lost commit ACK is resolved by the durable dispatch key;
                    # no database exception or schedule input is copied to logs.
                    with self._lock:
                        self._state["error_count"] += 1
                        self._state["state"] = "error"
                self._stop.wait(self.poll_seconds)
        finally:
            with self._lock:
                self._state["state"] = "stopped"
