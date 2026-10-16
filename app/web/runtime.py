"""Own database and worker lifetimes outside the HTTP route layer."""

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, ExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import ConfigurationError, DatabaseSettings
from app.database.connection import create_session_factory
from app.services.execution_service import ExecutionService
from app.services.flow_service import VISUAL_WORKFLOW, FlowService
from app.services.management_service import ManagementService
from app.services.schedule_service import ScheduleService
from app.workers.demo import DemoWorkerPool
from app.workers.runtime import WorkflowWorkerPool
from app.workers.scheduler import SchedulerRunner
from app.workflows.registered import build_registry
from app.workflows.registry import WorkflowRegistry


@dataclass(frozen=True, slots=True)
class WebServices:
    execution: ExecutionService
    management: ManagementService
    flows: FlowService
    schedules: ScheduleService
    scheduler: SchedulerRunner
    worker_pools: tuple[DemoWorkerPool | WorkflowWorkerPool, ...]
    workflow_registry: WorkflowRegistry
    workers_enabled: bool

    def worker_snapshot(self) -> list[dict]:
        return [
            {"workflow_names": ["infrastructure_demo"], **entry}
            for pool in self.worker_pools
            for entry in pool.snapshot()
        ]


def build_lifespan(
    *,
    session_factory: sessionmaker[Session] | None,
    start_workers: bool,
    registry: WorkflowRegistry,
    custom_registry: bool,
    engine_factory: Callable[[DatabaseSettings], Engine],
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if os.environ.get("APP_ENV", "development") == "production":
            raise ConfigurationError("Web management requires development or test environment")
        # Every acquired resource gets a cleanup callback immediately. ExitStack
        # still attempts the remaining callbacks if one stop/dispose fails.
        with ExitStack() as resources:
            pools: list[DemoWorkerPool | WorkflowWorkerPool] = []
            factory = session_factory
            if factory is None:
                engine = engine_factory(DatabaseSettings.from_env())
                resources.callback(engine.dispose)
                factory = create_session_factory(engine)
            execution = ExecutionService(factory, retry_base_seconds=1, retry_max_seconds=10)
            management = ManagementService(factory)
            flows = FlowService(factory)
            schedules = ScheduleService(factory)
            active_registry = registry if custom_registry else build_registry(factory)
            pools.append(DemoWorkerPool(execution, management))
            resources.callback(pools[-1].stop)
            if active_registry.names():
                pools.append(
                    WorkflowWorkerPool(
                        ExecutionService(
                            factory, system=True, retry_base_seconds=1, retry_max_seconds=10
                        ),
                        ManagementService(factory, system=True),
                        active_registry,
                    )
                )
                resources.callback(pools[-1].stop)
            scheduler = SchedulerRunner(
                ScheduleService(factory, system=True),
                enabled=bool(start_workers and VISUAL_WORKFLOW in active_registry.names()),
            )
            resources.callback(scheduler.stop)
            services = WebServices(
                execution,
                management,
                flows,
                schedules,
                scheduler,
                tuple(pools),
                active_registry,
                bool(start_workers),
            )
            application.state.services = services
            # Retain the runtime inspection contract used by operations and tests.
            application.state.scheduler = scheduler
            application.state.worker_pools = pools
            if not management.healthy():
                raise ConfigurationError("Database schema is not ready; apply migrations first")
            if start_workers:
                for pool in pools:
                    pool.start()
                scheduler.start()
            yield

    return lifespan
