"""PostgreSQL schedule locks, idempotent creation and bounded eligible dispatch."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.states import JobStatus
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import AuditLog, FlowSchedule, FlowVersion, Job, Transaction


class ScheduleRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def get(self, schedule_id: UUID, *, for_update=False):
        statement = select(FlowSchedule).where(
            self.scope.predicate(FlowSchedule), FlowSchedule.id == schedule_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def create_once(self, **values):
        inserted = self.session.scalar(
            insert(FlowSchedule)
            .values(tenant_id=self.scope.tenant_id, **values)
            .on_conflict_do_nothing(index_elements=[FlowSchedule.tenant_id, FlowSchedule.id])
            .returning(FlowSchedule.id)
        )
        return self.get(values["id"]), inserted is not None

    def list(self, *, limit, offset, flow_id=None):
        filters = [self.scope.predicate(FlowSchedule)]
        if flow_id is not None:
            filters.append(FlowSchedule.flow_id == flow_id)
        total = self.session.scalar(select(func.count()).select_from(FlowSchedule).where(*filters))
        rows = self.session.execute(
            select(FlowSchedule, FlowVersion.name, Job.status)
            .join(
                FlowVersion,
                (FlowVersion.tenant_id == FlowSchedule.tenant_id)
                & (FlowVersion.flow_id == FlowSchedule.flow_id)
                & (FlowVersion.version == FlowSchedule.version),
            )
            .outerjoin(
                Job,
                (Job.tenant_id == FlowSchedule.tenant_id) & (Job.id == FlowSchedule.last_job_id),
            )
            .where(*filters)
            .order_by(FlowSchedule.created_at.desc(), FlowSchedule.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return rows, total

    def details(self, schedule):
        self.scope.require_owner(schedule)
        name = self.session.scalar(
            select(FlowVersion.name).where(
                FlowVersion.tenant_id == schedule.tenant_id,
                FlowVersion.flow_id == schedule.flow_id,
                FlowVersion.version == schedule.version,
            )
        )
        status = (
            self.session.scalar(
                select(Job.status).where(
                    Job.tenant_id == schedule.tenant_id, Job.id == schedule.last_job_id
                )
            )
            if schedule.last_job_id
            else None
        )
        return name, status

    def due(self, now, *, limit):
        active_previous_run = (
            select(Job.id)
            .where(
                Job.tenant_id == FlowSchedule.tenant_id,
                Job.id == FlowSchedule.last_job_id,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            .exists()
        )
        uncertain_previous_run = (
            select(Transaction.id)
            .where(
                Transaction.tenant_id == FlowSchedule.tenant_id,
                Transaction.job_id == FlowSchedule.last_job_id,
                Transaction.reconciliation_required.is_(True),
            )
            .exists()
        )
        # Eligibility precedes LIMIT: busy schedules cannot starve a later due
        # schedule. Multiple schedulers lock disjoint rows without waiting.
        return list(
            self.session.scalars(
                select(FlowSchedule)
                .where(
                    self.scope.predicate(FlowSchedule),
                    FlowSchedule.enabled.is_(True),
                    FlowSchedule.next_run_at <= now,
                    ~active_previous_run,
                    ~uncertain_previous_run,
                )
                .order_by(FlowSchedule.next_run_at, FlowSchedule.id)
                .limit(limit)
                .with_for_update(skip_locked=True, of=FlowSchedule)
            )
        )

    def audit(self, schedule, action, *, job_id=None, due_at=None):
        self.scope.require_owner(schedule)
        value = {"schedule_id": str(schedule.id), "revision": schedule.revision}
        if due_at is not None:
            value["due_at"] = due_at.isoformat()
        self.session.add(
            AuditLog(tenant_id=schedule.tenant_id, job_id=job_id, action=action, new_value=value)
        )
