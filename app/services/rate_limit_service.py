"""Committed throttles that also count failed authentication attempts."""

import hashlib
import hmac

from sqlalchemy.orm import Session, sessionmaker

from app.database.repositories.rate_limit_repository import RateLimitRepository
from app.database.unit_of_work import DatabaseUnitOfWork


class RateLimitService:
    def __init__(self, session_factory: sessionmaker[Session], *, secret_key: str) -> None:
        self.unit_of_work = DatabaseUnitOfWork(session_factory)
        self._key = secret_key.encode("ascii")

    def allow(self, scope: str, identity: str, *, limit: int, seconds: int) -> bool:
        digest = hmac.new(self._key, f"{scope}\0{identity}".encode(), hashlib.sha256).hexdigest()
        with self.unit_of_work.transaction() as session:
            count = RateLimitRepository(session).consume(digest, seconds=seconds)
        return count <= limit

    def prune(self) -> None:
        with self.unit_of_work.transaction() as session:
            RateLimitRepository(session).prune()
