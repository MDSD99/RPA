"""Real PostgreSQL tests account approval, secret storage and concurrent credential use."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pyotp
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update

from app.identity.domain import AuthError
from app.identity.models import (
    Account,
    EmailOutbox,
    EmailToken,
    IdentityAudit,
    LoginSession,
    Tenant,
)
from app.identity.repository import IdentityRepository
from app.identity.security import hash_password, password_matches, token_digest
from app.identity.service import IdentityService

pytestmark = pytest.mark.postgres
PASSWORD = "Example correct horse battery 123"
REPLACEMENT = "A replacement horse battery 456"


@pytest.fixture
def identity(session_factory):
    return IdentityService(
        session_factory,
        secret_key=Fernet.generate_key().decode("ascii"),
        public_url="https://app.example.com",
    )


def latest_payload(identity, session_factory, email, kind="verify_email"):
    with session_factory() as session:
        row = session.scalar(
            select(EmailOutbox)
            .join(Account, Account.id == EmailOutbox.user_id)
            .where(Account.email == email.lower(), EmailOutbox.kind == kind)
            .order_by(EmailOutbox.created_at.desc(), EmailOutbox.id)
        )
        return json.loads(identity.cipher.decrypt(row.payload_ciphertext.encode("ascii")))


def register(identity, session_factory, *, verified=False):
    email = f"identity-{uuid4().hex}@example.com"
    identity.register(email, PASSWORD, "Örnek Kullanıcı")
    if verified:
        identity.verify_email(latest_payload(identity, session_factory, email)["token"])
    return email, identity.login(email, PASSWORD)


@pytest.fixture
def administrator(identity, session_factory):
    email = f"admin-{uuid4().hex}@example.com"
    secret = pyotp.random_base32()
    identity.bootstrap_admin(email, PASSWORD, "Operatör", secret)
    with session_factory() as session:
        now = session.scalar(select(func.clock_timestamp()))
    login = identity.login(email, PASSWORD, audience="admin", totp=pyotp.TOTP(secret).at(now))
    return email, secret, login


def assert_error(code, action):
    with pytest.raises(AuthError) as error:
        action()
    assert error.value.code == code
    assert str(error.value) == code


def test_registration_stores_argon2id_digest_and_encrypted_single_use_link(
    identity, session_factory
):
    email, login = register(identity, session_factory)
    payload = latest_payload(identity, session_factory, email)
    with session_factory() as session:
        account = session.get(Account, UUID(login.user.id))
        token = session.scalar(select(EmailToken).where(EmailToken.user_id == account.id))
        outbox = session.scalar(select(EmailOutbox).where(EmailOutbox.user_id == account.id))
        assert account.password_hash.startswith("$argon2id$")
        assert PASSWORD not in account.password_hash
        assert password_matches(PASSWORD, account.password_hash)
        assert token.token_hash == token_digest(payload["token"])
        assert token.token_hash != payload["token"]
        assert payload["token"] not in outbox.payload_ciphertext
        assert email not in outbox.payload_ciphertext
        assert PASSWORD not in outbox.payload_ciphertext
        assert payload["public_url"] == "https://app.example.com"
        assert token.expires_at - token.created_at == timedelta(hours=24)
        assert session.get(Tenant, account.tenant_id) is not None
        assert outbox.status == "pending"
    assert login.token not in repr(login)
    assert not login.user.email_verified
    assert login.user.approval == "pending"
    assert not login.user.is_admin


@pytest.mark.parametrize("password", ["short", "x" * 129, "a" * 15 + "\x00", "a" * 15 + "\n"])
def test_invalid_password_policy(password):
    assert_error("INVALID_PASSWORD", lambda: hash_password(password))


def test_password_hash_uses_independent_salts():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert password_matches(PASSWORD, first)
    assert not password_matches(REPLACEMENT, first)


def test_concurrent_duplicate_registration_creates_one_account_and_tenant(
    identity, session_factory
):
    email = f"duplicate-{uuid4().hex}@example.com"
    barrier = Barrier(4)

    def create(_):
        barrier.wait()
        return identity.register(email.upper(), PASSWORD, "Tek Hesap")

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(create, range(4))) == [None] * 4
    with session_factory() as session:
        accounts = list(session.scalars(select(Account).where(Account.email == email)))
        assert len(accounts) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(EmailOutbox)
                .where(EmailOutbox.user_id == accounts[0].id)
            )
            == 1
        )
    assert identity.register(email, REPLACEMENT, "Başka kişi") is None
    assert identity.login(email, PASSWORD).user.display_name == "Tek Hesap"
    assert_error("INVALID_CREDENTIALS", lambda: identity.login(email, REPLACEMENT))


def test_pending_customer_session_never_grants_execution_approval(
    identity, session_factory, administrator
):
    email, login = register(identity, session_factory)
    assert identity.authenticate(login.token).id == login.user.id
    assert_error("EMAIL_UNVERIFIED", lambda: identity.require_approved(login.token))
    assert_error(
        "EMAIL_UNVERIFIED",
        lambda: identity.decide(
            administrator[2].token, login.user.id, decision="approve", reason="İncelendi"
        ),
    )
    identity.verify_email(latest_payload(identity, session_factory, email)["token"])
    assert_error("APPROVAL_REQUIRED", lambda: identity.require_approved(login.token))
    approved = identity.decide(
        administrator[2].token, login.user.id, decision="approve", reason="İncelendi"
    )
    assert approved.approval == "approved"
    assert identity.require_approved(login.token).approval == "approved"


def test_parallel_link_consumption_succeeds_once(identity, session_factory):
    email, _ = register(identity, session_factory)
    raw = latest_payload(identity, session_factory, email)["token"]
    barrier = Barrier(6)

    def consume(_):
        barrier.wait()
        try:
            identity.verify_email(raw)
            return "verified"
        except AuthError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(consume, range(6)))
    assert outcomes.count("verified") == 1
    assert outcomes.count("INVALID_TOKEN") == 5


def test_resend_revokes_previous_link_and_is_generic_for_unknown_accounts(
    identity, session_factory
):
    email, _ = register(identity, session_factory)
    first = latest_payload(identity, session_factory, email)["token"]
    assert identity.request_verification(email) is None
    second = latest_payload(identity, session_factory, email)["token"]
    assert first != second
    assert identity.request_verification(f"unknown-{uuid4().hex}@example.com") is None
    assert_error("INVALID_TOKEN", lambda: identity.verify_email(first))
    identity.verify_email(second)
    assert identity.request_verification(email) is None


def test_link_expiry_uses_database_clock(identity, session_factory):
    email, login = register(identity, session_factory)
    raw = latest_payload(identity, session_factory, email)["token"]
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(EmailToken)
            .where(EmailToken.user_id == UUID(login.user.id))
            .values(created_at=now - timedelta(days=2), expires_at=now - timedelta(seconds=1))
        )
    assert_error("INVALID_TOKEN", lambda: identity.verify_email(raw))
    assert not identity.authenticate(login.token).email_verified


def test_password_reset_is_single_use_revokes_sessions_and_keeps_verification_required(
    identity, session_factory
):
    email, login = register(identity, session_factory)
    assert identity.request_password_reset(email) is None
    assert identity.request_password_reset(f"unknown-{uuid4().hex}@example.com") is None
    payload = latest_payload(identity, session_factory, email, "password_reset")
    assert_error("INVALID_TOKEN", lambda: identity.verify_email(payload["token"]))
    identity.reset_password(payload["token"], REPLACEMENT)
    assert_error("INVALID_TOKEN", lambda: identity.reset_password(payload["token"], PASSWORD))
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(login.token))
    assert_error("INVALID_CREDENTIALS", lambda: identity.login(email, PASSWORD))
    fresh = identity.login(email, REPLACEMENT)
    assert not fresh.user.email_verified
    assert_error("EMAIL_UNVERIFIED", lambda: identity.require_approved(fresh.token))


def test_expired_password_reset_leaves_password_and_session_unchanged(identity, session_factory):
    email, login = register(identity, session_factory)
    identity.request_password_reset(email)
    payload = latest_payload(identity, session_factory, email, "password_reset")
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == UUID(login.user.id), EmailToken.purpose == "password_reset"
            )
            .values(created_at=now - timedelta(hours=2), expires_at=now - timedelta(seconds=1))
        )
    assert_error("INVALID_TOKEN", lambda: identity.reset_password(payload["token"], REPLACEMENT))
    assert identity.authenticate(login.token).id == login.user.id
    assert identity.login(email, PASSWORD).user.id == login.user.id


def test_suspension_and_rejection_revoke_sessions_without_restore_revival(
    identity, session_factory, administrator
):
    email, login = register(identity, session_factory, verified=True)
    admin = administrator[2].token
    identity.decide(admin, login.user.id, decision="approve", reason="İncelendi")
    identity.decide(admin, login.user.id, decision="suspend", reason="Erişim durduruldu")
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(login.token))
    assert_error("ACCOUNT_SUSPENDED", lambda: identity.login(email, PASSWORD))
    identity.decide(admin, login.user.id, decision="restore", reason="Tekrar incelendi")
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(login.token))
    fresh = identity.login(email, PASSWORD)
    assert identity.require_approved(fresh.token).approval == "approved"
    identity.decide(admin, login.user.id, decision="reject", reason="Üyelik uygun değil")
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(fresh.token))
    rejected = identity.login(email, PASSWORD)
    assert rejected.user.approval == "rejected"
    assert_error("APPROVAL_REQUIRED", lambda: identity.require_approved(rejected.token))
    with session_factory() as session:
        decisions = list(
            session.scalars(
                select(IdentityAudit).where(
                    IdentityAudit.user_id == UUID(login.user.id),
                    IdentityAudit.actor_id == UUID(administrator[2].user.id),
                )
            )
        )
        assert len(decisions) == 4
        assert all(item.reason for item in decisions)
        assert len({item.correlation_id for item in decisions}) == 4


def test_session_hash_expiry_and_idempotent_logout(identity, session_factory):
    email, login = register(identity, session_factory)
    with session_factory() as session:
        stored = session.scalar(
            select(LoginSession).where(LoginSession.user_id == UUID(login.user.id))
        )
        assert stored.token_hash == token_digest(login.token)
        assert stored.expires_at - stored.created_at == timedelta(hours=8)
    identity.logout(login.token)
    identity.logout(login.token)
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(login.token))
    fresh = identity.login(email, PASSWORD)
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(LoginSession)
            .where(LoginSession.token_hash == token_digest(fresh.token))
            .values(created_at=now - timedelta(days=1), expires_at=now - timedelta(seconds=1))
        )
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(fresh.token))


def test_customer_and_admin_session_audiences_are_separate(
    identity, session_factory, administrator
):
    email, _ = register(identity, session_factory, verified=True)
    assert_error(
        "ADMIN_REQUIRED", lambda: identity.login(email, PASSWORD, audience="admin", totp="123456")
    )
    admin_email, _, admin_login = administrator
    customer_login = identity.login(admin_email, PASSWORD)
    assert_error("SESSION_EXPIRED", lambda: identity.list_accounts(customer_login.token))
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(admin_login.token))
    assert identity.authenticate(admin_login.token, audience="admin").is_admin
    assert_error(
        "INVALID_AUDIENCE", lambda: identity.authenticate(customer_login.token, audience="other")
    )
    with session_factory() as session:
        stored = session.scalar(
            select(LoginSession).where(LoginSession.token_hash == token_digest(admin_login.token))
        )
        assert stored.expires_at - stored.created_at == timedelta(minutes=30)


def test_admin_mfa_is_encrypted_required_and_replay_protected(identity, session_factory):
    email = f"mfa-{uuid4().hex}@example.com"
    secret = pyotp.random_base32()
    identity.bootstrap_admin(email, PASSWORD, "Operatör", secret)
    assert_error("MFA_REQUIRED", lambda: identity.login(email, PASSWORD, audience="admin"))
    assert_error(
        "MFA_INVALID", lambda: identity.login(email, PASSWORD, audience="admin", totp="invalid")
    )
    with session_factory() as session:
        account = session.scalar(select(Account).where(Account.email == email))
        assert secret not in account.totp_secret_ciphertext
        now = session.scalar(select(func.clock_timestamp()))
    code = pyotp.TOTP(secret).at(now)
    result = identity.login(email, PASSWORD, audience="admin", totp=code)
    assert result.user.is_admin
    assert_error(
        "MFA_INVALID", lambda: identity.login(email, PASSWORD, audience="admin", totp=code)
    )


def test_parallel_admin_totp_login_accepts_code_once(identity, session_factory):
    email = f"mfa-concurrent-{uuid4().hex}@example.com"
    secret = pyotp.random_base32()
    identity.bootstrap_admin(email, PASSWORD, "Operatör", secret)
    with session_factory() as session:
        now = session.scalar(select(func.clock_timestamp()))
    code, barrier = pyotp.TOTP(secret).at(now), Barrier(3)

    def login(_):
        barrier.wait()
        try:
            identity.login(email, PASSWORD, audience="admin", totp=code)
            return "accepted"
        except AuthError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=3) as executor:
        outcomes = list(executor.map(login, range(3)))
    assert outcomes.count("accepted") == 1
    assert outcomes.count("MFA_INVALID") == 2


def test_admin_decisions_prevent_self_changes_and_admin_target_changes(
    identity, session_factory, administrator
):
    email, login = register(identity, session_factory)
    admin = administrator[2]
    assert_error(
        "SELF_ACTION_FORBIDDEN",
        lambda: identity.decide(
            admin.token, admin.user.id, decision="suspend", reason="Yanlış işlem"
        ),
    )
    other_email = f"other-admin-{uuid4().hex}@example.com"
    identity.bootstrap_admin(other_email, PASSWORD, "Diğer operatör", pyotp.random_base32())
    other = identity.login(other_email, PASSWORD)
    assert_error(
        "ADMIN_ACTION_FORBIDDEN",
        lambda: identity.decide(
            admin.token, other.user.id, decision="suspend", reason="Yetkisiz işlem"
        ),
    )
    assert_error(
        "INVALID_DECISION",
        lambda: identity.decide(
            admin.token, login.user.id, decision="make_admin", reason="Yetkisiz işlem"
        ),
    )
    assert_error(
        "INVALID_REASON",
        lambda: identity.decide(admin.token, login.user.id, decision="reject", reason=""),
    )
    assert_error(
        "SESSION_EXPIRED",
        lambda: identity.decide(
            login.token, admin.user.id, decision="suspend", reason="Yetkisiz işlem"
        ),
    )
    assert not identity.login(email, PASSWORD).user.is_admin


def test_bootstrap_never_upgrades_registered_account(identity, session_factory):
    email, login = register(identity, session_factory)
    assert_error(
        "ACCOUNT_EXISTS",
        lambda: identity.bootstrap_admin(email, PASSWORD, "Operatör", pyotp.random_base32()),
    )
    assert not identity.authenticate(login.token).is_admin


def test_registration_rolls_back_account_tokens_and_outbox_if_audit_fails(
    identity, session_factory, monkeypatch
):
    email = f"rollback-{uuid4().hex}@example.com"

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit persistence failure")

    monkeypatch.setattr(IdentityRepository, "audit", fail)
    with pytest.raises(RuntimeError, match="injected audit"):
        identity.register(email, PASSWORD, "Geri alınmalı")
    with session_factory() as session:
        assert session.scalar(select(Account).where(Account.email == email)) is None
        assert session.scalar(select(Tenant).where(Tenant.name == "Geri alınmalı")) is None


def test_password_reset_revokes_admin_and_customer_sessions(
    identity, session_factory, administrator
):
    email, _, admin = administrator
    customer = identity.login(email, PASSWORD)
    identity.request_password_reset(email)
    raw = latest_payload(identity, session_factory, email, "password_reset")["token"]
    identity.reset_password(raw, REPLACEMENT)
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(admin.token, audience="admin"))
    assert_error("SESSION_EXPIRED", lambda: identity.authenticate(customer.token))
    assert_error("MFA_REQUIRED", lambda: identity.login(email, REPLACEMENT, audience="admin"))


def test_list_accounts_returns_bounded_safe_views(identity, session_factory, administrator):
    _, login = register(identity, session_factory)
    result = identity.list_accounts(administrator[2].token, limit=100)
    assert result["total"] >= 2
    assert result["limit"] == 100
    assert result["offset"] == 0
    assert any(account.id == login.user.id for account in result["items"])
    assert all(not hasattr(account, "password_hash") for account in result["items"])
    assert_error(
        "INVALID_PAGINATION", lambda: identity.list_accounts(administrator[2].token, limit=101)
    )
    assert_error(
        "INVALID_PAGINATION", lambda: identity.list_accounts(administrator[2].token, offset=-1)
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://app.example.com",
        "https://app.example.com/path",
        "https://user:secret@app.example.com",
        "https://app.example.com?redirect=example.org",
    ],
)
def test_identity_refuses_untrusted_public_mail_origins(session_factory, url):
    with pytest.raises(ValueError, match="HTTPS origin"):
        IdentityService(
            session_factory, secret_key=Fernet.generate_key().decode("ascii"), public_url=url
        )


def test_malformed_reset_token_is_rejected_before_password_hash(identity, monkeypatch):
    def unexpected_hash(value):
        pytest.fail("Malformed token reached expensive password hashing")

    monkeypatch.setattr("app.identity.service.hash_password", unexpected_hash)
    assert_error("INVALID_TOKEN", lambda: identity.reset_password("malformed", REPLACEMENT))


def test_multiline_admin_reason_is_preserved_but_control_codes_are_rejected(
    identity, session_factory, administrator
):
    _, login = register(identity, session_factory, verified=True)
    reason = "E-posta doğrulandı.\nBaşvuru uygun bulundu."
    identity.decide(administrator[2].token, login.user.id, decision="approve", reason=reason)
    with session_factory() as session:
        audit = session.scalar(
            select(IdentityAudit).where(
                IdentityAudit.user_id == UUID(login.user.id),
                IdentityAudit.action == "ACCOUNT_APPROVE",
            )
        )
        assert audit.reason == reason
    assert_error(
        "INVALID_REASON",
        lambda: identity.decide(
            administrator[2].token, login.user.id, decision="suspend", reason="Gizli\x00kontrol"
        ),
    )
