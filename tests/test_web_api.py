"""Operator API behavior against the explicitly isolated PostgreSQL database."""

import os
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, update

from app.core.exceptions import DatabaseOperationError, OwnershipLostError
from app.database.models import Transaction
from app.web.api import create_app

pytestmark = pytest.mark.postgres
LOCAL_ORIGIN = "http://127.0.0.1"
WRITE_HEADERS = {"X-RPA-Request": "1", "Origin": LOCAL_ORIGIN}


@pytest.fixture
def client(session_factory):
    app = create_app(session_factory=session_factory, start_workers=False)
    with TestClient(app, base_url=LOCAL_ORIGIN, headers=WRITE_HEADERS) as test_client:
        yield test_client


def _new_job(client):
    response = client.post(
        "/api/jobs",
        json={"job_id": f"web-{uuid4().hex}", "workflow_name": "operator-test"},
    )
    assert response.status_code in {200, 201}
    return response.json()["job"]


def _add_transaction(client, job_id, **overrides):
    body = {
        "record_id": f"record-{uuid4().hex}",
        "idempotency_key": f"operation-{uuid4().hex}",
        "max_attempts": 3,
    } | overrides
    response = client.post(f"/api/jobs/{job_id}/transactions", json=body)
    assert response.status_code in {200, 201}
    return response.json()["transaction"], body


def _expire(db_engine, transaction_id):
    with db_engine.begin() as connection:
        connection.execute(
            update(Transaction)
            .where(Transaction.id == transaction_id)
            .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )


def test_health_and_overview_are_readable_without_connection_secrets(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "database": "connected",
        "workers_enabled": False,
    }
    overview = client.get("/api/overview")
    assert overview.status_code == 200
    counts = overview.json()["counts"]
    assert all(isinstance(value, int) and value >= 0 for value in counts.values())
    assert {"jobs", "transactions", "processing", "reconciliation_required"} <= counts.keys()
    secrets = tuple(os.environ.get(key) for key in ("DB_PASSWORD", "DB_USER", "DB_NAME"))
    for response in (health, overview):
        assert not any(secret and secret in response.text for secret in secrets), (
            "Public API response exposed database connection metadata."
        )


def test_operator_can_create_reserve_start_and_finalize_job(client, service):
    job = _new_job(client)
    assert job["status"] == "PENDING"
    transaction, body = _add_transaction(client, job["id"])
    repeated = client.post(f"/api/jobs/{job['id']}/transactions", json=body)
    assert repeated.status_code in {200, 201}
    assert repeated.json()["transaction"]["id"] == transaction["id"]
    assert client.get(f"/api/jobs/{job['id']}").json()["job"]["total_records"] == 1

    started = client.post(f"/api/jobs/{job['id']}/start", json={})
    assert started.status_code == 200
    assert started.json()["job"]["status"] == "RUNNING"
    early_finalize = client.post(f"/api/jobs/{job['id']}/finalize", json={})
    assert early_finalize.status_code == 409
    assert early_finalize.json()["error"]["code"] == "INVALID_TRANSITION"
    claim = service.claim(job["id"], "api-test-worker")
    service.succeed(claim, {"verified": True})
    completed = client.post(f"/api/jobs/{job['id']}/finalize", json={})
    assert completed.status_code == 200
    assert completed.json()["job"]["status"] == "COMPLETED"
    assert completed.json()["job"]["success_count"] == 1
    detail = client.get(f"/api/transactions/{transaction['id']}")
    assert detail.status_code == 200
    assert detail.json()["transaction"]["status"] == "SUCCESS"
    assert detail.json()["transaction"]["result_data"] == {"verified": True}
    assert any(item["action"] == "TRANSACTION_SUCCEEDED" for item in detail.json()["audit"])


def test_idempotency_conflict_does_not_increment_job_count(client):
    job = _new_job(client)
    _, body = _add_transaction(client, job["id"])
    conflict = client.post(
        f"/api/jobs/{job['id']}/transactions",
        json=body | {"record_id": "different-business-record"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get(f"/api/jobs/{job['id']}").json()["job"]["total_records"] == 1


def test_recovery_and_reconciliation_require_current_fencing_token(client, service, db_engine):
    job = _new_job(client)
    transaction, _ = _add_transaction(client, job["id"])
    assert client.post(f"/api/jobs/{job['id']}/start", json={}).status_code == 200
    old_claim = service.claim(job["id"], "abandoned-api-worker")
    _expire(db_engine, transaction["id"])
    recovered = client.post(f"/api/jobs/{job['id']}/recover", json={})
    assert recovered.status_code == 200
    assert recovered.json()["count"] == 1
    uncertain = recovered.json()["recovered"][0]
    assert uncertain["reconciliation_required"] is True
    assert uncertain["owner_id"] is None
    assert uncertain["fencing_token"] > old_claim.fencing_token
    assert service.claim(job["id"], "replacement-api-worker") is None

    path = f"/api/transactions/{transaction['id']}/reconcile"
    stale = client.post(
        path,
        json={"expected_fencing_token": old_claim.fencing_token, "outcome": "not_applied"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "OWNERSHIP_LOST"
    safe = client.post(
        path,
        json={"expected_fencing_token": uncertain["fencing_token"], "outcome": "not_applied"},
    )
    assert safe.status_code == 200
    assert safe.json()["transaction"]["reconciliation_required"] is False
    repeated = client.post(
        path,
        json={"expected_fencing_token": uncertain["fencing_token"], "outcome": "succeeded"},
    )
    assert repeated.status_code == 409
    with pytest.raises(OwnershipLostError):
        service.succeed(old_claim)
    assert service.get_job(job["id"]).success_count == 0


def test_cancel_revokes_processing_owner_and_skips_safe_pending(client, service):
    job = _new_job(client)
    active, _ = _add_transaction(client, job["id"])
    pending, _ = _add_transaction(client, job["id"])
    assert client.post(f"/api/jobs/{job['id']}/start", json={}).status_code == 200
    claim = service.claim(job["id"], "cancel-api-worker")
    assert str(claim.transaction_id) == active["id"]
    response = client.post(f"/api/jobs/{job['id']}/cancel", json={})
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "CANCELLED"
    assert response.json()["job"]["skipped_count"] == 1
    assert service.get_transaction(pending["id"]).status == "SKIPPED"
    assert service.get_transaction(active["id"]).reconciliation_required is True
    with pytest.raises(OwnershipLostError):
        service.succeed(claim)


def test_job_and_transaction_filters_and_pagination_use_persisted_rows(client):
    job = _new_job(client)
    transactions = [_add_transaction(client, job["id"])[0] for _ in range(3)]
    listing = client.get("/api/jobs", params={"q": job["job_id"], "limit": 1})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == job["id"]
    page = client.get(f"/api/jobs/{job['id']}/transactions", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2
    second = client.get(f"/api/jobs/{job['id']}/transactions", params={"limit": 2, "offset": 2})
    actual = {item["id"] for item in page.json()["items"] + second.json()["items"]}
    assert actual == {item["id"] for item in transactions}
    search = client.get(
        "/api/transactions", params={"q": transactions[0]["record_id"], "status": "PENDING"}
    )
    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["id"] == transactions[0]["id"]


@pytest.mark.parametrize(
    "path",
    ["/api/jobs?limit=0", "/api/jobs?limit=100000", "/api/transactions?offset=-1"],
)
def test_query_limits_are_validated(client, path):
    response = client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_validation_and_not_found_errors_do_not_reflect_request_secrets(client):
    marker = "fake_sensitive_token_do_not_echo"
    response = client.post(
        "/api/jobs",
        json={"job_id": marker, "workflow_name": {}, "password": marker},
    )
    assert response.status_code == 422
    assert marker not in response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    malformed = client.post(
        "/api/jobs", content='{"password":"' + marker, headers={"Content-Type": "application/json"}
    )
    assert malformed.status_code == 422
    assert marker not in malformed.text
    missing = client.get(f"/api/jobs/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_database_failure_response_does_not_expose_exception_details(client, monkeypatch):
    def failed_read(_self):
        raise DatabaseOperationError("fake_sensitive_driver_detail")

    monkeypatch.setattr("app.services.management_service.ManagementService.counts", failed_read)
    response = client.get("/api/overview")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert "fake_sensitive_driver_detail" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example", "X-RPA-Request": "1"},
        {"Origin": "null", "X-RPA-Request": "1"},
        {"Origin": LOCAL_ORIGIN, "X-RPA-Request": ""},
        {"Origin": LOCAL_ORIGIN, "X-RPA-Request": "1", "Host": "attacker.example"},
    ],
)
def test_mutations_reject_foreign_origins_hosts_or_missing_request_marker(client, headers):
    response = client.post(
        "/api/jobs",
        json={"job_id": f"rejected-{uuid4().hex}", "workflow_name": "test"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "REQUEST_REJECTED"


def test_non_json_and_oversized_mutations_are_rejected(client):
    unsupported = client.post("/api/jobs", content="{}", headers={"Content-Type": "text/plain"})
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    oversized = client.post(
        "/api/jobs",
        content='{"job_id":"' + "x" * (2 * 1024 * 1024) + '"}',
        headers={"Content-Type": "application/json"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_streaming_request_without_content_length_cannot_bypass_size_limit(client):
    chunks = iter([b'{"job_id":"' + b"x" * (70 * 1024), b"x" * (70 * 1024) + b'"}'])
    response = client.post(
        "/api/jobs", content=chunks, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize("max_attempts", [0, 101, True, 1.5])
def test_http_retry_budget_rejects_invalid_or_coerced_values(client, max_attempts):
    job = _new_job(client)
    response = client.post(
        f"/api/jobs/{job['id']}/transactions",
        json={
            "record_id": "invalid-budget-record",
            "idempotency_key": f"invalid-budget-{uuid4().hex}",
            "max_attempts": max_attempts,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get(f"/api/jobs/{job['id']}").json()["job"]["total_records"] == 0


def test_demo_request_id_preserves_idempotency_and_rejects_different_scenario(client):
    body = {"scenario": "success", "record_count": 2, "request_id": uuid4().hex}
    first = client.post("/api/demo-jobs", json=body)
    assert first.status_code in {200, 201}
    again = client.post("/api/demo-jobs", json=body)
    assert again.status_code in {200, 201}
    assert again.json()["job"]["id"] == first.json()["job"]["id"]
    assert again.json()["job"]["total_records"] == 2
    conflict = client.post("/api/demo-jobs", json=body | {"scenario": "recovery"})
    assert conflict.status_code == 409
    assert client.post(f"/api/jobs/{first.json()['job']['id']}/cancel", json={}).status_code == 200


def test_production_web_startup_is_rejected_before_database_engine_creation(
    monkeypatch, session_factory
):
    # The fixture confirms a test database before we override configuration to exercise refusal.
    monkeypatch.setenv("APP_ENV", "production")
    create_engine = Mock(side_effect=AssertionError("Production database must not be contacted."))
    monkeypatch.setattr("app.web.api.create_db_engine", create_engine)
    with pytest.raises((RuntimeError, ValueError)) as caught:
        with TestClient(create_app(start_workers=False), base_url=LOCAL_ORIGIN):
            pass
    assert "fake_sensitive" not in str(caught.value)
    create_engine.assert_not_called()
