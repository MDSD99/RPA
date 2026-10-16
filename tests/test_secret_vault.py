"""AEAD tenant/alias/type/version binding and optimistic secret management."""

import base64
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import ConfigurationError
from app.core.exceptions import InvalidTransitionError, NotFoundError, ValidationError
from app.core.portal_config import PortalSettings
from app.entitlements.domain import EntitlementError
from app.identity.models import Account, IdentityAudit, Tenant
from app.identity.security import hash_password
from app.secrets.domain import validate_values
from app.secrets.models import Credential
from app.secrets.service import SecretService
from app.web.portal import create_portal_app
from app.workflows.registry import WorkflowFailure

pytestmark = pytest.mark.postgres
AUTHORIZATION = "Bearer confidential-token-4829"


def customer(session_factory, *, password_hash="unused-for-service-tests"):
    with session_factory.begin() as session:
        tenant = Tenant(id=uuid4(), name="Secret customer")
        session.add(tenant)
        session.flush()
        owner = Account(
            id=uuid4(),
            tenant_id=tenant.id,
            email=f"vault-{uuid4().hex}@example.com",
            display_name="Kasa müşterisi",
            password_hash=password_hash,
            email_verified=True,
            approval="approved",
        )
        session.add(owner)
        session.flush()
        return owner


@pytest.fixture
def owner(session_factory):
    return customer(session_factory)


@pytest.fixture
def vault(session_factory):
    return SecretService(session_factory, secret_key=Fernet.generate_key().decode())


def put(vault, owner, ref="api_main", *, version=None, value=AUTHORIZATION):
    return vault.put(owner.tenant_id, owner.id, ref, "http", {"authorization": value}, version)


def test_vault_metadata_ciphertext_and_audit_never_expose_plaintext(vault, owner, session_factory):
    stored = put(vault, owner)
    assert set(stored) == {"reference", "kind", "version", "updated_at"}
    assert AUTHORIZATION not in json.dumps(stored)
    assert AUTHORIZATION not in json.dumps(vault.list(owner.tenant_id))
    assert vault.resolver(owner.tenant_id)("api_main", "http") == {"authorization": AUTHORIZATION}
    with session_factory() as session:
        row = session.get(Credential, (owner.tenant_id, "api_main"))
        assert AUTHORIZATION not in row.ciphertext
        assert AUTHORIZATION.encode() not in base64.urlsafe_b64decode(row.ciphertext)
        audit = session.scalar(
            select(IdentityAudit).where(
                IdentityAudit.user_id == owner.id, IdentityAudit.action == "CREDENTIAL_STORED"
            )
        )
        assert audit.actor_id == owner.id and audit.correlation_id
        assert audit.reason == "api_main:http:v1"
        assert AUTHORIZATION not in repr(audit.reason)


@pytest.mark.parametrize("binding", ["tenant", "alias", "kind", "version", "tamper"])
def test_authenticated_encryption_rejects_ciphertext_swaps(vault, owner, session_factory, binding):
    put(vault, owner)
    target_owner, target_alias, target_kind = owner, "api_main", "http"
    with session_factory() as session:
        original = session.get(Credential, (owner.tenant_id, "api_main")).ciphertext
    if binding == "tenant":
        target_owner = customer(session_factory)
        put(vault, target_owner)
    elif binding == "alias":
        target_alias = "other_alias"
        put(vault, owner, target_alias)
    elif binding == "kind":
        target_alias, target_kind = "browser_login", "browser"
        vault.put(
            owner.tenant_id,
            owner.id,
            target_alias,
            target_kind,
            {"username": "login", "password": "private-password"},
            None,
        )
    elif binding == "version":
        put(vault, owner, version=1, value="Bearer next-value")
    elif binding == "tamper":
        raw = bytearray(base64.urlsafe_b64decode(original))
        raw[-1] ^= 1
        original = base64.urlsafe_b64encode(raw).decode()
    with session_factory.begin() as session:
        session.execute(
            update(Credential)
            .where(
                Credential.tenant_id == target_owner.tenant_id, Credential.reference == target_alias
            )
            .values(ciphertext=original)
        )
    with pytest.raises(WorkflowFailure) as error:
        vault.resolver(target_owner.tenant_id)(target_alias, target_kind)
    assert error.value.error_code == "FLOW_CREDENTIAL_UNAVAILABLE"
    assert AUTHORIZATION not in str(error.value)


def test_nonce_randomization_and_updated_version(vault, owner, session_factory):
    put(vault, owner)
    with session_factory() as session:
        first = session.get(Credential, (owner.tenant_id, "api_main")).ciphertext
    assert put(vault, owner, version=1)["version"] == 2
    with session_factory() as session:
        second = session.get(Credential, (owner.tenant_id, "api_main")).ciphertext
    assert first != second
    assert base64.urlsafe_b64decode(first)[:12] != base64.urlsafe_b64decode(second)[:12]
    assert vault.resolver(owner.tenant_id)("api_main", "http")["authorization"] == AUTHORIZATION


def test_concurrent_updates_accept_exactly_one_version(vault, owner):
    put(vault, owner)
    barrier = Barrier(5)

    def save(index):
        barrier.wait()
        try:
            return put(vault, owner, version=1, value=f"Bearer value-{index}")
        except InvalidTransitionError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(save, range(5)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count("conflict") == 4
    assert vault.list(owner.tenant_id)["items"][0]["version"] == 2


def test_concurrent_creation_of_same_alias_does_not_overwrite(vault, owner):
    barrier = Barrier(4)

    def create(index):
        barrier.wait()
        try:
            return put(vault, owner, value=f"Bearer create-{index}")
        except InvalidTransitionError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, range(4)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count("conflict") == 3
    assert vault.list(owner.tenant_id)["items"][0]["version"] == 1


def test_delete_tombstone_clears_ciphertext_and_recreate_never_reuses_version(
    vault, owner, session_factory
):
    put(vault, owner)
    with session_factory() as session:
        old_ciphertext = session.get(Credential, (owner.tenant_id, "api_main")).ciphertext
    with pytest.raises(InvalidTransitionError):
        vault.delete(owner.tenant_id, owner.id, "api_main", 2)
    vault.delete(owner.tenant_id, owner.id, "api_main", 1)
    assert vault.list(owner.tenant_id) == {"items": []}
    with pytest.raises(WorkflowFailure):
        vault.resolver(owner.tenant_id)("api_main", "http")
    with session_factory() as session:
        row = session.get(Credential, (owner.tenant_id, "api_main"))
        assert row.deleted_at is not None and row.ciphertext == ""
    assert put(vault, owner, value="Bearer replacement")["version"] == 2
    # Stale ciphertext from before deletion cannot be restored into the new record.
    with session_factory.begin() as session:
        session.execute(
            update(Credential)
            .where(Credential.tenant_id == owner.tenant_id, Credential.reference == "api_main")
            .values(ciphertext=old_ciphertext)
        )
    with pytest.raises(WorkflowFailure):
        vault.resolver(owner.tenant_id)("api_main", "http")


def test_cross_actor_and_cross_tenant_actions_are_denied(vault, owner, session_factory):
    other = customer(session_factory)
    put(vault, owner)
    assert vault.list(other.tenant_id) == {"items": []}
    with pytest.raises(NotFoundError):
        vault.put(
            owner.tenant_id, other.id, "api_main", "http", {"authorization": "Bearer attack"}, 1
        )
    with pytest.raises(NotFoundError):
        vault.delete(owner.tenant_id, other.id, "api_main", 1)
    with pytest.raises(WorkflowFailure):
        vault.resolver(other.tenant_id)("api_main", "http")
    assert vault.resolver(owner.tenant_id)("api_main", "http")["authorization"] == AUTHORIZATION


def test_current_account_suspension_blocks_resolution(vault, owner, session_factory):
    put(vault, owner)
    with session_factory.begin() as session:
        session.execute(update(Account).where(Account.id == owner.id).values(suspended=True))
    with pytest.raises(EntitlementError):
        vault.resolver(owner.tenant_id)("api_main", "http")


@pytest.mark.parametrize(
    "authorization",
    [
        "",
        "Bearer secret\r\nInjected: yes",
        "Bearer\x7fsecret",
        "Bearer\u202esecret",
        "Bearer 非ASCII",
        "x" * 4097,
    ],
    ids=["empty", "header_injection", "delete_control", "bidi_control", "non_ascii", "oversized"],
)
def test_http_credentials_reject_header_injection_and_invalid_values(authorization):
    with pytest.raises(ValidationError):
        validate_values("http", {"authorization": authorization})


@pytest.mark.parametrize("value", [True, 0, -1, "1", 1.1])
def test_runtime_version_validation_is_strict(vault, owner, value):
    with pytest.raises(ValidationError):
        put(vault, owner, version=value)
    with pytest.raises(ValidationError):
        vault.delete(owner.tenant_id, owner.id, "api_main", value)


def test_smtp_vault_never_stores_insecure_local_credentials():
    values = {
        "host": "127.0.0.1",
        "port": "2525",
        "sender": "sender@example.com",
        "security": "local",
        "username": "",
        "password": "",
    }
    with pytest.raises(ValidationError):
        validate_values("smtp", values)


@pytest.mark.parametrize("key", ["", "not-a-key", None])
def test_invalid_encryption_key_rejected_without_printing_it(session_factory, key):
    with pytest.raises(ConfigurationError) as error:
        SecretService(session_factory, secret_key=key)
    assert str(error.value) == "Vault encryption key is invalid"


def test_database_enforces_secret_alias_and_kind_constraints(owner, session_factory):
    for ref, kind in [("UPPER", "http"), ("valid", "shell")]:
        with pytest.raises(IntegrityError), session_factory.begin() as session:
            session.add(
                Credential(
                    tenant_id=owner.tenant_id,
                    reference=ref,
                    kind=kind,
                    version=1,
                    ciphertext="ciphertext",
                )
            )
            session.flush()


def test_customer_secret_http_has_write_only_values_and_csrf(session_factory):
    password = "Vault API integration passphrase 2026"
    owner = customer(session_factory, password_hash=hash_password(password))
    app = create_portal_app(
        session_factory, PortalSettings("http://testserver", Fernet.generate_key().decode(), "test")
    )
    with TestClient(app, headers={"X-RPA-Request": "1"}) as client:
        response = client.post("/api/auth/login", json={"email": owner.email, "password": password})
        assert response.status_code == 200
        csrf = response.json()["csrf_token"]
        body = {
            "kind": "http",
            "values": {"authorization": AUTHORIZATION},
            "expected_version": None,
        }
        assert client.put("/api/secrets/api_main", json=body).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        created = client.put("/api/secrets/api_main", json=body)
        assert created.status_code == 200
        assert AUTHORIZATION not in created.text
        listed = client.get("/api/secrets")
        assert AUTHORIZATION not in listed.text
        assert set(listed.json()["items"][0]) == {"reference", "kind", "version", "updated_at"}
        assert client.get("/api/secrets/api_main").status_code == 405
        forged = copy.deepcopy(body)
        forged["tenant_id"] = str(uuid4())
        assert client.put("/api/secrets/forged", json=forged).status_code == 422
