"""Short atomic database transactions with acquisition-only retries."""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DatabaseOperationError, ValidationError


class DatabaseUnitOfWork:
    """Own session lifetime without keeping a session between operations.

    Each transaction acquires its own session. Only connection acquisition may
    retry; business work and uncertain commits are never replayed automatically.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.connection_attempts = session_factory.kw.get("info", {}).get("connection_attempts", 3)
        if type(self.connection_attempts) is not int or not 1 <= self.connection_attempts <= 5:
            raise ValidationError("Connection attempts must be between 1 and 5")

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        # Only acquire retries, before the first application query. No operation
        # or commit is replayed after business work has begun.
        for attempt in range(self.connection_attempts):
            session = self.session_factory()
            try:
                session.connection()  # Starts this session's sole transaction.
                break
            except OperationalError:
                session.close()
                if attempt + 1 == self.connection_attempts:
                    raise DatabaseOperationError(
                        "Database connection could not be acquired"
                    ) from None
                time.sleep(min(0.1 * 2**attempt, 1))
            except DBAPIError:
                session.close()
                raise DatabaseOperationError("Database connection could not be acquired") from None
        try:
            yield session
            session.flush()
            # Return snapshots usable after close even with expire_on_commit.
            session.expunge_all()
            session.commit()
        except DBAPIError:
            # PostgreSQL DETAIL can expose row values even with hide_parameters.
            # Never include the underlying exception or automatically replay.
            raise DatabaseOperationError(
                "Database operation failed; "
                "re-read execution state before retrying an uncertain outcome"
            ) from None
        finally:
            session.close()  # Also rolls back unsuccessful operations.
