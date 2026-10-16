"""Scoped credential metadata and row locks; transaction ownership stays outside."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.secrets.models import Credential


class CredentialRepository:
    def __init__(self, session: Session, tenant_id: UUID) -> None:
        self.session, self.tenant_id = session, tenant_id

    def get(self, ref: str, *, lock: bool = False) -> Credential | None:
        statement = select(Credential).where(
            Credential.tenant_id == self.tenant_id, Credential.reference == ref
        )
        return self.session.scalar(statement.with_for_update() if lock else statement)

    def list(self) -> list[Credential]:
        return list(
            self.session.scalars(
                select(Credential)
                .where(Credential.tenant_id == self.tenant_id, Credential.deleted_at.is_(None))
                .order_by(Credential.reference)
            )
        )

    def add(self, value: Credential) -> None:
        self.session.add(value)
