"""Atomic rate counters; no plaintext email or address is stored."""

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import case, delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database.portal_limits import PortalRateLimit


class RateLimitRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def consume(self, key_hash: str, *, seconds: int) -> int:
        now = cast(datetime, self.session.scalar(func.clock_timestamp()))
        statement = insert(PortalRateLimit).values(
            key_hash=key_hash, hits=1, resets_at=now + timedelta(seconds=seconds)
        )
        expired = PortalRateLimit.resets_at <= now
        # INSERT ... RETURNING always yields this counter, including on conflict.
        result = self.session.scalar(
            statement.on_conflict_do_update(
                index_elements=[PortalRateLimit.key_hash],
                set_={
                    "hits": case((expired, 1), else_=func.least(PortalRateLimit.hits + 1, 1000000)),
                    "resets_at": case(
                        (expired, statement.excluded.resets_at), else_=PortalRateLimit.resets_at
                    ),
                },
            ).returning(PortalRateLimit.hits)
        )
        return cast(int, result)

    def prune(self) -> None:
        self.session.execute(
            delete(PortalRateLimit).where(PortalRateLimit.resets_at < func.clock_timestamp())
        )
