"""Leased outbox delivery. SMTP runs outside all database transactions."""

import hashlib
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.identity.models import Account, EmailOutbox, EmailToken


class MailRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def claim(self) -> EmailOutbox | None:
        now = cast(datetime, self.session.scalar(select(func.clock_timestamp())))
        row = self.session.scalar(
            select(EmailOutbox)
            .where(
                or_(
                    and_(EmailOutbox.status == "pending", EmailOutbox.available_at <= now),
                    and_(EmailOutbox.status == "sending", EmailOutbox.lease_until <= now),
                )
            )
            .order_by(EmailOutbox.available_at, EmailOutbox.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = "sending"
        row.lease_until = now + timedelta(minutes=2)
        row.claim_token = uuid4()
        row.attempts += 1
        self.session.flush()
        return row

    def token_active(self, user_id: UUID, kind: str, token: str) -> bool:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return bool(
            self.session.scalar(
                select(EmailToken.id)
                .join(Account, Account.id == EmailToken.user_id)
                .where(
                    EmailToken.user_id == user_id,
                    EmailToken.purpose == kind,
                    EmailToken.token_hash == digest,
                    EmailToken.consumed_at.is_(None),
                    EmailToken.expires_at > func.clock_timestamp(),
                    Account.suspended.is_(False),
                )
            )
        )

    def finish(self, row_id: UUID, claim: UUID, *, error: str | None, terminal: bool) -> bool:
        now = cast(datetime, self.session.scalar(select(func.clock_timestamp())))
        row = self.session.scalar(
            select(EmailOutbox)
            .where(
                EmailOutbox.id == row_id,
                EmailOutbox.status == "sending",
                EmailOutbox.claim_token == claim,
                EmailOutbox.lease_until > now,
            )
            .with_for_update()
        )
        if row is None:
            return False
        row.lease_until = None
        row.claim_token = None
        row.last_error = error
        if error is None:
            row.status = "sent"
            row.sent_at = now
            row.payload_ciphertext = None
        elif terminal or row.attempts >= 5:
            row.status = "failed"
            row.payload_ciphertext = None
        else:
            row.status = "pending"
            row.available_at = now + timedelta(seconds=min(60 * 2**row.attempts, 3600))
        return True
