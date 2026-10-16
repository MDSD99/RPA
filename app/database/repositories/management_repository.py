"""Read queries for the management application and registered workflow workers."""

from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.states import JobStatus, TransactionStatus
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import AuditLog, Checkpoint, Error, Job, Transaction
from app.database.schema import schema_is_current
from app.entitlements.access import eligible_tenant


class ManagementRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def healthy(self) -> bool:
        tables = [
            "rpa_jobs",
            "rpa_transactions",
            "rpa_errors",
            "rpa_checkpoints",
            "rpa_audit_log",
            "rpa_flows",
            "rpa_flow_versions",
            "rpa_flow_runs",
            "rpa_flow_schedules",
        ]
        ready = all(
            self.session.scalar(select(func.to_regclass(table))) is not None for table in tables
        )
        return ready and schema_is_current(self.session)

    def counts(self) -> dict[str, int]:
        jobs = dict(
            self.session.execute(
                select(Job.status, func.count())
                .where(self.scope.predicate(Job))
                .group_by(Job.status)
            ).all()
        )
        transactions = dict(
            self.session.execute(
                select(Transaction.status, func.count())
                .where(self.scope.predicate(Transaction))
                .group_by(Transaction.status)
            ).all()
        )
        return {
            "jobs": sum(jobs.values()),
            "pending_jobs": jobs.get(JobStatus.PENDING, 0),
            "running_jobs": jobs.get(JobStatus.RUNNING, 0),
            "transactions": sum(transactions.values()),
            **{status.value.lower(): transactions.get(status, 0) for status in TransactionStatus},
            "reconciliation_required": self.session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(
                    self.scope.predicate(Transaction), Transaction.reconciliation_required.is_(True)
                )
            ),
        }

    def jobs(self, *, status=None, q=None, limit=50, offset=0) -> tuple[list[Job], int]:
        filters = [self.scope.predicate(Job)]
        if status is not None:
            filters.append(Job.status == status)
        if q:
            filters.append(
                or_(
                    Job.job_id.icontains(q, autoescape=True),
                    Job.workflow_name.icontains(q, autoescape=True),
                )
            )
        total = self.session.scalar(select(func.count()).select_from(Job).where(*filters))
        items = list(
            self.session.scalars(
                select(Job)
                .where(*filters)
                .order_by(Job.created_at.desc(), Job.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def transactions(
        self,
        *,
        job_id: UUID | None = None,
        status=None,
        reconciliation_required=None,
        q=None,
        limit=50,
        offset=0,
    ) -> tuple[list[Transaction], int]:
        filters = [self.scope.predicate(Transaction)]
        if job_id is not None:
            filters.append(Transaction.job_id == job_id)
        if status is not None:
            filters.append(Transaction.status == status)
        if reconciliation_required is not None:
            filters.append(Transaction.reconciliation_required.is_(reconciliation_required))
        if q:
            filters.append(
                or_(
                    Transaction.record_id.icontains(q, autoescape=True),
                    Transaction.workflow_name.icontains(q, autoescape=True),
                    Transaction.idempotency_key.icontains(q, autoescape=True),
                )
            )
        total = self.session.scalar(select(func.count()).select_from(Transaction).where(*filters))
        items = list(
            self.session.scalars(
                select(Transaction)
                .where(*filters)
                .order_by(Transaction.created_at.desc(), Transaction.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def history(self, transaction_id: UUID) -> tuple[list, list, list]:
        checkpoints = list(
            self.session.scalars(
                select(Checkpoint)
                .where(
                    self.scope.predicate(Checkpoint), Checkpoint.transaction_id == transaction_id
                )
                .order_by(Checkpoint.updated_at.desc())
                .limit(100)
            )
        )
        errors = list(
            self.session.scalars(
                select(Error)
                .where(self.scope.predicate(Error), Error.transaction_id == transaction_id)
                .order_by(Error.created_at.desc())
                .limit(100)
            )
        )
        audit = list(
            self.session.scalars(
                select(AuditLog)
                .where(self.scope.predicate(AuditLog), AuditLog.transaction_id == transaction_id)
                .order_by(AuditLog.created_at.desc())
                .limit(100)
            )
        )
        return checkpoints, errors, audit

    def running_demo_job_ids(self, limit: int = 100) -> list[UUID]:
        return self.running_workflow_job_ids(("infrastructure_demo",), limit=limit)

    def running_workflow_job_ids(
        self, workflow_names: tuple[str, ...], limit: int = 100
    ) -> list[UUID]:
        if not workflow_names:
            return []
        # Quarantined or not-yet-due jobs must not occupy every slot in this
        # bounded scheduler batch. The database clock makes delayed retries
        # and expired leases eligible again on subsequent polls.
        actionable = (
            select(Transaction.id)
            .where(
                Transaction.tenant_id == Job.tenant_id,
                Transaction.job_id == Job.id,
                or_(
                    and_(
                        Transaction.status.in_(
                            [TransactionStatus.PENDING, TransactionStatus.RETRY]
                        ),
                        Transaction.reconciliation_required.is_(False),
                        Transaction.attempt_count < Transaction.max_attempts,
                        Transaction.available_at <= func.clock_timestamp(),
                    ),
                    and_(
                        Transaction.status == TransactionStatus.PROCESSING,
                        Transaction.lease_expires_at <= func.clock_timestamp(),
                    ),
                ),
            )
            .exists()
        )
        unfinished = (
            select(Transaction.id)
            .where(
                Transaction.tenant_id == Job.tenant_id,
                Transaction.job_id == Job.id,
                Transaction.status.in_(
                    [
                        TransactionStatus.PENDING,
                        TransactionStatus.PROCESSING,
                        TransactionStatus.RETRY,
                    ]
                ),
            )
            .exists()
        )
        expired_ownership = (
            select(Transaction.id)
            .where(
                Transaction.tenant_id == Job.tenant_id,
                Transaction.job_id == Job.id,
                Transaction.status == TransactionStatus.PROCESSING,
                Transaction.lease_expires_at <= func.clock_timestamp(),
            )
            .exists()
        )
        return list(
            self.session.scalars(
                select(Job.id)
                .where(
                    self.scope.predicate(Job),
                    or_(eligible_tenant(Job.tenant_id), expired_ownership),
                    Job.workflow_name.in_(workflow_names),
                    Job.status == JobStatus.RUNNING,
                    or_(actionable, ~unfinished),
                )
                .order_by(Job.created_at, Job.id)
                .limit(limit)
            )
        )
