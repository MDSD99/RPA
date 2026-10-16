"""HTTP schedule lifecycle and automatic dispatch against isolated PostgreSQL."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.web.api import create_app
from app.workflows.registry import WorkflowRegistry

pytestmark = pytest.mark.postgres
ORIGIN = "http://127.0.0.1"
WRITE_HEADERS = {"X-RPA-Request": "1", "Origin": ORIGIN}


@pytest.fixture
def client(session_factory):
    with TestClient(
        create_app(session_factory, start_workers=False), base_url=ORIGIN, headers=WRITE_HEADERS
    ) as result:
        yield result


def create_flow(client):
    response = client.post(
        "/api/flows",
        json={
            "name": f"Scheduled flow {uuid4().hex}",
            "graph": {
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
        },
    )
    assert response.status_code == 201
    return response.json()["flow"]


def schedule_body(flow, **overrides):
    return {
        "request_id": str(uuid4()),
        "name": "Günlük kontrol",
        "flow_id": flow["id"],
        "version": flow["version"],
        "kind": "interval",
        "next_run_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "interval_seconds": 86_400,
        "inputs": {"sample": "test-data"},
        "max_attempts": 2,
        **overrides,
    }


def test_schedule_api_create_replay_edit_conflict_pause_resume(client):
    flow = create_flow(client)
    body = schedule_body(flow)
    response = client.post("/api/schedules", json=body)
    assert response.status_code == 201
    saved = response.json()["schedule"]
    assert saved["revision"] == 1
    assert saved["state"] == "enabled"
    assert saved["flow_name"] == flow["name"]
    assert client.post("/api/schedules", json=body).json()["schedule"]["id"] == saved["id"]
    assert client.post("/api/schedules", json={**body, "name": "Different"}).status_code == 409
    page = client.get("/api/schedules", params={"flow_id": flow["id"], "limit": 1}).json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == saved["id"]
    assert page["scheduler"]["enabled"] is False
    assert page["scheduler"]["state"] == "stopped"
    path = f"/api/schedules/{saved['id']}"
    edited = {key: value for key, value in body.items() if key != "request_id"}
    edited.update(name="Düzenlenen zamanlama", expected_revision=1)
    response = client.put(path, json=edited)
    assert response.status_code == 200
    assert response.json()["schedule"]["revision"] == 2
    assert client.put(path, json=edited).status_code == 409
    paused = client.post(path + "/pause", json={"expected_revision": 2})
    assert paused.status_code == 200
    assert paused.json()["schedule"]["state"] == "paused"
    assert client.post(path + "/resume", json={"expected_revision": 2}).status_code == 409
    revision = paused.json()["schedule"]["revision"]
    resumed = client.post(path + "/resume", json={"expected_revision": revision})
    assert resumed.status_code == 200
    assert resumed.json()["schedule"]["state"] == "enabled"
    fetched = client.get(path)
    assert fetched.status_code == 200
    assert fetched.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "overrides",
    [
        {"next_run_at": "2027-01-01T10:30:00"},
        {"next_run_at": "2027-02-30T10:30:00Z"},
        {"next_run_at": 9999999999},
        {"next_run_at": "2020-01-01T10:30:00Z"},
        {"kind": "cron"},
        {"interval_seconds": 59},
        {"interval_seconds": True},
        {"interval_seconds": 2_678_401},
        {"kind": "once", "interval_seconds": 60},
        {"kind": "interval", "interval_seconds": None},
        {"enabled": "true"},
        {"version": True},
        {"max_attempts": 101},
        {"inputs": {"password": "FAKE_SENSITIVE_TEST_VALUE"}},
        {"inputs": {"text": "a" * 13_000}},
        {"request_id": "not-a-uuid"},
    ],
)
def test_schedule_api_rejects_invalid_configuration_without_reflection(client, overrides):
    flow = create_flow(client)
    response = client.post("/api/schedules", json=schedule_body(flow, **overrides))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "FAKE_SENSITIVE_TEST_VALUE" not in response.text
    assert client.get("/api/schedules", params={"flow_id": flow["id"]}).json()["total"] == 0


def test_schedule_api_guards_missing_revision_origin_and_unknown_flow(client):
    flow = create_flow(client)
    body = schedule_body(flow)
    response = client.post("/api/schedules", json=body)
    path = f"/api/schedules/{response.json()['schedule']['id']}"
    assert client.post(path + "/pause", json={}).status_code == 422
    assert client.post(path + "/pause", json={"expected_revision": True}).status_code == 422
    assert (
        client.post(
            path + "/pause",
            json={"expected_revision": 1},
            headers={"Origin": "https://outside.example"},
        ).status_code
        == 403
    )
    assert client.post("/api/schedules", json=schedule_body(flow, version=999)).status_code == 404
    assert client.get(f"/api/schedules/{uuid4()}").status_code == 404
    assert client.get("/api/schedules?limit=0").status_code == 422
    assert client.get("/api/schedules?offset=-1").status_code == 422


def test_app_scheduler_dispatches_saved_flow_to_real_workers(session_factory):
    app = create_app(session_factory, start_workers=True)
    with TestClient(app, base_url=ORIGIN, headers=WRITE_HEADERS) as client:
        flow = create_flow(client)
        body = schedule_body(
            flow,
            kind="once",
            interval_seconds=None,
            next_run_at=(datetime.now(UTC) + timedelta(seconds=0.5)).isoformat(),
        )
        response = client.post("/api/schedules", json=body)
        assert response.status_code == 201
        schedule_id = response.json()["schedule"]["id"]
        deadline = time.monotonic() + 12
        while True:
            schedule = client.get(f"/api/schedules/{schedule_id}").json()["schedule"]
            if schedule["last_job_id"]:
                job = client.get(f"/api/jobs/{schedule['last_job_id']}").json()["job"]
                if job["status"] == "COMPLETED":
                    break
            assert time.monotonic() < deadline, "Scheduled flow was not executed"
            time.sleep(0.05)
        assert schedule["state"] == "completed"
        assert schedule["run_count"] == 1
        assert job["success_count"] == 1
        state = client.get("/api/schedules").json()["scheduler"]
        assert state["enabled"] is True
        assert state["total_dispatched"] >= 1
        records = client.get(f"/api/jobs/{job['id']}/transactions").json()["items"]
        detail = client.get(f"/api/transactions/{records[0]['id']}").json()
        assert detail["transaction"]["result_data"] == {"scheduled": True}
    assert app.state.scheduler.snapshot()["state"] == "stopped"


def test_custom_registry_without_visual_flow_does_not_start_scheduler(session_factory):
    app = create_app(session_factory, start_workers=True, workflow_registry=WorkflowRegistry())
    with TestClient(app, base_url=ORIGIN, headers=WRITE_HEADERS) as client:
        state = client.get("/api/schedules").json()["scheduler"]
        assert state["enabled"] is False
        assert state["state"] == "stopped"
