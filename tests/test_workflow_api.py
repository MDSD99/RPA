"""Registered workflows are discoverable and executable through the real API."""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.web.api import create_app
from app.workflows.registry import WorkflowDefinition, WorkflowRegistry

pytestmark = pytest.mark.postgres
LOCAL_ORIGIN = "http://127.0.0.1"
WRITE_HEADERS = {"X-RPA-Request": "1", "Origin": LOCAL_ORIGIN}


def test_workflow_catalog_only_exposes_registered_metadata(session_factory):
    definition = WorkflowDefinition(
        name=f"catalog_{uuid4().hex}",
        title="Kayıt doğrulama",
        description="Yalnızca test verisiyle çalışan kayıtlı akış.",
        execute=lambda context: {"checked": True},
    )
    registry = WorkflowRegistry([definition])
    app = create_app(session_factory, start_workers=False, workflow_registry=registry)
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        response = client.get("/api/workflows")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        catalog = response.json()
        assert catalog["workers_enabled"] is False
        assert len(catalog["node_catalog"]) == 20
        assert {entry["type_version"] for entry in catalog["node_catalog"]} == {1}
        assert catalog["items"][0]["kind"] == "demo"
        assert catalog["items"][1] == {
            "name": definition.name,
            "title": definition.title,
            "description": definition.description,
            "kind": "automation",
            "registered": True,
            "workers_enabled": False,
        }
        workers = client.get("/api/workers").json()["items"]
        assert len(workers) == 4
        assert all(worker["state"] == "stopped" for worker in workers)
        assert sum(worker["workflow_names"] == [definition.name] for worker in workers) == 2


def test_api_job_uses_registered_handler_and_persists_result_and_checkpoint(session_factory):
    workflow_name = f"api_workflow_{uuid4().hex}"

    def execute(context):
        context.checkpoint("validation", {"stage": "checked"})
        return {"checked": True, "reference_present": bool(context.transaction.external_reference)}

    registry = WorkflowRegistry(
        [WorkflowDefinition(workflow_name, "Test akışı", "Yerel test doğrulaması.", execute)]
    )
    app = create_app(session_factory, start_workers=True, workflow_registry=registry)
    with TestClient(app, base_url=LOCAL_ORIGIN, headers=WRITE_HEADERS) as client:
        job_response = client.post(
            "/api/jobs",
            json={"job_id": f"registered-{uuid4().hex}", "workflow_name": workflow_name},
        )
        assert job_response.status_code == 201
        job_id = job_response.json()["job"]["id"]
        transaction_response = client.post(
            f"/api/jobs/{job_id}/transactions",
            json={
                "record_id": "registered-test-record",
                "idempotency_key": f"registered-{uuid4().hex}",
                "external_reference": "test-reference",
            },
        )
        assert transaction_response.status_code == 201
        transaction_id = transaction_response.json()["transaction"]["id"]
        assert client.post(f"/api/jobs/{job_id}/start", json={}).status_code == 200
        deadline = time.monotonic() + 10
        while True:
            job = client.get(f"/api/jobs/{job_id}").json()["job"]
            if job["status"] == "COMPLETED":
                break
            assert time.monotonic() < deadline, "Registered workflow did not complete."
            time.sleep(0.05)
        assert (job["total_records"], job["success_count"], job["failed_count"]) == (1, 1, 0)
        detail = client.get(f"/api/transactions/{transaction_id}").json()
        assert detail["transaction"]["result_data"] == {"checked": True, "reference_present": True}
        assert detail["transaction"]["attempt_count"] == 1
        assert detail["checkpoints"][0]["checkpoint_value"] == {"stage": "checked"}
        assert client.get("/api/workflows").json()["workers_enabled"] is True
    assert all(
        worker["state"] == "stopped"
        for pool in app.state.worker_pools
        for worker in pool.snapshot()
    )
