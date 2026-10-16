"""Strict HTTP request models shared by the management API routes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateJob(Body):
    job_id: str = Field(min_length=1, max_length=128)
    workflow_name: str = Field(min_length=1, max_length=128)


class CreateTransaction(Body):
    record_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    max_attempts: int = Field(default=3, ge=1, le=100)
    external_reference: str | None = Field(default=None, min_length=1, max_length=256)


class Reconcile(Body):
    expected_fencing_token: int = Field(ge=0)
    outcome: Literal["succeeded", "not_applied"]
    result_data: dict | None = None


class DemoJob(Body):
    scenario: Literal["success", "retry", "recovery"]
    record_count: int = Field(default=5, ge=1, le=20)
    request_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"
    )


class EmptyBody(Body):
    pass


class CreateFlow(Body):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    graph: dict


class ImportFlow(Body):
    document: dict[str, object] | str


class UpdateFlow(CreateFlow):
    expected_version: int = Field(ge=1, le=2_147_483_646)


class CreateFlowRun(Body):
    request_id: str = Field(min_length=36, max_length=36)
    version: int = Field(ge=1, le=2_147_483_647)
    inputs: dict = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=100)


class ScheduleConfiguration(Body):
    name: str = Field(min_length=1, max_length=160)
    flow_id: str = Field(min_length=36, max_length=36)
    version: int = Field(ge=1, le=2_147_483_647)
    kind: Literal["once", "interval"]
    next_run_at: str = Field(
        min_length=17,
        max_length=32,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$",
    )
    interval_seconds: int | None = Field(default=None, ge=60, le=2_678_400)
    inputs: dict = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=100)
    enabled: bool = True


class CreateSchedule(ScheduleConfiguration):
    request_id: str = Field(min_length=36, max_length=36)


class UpdateSchedule(ScheduleConfiguration):
    expected_revision: int = Field(ge=1, le=2_147_483_646)


class ScheduleRevision(Body):
    expected_revision: int = Field(ge=1, le=2_147_483_646)
