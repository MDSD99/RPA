"""Customer execution resources; request services never inherit system scope."""

from contextlib import ExitStack
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.secrets.service import SecretService
from app.services.execution_service import ExecutionService
from app.services.flow_service import FlowService
from app.services.management_service import ManagementService
from app.services.schedule_service import ScheduleService
from app.web.runtime import WebServices
from app.workers.runtime import WorkflowWorkerPool
from app.workers.scheduler import SchedulerRunner
from app.workflows.registered import build_registry
from app.workflows.registry import WorkflowRegistry


@dataclass(frozen=True)
class CustomerRuntime:
    factory: sessionmaker[Session]
    registry: WorkflowRegistry
    scheduler: SchedulerRunner
    pool: WorkflowWorkerPool
    workers_enabled: bool

    def scoped(self, tenant_id: UUID) -> WebServices:
        return WebServices(
            ExecutionService(self.factory, tenant_id=tenant_id),
            ManagementService(self.factory, tenant_id=tenant_id),
            FlowService(self.factory, tenant_id=tenant_id),
            ScheduleService(self.factory, tenant_id=tenant_id),
            self.scheduler,
            (),
            self.registry,
            self.workers_enabled,
        )


def build_customer_runtime(
    factory: sessionmaker[Session],
    resources: ExitStack,
    *,
    start_workers: bool,
    external_workers: bool = False,
    secret_key: str | None = None,
) -> CustomerRuntime:
    vault = SecretService(factory, secret_key=secret_key) if secret_key else None
    registry = build_registry(factory, resolver_factory=vault.resolver if vault else None)
    pool = WorkflowWorkerPool(
        ExecutionService(factory, system=True), ManagementService(factory, system=True), registry
    )
    resources.callback(pool.stop)
    scheduler = SchedulerRunner(ScheduleService(factory, system=True), enabled=start_workers)
    resources.callback(scheduler.stop)
    if start_workers:
        pool.start()
        scheduler.start()
    return CustomerRuntime(factory, registry, scheduler, pool, start_workers or external_workers)
