"""Minimal state audit; excludes business payloads and identifiers from messages."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import AuditLog, Job, Transaction


class AuditRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def record(
        self,
        job: Job,
        action: str,
        *,
        transaction: Transaction | None = None,
        old_status: str | None = None,
        new_status: str | None = None,
    ) -> None:
        self.scope.require_owner(job, *([transaction] if transaction else []))
        entry = AuditLog(
            tenant_id=job.tenant_id,
            job_id=job.id,
            transaction_id=transaction.id if transaction else None,
            record_id=transaction.record_id if transaction else None,
            action=action,
            old_value={"status": old_status} if old_status is not None else None,
            new_value={"status": new_status} if new_status is not None else None,
        )
        self.session.add(entry)
