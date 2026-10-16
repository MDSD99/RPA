"""Validated schedules and atomic, coalesced dispatch of immutable flow versions."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import (
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.core.validation import validate_json, validate_text, validate_uuid
from app.database.models import FlowSchedule
from app.database.repositories.flow_repository import FlowRepository
from app.database.repositories.job_repository import JobRepository
from app.database.repositories.schedule_repository import ScheduleRepository
from app.database.unit_of_work import DatabaseUnitOfWork
from app.entitlements.access import assert_access
from app.entitlements.domain import EntitlementError
from app.services.flow_runs import publish_flow_run
from app.workflows.flow_contract import configuration_digest, validate_flow_version
from app.workflows.flow_engine import MAX_VARIABLE_BYTES


def _date(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("Schedule time must be an ISO timestamp with timezone") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Schedule time must include a timezone")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValidationError("Schedule time is outside the supported range") from None


def _revision(value):
    if type(value) is not int or not 1 <= value <= 9_223_372_036_854_775_000:
        raise ValidationError("Schedule revision must be a positive integer")
    return value


def _configuration(
    *, name, flow_id, version, kind, next_run_at, interval_seconds, inputs, max_attempts, enabled
):
    name = validate_text(name, "name", 160).strip()
    if not name:
        raise ValidationError("Schedule name must not be empty")
    if kind not in ("once", "interval"):
        raise ValidationError("Schedule kind must be once or interval")
    if kind == "once":
        if interval_seconds is not None:
            raise ValidationError("One-time schedules must not have an interval")
    elif type(interval_seconds) is not int or not 60 <= interval_seconds <= 2_678_400:
        raise ValidationError("Schedule interval must be between 60 and 2678400 seconds")
    if type(enabled) is not bool:
        raise ValidationError("Schedule enabled must be a boolean")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
        raise ValidationError("max_attempts must be between 1 and 100")
    inputs = validate_json({} if inputs is None else inputs)
    if len(json.dumps(inputs, ensure_ascii=False).encode("utf-8")) > MAX_VARIABLE_BYTES:
        raise ValidationError("Schedule inputs must not exceed 12 KiB")
    due = _date(next_run_at)
    # Reserve room for the next interval instead of failing at datetime.max
    # during a future unattended dispatch.
    if due.year > 9998:
        raise ValidationError("Schedule time is outside the supported range")
    return dict(
        name=name,
        flow_id=validate_uuid(flow_id),
        version=validate_flow_version(version),
        kind=kind,
        next_run_at=due,
        interval_seconds=interval_seconds,
        inputs=inputs,
        max_attempts=max_attempts,
        enabled=enabled,
    )


def _creation_digest(values):
    values = dict(values)
    values["flow_id"] = str(values["flow_id"])
    values["next_run_at"] = values["next_run_at"].isoformat()
    return configuration_digest(values)


def _data(schedule, flow_name, last_job_status=None):
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "flow_id": str(schedule.flow_id),
        "flow_name": flow_name,
        "version": schedule.version,
        "kind": schedule.kind,
        "interval_seconds": schedule.interval_seconds,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "enabled": schedule.enabled,
        "state": "completed"
        if schedule.next_run_at is None
        else "enabled"
        if schedule.enabled
        else "paused",
        "revision": schedule.revision,
        "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        "last_job_id": str(schedule.last_job_id) if schedule.last_job_id else None,
        "last_job_status": last_job_status.value if last_job_status else None,
        "run_count": schedule.run_count,
        "missed_runs": schedule.missed_runs,
        "inputs": schedule.inputs,
        "max_attempts": schedule.max_attempts,
        "created_at": schedule.created_at.isoformat(),
        "updated_at": schedule.updated_at.isoformat(),
    }


class ScheduleService:
    """Advance schedules and publish their runs in one explicit transaction."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        tenant_id: UUID = LEGACY_TENANT_ID,
        system: bool = False,
    ) -> None:
        self.scope = TenantScope(tenant_id, system=system)
        self.unit_of_work = DatabaseUnitOfWork(session_factory)

    def create_schedule(
        self,
        *,
        request_id: UUID | str,
        name: str,
        flow_id: UUID | str,
        version: int,
        kind: str,
        next_run_at,
        interval_seconds=None,
        inputs=None,
        max_attempts=3,
        enabled=True,
    ) -> dict:
        schedule_id = validate_uuid(request_id)
        values = _configuration(
            name=name,
            flow_id=flow_id,
            version=version,
            kind=kind,
            next_run_at=next_run_at,
            interval_seconds=interval_seconds,
            inputs=inputs,
            max_attempts=max_attempts,
            enabled=enabled,
        )
        digest = _creation_digest(values)
        with self.unit_of_work.transaction() as session:
            # Request identity belongs to a tenant even in trusted system code.
            repository = ScheduleRepository(session, self.scope.tenant_id)
            assert_access(session, self.scope.tenant_id)
            # A successful original request remains replayable after its due date
            # or subsequent edits; compare its original digest, not current state.
            schedule = repository.get(schedule_id)
            if schedule is not None:
                if schedule.creation_digest != digest:
                    raise IdempotencyConflictError("Schedule request configuration differs")
                return _data(schedule, *repository.details(schedule))
            if (
                values["next_run_at"]
                <= JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            ):
                raise ValidationError("Schedule time must be in the future")
            if (
                FlowRepository(session, self.scope.tenant_id, system=self.scope.system).version(
                    values["flow_id"], values["version"]
                )
                is None
            ):
                raise NotFoundError("Flow revision not found")
            schedule, created = repository.create_once(
                id=schedule_id, creation_digest=digest, **values
            )
            if schedule.creation_digest != digest:
                raise IdempotencyConflictError("Schedule request configuration differs")
            if created:
                repository.audit(schedule, "SCHEDULE_CREATED")
            return _data(schedule, *repository.details(schedule))

    def get_schedule(self, schedule_id: UUID | str) -> dict:
        with self.unit_of_work.transaction() as session:
            repository = ScheduleRepository(session, self.scope.tenant_id, system=self.scope.system)
            schedule = repository.get(validate_uuid(schedule_id))
            if schedule is None:
                raise NotFoundError("Schedule not found")
            return _data(schedule, *repository.details(schedule))

    def schedules(self, *, limit=100, offset=0, flow_id=None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValidationError("Schedule page size must be between 1 and 500")
        if type(offset) is not int or offset < 0:
            raise ValidationError("Schedule page offset must be nonnegative")
        flow_id = validate_uuid(flow_id) if flow_id is not None else None
        with self.unit_of_work.transaction() as session:
            rows, total = ScheduleRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).list(limit=limit, offset=offset, flow_id=flow_id)
            return {
                "items": [_data(*row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    @staticmethod
    def _locked(repository, schedule_id, expected_revision):
        schedule = repository.get(schedule_id, for_update=True)
        if schedule is None:
            raise NotFoundError("Schedule not found")
        if schedule.revision != expected_revision:
            raise InvalidTransitionError("Schedule revision changed; reload before saving")
        return schedule

    def update_schedule(
        self,
        schedule_id: UUID | str,
        *,
        expected_revision: int,
        name: str,
        flow_id: UUID | str,
        version: int,
        kind: str,
        next_run_at,
        interval_seconds=None,
        inputs=None,
        max_attempts=3,
        enabled=True,
    ) -> dict:
        schedule_id = validate_uuid(schedule_id)
        expected_revision = _revision(expected_revision)
        values = _configuration(
            name=name,
            flow_id=flow_id,
            version=version,
            kind=kind,
            next_run_at=next_run_at,
            interval_seconds=interval_seconds,
            inputs=inputs,
            max_attempts=max_attempts,
            enabled=enabled,
        )
        with self.unit_of_work.transaction() as session:
            repository = ScheduleRepository(session, self.scope.tenant_id, system=self.scope.system)
            schedule = self._locked(repository, schedule_id, expected_revision)
            assert_access(session, schedule.tenant_id)
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            if values["next_run_at"] <= now:
                raise ValidationError("Schedule time must be in the future")
            if (
                FlowRepository(session, self.scope.tenant_id, system=self.scope.system).version(
                    values["flow_id"], values["version"]
                )
                is None
            ):
                raise NotFoundError("Flow revision not found")
            for field, value in values.items():
                setattr(schedule, field, value)
            schedule.revision += 1
            schedule.updated_at = now
            repository.audit(schedule, "SCHEDULE_UPDATED")
            return _data(schedule, *repository.details(schedule))

    def _set_enabled(self, schedule_id, *, expected_revision, enabled):
        schedule_id, expected_revision = validate_uuid(schedule_id), _revision(expected_revision)
        with self.unit_of_work.transaction() as session:
            repository = ScheduleRepository(session, self.scope.tenant_id, system=self.scope.system)
            schedule = self._locked(repository, schedule_id, expected_revision)
            if enabled:
                assert_access(session, schedule.tenant_id)
            if schedule.next_run_at is None:
                raise InvalidTransitionError("Completed schedule needs a new future date")
            schedule.enabled = enabled
            schedule.revision += 1
            schedule.updated_at = JobRepository(
                session, self.scope.tenant_id, system=self.scope.system
            ).now()
            repository.audit(schedule, "SCHEDULE_RESUMED" if enabled else "SCHEDULE_PAUSED")
            return _data(schedule, *repository.details(schedule))

    def pause(self, schedule_id, *, expected_revision):
        return self._set_enabled(schedule_id, expected_revision=expected_revision, enabled=False)

    def resume(self, schedule_id, *, expected_revision):
        return self._set_enabled(schedule_id, expected_revision=expected_revision, enabled=True)

    @staticmethod
    def _dispatch_schedule(
        session: Session,
        repository: ScheduleRepository,
        schedule: FlowSchedule,
        now: datetime,
    ) -> dict:
        assert_access(session, schedule.tenant_id)
        due_at = schedule.next_run_at
        missed = 0
        if schedule.kind == "interval":
            interval = timedelta(seconds=schedule.interval_seconds)
            missed = (now - due_at) // interval
            due_at += missed * interval
            schedule.next_run_at = due_at + interval
        else:
            schedule.next_run_at = None
            schedule.enabled = False
        request_id = uuid5(schedule.id, f"{schedule.revision}:{due_at.isoformat()}")
        run = publish_flow_run(
            session,
            tenant_id=schedule.tenant_id,
            flow_id=schedule.flow_id,
            request_id=request_id,
            version=schedule.version,
            inputs=schedule.inputs,
            inputs_digest=configuration_digest(schedule.inputs),
            max_attempts=schedule.max_attempts,
        )
        schedule.last_run_at = now
        schedule.last_job_id = UUID(run["job"]["id"])
        schedule.run_count += 1
        schedule.missed_runs += missed
        schedule.revision += 1
        schedule.updated_at = now
        repository.audit(
            schedule, "SCHEDULE_DISPATCHED", job_id=schedule.last_job_id, due_at=due_at
        )
        return {
            "schedule_id": str(schedule.id),
            "job_id": str(schedule.last_job_id),
            "due_at": due_at.isoformat(),
        }

    def dispatch_due(self, *, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("Schedule dispatch limit must be between 1 and 100")
        dispatched = []
        with self.unit_of_work.transaction() as session:
            repository = ScheduleRepository(session, self.scope.tenant_id, system=self.scope.system)
            now = JobRepository(session, self.scope.tenant_id, system=self.scope.system).now()
            for schedule in repository.due(now, limit=limit):
                try:
                    # Access can expire and stored definitions may require a
                    # newer runner. Roll back this due slot and any partial job
                    # before pausing it; retain other tenants' jobs in the UoW.
                    with session.begin_nested():
                        dispatched_run = self._dispatch_schedule(session, repository, schedule, now)
                except (EntitlementError, ValidationError) as error:
                    schedule.enabled = False
                    schedule.revision += 1
                    schedule.updated_at = now
                    # Audit the stable reason only, never exception text, flow
                    # definitions or schedule inputs from the failed publish.
                    action = (
                        "SCHEDULE_ACCESS_PAUSED"
                        if isinstance(error, EntitlementError)
                        else "SCHEDULE_VALIDATION_PAUSED"
                    )
                    repository.audit(schedule, action)
                    continue
                dispatched.append(dispatched_run)
        return dispatched
