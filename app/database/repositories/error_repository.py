"""Persist bounded error codes, never raw exception messages or stack traces."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import Error, Transaction


class ErrorRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def record(self, transaction: Transaction, code: str) -> Error:
        self.scope.require_owner(transaction)
        error = Error(
            tenant_id=transaction.tenant_id,
            job_id=transaction.job_id,
            transaction_id=transaction.id,
            error_type=code,
            error_message=code,
        )
        self.session.add(error)
        return error
