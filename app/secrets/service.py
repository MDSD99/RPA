"""Authenticated encryption binds each value to its tenant, alias, kind and version."""

import base64
import json
import secrets
from collections.abc import Callable
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import ConfigurationError
from app.core.exceptions import InvalidTransitionError, NotFoundError, ValidationError
from app.database.unit_of_work import DatabaseUnitOfWork
from app.entitlements.access import assert_account
from app.entitlements.repository import EntitlementRepository
from app.secrets.domain import reference, validate_values
from app.secrets.models import Credential
from app.secrets.repository import CredentialRepository
from app.workflows.registry import WorkflowFailure


def metadata(row: Credential) -> dict[str, object]:
    return {
        "reference": row.reference,
        "kind": row.kind,
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
    }


class SecretService:
    def __init__(self, factory: sessionmaker[Session], *, secret_key: str) -> None:
        self.uow = DatabaseUnitOfWork(factory)
        try:
            Fernet(secret_key.encode("ascii"))
            master = base64.urlsafe_b64decode(secret_key)
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise ConfigurationError("Vault encryption key is invalid") from None
        raw = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"MultiRPA vault v1"
        ).derive(master)
        self._cipher = AESGCM(raw)

    @staticmethod
    def _aad(tenant_id: UUID, ref: str, kind: str, version: int) -> bytes:
        return f"multirpa-vault-v1\0{tenant_id}\0{ref}\0{kind}\0{version}".encode()

    def list(self, tenant_id: UUID) -> dict[str, object]:
        with self.uow.transaction() as session:
            assert_account(EntitlementRepository(session).owner(tenant_id))
            return {
                "items": [metadata(row) for row in CredentialRepository(session, tenant_id).list()]
            }

    def put(
        self,
        tenant_id: UUID,
        actor_id: UUID,
        ref: str,
        kind: str,
        values: dict[str, str],
        expected_version: int | None,
    ) -> dict[str, object]:
        ref, values = reference(ref), validate_values(kind, values)
        if expected_version is not None and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise ValidationError("Credential version is invalid")
        with self.uow.transaction() as session:
            entitlement = EntitlementRepository(session)
            owner = entitlement.lock(tenant_id)
            assert_account(owner)
            if owner.id != actor_id:
                raise NotFoundError("Account not found")
            repo = CredentialRepository(session, tenant_id)
            row = repo.get(ref, lock=True)
            absent = row is None or row.deleted_at is not None
            if (absent and expected_version is not None) or (
                not absent
                and row is not None
                and (expected_version != row.version or kind != row.kind)
            ):
                raise InvalidTransitionError("Credential version or kind changed")
            if absent and len(repo.list()) >= 100:
                raise ValidationError("Credential limit reached")
            if row is None:
                row = Credential(
                    tenant_id=tenant_id, reference=ref, kind=kind, version=1, ciphertext=""
                )
                repo.add(row)
            else:
                row.version += 1
                row.kind = kind
                row.deleted_at = None
            nonce = secrets.token_bytes(12)
            payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
            encrypted = self._cipher.encrypt(
                nonce, payload, self._aad(tenant_id, ref, kind, row.version)
            )
            row.ciphertext = base64.urlsafe_b64encode(nonce + encrypted).decode()
            row.updated_at = entitlement.now()
            entitlement.audit(
                owner, "CREDENTIAL_STORED", actor_id=actor_id, reason=f"{ref}:{kind}:v{row.version}"
            )
            session.flush()
            return metadata(row)

    def delete(self, tenant_id: UUID, actor_id: UUID, ref: str, expected_version: int) -> None:
        ref = reference(ref)
        if type(expected_version) is not int or expected_version < 1:
            raise ValidationError("Credential version is invalid")
        with self.uow.transaction() as session:
            entitlement = EntitlementRepository(session)
            owner = entitlement.lock(tenant_id)
            assert_account(owner)
            if owner.id != actor_id:
                raise NotFoundError("Account not found")
            repo = CredentialRepository(session, tenant_id)
            row = repo.get(ref, lock=True)
            if row is None or row.deleted_at is not None:
                raise NotFoundError("Credential not found")
            if row.version != expected_version:
                raise InvalidTransitionError("Credential version changed")
            # Keep a tombstone so delete/recreate never reuses an encryption version.
            row.deleted_at = row.updated_at = entitlement.now()
            row.ciphertext = ""
            entitlement.audit(
                owner,
                "CREDENTIAL_DELETED",
                actor_id=actor_id,
                reason=f"{ref}:{row.kind}:v{row.version}",
            )

    def resolver(self, tenant_id: UUID) -> Callable[[str, str], dict[str, str]]:
        def resolve(ref: str, kind: str) -> dict[str, str]:
            ref = reference(ref)
            with self.uow.transaction() as session:
                assert_account(EntitlementRepository(session).owner(tenant_id))
                row = CredentialRepository(session, tenant_id).get(ref)
                if row is None or row.deleted_at is not None or row.kind != kind:
                    raise WorkflowFailure("FLOW_CREDENTIAL_UNAVAILABLE")
                try:
                    raw = base64.urlsafe_b64decode(row.ciphertext)
                    if base64.urlsafe_b64encode(raw).decode() != row.ciphertext or len(raw) < 28:
                        raise ValueError
                    payload = self._cipher.decrypt(
                        raw[:12], raw[12:], self._aad(tenant_id, ref, kind, row.version)
                    )
                    return validate_values(kind, json.loads(payload))
                except (ValueError, TypeError, InvalidTag, ValidationError):
                    raise WorkflowFailure("FLOW_CREDENTIAL_UNAVAILABLE") from None

        return resolve
