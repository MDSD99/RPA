"""Integration fixtures refuse all unconfirmed database targets before connecting."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import ConfigurationError, DatabaseSettings
from app.database.connection import create_db_engine, create_session_factory
from app.services.execution_service import ExecutionService


@pytest.fixture(scope="session")
def db_engine():
    if os.environ.get("APP_ENV") != "test":
        pytest.fail(
            "PostgreSQL tests require an isolated test database. "
            "Run .venv/bin/python scripts/test_postgres.py; no connection was opened.",
            pytrace=False,
        )
    try:
        settings = DatabaseSettings.from_env()
    except ConfigurationError:
        pytest.fail(
            "Isolated test database confirmation is invalid; no connection opened.", pytrace=False
        )
    engine = create_db_engine(settings)
    try:
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT current_database()")) == settings.name
                assert connection.scalar(text("SHOW server_version_num")) is not None
                assert connection.scalar(text("SELECT to_regclass('rpa_jobs')")) is not None
        except SQLAlchemyError:
            pytest.fail(
                "Isolated PostgreSQL connection failed; driver details suppressed.", pytrace=False
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def session_factory(db_engine):
    return create_session_factory(db_engine)


@pytest.fixture
def service(session_factory):
    return ExecutionService(session_factory, retry_base_seconds=0, retry_max_seconds=0)


@pytest.fixture
def job(service):
    return service.create_job(f"test-job-{uuid4().hex}", "test-workflow")


@pytest.fixture
def transaction(service, job):
    return service.add_transaction(job.id, "test-record", f"test-key-{uuid4().hex}")
