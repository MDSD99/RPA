"""Scheduler lifecycle, bounded shutdown, and actual persisted dispatch behavior."""

import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.core.config import ConfigurationError
from app.services.flow_service import FlowService
from app.services.schedule_service import ScheduleService
from app.workers.scheduler import SchedulerRunner


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Scheduler did not reach the expected state"
        time.sleep(0.01)


def test_disabled_scheduler_never_dispatches():
    service = Mock()
    runner = SchedulerRunner(service, enabled=False, poll_seconds=0.01)
    runner.start()
    runner.stop()
    service.dispatch_due.assert_not_called()
    assert runner.snapshot()["state"] == "stopped"
    assert runner.snapshot()["enabled"] is False


@pytest.mark.parametrize("value", [0, -1, 61, True, "1", float("inf"), float("nan")])
def test_invalid_scheduler_poll_is_rejected_before_dispatch(value):
    service = Mock()
    with pytest.raises(ConfigurationError):
        SchedulerRunner(service, poll_seconds=value)
    service.dispatch_due.assert_not_called()


def test_concurrent_start_is_idempotent_and_stop_is_bounded():
    entered, release = Event(), Event()
    lock = Lock()
    calls = 0

    def dispatch(**_kwargs):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(5)
        return []

    service = Mock(dispatch_due=dispatch)
    runner = SchedulerRunner(service, poll_seconds=0.01)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _index: runner.start(), range(8)))
    assert entered.wait(2)
    started = time.monotonic()
    runner.stop(timeout=0.02)
    assert time.monotonic() - started < 0.5
    runner.start()  # A blocked old thread cannot be replaced or have its stop flag cleared.
    assert calls == 1
    release.set()
    eventually(lambda: runner.snapshot()["state"] == "stopped")
    runner.stop()
    assert calls == 1


def test_dispatch_errors_are_sanitized_and_later_ticks_resume(capsys):
    service = Mock()
    service.dispatch_due.side_effect = [RuntimeError("FAKE_PRIVATE_CONFIGURATION"), [{}, {}]]
    runner = SchedulerRunner(service, poll_seconds=0.1)
    try:
        runner.start()
        eventually(lambda: runner.snapshot()["total_dispatched"] == 2)
    finally:
        runner.stop()
    state = runner.snapshot()
    assert state["state"] == "stopped"
    assert state["error_count"] >= 1
    assert state["last_tick"] is not None
    assert "FAKE_PRIVATE_CONFIGURATION" not in str(state)
    output = capsys.readouterr()
    assert "FAKE_PRIVATE_CONFIGURATION" not in output.out + output.err


def _command():
    path = Path(__file__).resolve().parents[1] / "scripts/scheduler.py"
    spec = importlib.util.spec_from_file_location("rpa_scheduler_command", path)
    assert spec is not None and spec.loader is not None
    command = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command)
    return command


@pytest.mark.parametrize("value", ["0", "nan", "inf", "61"])
def test_scheduler_cli_validates_poll_before_opening_database(monkeypatch, capsys, value):
    command = _command()
    database = Mock(side_effect=AssertionError("No database access allowed"))
    monkeypatch.setattr(command, "create_db_engine", database)
    assert command.main(["--once", "--poll-seconds", value]) == 1
    database.assert_not_called()
    assert "bastırıldı" in capsys.readouterr().err


@pytest.mark.postgres
def test_real_postgres_due_schedule_is_dispatched_once_by_parallel_runners(session_factory):
    flow = FlowService(session_factory).create_flow(
        name=f"Scheduler runtime {uuid4().hex}",
        graph={
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "label": "Başla",
                    "position": {"x": 80, "y": 100},
                    "config": {},
                },
                {
                    "id": "end",
                    "type": "end",
                    "label": "Tamamla",
                    "position": {"x": 360, "y": 100},
                    "config": {"output": {"scheduled": True}},
                },
            ],
            "edges": [{"source": "start", "target": "end", "port": "next"}],
        },
    )
    schedules = ScheduleService(session_factory)
    schedule = schedules.create_schedule(
        request_id=str(uuid4()),
        name="Parallel scheduler test",
        flow_id=flow["id"],
        version=1,
        kind="once",
        next_run_at=datetime.now(UTC) + timedelta(seconds=0.5),
    )
    runners = [SchedulerRunner(schedules, poll_seconds=0.01) for _ in range(2)]
    try:
        for runner in runners:
            runner.start()
        eventually(lambda: schedules.get_schedule(schedule["id"])["run_count"] == 1)
    finally:
        for runner in runners:
            runner.stop()
    saved = schedules.get_schedule(schedule["id"])
    assert saved["state"] == "completed"
    assert saved["next_run_at"] is None
    assert saved["last_job_id"] is not None
    assert sum(runner.snapshot()["total_dispatched"] for runner in runners) >= 1
    assert FlowService(session_factory).list_runs(flow["id"])["total"] == 1
