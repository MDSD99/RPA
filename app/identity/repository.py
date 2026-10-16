"""Identity persistence with account-first locks and database-authoritative time."""

import hashlib
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.identity.models import Account, EmailToken, IdentityAudit, LoginSession


class IdentityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def now(self) -> datetime:
        # PostgreSQL clock_timestamp() always returns a non-null timestamp.
        return cast(datetime, self.session.scalar(select(func.clock_timestamp())))

    def lock_email(self, email: str) -> None:
        # Serialize absent-row registration too. Collisions only delay unrelated emails.
        key = int.from_bytes(hashlib.sha256(email.encode("utf-8")).digest()[:8], signed=True)
        self.session.execute(select(func.pg_advisory_xact_lock(key)))

    def by_email(self, email: str, *, lock: bool = False) -> Account | None:
        query = select(Account).where(Account.email == email)
        if lock:
            query = query.with_for_update()
        return self.session.scalar(query.execution_options(populate_existing=True))

    def account(self, user_id: UUID, *, lock: bool = False) -> Account | None:
        query = select(Account).where(Account.id == user_id)
        if lock:
            query = query.with_for_update()
        return self.session.scalar(query.execution_options(populate_existing=True))

    def email_token(self, digest: str, purpose: str) -> EmailToken | None:
        return self.session.scalar(
            select(EmailToken)
            .where(EmailToken.token_hash == digest, EmailToken.purpose == purpose)
            .execution_options(populate_existing=True)
        )

    def revoke_email_tokens(self, user_id: UUID, purpose: str, now: datetime) -> None:
        self.session.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )

    def session_token(self, digest: str, audience: str) -> LoginSession | None:
        return self.session.scalar(
            select(LoginSession)
            .where(LoginSession.token_hash == digest, LoginSession.audience == audience)
            .execution_options(populate_existing=True)
        )

    def revoke_sessions(self, user_id: UUID, now: datetime) -> None:
        self.session.execute(
            update(LoginSession)
            .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def accounts(self, limit: int, offset: int) -> tuple[list[Account], int]:
        count = cast(int, self.session.scalar(select(func.count()).select_from(Account)))
        rows = self.session.scalars(
            select(Account)
            .order_by(Account.created_at.desc(), Account.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), count

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    def audit(
        self, user_id: UUID, action: str, *, actor_id: UUID | None = None, reason: str | None = None
    ) -> None:
        self.add(IdentityAudit(user_id=user_id, actor_id=actor_id, action=action, reason=reason))

    def flush(self) -> None:
        self.session.flush()
