"""One device per tenant with replay-proof possession checks and signed short leases."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.database.unit_of_work import DatabaseUnitOfWork
from app.entitlements.access import active_window, assert_account
from app.entitlements.crypto import LeaseSigner, canonical, thumbprint, verify
from app.entitlements.domain import EntitlementError, timestamp
from app.entitlements.models import DeviceChallenge, Entitlement, LicenseDevice
from app.entitlements.repository import EntitlementRepository
from app.entitlements.service import require_reason, status_in_session


class DeviceLicenseService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        signing_private_key: str,
        issuer: str = "multirpa",
    ) -> None:
        self.uow = DatabaseUnitOfWork(session_factory)
        self.signer = LeaseSigner(signing_private_key)
        self.issuer = issuer

    @property
    def public_key(self) -> str:
        return self.signer.public_key

    def challenge(self, tenant_id: UUID, public_key: str, purpose: str) -> dict[str, object]:
        key_thumbprint = thumbprint(public_key)
        if purpose not in {"activate", "renew"}:
            raise EntitlementError("INVALID_CHALLENGE_PURPOSE")
        with self.uow.transaction() as session:
            repo = EntitlementRepository(session)
            assert_account(repo.lock(tenant_id))
            now = repo.now()
            state = repo.state(tenant_id)
            active_window(state, now)
            self._available(state, now)
            device = repo.device(tenant_id)
            if device is not None and device.public_key != public_key:
                raise EntitlementError("DEVICE_ALREADY_BOUND")
            if purpose == "renew" and device is None:
                raise EntitlementError("DEVICE_NOT_BOUND")
            if device is None and repo.key_seen(key_thumbprint):
                raise EntitlementError("DEVICE_KEY_RETIRED")
            if repo.pending_challenges(tenant_id, now) >= 10:
                raise EntitlementError("TOO_MANY_CHALLENGES")
            challenge_id = uuid4()
            expires = now + timedelta(minutes=2)
            message = canonical(
                {
                    "version": 1,
                    "purpose": purpose,
                    "id": str(challenge_id),
                    "tenant_id": str(tenant_id),
                    "device_id": str(device.id) if device else None,
                    "key_thumbprint": key_thumbprint,
                    "nonce": secrets.token_urlsafe(32),
                    "expires_at": int(expires.timestamp()),
                }
            )
            repo.add(
                DeviceChallenge(
                    id=challenge_id,
                    tenant_id=tenant_id,
                    public_key=public_key,
                    purpose=purpose,
                    device_id=device.id if device else None,
                    message=message,
                    created_at=now,
                    expires_at=expires,
                )
            )
            return {
                "challenge_id": str(challenge_id),
                "message": message,
                "expires_at": timestamp(expires),
            }

    @staticmethod
    def _available(state: Entitlement | None, now: datetime) -> None:
        if (
            state is not None
            and state.device_blocked_until is not None
            and state.device_blocked_until > now
        ):
            raise EntitlementError("DEVICE_RESET_PENDING")

    def activate(self, tenant_id: UUID, challenge_id: UUID, signature: str) -> dict[str, str]:
        return self._issue(tenant_id, challenge_id, signature, "activate")

    def renew(self, tenant_id: UUID, challenge_id: UUID, signature: str) -> dict[str, str]:
        return self._issue(tenant_id, challenge_id, signature, "renew")

    def _issue(
        self, tenant_id: UUID, challenge_id: UUID, signature: str, purpose: str
    ) -> dict[str, str]:
        with self.uow.transaction() as session:
            repo = EntitlementRepository(session)
            owner = repo.lock(tenant_id)
            assert_account(owner)
            now = repo.now()
            state = repo.state(tenant_id)
            entitlement_expiry, _, _ = active_window(state, now)
            self._available(state, now)
            proof = repo.challenge(tenant_id, challenge_id)
            if (
                proof is None
                or proof.purpose != purpose
                or proof.consumed_at is not None
                or proof.expires_at <= now
            ):
                raise EntitlementError("INVALID_DEVICE_CHALLENGE")
            verify(proof.public_key, signature, proof.message)
            device = repo.device(tenant_id)
            if device is not None and device.public_key != proof.public_key:
                raise EntitlementError("DEVICE_ALREADY_BOUND")
            if purpose == "renew" and (device is None or proof.device_id != device.id):
                raise EntitlementError("DEVICE_NOT_BOUND")
            if device is None:
                if proof.device_id is not None or repo.key_seen(thumbprint(proof.public_key)):
                    raise EntitlementError("DEVICE_KEY_RETIRED")
                device = LicenseDevice(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    public_key=proof.public_key,
                    key_thumbprint=thumbprint(proof.public_key),
                    activated_at=now,
                )
                repo.add(device)
                repo.audit(owner, "DEVICE_ACTIVATED")
            proof.consumed_at = now
            # Integers avoid locale/float ambiguities across Python and Electron.
            expires_seconds = int(min(now + timedelta(seconds=60), entitlement_expiry).timestamp())
            if expires_seconds <= int(now.timestamp()):
                raise EntitlementError("TRIAL_EXPIRED")
            expires_at = datetime.fromtimestamp(expires_seconds, UTC)
            valid_for_ms = int((expires_at - now).total_seconds() * 1000)
            if valid_for_ms <= 0:
                raise EntitlementError("TRIAL_EXPIRED")
            device.lease_expires_at = max(device.lease_expires_at or expires_at, expires_at)
            return self.signer.sign(
                {
                    "version": 1,
                    "issuer": self.issuer,
                    "tenant_id": str(tenant_id),
                    "device_id": str(device.id),
                    "key_thumbprint": device.key_thumbprint,
                    "issued_at": int(now.timestamp()),
                    "expires_at": expires_seconds,
                    "valid_for_ms": valid_for_ms,
                    "entitlement_expires_at": int(entitlement_expiry.timestamp()),
                    "lease_id": str(uuid4()),
                    "flow_ids": repo.flow_ids(tenant_id),
                }
            )

    def reset_device(self, actor_id: UUID, tenant_id: UUID, reason: str) -> dict[str, object]:
        reason = require_reason(reason)
        with self.uow.transaction() as session:
            repo = EntitlementRepository(session)
            repo.administrator(actor_id)
            owner = repo.lock(tenant_id)
            now = repo.now()
            state = repo.state(tenant_id, create=True)
            assert state is not None
            device = repo.device(tenant_id)
            if device is not None:
                device.revoked_at = now
                state.device_blocked_until = max(now, device.lease_expires_at or now)
                repo.audit(owner, "DEVICE_RESET", actor_id=actor_id, reason=reason)
                session.flush()
            return status_in_session(repo, tenant_id)
