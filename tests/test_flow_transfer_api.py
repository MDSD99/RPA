"""Customer transfer keeps tenant, authorization, quota and execution boundaries."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_customer_api import HEADERS, registered_customer
from test_customer_api import customer_portal as customer_portal
from test_flow_transfer import transfer_document

from app.database.models import Flow, FlowRun, FlowSchedule, FlowVersion, Job
from app.entitlements.models import Entitlement

pytestmark = pytest.mark.postgres


def tenant_counts(session_factory, tenant_id):
    with session_factory() as session:
        return tuple(
            session.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
            )
            for model in (Flow, FlowVersion, FlowRun, Job, FlowSchedule)
        )


def test_export_current_revision_and_import_new_identity_without_secrets_or_execution(
    customer_portal, session_factory
):
    _admin, [(alice, owner), (bob, recipient)] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    assert bob.post("/api/trial/start", json={}).status_code == 200
    definition = transfer_document()["flow"]
    created = alice.post("/api/flows", json=definition).json()["flow"]
    edited = alice.put(
        f"/api/flows/{created['id']}",
        json={**definition, "name": "Güncel sürüm", "expected_version": 1},
    ).json()["flow"]
    assert edited["version"] == 2
    marker = "a-private-vault-value-that-must-not-travel"
    assert (
        alice.put(
            "/api/secrets/login",
            json={"kind": "browser", "values": {"username": "user", "password": marker}},
        ).status_code
        == 200
    )
    exported = alice.get(f"/api/flows/{created['id']}/export")
    assert exported.status_code == 200 and marker not in exported.text
    archive = exported.json()
    assert archive["filename"] == f"MultiRPA-flow-{created['id']}-v2.multirpa.json"
    assert archive["document"]["flow"] == {**definition, "name": "Güncel sürüm"}
    assert set(archive["document"]["flow"]) == {"name", "description", "graph"}
    assert alice.get("/api/trial").json()["flows_used"] == 1
    assert bob.get(f"/api/flows/{created['id']}/export").status_code == 404

    before = tenant_counts(session_factory, UUID(recipient["tenant_id"]))
    imported = bob.post("/api/flows/import", json={"document": archive["document"]})
    assert imported.status_code == 201, imported.text
    copy = imported.json()["flow"]
    assert copy["id"] != created["id"] and copy["version"] == 1
    assert copy["graph"] == definition["graph"] and copy["name"] == "Güncel sürüm"
    assert tenant_counts(session_factory, UUID(recipient["tenant_id"])) == (
        before[0] + 1,
        before[1] + 1,
        *before[2:],
    )
    assert bob.get("/api/trial").json()["flows_used"] == 1
    assert bob.get("/api/secrets").json()["items"] == []
    assert alice.get(f"/api/flows/{copy['id']}/export").status_code == 404
    assert alice.get(f"/api/flows/{created['id']}").json()["flow"] == edited
    assert tenant_counts(session_factory, UUID(owner["tenant_id"])) == (1, 2, 0, 0, 0)


def test_v2_api_roundtrip_keeps_node_versions_separate_from_saved_revision(customer_portal):
    _admin, [(alice, _), _] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    definition = transfer_document()["flow"]
    definition["graph"]["nodes"][2]["type_version"] = 1
    created = alice.post("/api/flows", json=definition)
    assert created.status_code == 201
    flow = created.json()["flow"]
    for revision in (1, 2):
        response = alice.put(
            f"/api/flows/{flow['id']}", json={**definition, "expected_version": revision}
        )
        assert response.status_code == 200
    archive = alice.get(f"/api/flows/{flow['id']}/export").json()
    assert archive["filename"].endswith("-v3.multirpa.json")
    assert archive["document"]["schema_version"] == 2
    assert archive["document"]["flow"] == definition
    imported = alice.post("/api/flows/import", json={"document": archive["document"]})
    assert imported.status_code == 201
    copy = imported.json()["flow"]
    assert copy["version"] == 1 and copy["id"] != flow["id"]
    assert copy["graph"] == definition["graph"]
    assert copy["graph"]["nodes"][2]["type_version"] == 1
    assert "type_version" not in copy["graph"]["nodes"][0]


@pytest.mark.parametrize("version", [True, 1.0, "1", 2, None])
def test_bad_node_version_create_import_and_edit_do_not_reserve_quota_or_publish_jobs(
    customer_portal, session_factory, version
):
    _admin, [(alice, user), _] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    document = transfer_document()
    original = alice.post("/api/flows", json=document["flow"]).json()["flow"]
    before = tenant_counts(session_factory, UUID(user["tenant_id"]))
    document["schema_version"] = 2
    document["flow"]["graph"]["nodes"][-1]["type_version"] = version
    assert alice.post("/api/flows", json=document["flow"]).status_code == 422
    assert alice.post("/api/flows/import", json={"document": document}).status_code == 422
    assert (
        alice.put(
            f"/api/flows/{original['id']}",
            json={**document["flow"], "expected_version": 1},
        ).status_code
        == 422
    )
    assert tenant_counts(session_factory, UUID(user["tenant_id"])) == before == (1, 1, 0, 0, 0)
    assert alice.get("/api/trial").json()["flows_used"] == 1
    assert alice.get(f"/api/flows/{original['id']}").json()["flow"] == original


def test_customer_node_catalog_describes_contracts_without_claiming_windows_readiness(
    customer_portal,
):
    _admin, [(alice, _), _] = customer_portal
    metadata = alice.get("/api/workflows").json()["node_catalog"]
    windows = next(entry for entry in metadata if entry["type"] == "windows_click")
    assert windows == {
        "type": "windows_click",
        "type_version": 1,
        "output_ports": ["next"],
        "required_capability": "windows_uia",
    }
    assert len(metadata) == 20


def test_import_requires_csrf_and_verified_approval(customer_portal, session_factory):
    _admin, [(alice, _), _] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    body = {"document": transfer_document()}
    flow = alice.post("/api/flows/import", json=body).json()["flow"]
    csrf = alice.headers.pop("X-CSRF-Token")
    assert alice.post("/api/flows/import", json=body).status_code == 403
    # Export is read-only and requires no mutation token.
    assert alice.get(f"/api/flows/{flow['id']}/export").status_code == 200
    alice.headers["X-CSRF-Token"] = csrf
    with TestClient(alice.app, headers=HEADERS) as pending:
        assert pending.get(f"/api/flows/{flow['id']}/export").status_code == 401
        anonymous_write = pending.post("/api/flows/import", json=body)
        assert anonymous_write.status_code == 403
        assert anonymous_write.json()["error"]["code"] == "CSRF_REJECTED"
        registered_customer(pending, alice.app.state.portal.settings, session_factory)
        assert pending.get(f"/api/flows/{flow['id']}/export").status_code == 403
        assert pending.post("/api/flows/import", json=body).status_code == 403
    assert alice.get("/api/flows").json()["total"] == 1


def test_expired_customer_can_export_but_cannot_import_or_reset_the_old_flow(
    customer_portal, session_factory
):
    _admin, [(alice, user), _] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    flow = alice.post("/api/flows/import", json={"document": transfer_document()}).json()["flow"]
    tenant_id = UUID(user["tenant_id"])
    before = tenant_counts(session_factory, tenant_id)
    with session_factory.begin() as session:
        entitlement = session.get(Entitlement, tenant_id)
        now = session.scalar(select(func.clock_timestamp()))
        entitlement.trial_started_at = now - timedelta(days=40)
        entitlement.trial_ends_at = now - timedelta(seconds=1)
    exported = alice.get(f"/api/flows/{flow['id']}/export")
    assert exported.status_code == 200
    denied = alice.post("/api/flows/import", json={"document": exported.json()["document"]})
    assert denied.status_code == 403
    assert tenant_counts(session_factory, tenant_id) == before
    assert alice.get(f"/api/flows/{flow['id']}").json()["flow"] == flow
    assert alice.get("/api/trial").json()["flows_used"] == 1


def test_full_quota_import_rolls_back_both_new_flow_and_version(customer_portal, session_factory):
    _admin, [(alice, user), _] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    body = {"document": transfer_document()}
    created = [alice.post("/api/flows/import", json=body).json()["flow"] for _ in range(3)]
    assert len({flow["id"] for flow in created}) == 3
    tenant_id = UUID(user["tenant_id"])
    before = tenant_counts(session_factory, tenant_id)
    denied = alice.post("/api/flows/import", json=body)
    assert denied.status_code == 409
    assert tenant_counts(session_factory, tenant_id) == before == (3, 3, 0, 0, 0)
    assert alice.get("/api/trial").json()["flows_used"] == 3
    for original in created:
        assert alice.get(f"/api/flows/{original['id']}").json()["flow"] == original


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.update(tenant_id="forged"),
        lambda body: body.update(document=[]),
        lambda body: body["document"].update(schema_version=True),
        lambda body: body["document"]["flow"].update(id="forged"),
        lambda body: body["document"]["flow"]["graph"]["nodes"][-1]["config"].update(
            output={"nested": {"__proto__": {"polluted": True}}}
        ),
        lambda body: body["document"]["flow"].update(description="ç" * (128 * 1024 // 2)),
    ],
)
def test_invalid_import_does_not_create_any_record_or_reserve_quota(
    customer_portal, session_factory, change
):
    _admin, [(alice, user), _] = customer_portal
    assert alice.post("/api/trial/start", json={}).status_code == 200
    body = {"document": deepcopy(transfer_document())}
    change(body)
    rejected = alice.post("/api/flows/import", json=body)
    assert rejected.status_code in {413, 422}
    assert tenant_counts(session_factory, UUID(user["tenant_id"])) == (0, 0, 0, 0, 0)
    assert alice.get("/api/trial").json()["flows_used"] == 0
