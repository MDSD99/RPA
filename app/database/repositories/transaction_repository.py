"""PostgreSQL reservation, queue selection, and execution record persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.states import TransactionStatus
from app.core.tenancy import LEGACY_TENANT_ID, TenantScope
from app.database.models import Transaction


class TransactionRepository:
    def __init__(
        self, session: Session, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False
    ) -> None:
        self.session = session
        self.scope = TenantScope(tenant_id, system=system)

    def get(self, transaction_id: UUID, *, for_update: bool = False) -> Transaction | None:
        statement = select(Transaction).where(
            self.scope.predicate(Transaction), Transaction.id == transaction_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def parent_id(self, transaction_id: UUID) -> UUID | None:
        return self.session.scalar(
            select(Transaction.job_id).where(
                self.scope.predicate(Transaction), Transaction.id == transaction_id
            )
        )

    def by_idempotency_key(self, key: str) -> Transaction | None:
        return self.session.scalar(
            select(Transaction).where(
                self.scope.predicate(Transaction), Transaction.idempotency_key == key
            )
        )

    def create_once(self, **values) -> tuple[Transaction | None, bool]:
        # No conflict target: both the key and business identity are unique.
        result = self.session.scalar(
            insert(Transaction)
            .values(tenant_id=self.scope.tenant_id, **values)
            .on_conflict_do_nothing()
            .returning(Transaction)
        )
        if result is not None:
            return result, True
        return self.by_idempotency_key(values["idempotency_key"]), False

    def claim_candidate(self, job_id: UUID, now: datetime) -> Transaction | None:
        return self.session.scalar(
            select(Transaction)
            .where(
                self.scope.predicate(Transaction),
                Transaction.job_id == job_id,
                Transaction.status.in_([TransactionStatus.PENDING, TransactionStatus.RETRY]),
                Transaction.reconciliation_required.is_(False),
                Transaction.attempt_count < Transaction.max_attempts,
                Transaction.available_at <= now,
            )
            .order_by(Transaction.available_at, Transaction.created_at, Transaction.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def expired(self, job_id: UUID, now: datetime, limit: int) -> list[Transaction]:
        return list(
            self.session.scalars(
                select(Transaction)
                .where(
                    self.scope.predicate(Transaction),
                    Transaction.job_id == job_id,
                    Transaction.status == TransactionStatus.PROCESSING,
                    Transaction.lease_expires_at <= now,
                )
                .order_by(Transaction.lease_expires_at, Transaction.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    def unfinished(self, job_id: UUID) -> list[Transaction]:
        return list(
            self.session.scalars(
                select(Transaction)
                .where(
                    self.scope.predicate(Transaction),
                    Transaction.job_id == job_id,
                    Transaction.status.in_(
                        [
                            TransactionStatus.PENDING,
                            TransactionStatus.PROCESSING,
                            TransactionStatus.RETRY,
                        ]
                    ),
                )
                .order_by(Transaction.id)
                .with_for_update()
            )
        )

    def status_counts(self, job_id: UUID) -> dict[TransactionStatus, int]:
        return dict(
            self.session.execute(
                select(Transaction.status, func.count())
                .where(self.scope.predicate(Transaction), Transaction.job_id == job_id)
                .group_by(Transaction.status)
            ).all()
        )
