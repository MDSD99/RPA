"""Job persistence and short-lived coordination locks."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import Job


class JobRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def now(self) -> datetime:
        # transaction_timestamp()/now() would be stale after a lock wait.
        return self.session.scalar(select(func.clock_timestamp()))

    def get(self, job_id: UUID, *, for_update: bool = False) -> Job | None:
        statement = select(Job).where(self.scope.predicate(Job), Job.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def create_once(self, job_id: str, workflow_name: str) -> tuple[Job, bool]:
        statement = (
            insert(Job)
            .values(tenant_id=self.scope.tenant_id, job_id=job_id, workflow_name=workflow_name)
            .on_conflict_do_nothing(index_elements=[Job.tenant_id, Job.job_id])
            .returning(Job)
        )
        result = self.session.scalar(statement)
        if result is not None:
            return result, True
        existing = self.session.scalar(
            # Creation always targets the specified tenant, including trusted
            # system callers. A cross-tenant read scope is not a write identity.
            select(Job)
            .where(Job.tenant_id == self.scope.tenant_id, Job.job_id == job_id)
            .with_for_update()
        )
        return existing, False
