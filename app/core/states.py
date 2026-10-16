"""Execution states. Services enforce transitions while holding database row locks."""

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    RETRY = "RETRY"


JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.PARTIALLY_COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.PARTIALLY_COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

TRANSACTION_TRANSITIONS: dict[TransactionStatus, frozenset[TransactionStatus]] = {
    TransactionStatus.PENDING: frozenset({TransactionStatus.PROCESSING, TransactionStatus.SKIPPED}),
    TransactionStatus.PROCESSING: frozenset(
        {
            TransactionStatus.SUCCESS,
            TransactionStatus.FAILED,
            TransactionStatus.SKIPPED,
            TransactionStatus.RETRY,
        }
    ),
    # A RETRY requiring reconciliation cannot be claimed; reconciliation may finish it.
    TransactionStatus.RETRY: frozenset(
        {
            TransactionStatus.PROCESSING,
            TransactionStatus.SUCCESS,
            TransactionStatus.FAILED,
            TransactionStatus.SKIPPED,
        }
    ),
    TransactionStatus.SUCCESS: frozenset(),
    TransactionStatus.FAILED: frozenset(),
    TransactionStatus.SKIPPED: frozenset(),
}
