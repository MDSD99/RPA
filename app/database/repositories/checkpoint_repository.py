"""A transaction-scoped checkpoint with the writer's fencing token."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import Checkpoint, Transaction


class CheckpointRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def save(self, transaction: Transaction, key: str, value, now: datetime) -> Checkpoint:
        self.scope.require_owner(transaction)
        statement = insert(Checkpoint).values(
            tenant_id=transaction.tenant_id,
            job_id=transaction.job_id,
            transaction_id=transaction.id,
            workflow=transaction.workflow_name,
            checkpoint_key=key,
            checkpoint_value=value,
            fencing_token=transaction.fencing_token,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[Checkpoint.transaction_id, Checkpoint.checkpoint_key],
            set_={
                "checkpoint_value": statement.excluded.checkpoint_value,
                "fencing_token": statement.excluded.fencing_token,
                "updated_at": now,
            },
        ).returning(Checkpoint)
        return self.session.scalar(statement, execution_options={"populate_existing": True})

    def list_for_transaction(self, transaction_id: UUID) -> list[Checkpoint]:
        return list(
            self.session.scalars(
                select(Checkpoint)
                .where(
                    self.scope.predicate(Checkpoint), Checkpoint.transaction_id == transaction_id
                )
                .order_by(Checkpoint.checkpoint_key)
            )
        )
