"""Shared job state mutation inside an existing service transaction."""

from datetime import datetime

from app.core.exceptions import InvalidTransitionError
from app.core.states import JOB_TRANSITIONS, JobStatus
from app.database.models import Job


def transition_job(job: Job, new_status: JobStatus, now: datetime) -> None:
    if new_status not in JOB_TRANSITIONS[job.status]:
        raise InvalidTransitionError("Job state transition is not allowed")
    job.status = new_status
    job.updated_at = now
