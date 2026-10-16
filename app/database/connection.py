"""Pooled PostgreSQL connections; retry acquisition only, never committed work."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import ConfigurationError, DatabaseSettings


class DatabaseConnectionError(RuntimeError):
    """Sanitized acquisition failure, with no driver text or credentials."""


def create_db_engine(settings: DatabaseSettings) -> Engine:
    settings.validate()  # The test safety gate runs before any network connection.
    query = {"sslmode": settings.sslmode}
    if settings.sslrootcert:
        query["sslrootcert"] = settings.sslrootcert
    url = URL.create(
        "postgresql+psycopg",
        username=settings.user,
        password=settings.password,
        host=settings.host,
        port=settings.port,
        database=settings.name,
        query=query,
    )
    engine = create_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_recycle=1800,
        execution_options={"rpa_connect_attempts": settings.connect_attempts},
        connect_args={
            "connect_timeout": settings.connect_timeout,
            "application_name": "rpa-infrastructure",
            "options": (
                f"-c statement_timeout={settings.statement_timeout_ms} "
                f"-c lock_timeout={settings.lock_timeout_ms}"
            ),
        },
    )
    if settings.app_env == "production":

        @event.listens_for(engine, "connect")
        def enforce_role(dbapi_connection, _connection_record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
                    "WHERE rolname = current_user"
                )
                role = cursor.fetchone()
            dbapi_connection.rollback()
            if role is None or any(role):
                dbapi_connection.close()
                raise ConfigurationError("Production database role has excessive privileges.")

    return engine


def connect_with_retry(
    engine: Engine, *, attempts: int = 3, delay_seconds: float = 0.1
) -> Connection:
    """Acquire a connection before work starts; do not retry transactions or COMMIT."""
    # Bounds also reject NaN and infinities; bool must not count as an integer.
    if (
        type(attempts) is not int
        or not 1 <= attempts <= 5
        or type(delay_seconds) not in {int, float}
        or not 0 <= delay_seconds <= 5
    ):
        raise ValueError("Connection retry settings are outside the allowed range.")
    for attempt in range(attempts):
        try:
            return engine.connect()
        except OperationalError:
            if attempt + 1 < attempts:
                time.sleep(min(delay_seconds * 2**attempt, 5))
    raise DatabaseConnectionError("Database connection could not be acquired.") from None


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        info={"connection_attempts": engine.get_execution_options().get("rpa_connect_attempts", 3)},
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One atomic unit of work; failures roll back and are never automatically replayed."""
    with factory() as session:
        with session.begin():
            yield session
