"""MultiRPA schedules endpoints; business rules belong to services."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.exceptions import ValidationError
from app.web.dependencies import Services
from app.web.schemas import CreateSchedule, ScheduleRevision, UpdateSchedule

router = APIRouter()


def _schedule_fields(body):
    values = body.model_dump()
    try:
        values["next_run_at"] = datetime.fromisoformat(values["next_run_at"])
    except (TypeError, ValueError):
        raise ValidationError("Schedule time must be a timezone-aware ISO timestamp") from None
    if values["next_run_at"].utcoffset() is None:
        raise ValidationError("Schedule time must include a timezone")
    return values


@router.get("/api/schedules")
def list_schedules(
    services: Services,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    flow_id: UUID | None = None,
):
    return {
        **services.schedules.schedules(limit=limit, offset=offset, flow_id=flow_id),
        "scheduler": services.scheduler.snapshot(),
    }


@router.post("/api/schedules", status_code=201)
def create_schedule(body: CreateSchedule, services: Services):
    return {"schedule": services.schedules.create_schedule(**_schedule_fields(body))}


@router.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: UUID, services: Services):
    return {"schedule": services.schedules.get_schedule(schedule_id)}


@router.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: UUID, body: UpdateSchedule, services: Services):
    return {"schedule": services.schedules.update_schedule(schedule_id, **_schedule_fields(body))}


@router.post("/api/schedules/{schedule_id}/pause")
def pause_schedule(schedule_id: UUID, body: ScheduleRevision, services: Services):
    return {
        "schedule": services.schedules.pause(schedule_id, expected_revision=body.expected_revision)
    }


@router.post("/api/schedules/{schedule_id}/resume")
def resume_schedule(schedule_id: UUID, body: ScheduleRevision, services: Services):
    return {
        "schedule": services.schedules.resume(schedule_id, expected_revision=body.expected_revision)
    }
