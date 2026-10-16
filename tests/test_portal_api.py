"""Customer onboarding must never expose the global development execution API."""

import json
from uuid import uuid4

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.portal_config import PortalSettings
from app.identity.models import Account, EmailOutbox
from app.identity.service import IdentityService
from app.web.portal import create_portal_app

pytestmark = pytest.mark.postgres
PASSWORD = "Portal test passphrase 2026!"
HEADERS = {"X-RPA-Request": "1"}


@pytest.fixture
def portal(session_factory):
    key = Fernet.generate_key().decode()
    config = PortalSettings("http://testserver", key, "test")
    identity = IdentityService(session_factory, secret_key=key, public_url=config.public_url)
    with TestClient(create_portal_app(session_factory, config)) as client:
        client.headers.update(HEADERS)
        yield client, identity, key


def token_for(session_factory, key, email, kind):
    with session_factory() as session:
        row = session.scalar(
            select(EmailOutbox)
            .join(Account)
            .where(
                Account.email == email,
                EmailOutbox.kind == kind,
            )
            .order_by(EmailOutbox.created_at.desc(), EmailOutbox.id.desc())
        )
        return json.loads(Fernet(key.encode()).decrypt(row.payload_ciphertext.encode()))["token"]


def test_registration_verification_admin_approval_and_revocation(portal, session_factory):
    client, identity, key = portal
    email = f"portal-{uuid4().hex}@example.com"
    payload = {"email": email, "password": PASSWORD, "display_name": "Deniz"}
    registered = client.post("/api/auth/register", json=payload)
    assert registered.status_code == 202
    duplicate = client.post("/api/auth/register", json=payload)
    assert duplicate.json() == registered.json()
    assert "token" not in registered.text and "password" not in registered.text
    token = token_for(session_factory, key, email, "verify_email")
    assert client.get("/verify-email", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    user = login.json()["user"]
    assert user["approval"] == "pending" and user["email_verified"] is False
    assert not user["is_admin"]
    assert client.post("/api/auth/verification/confirm", json={"token": token}).status_code == 200
    assert client.post("/api/auth/verification/confirm", json={"token": token}).status_code == 400
    assert client.get("/api/auth/me").json()["user"]["approval"] == "pending"
    assert client.get("/api/admin/accounts").status_code in {401, 403}

    secret = pyotp.random_base32()
    admin_email = f"admin-{uuid4().hex}@example.com"
    identity.bootstrap_admin(admin_email, PASSWORD, "Yönetici", secret)
    admin = client.post(
        "/api/admin/login",
        json={
            "email": admin_email,
            "password": PASSWORD,
            "totp": pyotp.TOTP(secret).now(),
        },
    )
    assert admin.status_code == 200
    csrf = {"X-CSRF-Token": admin.json()["csrf_token"]}
    approved = client.post(
        f"/api/admin/accounts/{user['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Başvuru incelendi.",
        },
        headers=csrf,
    )
    assert approved.status_code == 200 and approved.json()["user"]["approval"] == "approved"
    assert client.get("/api/auth/me").json()["user"]["approval"] == "approved"
    accounts = client.get("/api/admin/accounts?limit=1&offset=0")
    assert accounts.status_code == 200 and len(accounts.json()["items"]) == 1
    forbidden = ("password_hash", "totp_secret", "token_hash", "payload_ciphertext")
    assert all(word not in accounts.text for word in forbidden)
    suspended = client.post(
        f"/api/admin/accounts/{user['id']}/decision",
        json={
            "decision": "suspend",
            "reason": "Erişim incelemesi.",
        },
        headers=csrf,
    )
    assert suspended.status_code == 200
    assert client.get("/api/auth/me").status_code in {401, 403}


@pytest.mark.parametrize(
    "path",
    [
        "/api/flows",
        "/api/jobs",
        "/api/schedules",
        "/api/overview",
        "/api/workers",
        "/api/workflows",
    ],
)
def test_execution_requires_verified_approved_customer(portal, path):
    client, identity, _key = portal
    email = f"isolated-{uuid4().hex}@example.com"
    identity.register(email, PASSWORD, "Ayrı kurum")
    assert (
        client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 200
    )
    assert client.get(path).status_code == (404 if path == "/api/workers" else 403)
    assert client.post(path, json={}).status_code in {403, 404, 405, 422}


def test_registration_cannot_assign_role_or_tenant(portal):
    client, _identity, _key = portal
    result = client.post(
        "/api/auth/register",
        json={
            "email": f"role-{uuid4().hex}@example.com",
            "password": PASSWORD,
            "display_name": "Müşteri",
            "is_admin": True,
            "tenant_id": str(uuid4()),
        },
    )
    assert result.status_code == 422
    assert PASSWORD not in result.text


def test_password_reset_closes_session_and_requires_new_password(portal, session_factory):
    client, identity, key = portal
    email = f"reset-{uuid4().hex}@example.com"
    identity.register(email, PASSWORD, "Parola yenileme")
    client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert client.post("/api/auth/password/request", json={"email": email}).status_code == 202
    token = token_for(session_factory, key, email, "password_reset")
    new_password = "Renewed portal test passphrase!"
    response = client.post(
        "/api/auth/password/reset", json={"token": token, "password": new_password}
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 401
    )
    assert (
        client.post("/api/auth/login", json={"email": email, "password": new_password}).status_code
        == 200
    )
