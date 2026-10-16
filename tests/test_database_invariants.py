"""Real PostgreSQL constraints and uncertain-commit behavior."""

from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DatabaseOperationError
from app.core.states import TransactionStatus
from app.database.models import AuditLog, Checkpoint, Job, Transaction
from app.services.execution_service import ExecutionService

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "values",
    [
        {"status": TransactionStatus.PROCESSING},
        {"attempt_count": 4},
        {"max_attempts": 0},
        {"fencing_token": -1},
        {"last_error": "raw error with sensitive context"},
        {"result_data": {"large": "x" * 17000}},
    ],
)
def test_database_rejects_invalid_execution_state(db_engine, transaction, values):
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        connection.execute(
            update(Transaction).where(Transaction.id == transaction.id).values(**values)
        )


def test_database_enforces_global_idempotency(db_engine, service, job, transaction):
    other = service.create_job(f"other-{uuid4().hex}", job.workflow_name)
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        connection.execute(
            insert(Transaction).values(
                job_id=other.id,
                workflow_name=job.workflow_name,
                record_id="duplicate",
                idempotency_key=transaction.idempotency_key,
            )
        )


def test_checkpoint_cannot_reference_wrong_parent(db_engine, service, job, transaction):
    other = service.create_job(f"other-{uuid4().hex}", job.workflow_name)
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        connection.execute(
            insert(Checkpoint).values(
                job_id=other.id,
                transaction_id=transaction.id,
                workflow=job.workflow_name,
                checkpoint_key="cursor",
                checkpoint_value={"offset": 1},
            )
        )


def test_commit_ack_loss_does_not_replay_and_key_recovers_committed_state(db_engine, service):
    commits = []

    class LostAcknowledgementSession(Session):
        def commit(self):
            super().commit()
            commits.append(True)
            raise OperationalError("COMMIT", None, RuntimeError("synthetic acknowledgement loss"))

    uncertain = ExecutionService(sessionmaker(bind=db_engine, class_=LostAcknowledgementSession))
    key = f"commit-test-{uuid4().hex}"
    with pytest.raises(DatabaseOperationError, match="re-read execution state"):
        uncertain.create_job(key, "test-workflow")
    assert len(commits) == 1
    recovered = service.create_job(key, "test-workflow")
    with db_engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(Job).where(Job.job_id == key)) == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.job_id == recovered.id, AuditLog.action == "JOB_CREATED")
            )
            == 1
        )


def test_commit_failure_before_commit_rolls_back_without_replay(db_engine, service):
    commits = []

    class FailedCommitSession(Session):
        def commit(self):
            commits.append(True)
            raise OperationalError("COMMIT", None, RuntimeError("synthetic disconnect"))

    failing = ExecutionService(sessionmaker(bind=db_engine, class_=FailedCommitSession))
    key = f"rollback-test-{uuid4().hex}"
    with pytest.raises(DatabaseOperationError):
        failing.create_job(key, "test-workflow")
    assert len(commits) == 1
    with db_engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(Job).where(Job.job_id == key)) == 0
        )
    assert service.create_job(key, "test-workflow").job_id == key
