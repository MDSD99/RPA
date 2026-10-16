"""Actual worker threads and crash recovery over the isolated PostgreSQL database."""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import OwnershipLostError
from app.services.execution_service import Claim
from app.web.api import create_app

pytestmark = pytest.mark.postgres
LOCAL_ORIGIN = "http://127.0.0.1"
WRITE_HEADERS = {"X-RPA-Request": "1", "Origin": LOCAL_ORIGIN}


@pytest.fixture
def worker_client(session_factory):
    app = create_app(session_factory=session_factory, start_workers=True)
    with TestClient(app, base_url=LOCAL_ORIGIN, headers=WRITE_HEADERS) as client:
        yield client


def _demo(client, scenario, count):
    response = client.post(
        "/api/demo-jobs",
        json={"scenario": scenario, "record_count": count, "request_id": uuid4().hex},
    )
    assert response.status_code in {200, 201}
    return response.json()["job"]


def _poll(client, path, predicate, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        if predicate(payload):
            return payload
        time.sleep(0.05)
    pytest.fail("Demo worker did not reach the expected persisted state before the deadline.")


@pytest.mark.parametrize(("scenario", "attempts"), [("success", 1), ("retry", 2)])
def test_two_worker_lifespan_completes_demo_and_enforces_attempt_counts(
    worker_client, scenario, attempts
):
    workers = worker_client.get("/api/workers")
    assert workers.status_code == 200
    assert len(workers.json()["items"]) == 4
    assert len({worker["worker_id"] for worker in workers.json()["items"]}) == 4
    assert (
        sum(
            worker["workflow_names"] == ["infrastructure_demo"]
            for worker in workers.json()["items"]
        )
        == 2
    )
    job = _demo(worker_client, scenario, 4)
    final = _poll(
        worker_client,
        f"/api/jobs/{job['id']}",
        lambda payload: payload["job"]["status"] == "COMPLETED",
    )["job"]
    assert (final["total_records"], final["success_count"], final["failed_count"]) == (4, 4, 0)
    transactions = worker_client.get(f"/api/jobs/{job['id']}/transactions").json()["items"]
    assert len(transactions) == 4
    assert len({transaction["id"] for transaction in transactions}) == 4
    assert all(transaction["status"] == "SUCCESS" for transaction in transactions)
    assert all(transaction["attempt_count"] == attempts for transaction in transactions)
    assert all(transaction["owner_id"] is None for transaction in transactions)
    completed_workers = [
        worker
        for worker in worker_client.get("/api/workers").json()["items"]
        if worker["workflow_names"] == ["infrastructure_demo"]
    ]
    assert all(worker["processed_count"] > 0 for worker in completed_workers)
    if scenario == "retry":
        for transaction in transactions:
            detail = worker_client.get(f"/api/transactions/{transaction['id']}").json()
            assert len(detail["errors"]) == 1


def test_abandoned_demo_lease_requires_reconciliation_and_revokes_old_worker(
    worker_client, service
):
    job = _demo(worker_client, "recovery", 1)
    processing = _poll(
        worker_client,
        f"/api/jobs/{job['id']}/transactions",
        lambda payload: (
            len(payload["items"]) == 1 and payload["items"][0]["status"] == "PROCESSING"
        ),
    )["items"][0]
    state = service.get_transaction(processing["id"])
    old_claim = Claim(
        job_id=state.job_id,
        transaction_id=state.id,
        owner_id=state.owner_id,
        fencing_token=state.fencing_token,
        lease_expires_at=state.lease_expires_at,
        attempt_count=state.attempt_count,
        idempotency_key=state.idempotency_key,
    )
    assert old_claim.owner_id is not None
    assert old_claim.attempt_count == 1
    uncertain = _poll(
        worker_client,
        f"/api/transactions/{processing['id']}",
        lambda payload: payload["transaction"]["reconciliation_required"] is True,
    )["transaction"]
    assert uncertain["status"] == "RETRY"
    assert uncertain["owner_id"] is None
    assert uncertain["attempt_count"] == 1
    assert uncertain["fencing_token"] > old_claim.fencing_token
    with pytest.raises(OwnershipLostError):
        service.succeed(old_claim)
    with pytest.raises(OwnershipLostError):
        service.checkpoint(old_claim, "stale", {"step": 99})

    # More scheduler ticks cannot turn uncertain external work into an automatic retry.
    time.sleep(0.6)
    held = worker_client.get(f"/api/transactions/{processing['id']}").json()["transaction"]
    assert held["attempt_count"] == 1
    assert held["reconciliation_required"] is True
    stale = worker_client.post(
        f"/api/transactions/{processing['id']}/reconcile",
        json={"expected_fencing_token": old_claim.fencing_token, "outcome": "not_applied"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OWNERSHIP_LOST"
    accepted = worker_client.post(
        f"/api/transactions/{processing['id']}/reconcile",
        json={"expected_fencing_token": held["fencing_token"], "outcome": "not_applied"},
    )
    assert accepted.status_code == 200
    final = _poll(
        worker_client,
        f"/api/jobs/{job['id']}",
        lambda payload: payload["job"]["status"] == "COMPLETED",
    )["job"]
    assert final["success_count"] == 1
    detail = worker_client.get(f"/api/transactions/{processing['id']}").json()
    assert detail["transaction"]["attempt_count"] == 2
    assert detail["transaction"]["reconciliation_required"] is False
    assert detail["transaction"]["fencing_token"] > held["fencing_token"]
    assert len(detail["checkpoints"]) == 1
    assert detail["checkpoints"][0]["checkpoint_value"] == {"step": "completed", "demo": True}
    assert detail["checkpoints"][0]["fencing_token"] > old_claim.fencing_token
    actions = {entry["action"] for entry in detail["audit"]}
    assert "TRANSACTION_RECOVERY_REQUIRED" in actions
    assert "TRANSACTION_RECONCILED_NOT_APPLIED" in actions
    with pytest.raises(OwnershipLostError):
        service.succeed(old_claim)


def test_demo_workers_do_not_invent_execution_for_unknown_business_workflow(worker_client, service):
    job = service.create_job(f"custom-{uuid4().hex}", "unknown-business-workflow")
    transaction = service.add_transaction(job.id, "custom-record", f"custom-op-{uuid4().hex}")
    service.start_job(job.id)
    demo = _demo(worker_client, "success", 2)
    _poll(
        worker_client,
        f"/api/jobs/{demo['id']}",
        lambda payload: payload["job"]["status"] == "COMPLETED",
    )
    untouched = service.get_transaction(transaction.id)
    assert untouched.status == "PENDING"
    assert untouched.attempt_count == 0
    assert untouched.owner_id is None
    assert service.cancel_job(job.id).status == "CANCELLED"
