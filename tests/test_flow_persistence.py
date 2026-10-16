"""Real PostgreSQL proves immutable flow versions and atomic run publication."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import IdempotencyConflictError, InvalidTransitionError, ValidationError
from app.database.models import FlowRun, FlowVersion, Job, Transaction
from app.database.repositories.flow_repository import FlowRepository
from app.services.flow_service import FlowService

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
def flows(session_factory):
    return FlowService(session_factory)


@pytest.fixture
def flow(flows):
    return flows.create_flow(name=f"Test akışı {uuid4().hex[:8]}", description="", graph=graph())


def test_versions_remain_immutable_and_old_run_keeps_snapshot(flows, flow, session_factory):
    inputs = {"customer": {"name": "Örnek"}}
    run = flows.start_run(flow["id"], request_id=uuid4(), version=1, inputs=inputs)
    inputs["customer"]["name"] = "Caller mutation"
    changed = copy.deepcopy(flow["graph"])
    changed["nodes"][1]["label"] = "Yeni bitiş"
    saved = flows.update_flow(
        flow["id"], name="Yeni ad", description="Yeni açıklama", graph=changed, expected_version=1
    )
    assert saved["version"] == 2
    assert flows.get_flow(flow["id"])["graph"] == changed
    spec = flows.execution_spec(run["job"]["id"])
    assert spec["version"] == 1
    assert spec["name"] == flow["name"]
    assert spec["graph"] == flow["graph"]
    assert spec["inputs"] == {"customer": {"name": "Örnek"}}
    with session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(FlowVersion)
            .where(FlowVersion.flow_id == UUID(flow["id"]))
        )
        assert count == 2


def test_concurrent_edits_detect_lost_update(flows, flow):
    barrier = Barrier(2)

    def save(index):
        barrier.wait()
        try:
            return flows.update_flow(
                flow["id"], name=f"Edit {index}", graph=graph(), expected_version=1
            )
        except InvalidTransitionError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("conflict") == 1
    assert flows.get_flow(flow["id"])["version"] == 2


def test_concurrent_run_requests_publish_exactly_one_complete_job(flows, flow, session_factory):
    request_id = uuid4()
    barrier = Barrier(8)

    def start(_index):
        barrier.wait()
        return flows.start_run(
            flow["id"], request_id=request_id, version=1, inputs={"batch": "test"}, max_attempts=2
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(start, range(8)))
    job_ids = {result["job"]["id"] for result in results}
    assert len(job_ids) == 1
    job_id = UUID(job_ids.pop())
    assert all(result["job"]["status"] == "RUNNING" for result in results)
    assert all(result["job"]["total_records"] == 1 for result in results)
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(FlowRun).where(FlowRun.request_id == request_id)
            )
            == 1
        )
        records = session.scalars(select(Transaction).where(Transaction.job_id == job_id)).all()
        assert len(records) == 1
        assert records[0].max_attempts == 2
        assert session.get(FlowRun, job_id).inputs == {"batch": "test"}


@pytest.mark.parametrize("mutation", ["inputs", "input_type", "version", "attempts", "flow"])
def test_run_request_id_rejects_changed_intent(flows, flow, mutation):
    request_id = uuid4()
    flows.start_run(flow["id"], request_id=request_id, version=1, inputs={"value": 1})
    values = {"request_id": request_id, "version": 1, "inputs": {"value": 1}, "max_attempts": 3}
    target_id = flow["id"]
    if mutation == "inputs":
        values["inputs"] = {"value": 2}
    elif mutation == "input_type":
        values["inputs"] = {"value": True}
    elif mutation == "attempts":
        values["max_attempts"] = 4
    elif mutation == "version":
        flows.update_flow(flow["id"], name=flow["name"], graph=graph(), expected_version=1)
        values["version"] = 2
    else:
        target_id = flows.create_flow(name="Other flow", graph=graph())["id"]
    with pytest.raises(IdempotencyConflictError):
        flows.start_run(target_id, **values)


def test_run_idempotency_survives_job_completion(flows, flow, service):
    request_id = uuid4()
    run = flows.start_run(flow["id"], request_id=request_id, version=1)
    claim = service.claim(run["job"]["id"], "flow-completion-test", lease_seconds=30)
    service.succeed(claim, result_data={"complete": True})
    service.finalize_job(claim.job_id)
    repeated = flows.start_run(flow["id"], request_id=request_id, version=1)
    assert repeated["job"]["id"] == run["job"]["id"]
    assert repeated["job"]["status"] == "COMPLETED"
    assert repeated["job"]["total_records"] == 1


def test_run_replay_survives_postgresql_numeric_normalization(flows, flow):
    request_id = uuid4()
    inputs = {"large_value": 1e20, "nested": {"active": True}}
    first = flows.start_run(flow["id"], request_id=request_id, version=1, inputs=inputs)
    repeated = flows.start_run(flow["id"], request_id=request_id, version=1, inputs=inputs)
    assert repeated["job"]["id"] == first["job"]["id"]
    with pytest.raises(IdempotencyConflictError):
        flows.start_run(
            flow["id"],
            request_id=request_id,
            version=1,
            inputs={"large_value": 1e20, "nested": {"active": 1}},
        )


def test_failed_run_assembly_rolls_back_job_record_and_snapshot(
    flows, flow, session_factory, monkeypatch
):
    request_id = uuid4()

    def fail_add_run(_self, **_values):
        raise ValidationError("Test assembly interruption")

    monkeypatch.setattr(FlowRepository, "add_run", fail_add_run)
    with pytest.raises(ValidationError):
        flows.start_run(flow["id"], request_id=request_id, version=1)
    with session_factory() as session:
        assert session.scalar(select(Job).where(Job.job_id == f"flow:{request_id}")) is None
        assert (
            session.scalar(
                select(Transaction).where(Transaction.idempotency_key == f"flow-run:{request_id}")
            )
            is None
        )
        assert session.scalar(select(FlowRun).where(FlowRun.request_id == request_id)) is None


@pytest.mark.parametrize("model", [FlowVersion, FlowRun])
def test_database_rejects_snapshot_mutations(flows, flow, session_factory, model):
    run = flows.start_run(flow["id"], request_id=uuid4(), version=1)
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        if model is FlowVersion:
            session.execute(
                update(FlowVersion)
                .where(FlowVersion.flow_id == UUID(flow["id"]))
                .values(graph=graph())
            )
        else:
            session.execute(
                update(FlowRun)
                .where(FlowRun.job_id == UUID(run["job"]["id"]))
                .values(inputs={"changed": True})
            )


@pytest.mark.parametrize(
    "inputs",
    [
        {"password": "fake-value"},
        {"nested": {"api_key": "fake"}},
        {"text": "x" * 13000},
        {"text": "x" * 17000},
    ],
)
def test_sensitive_or_oversized_inputs_are_rejected_before_job_creation(
    flows, flow, session_factory, inputs
):
    request_id = uuid4()
    with pytest.raises(ValidationError):
        flows.start_run(flow["id"], request_id=request_id, version=1, inputs=inputs)
    with session_factory() as session:
        assert session.scalar(select(Job).where(Job.job_id == f"flow:{request_id}")) is None


def test_invalid_graph_does_not_create_revision(flows, flow, session_factory):
    invalid = graph()
    invalid["edges"][0]["target"] = "missing"
    with pytest.raises(ValidationError):
        flows.update_flow(flow["id"], name="Invalid", graph=invalid, expected_version=1)
    assert flows.get_flow(flow["id"])["version"] == 1
    with session_factory() as session:
        assert session.get(FlowVersion, (UUID(flow["id"]), 2)) is None


def test_future_stored_node_version_rolls_back_new_run_publication(flows, session_factory):
    source = graph()
    source["nodes"][-1]["type_version"] = 2
    # A newer trusted producer could have stored a revision this runner cannot
    # execute. Use the persistence boundary to model that compatibility case.
    with session_factory.begin() as session:
        saved, _revision = FlowRepository(session).create("Future flow", "", source)
        flow_id = saved.id
    request_id = uuid4()
    with pytest.raises(ValidationError, match="type version"):
        flows.start_run(flow_id, request_id=request_id, version=1)
    with session_factory() as session:
        assert session.scalar(select(Job).where(Job.job_id == f"flow:{request_id}")) is None
        assert session.scalar(select(FlowRun).where(FlowRun.request_id == request_id)) is None
        assert (
            session.scalar(
                select(Transaction).where(Transaction.idempotency_key == f"flow-run:{request_id}")
            )
            is None
        )
