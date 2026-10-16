"""Thread allocation failures leave workers stoppable and restartable."""

from threading import Event, Thread
from unittest.mock import Mock

import pytest

from app.workers import demo, runtime, scheduler
from app.workflows.registry import WorkflowRegistry


def _fail_thread_allocation(monkeypatch, module, stage, ordinal, entered=None):
    failure = RuntimeError("Thread allocation failed")
    created = []
    attempts = 0

    def thread_factory(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        current = attempts
        if stage == "construct" and current == ordinal:
            raise failure
        thread = Thread(*args, **kwargs)
        original_start = thread.start

        def start():
            if stage == "start" and current == ordinal:
                raise failure
            original_start()
            if entered is not None and current < ordinal:
                assert entered.wait(2), "The earlier worker must be running before the failure"

        monkeypatch.setattr(thread, "start", start)
        created.append(thread)
        return thread

    monkeypatch.setattr(module, "Thread", thread_factory)
    return failure, created


@pytest.mark.parametrize("module", [demo, runtime], ids=["demo", "workflow"])
@pytest.mark.parametrize("stage", ["construct", "start"])
@pytest.mark.parametrize("ordinal", [1, 2], ids=["first-thread", "partial-start"])
def test_worker_allocation_failure_stops_earlier_threads_and_allows_restart(
    monkeypatch, module, stage, ordinal
):
    entered, release = Event(), Event()

    def read_jobs(*_args):
        entered.set()
        release.wait(5)
        return []

    management = Mock(running_demo_job_ids=read_jobs, running_workflow_job_ids=read_jobs)
    if module is demo:
        pool = demo.DemoWorkerPool(Mock(), management, poll_seconds=0.01)
    else:
        pool = runtime.WorkflowWorkerPool(Mock(), management, WorkflowRegistry(), poll_seconds=0.01)
    failure, created = _fail_thread_allocation(monkeypatch, module, stage, ordinal, entered)
    try:
        with pytest.raises(RuntimeError) as caught:
            pool.start()
        assert caught.value is failure
        release.set()
        # A failed start signals its already running workers immediately,
        # even before the owner reaches its shutdown handler.
        for thread in created:
            if thread.ident is not None:
                thread.join(timeout=2)
                assert not thread.is_alive()
        pool.stop()
        pool.stop()

        monkeypatch.setattr(module, "Thread", Thread)
        entered.clear()
        pool.start()
        assert entered.wait(2), "Workers must be restartable after allocation recovers"
    finally:
        release.set()
        pool.stop()
    assert all(worker["state"] == "stopped" for worker in pool.snapshot())


@pytest.mark.parametrize("stage", ["construct", "start"])
def test_scheduler_allocation_failure_allows_repeated_stop_and_restart(monkeypatch, stage):
    dispatched = Event()

    def dispatch(**_kwargs):
        dispatched.set()
        return []

    runner = scheduler.SchedulerRunner(Mock(dispatch_due=dispatch), poll_seconds=0.01)
    failure, _created = _fail_thread_allocation(monkeypatch, scheduler, stage, 1)
    try:
        with pytest.raises(RuntimeError) as caught:
            runner.start()
        assert caught.value is failure
        runner.stop()
        runner.stop()
        assert not dispatched.is_set()
        assert runner.snapshot()["state"] == "stopped"

        monkeypatch.setattr(scheduler, "Thread", Thread)
        runner.start()
        assert dispatched.wait(2), "Scheduler must be restartable after allocation recovers"
    finally:
        runner.stop()
    assert runner.snapshot()["state"] == "stopped"
