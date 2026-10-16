"""Real PostgreSQL proves due-slot atomicity, concurrency and safe catch-up."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.exceptions import (
    DatabaseOperationError,
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    OwnershipLostError,
    ValidationError,
)
from app.database.models import AuditLog, FlowRun, FlowSchedule, Job, Transaction
from app.database.repositories.flow_repository import FlowRepository
from app.services.flow_service import FlowService
from app.services.schedule_service import ScheduleService

pytestmark = pytest.mark.postgres


def graph():
    return {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "label": "Başla",
                "position": {"x": 80, "y": 120},
                "config": {},
            },
            {
                "id": "end",
                "type": "end",
                "label": "Tamamla",
                "position": {"x": 360, "y": 120},
                "config": {"output": {}},
            },
        ],
        "edges": [{"source": "start", "target": "end", "port": "next"}],
    }


@pytest.fixture
def schedules(session_factory):
    return ScheduleService(session_factory)


@pytest.fixture
def flows(session_factory):
    return FlowService(session_factory)


@pytest.fixture
def flow(flows, session_factory):
    result = flows.create_flow(name=f"Zamanlı test {uuid4().hex[:8]}", graph=graph())
    yield result
    # Other tests share this isolated database; leave no due background work.
    with session_factory.begin() as session:
        session.execute(
            update(FlowSchedule)
            .where(FlowSchedule.flow_id == UUID(result["id"]))
            .values(enabled=False)
        )


def configuration(flow, **changes):
    return {
        "request_id": uuid4(),
        "name": "Test zamanlaması",
        "flow_id": flow["id"],
        "version": 1,
        "kind": "once",
        "next_run_at": datetime.now(UTC) + timedelta(days=1),
        **changes,
    }


def due(session_factory, schedule, seconds_ago=1):
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        value = now - timedelta(seconds=seconds_ago)
        session.execute(
            update(FlowSchedule)
            .where(FlowSchedule.id == UUID(schedule["id"]))
            .values(next_run_at=value)
        )
        return value


def finished(service, job_id):
    claim = service.claim(job_id, "schedule-completion-test", lease_seconds=30)
    assert claim is not None
    service.succeed(claim, result_data={"done": True})
    service.finalize_job(job_id)


def test_eight_schedulers_publish_one_atomic_job(schedules, flow, session_factory):
    schedule = schedules.create_schedule(**configuration(flow))
    due(session_factory, schedule)
    barrier = Barrier(8)

    def dispatch(_):
        barrier.wait()
        return schedules.dispatch_due()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(dispatch, range(8)))
    published = [item for batch in results for item in batch]
    assert len(published) == 1
    saved = schedules.get_schedule(schedule["id"])
    assert saved["state"] == "completed"
    assert saved["run_count"] == 1
    assert saved["revision"] == 2
    assert saved["next_run_at"] is None
    with session_factory() as session:
        job = session.get(Job, UUID(saved["last_job_id"]))
        assert job.status.value == "RUNNING"
        assert job.total_records == 1
        assert (
            session.scalar(
                select(func.count()).select_from(FlowRun).where(FlowRun.flow_id == UUID(flow["id"]))
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Transaction).where(Transaction.job_id == job.id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.job_id == job.id, AuditLog.action == "SCHEDULE_DISPATCHED")
            )
            == 1
        )


def test_eight_identical_creations_are_idempotent(schedules, flow, session_factory):
    values = configuration(flow, inputs={"amount": 1e20, "ready": True})
    barrier = Barrier(8)

    def create(_):
        barrier.wait()
        return schedules.create_schedule(**values)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create, range(8)))
    assert {r["id"] for r in results} == {str(values["request_id"])}
    assert schedules.schedules(flow_id=flow["id"])["total"] == 1
    with pytest.raises(IdempotencyConflictError):
        schedules.create_schedule(**{**values, "inputs": {"amount": 1e20, "ready": 1}})
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "SCHEDULE_CREATED",
                    AuditLog.new_value["schedule_id"].astext == str(values["request_id"]),
                )
            )
            == 1
        )


def test_validation_failure_pauses_after_rolling_back_partial_job_snapshot_and_audit(
    schedules, flow, session_factory, monkeypatch
):
    schedule = schedules.create_schedule(**configuration(flow))
    original_due = due(session_factory, schedule)

    def interrupted(_self, **_values):
        raise ValidationError("FAKE_PRIVATE_ASSEMBLY_PAYLOAD")

    with monkeypatch.context() as patch:
        patch.setattr(FlowRepository, "add_run", interrupted)
        assert schedules.dispatch_due() == []
    saved = schedules.get_schedule(schedule["id"])
    assert datetime.fromisoformat(saved["next_run_at"]) == original_due
    assert saved["state"] == "paused"
    assert saved["run_count"] == saved["missed_runs"] == 0
    assert saved["revision"] == 2
    assert saved["last_job_id"] is None
    assert saved["last_run_at"] is None
    request_id = uuid5(UUID(schedule["id"]), f"1:{original_due.isoformat()}")
    with session_factory() as session:
        assert session.scalar(select(Job).where(Job.job_id == f"flow:{request_id}")) is None
        audit = session.scalars(
            select(AuditLog)
            .where(AuditLog.new_value["schedule_id"].astext == schedule["id"])
            .order_by(AuditLog.created_at)
        ).all()
        assert [row.action for row in audit] == ["SCHEDULE_CREATED", "SCHEDULE_VALIDATION_PAUSED"]
        assert audit[-1].new_value == {"schedule_id": schedule["id"], "revision": 2}
        assert audit[-1].old_value is None
        assert audit[-1].job_id is audit[-1].transaction_id is None
        assert (
            session.scalar(
                select(func.count()).select_from(FlowRun).where(FlowRun.flow_id == UUID(flow["id"]))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.external_reference == flow["id"])
            )
            == 0
        )
    assert schedules.dispatch_due() == []
    schedules.resume(schedule["id"], expected_revision=2)
    assert len(schedules.dispatch_due()) == 1


def test_uncertain_commit_acknowledgement_never_publishes_second_run(
    flows, schedules, flow, session_factory, monkeypatch
):
    schedule = schedules.create_schedule(**configuration(flow))
    due(session_factory, schedule)
    original_commit = session_factory.class_.commit

    def committed_but_reply_lost(session):
        original_commit(session)
        raise OperationalError("Test lost acknowledgement", None, RuntimeError("test"))

    with monkeypatch.context() as patch:
        patch.setattr(session_factory.class_, "commit", committed_but_reply_lost)
        with pytest.raises(DatabaseOperationError):
            schedules.dispatch_due()
    assert schedules.dispatch_due() == []
    assert schedules.get_schedule(schedule["id"])["run_count"] == 1
    assert flows.list_runs(flow["id"])["total"] == 1


def test_interval_coalesces_missed_slots_and_keeps_anchor(schedules, flow, session_factory):
    schedule = schedules.create_schedule(
        **configuration(flow, kind="interval", interval_seconds=60)
    )
    original_due = due(session_factory, schedule, seconds_ago=190)
    result = schedules.dispatch_due()
    assert len(result) == 1
    assert datetime.fromisoformat(result[0]["due_at"]) == original_due + timedelta(seconds=180)
    saved = schedules.get_schedule(schedule["id"])
    assert saved["missed_runs"] == 3
    assert saved["run_count"] == 1
    assert datetime.fromisoformat(saved["next_run_at"]) == original_due + timedelta(seconds=240)
    assert schedules.dispatch_due() == []


def test_active_previous_job_blocks_overlap_until_finished(
    schedules, flow, session_factory, service
):
    schedule = schedules.create_schedule(
        **configuration(flow, kind="interval", interval_seconds=60)
    )
    due(session_factory, schedule)
    first = schedules.dispatch_due()[0]
    due(session_factory, schedule, seconds_ago=190)
    assert schedules.dispatch_due() == []
    finished(service, first["job_id"])
    second = schedules.dispatch_due()[0]
    assert second["job_id"] != first["job_id"]
    assert schedules.get_schedule(schedule["id"])["missed_runs"] == 3


def test_cancelled_uncertain_run_blocks_overlap_and_invalidates_old_worker(
    schedules, flow, session_factory, service
):
    schedule = schedules.create_schedule(
        **configuration(flow, kind="interval", interval_seconds=60)
    )
    due(session_factory, schedule)
    first = schedules.dispatch_due()[0]
    claim = service.claim(first["job_id"], "uncertain-schedule-test", lease_seconds=30)
    service.cancel_job(first["job_id"])
    due(session_factory, schedule)
    assert schedules.dispatch_due() == []
    with pytest.raises(OwnershipLostError):
        service.succeed(claim, result_data={})
    transaction = service.get_transaction(claim.transaction_id)
    assert transaction.reconciliation_required
    service.reconcile(
        transaction.id, expected_fencing_token=transaction.fencing_token, outcome="not_applied"
    )
    assert len(schedules.dispatch_due()) == 1


def test_blocked_schedules_do_not_starve_later_due_schedule(schedules, flow, session_factory):
    blocked = [
        schedules.create_schedule(**configuration(flow, kind="interval", interval_seconds=60))
        for _ in range(4)
    ]
    for schedule in blocked:
        due(session_factory, schedule)
    assert len(schedules.dispatch_due()) == 4
    for schedule in blocked:
        due(session_factory, schedule, seconds_ago=120)
    ready = schedules.create_schedule(**configuration(flow))
    due(session_factory, ready)
    assert schedules.dispatch_due(limit=1)[0]["schedule_id"] == ready["id"]


def test_locked_due_row_is_skipped_without_blocking_other_schedule(
    schedules, flow, session_factory
):
    first = schedules.create_schedule(**configuration(flow))
    second = schedules.create_schedule(**configuration(flow))
    due(session_factory, first, seconds_ago=120)
    due(session_factory, second)
    with session_factory.begin() as session:
        session.scalar(
            select(FlowSchedule).where(FlowSchedule.id == UUID(first["id"])).with_for_update()
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(schedules.dispatch_due, limit=1).result(timeout=5)
    assert result[0]["schedule_id"] == second["id"]
    assert schedules.dispatch_due(limit=1)[0]["schedule_id"] == first["id"]


def test_pause_resume_and_edit_reject_stale_revision(schedules, flow, session_factory):
    values = configuration(flow)
    schedule = schedules.create_schedule(**values)
    paused = schedules.pause(schedule["id"], expected_revision=1)
    due(session_factory, schedule)
    assert paused["state"] == "paused"
    assert schedules.dispatch_due() == []
    with pytest.raises(InvalidTransitionError):
        schedules.resume(schedule["id"], expected_revision=1)
    resumed = schedules.resume(schedule["id"], expected_revision=2)
    assert resumed["revision"] == 3
    edited_values = {key: value for key, value in values.items() if key != "request_id"}
    edited = schedules.update_schedule(
        schedule["id"], expected_revision=3, **{**edited_values, "name": "Edited"}
    )
    assert edited["revision"] == 4
    with pytest.raises(InvalidTransitionError):
        schedules.update_schedule(schedule["id"], expected_revision=3, **edited_values)
    # Original creation retries still return the canonical, now edited schedule.
    assert schedules.create_schedule(**values)["name"] == "Edited"


def test_completed_once_cannot_resume_but_accepts_explicit_new_date(
    schedules, flow, session_factory, service
):
    values = configuration(flow)
    schedule = schedules.create_schedule(**values)
    due(session_factory, schedule)
    first = schedules.dispatch_due()[0]
    with pytest.raises(InvalidTransitionError):
        schedules.resume(schedule["id"], expected_revision=2)
    assert schedules.create_schedule(**values)["state"] == "completed"
    updated = schedules.update_schedule(
        schedule["id"],
        expected_revision=2,
        **{key: value for key, value in values.items() if key != "request_id"},
    )
    assert updated["state"] == "enabled"
    assert updated["last_job_id"] == first["job_id"]
    due(session_factory, schedule)
    assert schedules.dispatch_due() == []
    finished(service, first["job_id"])
    assert len(schedules.dispatch_due()) == 1


def test_schedule_pins_original_flow_version_and_inputs(flows, schedules, flow, session_factory):
    schedule = schedules.create_schedule(
        **configuration(flow, inputs={"amount": 7}, max_attempts=2)
    )
    flows.update_flow(flow["id"], name="New flow name", graph=graph(), expected_version=1)
    due(session_factory, schedule)
    run = schedules.dispatch_due()[0]
    spec = flows.execution_spec(run["job_id"])
    assert spec["version"] == 1
    assert spec["name"] == flow["name"]
    assert spec["inputs"] == {"amount": 7}
    with session_factory() as session:
        row = session.get(FlowRun, UUID(run["job_id"]))
        assert row.max_attempts == 2
    assert schedules.get_schedule(schedule["id"])["flow_name"] == flow["name"]


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "unknown"},
        {"kind": "once", "interval_seconds": 60},
        {"kind": "interval"},
        {"kind": "interval", "interval_seconds": 59},
        {"kind": "interval", "interval_seconds": True},
        {"kind": "interval", "interval_seconds": 2678401},
        {"next_run_at": "2027-01-01T12:00:00"},
        {"next_run_at": "bad-date"},
        {"next_run_at": datetime(2020, 1, 1, tzinfo=UTC)},
        {"enabled": 1},
        {"max_attempts": 0},
        {"max_attempts": True},
        {"name": "   "},
        {"inputs": {"password": "fake-value"}},
        {"inputs": {"text": "x" * 13000}},
    ],
)
def test_invalid_schedule_never_persists(schedules, flow, changes):
    with pytest.raises(ValidationError):
        schedules.create_schedule(**configuration(flow, **changes))
    assert schedules.schedules(flow_id=flow["id"])["total"] == 0


def test_unknown_flow_revision_is_rejected(schedules, flow):
    with pytest.raises(NotFoundError):
        schedules.create_schedule(**configuration(flow, version=2))


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "interval", "interval_seconds": None},
        {"kind": "once", "interval_seconds": 60},
        {"enabled": True, "next_run_at": None},
        {"run_count": -1},
        {"max_attempts": 101},
    ],
)
def test_postgresql_enforces_schedule_invariants(schedules, flow, session_factory, changes):
    schedule = schedules.create_schedule(**configuration(flow))
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.execute(
            update(FlowSchedule).where(FlowSchedule.id == UUID(schedule["id"])).values(**changes)
        )
