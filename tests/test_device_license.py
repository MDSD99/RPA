"""Real PostgreSQL verifies device binding races, replay, expiry and reset fencing."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.entitlements.access import reserve_flow
from app.entitlements.crypto import canonical, decode, encode, thumbprint
from app.entitlements.devices import DeviceLicenseService
from app.entitlements.domain import EntitlementError
from app.entitlements.models import DeviceChallenge, Entitlement, LicenseDevice
from app.entitlements.service import TrialService
from app.identity.models import Account, IdentityAudit, Tenant

pytestmark = pytest.mark.postgres


def account(session_factory, *, admin=False):
    with session_factory.begin() as session:
        tenant = Tenant(id=uuid4(), name="Device test account")
        session.add(tenant)
        session.flush()
        owner = Account(
            id=uuid4(),
            tenant_id=tenant.id,
            email=f"device-{uuid4().hex}@example.com",
            display_name="Cihaz hesabı",
            password_hash="unused-by-license-service",
            email_verified=True,
            approval="approved",
            is_admin=admin,
            totp_secret_ciphertext="unused-by-license-service" if admin else None,
        )
        session.add(owner)
        session.flush()
        return owner


def key_pair():
    private = Ed25519PrivateKey.generate()
    public = encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    return private, public


@pytest.fixture
def owner(session_factory):
    owner = account(session_factory)
    TrialService(session_factory).start(owner.tenant_id)
    return owner


@pytest.fixture
def administrator(session_factory):
    return account(session_factory, admin=True)


@pytest.fixture
def devices(session_factory):
    signing_key = Ed25519PrivateKey.generate()
    return DeviceLicenseService(
        session_factory,
        signing_private_key=encode(
            signing_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        ),
    )


def proof(private, challenge):
    return encode(private.sign(challenge["message"].encode("utf-8")))


def activate(devices, owner, pair=None):
    private, public = pair or key_pair()
    challenge = devices.challenge(owner.tenant_id, public, "activate")
    lease = devices.activate(
        owner.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge)
    )
    return private, public, challenge, lease


def lease_payload(devices, lease):
    # The trusted key is obtained out of band, never trusted from an envelope.
    assert lease["public_key"] == devices.public_key
    Ed25519PublicKey.from_public_bytes(decode(devices.public_key, 32)).verify(
        decode(lease["signature"], 64), lease["payload"].encode("utf-8")
    )
    payload = json.loads(lease["payload"])
    assert canonical(payload) == lease["payload"]
    return payload


def assert_error(code, action):
    with pytest.raises(EntitlementError) as error:
        action()
    assert error.value.code == code


def test_activation_proves_key_and_issues_short_signed_shared_quota_lease(
    devices, owner, session_factory
):
    web_id, desktop_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        reserve_flow(session, owner.tenant_id, web_id)
    TrialService(session_factory).reserve_for_desktop(owner.tenant_id, desktop_id)
    _, public, challenge, lease = activate(devices, owner)
    payload = lease_payload(devices, lease)
    assert payload["version"] == 1
    assert payload["issuer"] == "multirpa"
    assert payload["tenant_id"] == str(owner.tenant_id)
    assert payload["key_thumbprint"] == thumbprint(public)
    assert set(payload["flow_ids"]) == {str(web_id), str(desktop_id)}
    assert 0 < payload["expires_at"] - payload["issued_at"] <= 60
    assert 0 < payload["valid_for_ms"] <= 60000
    assert payload["expires_at"] <= payload["entitlement_expires_at"]
    assert UUID(payload["device_id"]) and UUID(payload["lease_id"])
    with session_factory() as session:
        device = session.get(LicenseDevice, UUID(payload["device_id"]))
        assert device.public_key == public
        assert device.tenant_id == owner.tenant_id
        assert device.revoked_at is None
        assert int(device.lease_expires_at.timestamp()) == payload["expires_at"]
        row = session.get(DeviceChallenge, UUID(challenge["challenge_id"]))
        assert row.consumed_at is not None
        assert row.expires_at - row.created_at == timedelta(minutes=2)


def test_renewal_uses_new_proof_same_device_and_fresh_lease_id(devices, owner):
    private, public, _, original = activate(devices, owner)
    challenge = devices.challenge(owner.tenant_id, public, "renew")
    renewed = devices.renew(
        owner.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge)
    )
    first, second = lease_payload(devices, original), lease_payload(devices, renewed)
    assert first["device_id"] == second["device_id"]
    assert first["lease_id"] != second["lease_id"]
    assert second["expires_at"] >= first["expires_at"]


@pytest.mark.parametrize("mutation", ["wrong_key", "altered_message", "invalid_encoding"])
def test_invalid_proof_never_binds_device_or_consumes_valid_challenge(
    devices, owner, session_factory, mutation
):
    private, public = key_pair()
    challenge = devices.challenge(owner.tenant_id, public, "activate")
    signature = proof(private, challenge)
    if mutation == "wrong_key":
        signature = proof(key_pair()[0], challenge)
    elif mutation == "altered_message":
        signature = encode(private.sign((challenge["message"] + " ").encode("utf-8")))
    else:
        signature += "="
    challenge_id = UUID(challenge["challenge_id"])
    assert_error(
        "INVALID_DEVICE_PROOF", lambda: devices.activate(owner.tenant_id, challenge_id, signature)
    )
    with session_factory() as session:
        assert session.get(DeviceChallenge, challenge_id).consumed_at is None
        assert (
            session.scalar(select(LicenseDevice).where(LicenseDevice.tenant_id == owner.tenant_id))
            is None
        )
    assert lease_payload(
        devices, devices.activate(owner.tenant_id, challenge_id, proof(private, challenge))
    )["device_id"]


def test_challenge_is_single_use_and_bound_to_purpose(devices, owner):
    private, public = key_pair()
    challenge = devices.challenge(owner.tenant_id, public, "activate")
    challenge_id, signature = UUID(challenge["challenge_id"]), proof(private, challenge)
    assert_error(
        "INVALID_DEVICE_CHALLENGE", lambda: devices.renew(owner.tenant_id, challenge_id, signature)
    )
    devices.activate(owner.tenant_id, challenge_id, signature)
    assert_error(
        "INVALID_DEVICE_CHALLENGE",
        lambda: devices.activate(owner.tenant_id, challenge_id, signature),
    )


def test_parallel_challenge_replay_returns_only_one_lease(devices, owner):
    private, public = key_pair()
    challenge = devices.challenge(owner.tenant_id, public, "activate")
    barrier = Barrier(6)

    def issue(_):
        barrier.wait()
        try:
            return devices.activate(
                owner.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge)
            )
        except EntitlementError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(issue, range(6)))
    assert sum(isinstance(row, dict) for row in outcomes) == 1
    assert outcomes.count("INVALID_DEVICE_CHALLENGE") == 5


def test_two_first_machine_activations_race_for_one_account_binding(
    devices, owner, session_factory
):
    pairs = [key_pair(), key_pair()]
    challenges = [devices.challenge(owner.tenant_id, public, "activate") for _, public in pairs]
    barrier = Barrier(2)

    def issue(index):
        barrier.wait()
        try:
            return devices.activate(
                owner.tenant_id,
                UUID(challenges[index]["challenge_id"]),
                proof(pairs[index][0], challenges[index]),
            )
        except EntitlementError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(issue, range(2)))
    assert sum(isinstance(row, dict) for row in outcomes) == 1
    assert outcomes.count("DEVICE_ALREADY_BOUND") == 1
    with session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(LicenseDevice)
                .where(
                    LicenseDevice.tenant_id == owner.tenant_id, LicenseDevice.revoked_at.is_(None)
                )
            )
            == 1
        )


def test_second_machine_and_cross_account_key_reuse_are_rejected(devices, owner, session_factory):
    _, public, _, _ = activate(devices, owner)
    assert_error(
        "DEVICE_ALREADY_BOUND",
        lambda: devices.challenge(owner.tenant_id, key_pair()[1], "activate"),
    )
    other = account(session_factory)
    TrialService(session_factory).start(other.tenant_id)
    assert_error(
        "DEVICE_KEY_RETIRED", lambda: devices.challenge(other.tenant_id, public, "activate")
    )


def test_challenge_is_tenant_scoped(devices, owner, session_factory):
    private, public = key_pair()
    challenge = devices.challenge(owner.tenant_id, public, "activate")
    other = account(session_factory)
    TrialService(session_factory).start(other.tenant_id)
    assert_error(
        "INVALID_DEVICE_CHALLENGE",
        lambda: devices.activate(
            other.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge)
        ),
    )
    devices.activate(owner.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge))


def test_expired_challenge_is_rejected_using_database_clock(devices, owner, session_factory):
    private, public = key_pair()
    challenge = devices.challenge(owner.tenant_id, public, "activate")
    challenge_id = UUID(challenge["challenge_id"])
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(DeviceChallenge)
            .where(DeviceChallenge.id == challenge_id)
            .values(created_at=now - timedelta(minutes=3), expires_at=now - timedelta(seconds=1))
        )
    assert_error(
        "INVALID_DEVICE_CHALLENGE",
        lambda: devices.activate(owner.tenant_id, challenge_id, proof(private, challenge)),
    )


def test_pending_challenge_budget_is_bounded_and_expired_entries_do_not_block(
    devices, owner, session_factory
):
    public = key_pair()[1]
    for _ in range(10):
        devices.challenge(owner.tenant_id, public, "activate")
    assert_error(
        "TOO_MANY_CHALLENGES", lambda: devices.challenge(owner.tenant_id, public, "activate")
    )
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(DeviceChallenge)
            .where(DeviceChallenge.tenant_id == owner.tenant_id)
            .values(created_at=now - timedelta(minutes=3), expires_at=now - timedelta(seconds=1))
        )
    assert devices.challenge(owner.tenant_id, public, "activate")["challenge_id"]


def test_renewal_without_binding_is_rejected(devices, owner):
    assert_error(
        "DEVICE_NOT_BOUND", lambda: devices.challenge(owner.tenant_id, key_pair()[1], "renew")
    )


@pytest.mark.parametrize(
    "changes", [{"suspended": True}, {"email_verified": False}, {"approval": "rejected"}]
)
def test_account_permission_rechecked_between_challenge_and_issue(
    devices, owner, session_factory, changes
):
    private, public, _, _ = activate(devices, owner)
    challenge = devices.challenge(owner.tenant_id, public, "renew")
    with session_factory.begin() as session:
        session.execute(update(Account).where(Account.id == owner.id).values(**changes))
    code = "ACCOUNT_SUSPENDED" if changes.get("suspended") else "ACCOUNT_UNAPPROVED"
    assert_error(
        code,
        lambda: devices.renew(
            owner.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge)
        ),
    )


def test_trial_expiry_prevents_renewal_and_lease_never_outlives_trial(
    devices, owner, session_factory
):
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(Entitlement)
            .where(Entitlement.tenant_id == owner.tenant_id)
            .values(
                trial_started_at=now - timedelta(days=30), trial_ends_at=now + timedelta(seconds=10)
            )
        )
    private, public, _, lease = activate(devices, owner)
    payload = lease_payload(devices, lease)
    assert payload["expires_at"] == payload["entitlement_expires_at"]
    assert payload["expires_at"] - payload["issued_at"] <= 10
    challenge = devices.challenge(owner.tenant_id, public, "renew")
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(Entitlement)
            .where(Entitlement.tenant_id == owner.tenant_id)
            .values(trial_ends_at=now - timedelta(seconds=1))
        )
    assert_error(
        "TRIAL_EXPIRED",
        lambda: devices.renew(
            owner.tenant_id, UUID(challenge["challenge_id"]), proof(private, challenge)
        ),
    )


def test_admin_device_reset_blocks_new_machine_until_last_lease_ends(
    devices, owner, administrator, session_factory
):
    _, public, _, lease = activate(devices, owner)
    old = lease_payload(devices, lease)
    reset = devices.reset_device(administrator.id, owner.tenant_id, "Bilgisayar değişimi")
    assert reset["device_id"] is None
    assert reset["device_blocked_until"] is not None
    assert_error(
        "DEVICE_RESET_PENDING",
        lambda: devices.challenge(owner.tenant_id, key_pair()[1], "activate"),
    )
    assert_error(
        "DEVICE_RESET_PENDING", lambda: devices.challenge(owner.tenant_id, public, "renew")
    )
    repeated = devices.reset_device(administrator.id, owner.tenant_id, "Yinelenen operatör isteği")
    assert repeated["device_blocked_until"] == reset["device_blocked_until"]
    with session_factory() as session:
        row = session.get(Entitlement, owner.tenant_id)
        assert int(row.device_blocked_until.timestamp()) == old["expires_at"]
        assert session.get(LicenseDevice, UUID(old["device_id"])).revoked_at is not None


def test_device_change_after_old_lease_expiry_preserves_trial_and_flow_quota(
    devices, owner, administrator, session_factory
):
    trials = TrialService(session_factory)
    for _ in range(3):
        trials.reserve_for_desktop(owner.tenant_id, uuid4())
    original = trials.status(owner.tenant_id)
    private, public, _, first_lease = activate(devices, owner)
    stale_challenge = devices.challenge(owner.tenant_id, public, "renew")
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(LicenseDevice)
            .where(LicenseDevice.tenant_id == owner.tenant_id)
            .values(lease_expires_at=now - timedelta(seconds=1))
        )
    devices.reset_device(administrator.id, owner.tenant_id, "Süresi bitmiş cihaz değişimi")
    _, _, _, second_lease = activate(devices, owner)
    first, second = lease_payload(devices, first_lease), lease_payload(devices, second_lease)
    assert first["device_id"] != second["device_id"]
    assert first["flow_ids"] == second["flow_ids"]
    current = trials.status(owner.tenant_id)
    assert current["started_at"] == original["started_at"]
    assert current["expires_at"] == original["expires_at"]
    assert current["flows_used"] == 3
    assert_error(
        "DEVICE_ALREADY_BOUND",
        lambda: devices.renew(
            owner.tenant_id, UUID(stale_challenge["challenge_id"]), proof(private, stale_challenge)
        ),
    )
    with session_factory() as session:
        audit = session.scalar(
            select(IdentityAudit).where(
                IdentityAudit.user_id == owner.id, IdentityAudit.action == "DEVICE_RESET"
            )
        )
        assert audit.actor_id == administrator.id
        assert audit.reason == "Süresi bitmiş cihaz değişimi"


def test_retired_device_key_cannot_reactivate_after_reset(
    devices, owner, administrator, session_factory
):
    _, public, _, _ = activate(devices, owner)
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(LicenseDevice)
            .where(LicenseDevice.tenant_id == owner.tenant_id)
            .values(lease_expires_at=now - timedelta(seconds=1))
        )
    devices.reset_device(administrator.id, owner.tenant_id, "Anahtar kaybı sonrası kurtarma")
    assert_error(
        "DEVICE_KEY_RETIRED", lambda: devices.challenge(owner.tenant_id, public, "activate")
    )


def test_customer_cannot_reset_device(devices, owner):
    activate(devices, owner)
    assert_error(
        "ADMIN_REQUIRED",
        lambda: devices.reset_device(owner.id, owner.tenant_id, "Yetkisiz sıfırlama"),
    )


def test_database_blocks_cross_tenant_challenge_device_reference(devices, owner, session_factory):
    _, public, _, lease = activate(devices, owner)
    payload = lease_payload(devices, lease)
    other = account(session_factory)
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.add(
            DeviceChallenge(
                id=uuid4(),
                tenant_id=other.tenant_id,
                public_key=public,
                purpose="renew",
                device_id=UUID(payload["device_id"]),
                message="invalid cross-tenant relation",
                created_at=now,
                expires_at=now + timedelta(minutes=2),
            )
        )
        session.flush()


def test_database_unique_constraint_blocks_second_active_device(devices, owner, session_factory):
    activate(devices, owner)
    _, public = key_pair()
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(
            LicenseDevice(
                id=uuid4(),
                tenant_id=owner.tenant_id,
                public_key=public,
                key_thumbprint=thumbprint(public),
            )
        )
        session.flush()


@pytest.mark.parametrize("public", ["", "A" * 42, "A" * 43 + "=", "+" * 43])
def test_device_public_key_encoding_is_strict(devices, owner, public):
    assert_error(
        "INVALID_DEVICE_PROOF", lambda: devices.challenge(owner.tenant_id, public, "activate")
    )
