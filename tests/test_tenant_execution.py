"""PostgreSQL proves account ownership through API services, workers, and schedules."""

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import NotFoundError
from app.core.states import TransactionStatus
from app.core.tenancy import LEGACY_TENANT_ID
from app.database.models import (
    AuditLog,
    Checkpoint,
    Error,
    FlowRun,
    FlowSchedule,
    FlowVersion,
    Job,
    Transaction,
)
from app.entitlements.domain import EntitlementError
from app.entitlements.models import Entitlement, FlowReservation
from app.identity.models import Account, Tenant
from app.services.execution_service import ExecutionService
from app.services.flow_service import FlowService
from app.services.management_service import ManagementService
from app.services.schedule_service import ScheduleService
from app.workflows.registered import build_registry

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
                "config": {"output": {"finished": True}},
            },
        ],
        "edges": [{"source": "start", "target": "end", "port": "next"}],
    }


@dataclass(frozen=True)
class Customer:
    tenant_id: UUID
    account_id: UUID
    execution: ExecutionService
    flows: FlowService
    schedules: ScheduleService
    management: ManagementService


@pytest.fixture
def customers(session_factory):
    result = []
    for _ in range(2):
        tenant_id, account_id = uuid4(), uuid4()
        with session_factory.begin() as session:
            now = session.scalar(select(func.clock_timestamp()))
            session.add(Tenant(id=tenant_id, name="Execution isolation test"))
            session.flush()
            session.add(
                Account(
                    id=account_id,
                    tenant_id=tenant_id,
                    email=f"execution-{uuid4().hex}@example.com",
                    display_name="Execution customer",
                    password_hash="No login credential for execution fixtures",
                    email_verified=True,
                    approval="approved",
                )
            )
            session.add(
                Entitlement(
                    tenant_id=tenant_id,
                    trial_started_at=now - timedelta(days=1),
                    trial_ends_at=now + timedelta(days=1),
                )
            )
        result.append(
            Customer(
                tenant_id,
                account_id,
                ExecutionService(session_factory, tenant_id=tenant_id, retry_base_seconds=0),
                FlowService(session_factory, tenant_id=tenant_id),
                ScheduleService(session_factory, tenant_id=tenant_id),
                ManagementService(session_factory, tenant_id=tenant_id),
            )
        )
    yield tuple(result)
    # Later system-worker tests must not execute these fixtures' leftover jobs.
    with session_factory.begin() as session:
        tenant_ids = [customer.tenant_id for customer in result]
        session.execute(
            update(FlowSchedule).where(FlowSchedule.tenant_id.in_(tenant_ids)).values(enabled=False)
        )
        session.execute(
            update(Account).where(Account.tenant_id.in_(tenant_ids)).values(suspended=True)
        )


def create_flow(customer):
    return customer.flows.create_flow(name="Customer flow", graph=graph())


def create_run(customer, flow, *, request_id=None):
    return customer.flows.start_run(
        flow["id"], request_id=request_id or uuid4(), version=flow["version"]
    )


def create_schedule(customer, flow, session_factory, *, request_id=None):
    with session_factory() as session:
        now = session.scalar(select(func.clock_timestamp()))
    return customer.schedules.create_schedule(
        request_id=request_id or uuid4(),
        name="Customer schedule",
        flow_id=flow["id"],
        version=1,
        kind="once",
        next_run_at=now + timedelta(days=1),
    )


def set_due(session_factory, customer, schedule, *, seconds_ago=1):
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(FlowSchedule)
            .where(
                FlowSchedule.tenant_id == customer.tenant_id,
                FlowSchedule.id == UUID(schedule["id"]),
            )
            .values(next_run_at=now - timedelta(seconds=seconds_ago))
        )


def revoke(session_factory, customer, reason):
    with session_factory.begin() as session:
        if reason == "expired":
            now = session.scalar(select(func.clock_timestamp()))
            session.execute(
                update(Entitlement)
                .where(Entitlement.tenant_id == customer.tenant_id)
                .values(trial_ends_at=now - timedelta(seconds=1))
            )
        else:
            values = {
                "suspended": {"suspended": True},
                "unverified": {"email_verified": False},
                "unapproved": {"approval": "pending"},
            }[reason]
            session.execute(
                update(Account).where(Account.id == customer.account_id).values(**values)
            )


def test_reads_and_business_idempotency_stay_in_each_customer(customers, session_factory):
    first, second = customers
    first_flow, second_flow = create_flow(first), create_flow(second)
    business_key = f"shared-business-key-{uuid4()}"
    jobs = [customer.execution.create_job(business_key, "test") for customer in customers]
    transactions = [
        customer.execution.add_transaction(job.id, "same-record", business_key)
        for customer, job in zip(customers, jobs, strict=True)
    ]
    assert jobs[0].id != jobs[1].id
    assert transactions[0].id != transactions[1].id
    for customer, own_job, own_transaction, own_flow in zip(
        customers, jobs, transactions, [first_flow, second_flow], strict=True
    ):
        assert customer.management.counts()["jobs"] == 1
        assert customer.management.jobs()["items"][0]["id"] == str(own_job.id)
        assert customer.management.transactions()["items"][0]["id"] == str(own_transaction.id)
        assert customer.flows.list_flows()["items"][0]["id"] == own_flow["id"]
    with pytest.raises(NotFoundError):
        first.execution.get_job(jobs[1].id)
    with pytest.raises(NotFoundError):
        first.execution.get_transaction(transactions[1].id)
    assert first.management.transactions(job_id=jobs[1].id)["total"] == 0
    assert ManagementService(session_factory).jobs(q=business_key)["total"] == 0
    with session_factory() as session:
        assert session.get(Job, jobs[0].id).tenant_id == first.tenant_id


def test_flow_run_and_schedule_request_ids_are_tenant_scoped(customers, session_factory):
    request_id = uuid4()
    flows = [create_flow(customer) for customer in customers]
    runs = [
        create_run(customer, flow, request_id=request_id)
        for customer, flow in zip(customers, flows, strict=True)
    ]
    schedules = [
        create_schedule(customer, flow, session_factory, request_id=request_id)
        for customer, flow in zip(customers, flows, strict=True)
    ]
    assert runs[0]["job"]["id"] != runs[1]["job"]["id"]
    assert schedules[0]["id"] == schedules[1]["id"] == str(request_id)
    for customer, flow, run in zip(customers, flows, runs, strict=True):
        assert create_run(customer, flow, request_id=request_id)["job"]["id"] == run["job"]["id"]
        assert customer.flows.list_runs(flow["id"])["total"] == 1
        assert customer.schedules.schedules()["total"] == 1


def test_customer_cannot_change_or_execute_another_customer_resources(customers, session_factory):
    first, second = customers
    flow = create_flow(second)
    run = create_run(second, flow)
    schedule = create_schedule(second, flow, session_factory)
    transaction_id = second.management.transactions(job_id=UUID(run["job"]["id"]))["items"][0]["id"]
    actions = [
        lambda: first.flows.get_flow(flow["id"]),
        lambda: first.flows.update_flow(
            flow["id"], name="Changed", graph=graph(), expected_version=1
        ),
        lambda: first.flows.start_run(flow["id"], request_id=uuid4(), version=1),
        lambda: first.flows.execution_spec(run["job"]["id"]),
        lambda: first.flows.list_runs(flow["id"]),
        lambda: first.schedules.get_schedule(schedule["id"]),
        lambda: first.schedules.pause(schedule["id"], expected_revision=1),
        lambda: create_schedule(first, flow, session_factory),
        lambda: first.execution.cancel_job(run["job"]["id"]),
        lambda: first.execution.claim(run["job"]["id"], "other-customer"),
        lambda: first.execution.add_transaction(run["job"]["id"], "record", "key"),
        lambda: first.execution.get_checkpoints(transaction_id),
        lambda: first.management.transaction_detail(second.execution, transaction_id),
    ]
    for action in actions:
        with pytest.raises(NotFoundError):
            action()
    assert first.management.jobs()["total"] == 0
    assert second.flows.get_flow(flow["id"])["version"] == 1
    assert second.schedules.get_schedule(schedule["id"])["state"] == "enabled"


@pytest.fixture
def execution_rows(customers, session_factory):
    first, second = customers
    flow = create_flow(first)
    run = create_run(first, flow)
    job_id = UUID(run["job"]["id"])
    claim = first.execution.claim(job_id, "ownership-constraint-test")
    checkpoint = first.execution.checkpoint(claim, "cursor", {"index": 1})
    transaction = first.execution.fail(claim, "TEST_SAFE_FAILURE", retryable=True)
    schedule = create_schedule(first, flow, session_factory)
    with session_factory() as session:
        error_id = session.scalar(select(Error.id).where(Error.transaction_id == transaction.id))
        audit_id = session.scalar(
            select(AuditLog.id).where(AuditLog.transaction_id == transaction.id).limit(1)
        )
    return {
        "foreign_tenant": second.tenant_id,
        "job": job_id,
        "transaction": transaction.id,
        "checkpoint": checkpoint.id,
        "error": error_id,
        "audit": audit_id,
        "flow": UUID(flow["id"]),
        "schedule": UUID(schedule["id"]),
    }


@pytest.mark.parametrize(
    ("model", "key", "column"),
    [
        (Transaction, "transaction", Transaction.id),
        (Checkpoint, "checkpoint", Checkpoint.id),
        (Error, "error", Error.id),
        (AuditLog, "audit", AuditLog.id),
        (FlowVersion, "flow", FlowVersion.flow_id),
        (FlowRun, "job", FlowRun.job_id),
        (FlowSchedule, "schedule", FlowSchedule.id),
    ],
)
def test_postgresql_rejects_cross_tenant_execution_links(
    db_engine, execution_rows, model, key, column
):
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        connection.execute(
            update(model)
            .where(column == execution_rows[key])
            .values(tenant_id=execution_rows["foreign_tenant"])
        )


def test_audit_cannot_bypass_ownership_by_omitting_nullable_parent(db_engine, execution_rows):
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        connection.execute(
            insert(AuditLog).values(
                tenant_id=execution_rows["foreign_tenant"],
                transaction_id=execution_rows["transaction"],
                job_id=None,
                action="TEST_INVALID_LINK",
            )
        )


def test_customer_history_contains_only_its_checkpoints_errors_and_audit(customers, execution_rows):
    first, second = customers
    detail = first.management.transaction_detail(first.execution, execution_rows["transaction"])
    assert detail["checkpoints"][0]["checkpoint_value"] == {"index": 1}
    assert detail["errors"][0]["error_type"] == "TEST_SAFE_FAILURE"
    assert detail["audit"]
    with pytest.raises(NotFoundError):
        second.management.transaction_detail(first.execution, execution_rows["transaction"])


def test_system_creation_replays_its_explicit_tenant_identity(customers, session_factory):
    customer, _ = customers
    key = f"same-system-key-{uuid4()}"
    customer_job = customer.execution.create_job(key, "test")
    trusted = ExecutionService(session_factory, system=True)
    legacy_job = trusted.create_job(key, "test")
    assert legacy_job.tenant_id == LEGACY_TENANT_ID
    assert trusted.create_job(key, "test").id == legacy_job.id
    assert legacy_job.id != customer_job.id


@pytest.mark.parametrize("reason", ["expired", "suspended", "unverified", "unapproved"])
def test_revocation_blocks_queued_claim_retry_checkpoint_and_external_step(
    customers, session_factory, reason
):
    customer, _ = customers
    flow = create_flow(customer)
    running = create_run(customer, flow)
    pending = create_run(customer, flow)
    trusted = ExecutionService(session_factory, system=True)
    claim = trusted.claim(running["job"]["id"], "system-worker")
    assert claim is not None
    trusted.checkpoint(claim, "before-revoke", {"safe": True})
    revoke(session_factory, customer, reason)
    assert trusted.claim(pending["job"]["id"], "later-worker") is None
    with pytest.raises(EntitlementError):
        trusted.heartbeat(claim)
    with pytest.raises(EntitlementError):
        trusted.checkpoint(claim, "after-revoke", {"unsafe": True})
    with pytest.raises(EntitlementError):
        create_run(customer, flow)
    assert (
        ManagementService(session_factory, system=True)
        .running_workflow_job_ids(("visual_flow",))
        .count(UUID(pending["job"]["id"]))
        == 0
    )
    # Recovery records a known result even if no new external step is allowed.
    trusted.succeed(claim, {"already_applied": True})
    assert trusted.finalize_job(running["job"]["id"]).success_count == 1
    customer.execution.cancel_job(pending["job"]["id"])


def test_existing_worker_registry_executes_with_the_actual_transaction_tenant(
    customers, session_factory
):
    customer, _ = customers
    flow = create_flow(customer)
    run = create_run(customer, flow)
    execution = ExecutionService(session_factory, system=True)
    claim = execution.claim(run["job"]["id"], "registered-worker")
    checkpoints = {}
    context = SimpleNamespace(
        claim=claim,
        transaction=execution.get_transaction(claim.transaction_id),
        check_active=lambda: execution.heartbeat(claim),
        checkpoint=lambda key, value: checkpoints.update({key: value}),
        get_checkpoint=lambda key: checkpoints.get(key),
    )
    assert build_registry(session_factory).get("visual_flow").execute(context) == {"finished": True}


def test_scheduler_continues_after_a_revoked_customer_and_pauses_its_due_slot(
    customers, session_factory
):
    first, second = customers
    first_schedule = create_schedule(first, create_flow(first), session_factory)
    second_schedule = create_schedule(second, create_flow(second), session_factory)
    set_due(session_factory, first, first_schedule, seconds_ago=10)
    set_due(session_factory, second, second_schedule)
    revoke(session_factory, first, "suspended")
    dispatched = ScheduleService(session_factory, system=True).dispatch_due()
    assert [row["schedule_id"] for row in dispatched] == [second_schedule["id"]]
    rejected = first.schedules.get_schedule(first_schedule["id"])
    assert rejected["state"] == "paused"
    assert rejected["run_count"] == 0
    assert rejected["last_job_id"] is None
    assert second.schedules.get_schedule(second_schedule["id"])["run_count"] == 1


@pytest.mark.parametrize("invalid_first", [True, False])
@pytest.mark.parametrize("kind", ["once", "interval"])
def test_unsupported_saved_node_version_pauses_only_its_schedule(
    customers, session_factory, invalid_first, kind
):
    rejected_customer, valid_customer = customers
    rejected_flow = create_flow(rejected_customer)
    rejected_schedule = create_schedule(rejected_customer, rejected_flow, session_factory)
    valid_schedule = create_schedule(valid_customer, create_flow(valid_customer), session_factory)
    set_due(
        session_factory,
        rejected_customer,
        rejected_schedule,
        seconds_ago=190 if invalid_first else 1,
    )
    set_due(
        session_factory,
        valid_customer,
        valid_schedule,
        seconds_ago=1 if invalid_first else 190,
    )
    unsupported_graph = graph()
    unsupported_graph["nodes"][0]["type_version"] = 2
    unsupported_graph["nodes"][0]["label"] = "FAKE_PRIVATE_NODE_LABEL"
    # Simulate a newer producer adding an immutable revision; existing
    # revisions are protected by PostgreSQL and must never be rewritten.
    with session_factory.begin() as session:
        session.add(
            FlowVersion(
                tenant_id=rejected_customer.tenant_id,
                flow_id=UUID(rejected_flow["id"]),
                version=2,
                name="Newer producer definition",
                description="",
                graph=unsupported_graph,
                node_count=2,
            )
        )
        session.flush()
        session.execute(
            update(FlowSchedule)
            .where(
                FlowSchedule.tenant_id == rejected_customer.tenant_id,
                FlowSchedule.id == UUID(rejected_schedule["id"]),
            )
            .values(version=2, kind=kind, interval_seconds=60 if kind == "interval" else None)
        )
    before = rejected_customer.schedules.get_schedule(rejected_schedule["id"])
    scheduler = ScheduleService(session_factory, system=True)
    dispatched = scheduler.dispatch_due()
    assert [row["schedule_id"] for row in dispatched] == [valid_schedule["id"]]
    rejected = rejected_customer.schedules.get_schedule(rejected_schedule["id"])
    assert rejected["state"] == "paused" and rejected["revision"] == 2
    assert rejected["next_run_at"] == before["next_run_at"]
    assert rejected["run_count"] == rejected["missed_runs"] == 0
    assert rejected["last_job_id"] is rejected["last_run_at"] is None
    assert rejected_customer.management.jobs()["total"] == 0
    assert rejected_customer.flows.list_runs(rejected_flow["id"])["total"] == 0
    assert valid_customer.schedules.get_schedule(valid_schedule["id"])["run_count"] == 1
    assert valid_customer.management.jobs()["total"] == 1
    assert scheduler.dispatch_due() == []
    with session_factory() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.tenant_id == rejected_customer.tenant_id)
            .order_by(AuditLog.created_at)
        ).all()
        assert [row.action for row in rows] == ["SCHEDULE_CREATED", "SCHEDULE_VALIDATION_PAUSED"]
        assert rows[-1].new_value == {"schedule_id": rejected_schedule["id"], "revision": 2}
        assert rows[-1].old_value is None
        assert rows[-1].job_id is rows[-1].transaction_id is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.tenant_id == rejected_customer.tenant_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(FlowReservation)
                .where(FlowReservation.tenant_id == rejected_customer.tenant_id)
            )
            == 1
        )
        assert session.get(FlowVersion, (UUID(rejected_flow["id"]), 2)).graph == unsupported_graph


def test_expiry_during_schedule_publication_does_not_rollback_other_tenants(
    customers, session_factory, monkeypatch
):
    first, second = customers
    first_schedule = create_schedule(first, create_flow(first), session_factory)
    second_schedule = create_schedule(second, create_flow(second), session_factory)
    set_due(session_factory, first, first_schedule, seconds_ago=10)
    set_due(session_factory, second, second_schedule)
    from app.services import flow_runs

    actual_access = flow_runs.assert_access

    def expired_at_publish(session, tenant_id):
        if tenant_id == second.tenant_id:
            raise EntitlementError("TRIAL_EXPIRED")
        return actual_access(session, tenant_id)

    monkeypatch.setattr(flow_runs, "assert_access", expired_at_publish)
    dispatched = ScheduleService(session_factory, system=True).dispatch_due()
    assert [row["schedule_id"] for row in dispatched] == [first_schedule["id"]]
    assert first.schedules.get_schedule(first_schedule["id"])["run_count"] == 1
    rejected = second.schedules.get_schedule(second_schedule["id"])
    assert rejected["run_count"] == 0
    assert rejected["state"] == "paused"
    assert rejected["last_job_id"] is None
    assert second.management.jobs()["total"] == 0


def test_server_flows_reserve_lifetime_quota_and_roll_back_a_fourth_creation(
    customers, session_factory
):
    customer, _ = customers
    for _ in range(3):
        create_flow(customer)
    with pytest.raises(EntitlementError, match="FLOW_LIMIT_REACHED"):
        create_flow(customer)
    assert customer.flows.list_flows()["total"] == 3
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(FlowReservation)
                .where(FlowReservation.tenant_id == customer.tenant_id)
            )
            == 3
        )


def test_scoped_worker_cannot_consume_a_foreign_claim(customers, session_factory):
    first, second = customers
    run = create_run(first, create_flow(first))
    claim = ExecutionService(session_factory, system=True).claim(run["job"]["id"], "trusted-worker")
    with pytest.raises(NotFoundError):
        second.execution.heartbeat(claim)
    with pytest.raises(NotFoundError):
        second.execution.succeed(claim, {"injected": True})
    assert (
        first.execution.get_transaction(claim.transaction_id).status == TransactionStatus.PROCESSING
    )
