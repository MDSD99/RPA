"""Real customer HTTP journeys prove tenant ownership and shared license policy."""

import hashlib
import json
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pyotp
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.portal_config import PortalSettings
from app.entitlements.crypto import encode
from app.entitlements.models import Entitlement
from app.identity.models import Account, EmailOutbox
from app.web.portal import create_portal_app

pytestmark = pytest.mark.postgres
PASSWORD = "Customer integration passphrase 2026!"
HEADERS = {"X-RPA-Request": "1"}


def graph():
    return {
        "nodes": [
            {
                "id": "s",
                "type": "start",
                "label": "Başla",
                "position": {"x": 0, "y": 0},
                "config": {},
            },
            {
                "id": "e",
                "type": "end",
                "label": "Bitir",
                "position": {"x": 200, "y": 0},
                "config": {"output": {}},
            },
        ],
        "edges": [{"source": "s", "target": "e", "port": "next"}],
    }


def registered_customer(client, config, session_factory):
    """Follow the public signup/verification/login journey, leaving approval pending."""
    email = f"customer-{uuid4().hex}@example.com"
    assert (
        client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": "Müşteri"},
        ).status_code
        == 202
    )
    with session_factory() as session:
        message = session.scalar(select(EmailOutbox).join(Account).where(Account.email == email))
        token = json.loads(
            Fernet(config.secret_key.encode()).decrypt(message.payload_ciphertext.encode())
        )["token"]
    assert client.post("/api/auth/verification/confirm", json={"token": token}).status_code == 200
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    data = response.json()
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data["user"]


@pytest.fixture
def customer_portal(session_factory, tmp_path):
    config = PortalSettings(
        "http://testserver", Fernet.generate_key().decode(), "test", release_dir=str(tmp_path)
    )
    app = create_portal_app(session_factory, config)
    with ExitStack() as stack:
        admin = stack.enter_context(TestClient(app, headers=HEADERS))
        identity = app.state.portal.identity
        secret = pyotp.random_base32()
        email = f"customer-admin-{uuid4().hex}@example.com"
        identity.bootstrap_admin(email, PASSWORD, "Yönetici", secret)
        response = admin.post(
            "/api/admin/login",
            json={"email": email, "password": PASSWORD, "totp": pyotp.TOTP(secret).now()},
        )
        assert response.status_code == 200
        admin.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        clients = []
        for _ in range(2):
            client = stack.enter_context(TestClient(app, headers=HEADERS))
            user = registered_customer(client, config, session_factory)
            assert (
                admin.post(
                    f"/api/admin/accounts/{user['id']}/decision",
                    json={"decision": "approve", "reason": "Test başvurusu incelendi."},
                ).status_code
                == 200
            )
            clients.append((client, user))
        yield admin, clients


@pytest.fixture
def published_customer_release(customer_portal):
    _admin, [(client, _), _] = customer_portal
    portal = client.app.state.portal
    directory = Path(portal.settings.release_dir)
    filename = "MultiRPA-0.1.0-linux-x64.tar.gz"
    content = b"tiny explicit customer download fixture"
    (directory / filename).write_bytes(content)
    manifest = {
        "schema": 1,
        "channel": "development",
        "server_url": portal.settings.public_url,
        "server_public_key": portal.licenses.public_key,
        "items": [
            {
                "platform": "linux",
                "filename": filename,
                "version": "0.1.0",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory, manifest, content


def create_flow(client):
    response = client.post("/api/flows", json={"name": "Müşteri akışı", "graph": graph()})
    assert response.status_code == 201, response.text
    return response.json()["flow"]


def test_demo_explicit_start_shared_quota_and_private_workspace(customer_portal):
    _admin, [(alice, _), (bob, _)] = customer_portal
    assert alice.get("/api/trial").json()["state"] == "not_started"
    assert alice.post("/api/flows", json={"name": "Erken", "graph": graph()}).status_code == 403
    assert alice.post("/api/trial/start", json={}).status_code == 200
    first_start = alice.get("/api/trial").json()["started_at"]
    assert alice.post("/api/trial/start", json={}).json()["started_at"] == first_start
    flow = create_flow(alice)
    desktop_id = str(uuid4())
    assert alice.post("/api/trial/flows", json={"flow_id": desktop_id}).status_code == 200
    assert alice.post("/api/trial/flows", json={"flow_id": desktop_id}).json()["flows_used"] == 2
    create_flow(alice)
    assert alice.post("/api/flows", json={"name": "Dördüncü", "graph": graph()}).status_code == 409
    assert alice.get("/api/trial").json()["flows_used"] == 3
    assert bob.get("/api/flows").json()["total"] == 0
    assert bob.get(f"/api/flows/{flow['id']}").status_code == 404
    assert (
        bob.put(
            f"/api/flows/{flow['id']}",
            json={"name": "İhlal", "graph": graph(), "expected_version": 1},
        ).status_code
        == 404
    )
    workspace = alice.get("/workspace")
    assert workspace.status_code == 200 and 'data-workspace="customer"' in workspace.text
    assert "workspace-bootstrap.js" in workspace.text
    assert (
        alice.post("/api/jobs", json={"job_id": "bypass", "workflow_name": "anything"}).status_code
        == 405
    )
    assert alice.post("/api/demo-jobs", json={}).status_code == 404
    assert alice.get("/api/workers").status_code == 404


def test_expired_demo_keeps_history_and_blocks_new_effects(customer_portal, session_factory):
    _admin, [(alice, user), _] = customer_portal
    alice.post("/api/trial/start", json={})
    flow = create_flow(alice)
    run = alice.post(
        f"/api/flows/{flow['id']}/runs",
        json={"request_id": str(uuid4()), "version": 1, "inputs": {}, "max_attempts": 1},
    )
    assert run.status_code == 201
    with session_factory.begin() as session:
        row = session.get(Entitlement, UUID(user["tenant_id"]))
        now = session.scalar(select(func.clock_timestamp()))
        row.trial_started_at = now - timedelta(days=40)
        row.trial_ends_at = now - timedelta(seconds=1)
    assert alice.get("/api/trial").json()["state"] == "expired"
    assert alice.get(f"/api/flows/{flow['id']}").status_code == 200
    assert alice.get("/api/jobs").json()["total"] == 1
    assert (
        alice.post(
            f"/api/flows/{flow['id']}/runs",
            json={"request_id": str(uuid4()), "version": 1, "inputs": {}, "max_attempts": 1},
        ).status_code
        == 403
    )
    assert alice.post("/api/trial/start", json={}).json()["state"] == "expired"


def test_trial_and_license_writes_require_csrf_and_admin_audience(customer_portal):
    _admin, [(alice, user), _] = customer_portal
    saved = alice.headers.pop("X-CSRF-Token")
    assert alice.post("/api/trial/start", json={}).status_code == 403
    alice.headers["X-CSRF-Token"] = saved
    assert (
        alice.post(
            f"/api/admin/tenants/{user['tenant_id']}/license/grant",
            json={
                "expires_at": "2099-01-01T00:00:00Z",
                "flow_limit": 10,
                "reason": "Yetki ihlali.",
            },
        ).status_code
        == 403
    )
    assert alice.post("/api/trial/start", json={"tenant_id": str(uuid4())}).status_code == 422
    assert alice.get("/api/admin/accounts").status_code == 401


def test_device_bound_proof_replay_and_audited_reset(customer_portal):
    admin, [(alice, user), (bob, _)] = customer_portal
    alice.post("/api/trial/start", json={})
    bob.post("/api/trial/start", json={})
    key = Ed25519PrivateKey.generate()
    public = encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    challenge = alice.post(
        "/api/license/challenge", json={"public_key": public, "purpose": "activate"}
    )
    assert challenge.status_code == 200
    proof = {
        "challenge_id": challenge.json()["challenge_id"],
        "signature": encode(key.sign(challenge.json()["message"].encode())),
    }
    assert bob.post("/api/license/activate", json=proof).status_code == 409
    lease = alice.post("/api/license/activate", json=proof)
    assert lease.status_code == 200
    payload = json.loads(lease.json()["payload"])
    assert payload["tenant_id"] == user["tenant_id"] and 0 < payload["valid_for_ms"] <= 60000
    assert alice.post("/api/license/activate", json=proof).status_code == 409
    another = encode(
        Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    assert (
        alice.post(
            "/api/license/challenge", json={"public_key": another, "purpose": "activate"}
        ).status_code
        == 409
    )
    reset = admin.post(
        f"/api/admin/tenants/{user['tenant_id']}/device/reset",
        json={"reason": "Bilgisayar değişimi incelendi."},
    )
    assert reset.status_code == 200
    assert (
        alice.post(
            "/api/license/challenge", json={"public_key": another, "purpose": "activate"}
        ).json()["error"]["code"]
        == "DEVICE_RESET_PENDING"
    )
    assert alice.get("/api/trial").json()["state"] == "active"


def test_credential_api_never_reads_values_or_accepts_foreign_tenant(customer_portal):
    _admin, [(alice, _), (bob, _)] = customer_portal
    marker = "Bearer api-private-fixture-2026"
    body = {"kind": "http", "values": {"authorization": marker}}
    stored = alice.put("/api/secrets/private_api", json=body)
    assert stored.status_code == 200 and marker not in stored.text
    assert stored.json()["credential"]["version"] == 1
    metadata = alice.get("/api/secrets")
    assert metadata.status_code == 200 and marker not in metadata.text
    assert bob.get("/api/secrets").json()["items"] == []
    assert alice.get("/api/secrets/private_api").status_code == 405
    assert (
        bob.request("DELETE", "/api/secrets/private_api", json={"expected_version": 1}).status_code
        == 404
    )
    assert alice.put("/api/secrets/private_api", json=body).status_code == 409
    rotated = alice.put("/api/secrets/private_api", json={**body, "expected_version": 1})
    assert rotated.status_code == 200 and marker not in rotated.text
    assert rotated.json()["credential"]["version"] == 2
    assert (
        alice.request(
            "DELETE", "/api/secrets/private_api", json={"expected_version": 1}
        ).status_code
        == 409
    )
    assert (
        alice.request(
            "DELETE", "/api/secrets/private_api", json={"expected_version": 2}
        ).status_code
        == 200
    )
    assert alice.get("/api/secrets").json()["items"] == []


def test_customer_downloads_require_authenticated_approval(
    customer_portal, published_customer_release, session_factory
):
    admin, [(alice, _), _] = customer_portal
    directory, manifest, content = published_customer_release
    with TestClient(alice.app, headers=HEADERS) as pending:
        for path in ("/api/downloads", "/download/linux"):
            assert pending.get(path).status_code == 401

        user = registered_customer(pending, alice.app.state.portal.settings, session_factory)
        assert user["email_verified"] is True and user["approval"] == "pending"
        for path in ("/api/downloads", "/download/linux"):
            assert pending.get(path).status_code == 403

        assert (
            admin.post(
                f"/api/admin/accounts/{user['id']}/decision",
                json={"decision": "approve", "reason": "İndirme başvurusu incelendi."},
            ).status_code
            == 200
        )
        # Download access follows approval; downloading cannot silently start a trial.
        assert pending.get("/api/trial").json()["state"] == "not_started"
        response = pending.get("/api/downloads")
        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {
                    "platform": "linux",
                    "version": "0.1.0",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "url": "/download/linux",
                }
            ]
        }
        assert str(directory) not in response.text
        download = pending.get(response.json()["items"][0]["url"])
        assert download.status_code == 200 and download.content == content
        assert download.headers["cache-control"] == "private, no-store"
        assert manifest["items"][0]["filename"] in download.headers["content-disposition"]
        assert pending.get("/api/trial").json()["state"] == "not_started"

        assert (
            admin.post(
                f"/api/admin/accounts/{user['id']}/decision",
                json={"decision": "suspend", "reason": "İndirme erişimi inceleme için kapatıldı."},
            ).status_code
            == 200
        )
        for path in ("/api/downloads", "/download/linux"):
            assert pending.get(path).status_code == 401


@pytest.mark.parametrize("incompatible_field", ["server_url", "server_public_key"])
def test_customer_downloads_fail_closed_for_an_incompatible_release(
    customer_portal, published_customer_release, incompatible_field
):
    _admin, [(alice, _), _] = customer_portal
    directory, manifest, content = published_customer_release
    assert alice.get("/api/downloads").status_code == 200
    manifest[incompatible_field] = (
        "https://different-installation.example.com"
        if incompatible_field == "server_url"
        else encode(
            Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
    )
    (directory / "manifest.json").write_text(json.dumps(manifest))
    for path in ("/api/downloads", "/download/linux"):
        response = alice.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert content not in response.content
        assert str(directory) not in response.text
