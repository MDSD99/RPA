"""Versioned user flows and atomically published, idempotent execution runs."""

import json
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.core.validation import validate_json, validate_uuid
from app.database.repositories.flow_repository import FlowRepository
from app.database.repositories.job_repository import JobRepository
from app.database.unit_of_work import DatabaseUnitOfWork
from app.entitlements.access import assert_access, reserve_flow
from app.services.flow_runs import publish_flow_run
from app.services.management_service import job_data
from app.workflows.flow_contract import VISUAL_WORKFLOW as VISUAL_WORKFLOW
from app.workflows.flow_contract import configuration_digest, validate_flow_version
from app.workflows.flow_engine import MAX_VARIABLE_BYTES, validate_graph
from app.workflows.flow_transfer import export_document, import_document, validate_metadata


def _flow_data(flow, revision, *, include_graph=True) -> dict:
    result = {
        "id": str(flow.id),
        "name": flow.name,
        "description": flow.description,
        "version": flow.version,
        "node_count": revision.node_count,
        "created_at": flow.created_at.isoformat(),
        "updated_at": flow.updated_at.isoformat(),
    }
    if include_graph:
        result["graph"] = revision.graph
    return result


class FlowService:
    """Manage flow versions and runs through an explicit transaction component."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        tenant_id: UUID = LEGACY_TENANT_ID,
        system: bool = False,
    ) -> None:
        self.scope = TenantScope(tenant_id, system=system)
        self.unit_of_work = DatabaseUnitOfWork(session_factory)

    def list_flows(self, *, limit=100, offset=0) -> dict:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValidationError("Flow page size must be between 1 and 500")
        if type(offset) is not int or offset < 0:
            raise ValidationError("Flow page offset must be nonnegative")
        with self.unit_of_work.transaction() as session:
            rows, total = FlowRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).list(limit=limit, offset=offset)
            return {
                "items": [
                    _flow_data(flow, revision, include_graph=False) for flow, revision in rows
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def create_flow(self, *, name: str, description: str = "", graph: dict) -> dict:
        name, description = validate_metadata(name, description)
        graph = validate_graph(graph)
        with self.unit_of_work.transaction() as session:
            assert_access(session, self.scope.tenant_id)
            flow, revision = FlowRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).create(name, description, graph)
            reserve_flow(session, self.scope.tenant_id, flow.id)
            return _flow_data(flow, revision)

    def import_flow(self, document: object) -> dict[str, object]:
        return self.create_flow(**import_document(document))

    def export_flow(self, flow_id: UUID | str) -> dict[str, object]:
        flow = self.get_flow(flow_id)
        return {
            "document": export_document(flow),
            "filename": f"MultiRPA-flow-{flow['id']}-v{flow['version']}.multirpa.json",
        }

    def get_flow(self, flow_id: UUID | str) -> dict:
        with self.unit_of_work.transaction() as session:
            repository = FlowRepository(session, self.scope.tenant_id, system=self.scope.system)
            flow = repository.get(validate_uuid(flow_id))
            if flow is None:
                raise NotFoundError("Flow not found")
            revision = repository.version(flow.id, flow.version)
            if revision is None:
                raise NotFoundError("Flow revision not found")
            return _flow_data(flow, revision)

    def update_flow(
        self,
        flow_id: UUID | str,
        *,
        name: str,
        description: str = "",
        graph: dict,
        expected_version: int,
    ) -> dict:
        name, description = validate_metadata(name, description)
        graph = validate_graph(graph)
        expected_version = validate_flow_version(expected_version)
        with self.unit_of_work.transaction() as session:
            repository = FlowRepository(session, self.scope.tenant_id, system=self.scope.system)
            flow = repository.get(validate_uuid(flow_id), for_update=True)
            if flow is None:
                raise NotFoundError("Flow not found")
            assert_access(session, flow.tenant_id)
            if flow.version != expected_version:
                raise InvalidTransitionError("Flow revision changed; reload before saving")
            flow.name = name
            flow.description = description
            flow.version += 1
            flow.updated_at = JobRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).now()
            revision = repository.append_version(flow, graph)
            return _flow_data(flow, revision)

    def start_run(
        self,
        flow_id: UUID | str,
        *,
        request_id: UUID | str,
        version: int,
        inputs: dict | None = None,
        max_attempts: int = 3,
    ) -> dict:
        flow_id, request_id = validate_uuid(flow_id), validate_uuid(request_id)
        version = validate_flow_version(version)
        inputs = validate_json({} if inputs is None else inputs)
        if len(json.dumps(inputs, ensure_ascii=False).encode("utf-8")) > MAX_VARIABLE_BYTES:
            raise ValidationError("Flow run inputs must not exceed 12 KiB")
        inputs_digest = configuration_digest(inputs)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValidationError("max_attempts must be between 1 and 100")
        with self.unit_of_work.transaction() as session:
            return publish_flow_run(
                session,
                tenant_id=self.scope.tenant_id,
                flow_id=flow_id,
                request_id=request_id,
                version=version,
                inputs=inputs,
                inputs_digest=inputs_digest,
                max_attempts=max_attempts,
            )

    def execution_spec(self, job_id: UUID | str) -> dict:
        with self.unit_of_work.transaction() as session:
            repository = FlowRepository(session, self.scope.tenant_id, system=self.scope.system)
            run = repository.run(validate_uuid(job_id))
            if run is None:
                raise NotFoundError("Flow run not found")
            revision = repository.version(run.flow_id, run.version)
            if revision is None:
                raise NotFoundError("Flow revision not found")
            return {
                "flow_id": str(run.flow_id),
                "version": run.version,
                "name": revision.name,
                "graph": revision.graph,
                "inputs": run.inputs,
            }

    def list_runs(self, flow_id: UUID | str, *, limit=50, offset=0) -> dict:
        flow_id = validate_uuid(flow_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValidationError("Run page size must be between 1 and 500")
        if type(offset) is not int or offset < 0:
            raise ValidationError("Run page offset must be nonnegative")
        with self.unit_of_work.transaction() as session:
            repository = FlowRepository(session, self.scope.tenant_id, system=self.scope.system)
            if repository.get(flow_id) is None:
                raise NotFoundError("Flow not found")
            rows, total = repository.list_runs(flow_id, limit=limit, offset=offset)
            return {
                "items": [
                    {
                        **job_data(job),
                        "flow_id": str(run.flow_id),
                        "version": run.version,
                        "name": name,
                    }
                    for run, job, name in rows
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
