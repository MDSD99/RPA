"""Management API exercises saved revisions, run replay, and reservation guards."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.web.api import create_app

pytestmark = pytest.mark.postgres
ORIGIN = "http://127.0.0.1"
WRITE_HEADERS = {"X-RPA-Request": "1", "Origin": ORIGIN}


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
def client(session_factory):
    with TestClient(
        create_app(session_factory, start_workers=False), base_url=ORIGIN, headers=WRITE_HEADERS
    ) as result:
        yield result


def create_flow(client):
    response = client.post("/api/flows", json={"name": f"API flow {uuid4().hex}", "graph": graph()})
    assert response.status_code == 201
    return response.json()["flow"]


def test_api_flow_create_list_edit_and_optimistic_conflict(client):
    flow = create_flow(client)
    assert flow["version"] == 1
    assert flow["node_count"] == 2
    items = client.get("/api/flows?limit=500").json()["items"]
    listed = next(item for item in items if item["id"] == flow["id"])
    assert "graph" not in listed
    assert listed["name"] == flow["name"]
    body = {
        "name": "Düzenlenmiş",
        "description": "Test açıklaması",
        "graph": graph(),
        "expected_version": 1,
    }
    saved = client.put(f"/api/flows/{flow['id']}", json=body)
    assert saved.status_code == 200
    assert saved.json()["flow"]["version"] == 2
    stale = client.put(f"/api/flows/{flow['id']}", json=body)
    assert stale.status_code == 409
    fetched = client.get(f"/api/flows/{flow['id']}")
    assert fetched.json()["flow"]["version"] == 2
    assert fetched.headers["cache-control"] == "no-store"


def test_api_run_replay_snapshots_and_reserved_job_guards(client):
    flow = create_flow(client)
    request = {
        "request_id": str(uuid4()),
        "version": 1,
        "inputs": {"customer": "Örnek"},
        "max_attempts": 2,
    }
    path = f"/api/flows/{flow['id']}/runs"
    response = client.post(path, json=request)
    assert response.status_code == 201
    job = response.json()["job"]
    assert (job["status"], job["workflow_name"], job["total_records"]) == (
        "RUNNING",
        "visual_flow",
        1,
    )
    assert client.post(path, json=request).json()["job"]["id"] == job["id"]
    changed = {**request, "inputs": {"customer": "Başka"}}
    assert client.post(path, json=changed).status_code == 409
    spec = client.get(f"/api/flows/runs/by-job/{job['id']}").json()
    assert spec["inputs"] == request["inputs"]
    assert spec["graph"] == graph()
    assert spec["flow_id"] == flow["id"]
    records = client.get(f"/api/jobs/{job['id']}/transactions").json()["items"]
    assert len(records) == 1
    assert records[0]["max_attempts"] == 2
    assert (
        client.post(
            f"/api/jobs/{job['id']}/transactions",
            json={"record_id": "unbound", "idempotency_key": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/jobs", json={"job_id": "unbound", "workflow_name": "visual_flow"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/jobs", json={"job_id": f"flow:{uuid4()}", "workflow_name": "other"}
        ).status_code
        == 422
    )


def test_api_rejects_unknown_revisions_and_invalid_run_inputs(client):
    flow = create_flow(client)
    path = f"/api/flows/{flow['id']}/runs"
    request = {"request_id": str(uuid4()), "version": 1}
    assert client.post(path, json={**request, "version": 99}).status_code == 404
    assert client.post(path, json={**request, "request_id": "bad"}).status_code == 422
    assert (
        client.post(path, json={**request, "inputs": {"password": "fake-value"}}).status_code == 422
    )
    assert client.post(path, json={**request, "max_attempts": True}).status_code == 422
    assert client.post(path, json={**request, "version": True}).status_code == 422
    assert client.get(f"/api/flows/{uuid4()}").status_code == 404


def test_flow_run_history_is_scoped_paginated_and_keeps_snapshot_names(client):
    flow, other = create_flow(client), create_flow(client)
    path = f"/api/flows/{flow['id']}/runs"
    first = client.post(path, json={"request_id": str(uuid4()), "version": 1}).json()["job"]
    client.post(f"/api/flows/{other['id']}/runs", json={"request_id": str(uuid4()), "version": 1})
    second = client.post(path, json={"request_id": str(uuid4()), "version": 1}).json()["job"]
    client.put(
        f"/api/flows/{flow['id']}",
        json={"name": "Renamed", "graph": graph(), "expected_version": 1},
    )
    recent = client.get(path, params={"limit": 1}).json()
    assert recent["total"] == 2
    assert recent["items"][0]["id"] == second["id"]
    assert recent["items"][0]["name"] == flow["name"]
    assert recent["items"][0]["version"] == 1
    older = client.get(path, params={"limit": 1, "offset": 1}).json()
    assert older["items"][0]["id"] == first["id"]
    assert client.get(f"/api/flows/{uuid4()}/runs").status_code == 404


def test_flow_writes_keep_same_origin_and_body_size_guards(client):
    flow = create_flow(client)
    path = f"/api/flows/{flow['id']}"
    body = {"name": "Guard test", "graph": graph(), "expected_version": 1}
    assert (
        client.put(path, json=body, headers={"Origin": "https://outside.example"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/flows", json={"name": "Too large", "graph": {"text": "x" * 140000}}
        ).status_code
        == 413
    )
