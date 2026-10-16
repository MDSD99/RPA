"""Persistence for editable flow metadata and append-only execution snapshots."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import Flow, FlowRun, FlowVersion, Job


class FlowRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def get(self, flow_id: UUID, *, for_update: bool = False) -> Flow | None:
        statement = select(Flow).where(self.scope.predicate(Flow), Flow.id == flow_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def version(self, flow_id: UUID, version: int) -> FlowVersion | None:
        return self.session.scalar(
            select(FlowVersion).where(
                self.scope.predicate(FlowVersion),
                FlowVersion.flow_id == flow_id,
                FlowVersion.version == version,
            )
        )

    def list(self, *, limit: int, offset: int) -> tuple[list[tuple[Flow, FlowVersion]], int]:
        total = self.session.scalar(
            select(func.count()).select_from(Flow).where(self.scope.predicate(Flow))
        )
        rows = self.session.execute(
            select(Flow, FlowVersion)
            .join(
                FlowVersion,
                (FlowVersion.tenant_id == Flow.tenant_id)
                & (FlowVersion.flow_id == Flow.id)
                & (FlowVersion.version == Flow.version),
            )
            .where(self.scope.predicate(Flow))
            .order_by(Flow.updated_at.desc(), Flow.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return rows, total

    def create(self, name: str, description: str, graph: dict) -> tuple[Flow, FlowVersion]:
        flow = Flow(tenant_id=self.scope.tenant_id, name=name, description=description, version=1)
        self.session.add(flow)
        self.session.flush()
        return flow, self.append_version(flow, graph)

    def append_version(self, flow: Flow, graph: dict) -> FlowVersion:
        self.scope.require_owner(flow)
        revision = FlowVersion(
            tenant_id=flow.tenant_id,
            flow_id=flow.id,
            version=flow.version,
            name=flow.name,
            description=flow.description,
            graph=graph,
            node_count=len(graph["nodes"]),
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def run(self, job_id: UUID) -> FlowRun | None:
        return self.session.scalar(
            select(FlowRun).where(self.scope.predicate(FlowRun), FlowRun.job_id == job_id)
        )

    def list_runs(self, flow_id: UUID, *, limit: int, offset: int):
        total = self.session.scalar(
            select(func.count())
            .select_from(FlowRun)
            .where(self.scope.predicate(FlowRun), FlowRun.flow_id == flow_id)
        )
        rows = self.session.execute(
            select(FlowRun, Job, FlowVersion.name)
            .join(Job, (Job.tenant_id == FlowRun.tenant_id) & (Job.id == FlowRun.job_id))
            .join(
                FlowVersion,
                (FlowVersion.tenant_id == FlowRun.tenant_id)
                & (FlowVersion.flow_id == FlowRun.flow_id)
                & (FlowVersion.version == FlowRun.version),
            )
            .where(self.scope.predicate(FlowRun), FlowRun.flow_id == flow_id)
            .order_by(FlowRun.created_at.desc(), FlowRun.job_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return rows, total

    def add_run(self, **values) -> FlowRun:
        run = FlowRun(tenant_id=self.scope.tenant_id, **values)
        self.session.add(run)
        self.session.flush()
        return run
