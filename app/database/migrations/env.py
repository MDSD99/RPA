"""Alembic reads the same validated environment as the application."""

import os
from logging.config import fileConfig

from alembic import context
from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import ConfigurationError, DatabaseSettings
from app.database import portal_limits as _portal_limits  # noqa: F401
from app.database.connection import (
    DatabaseConnectionError,
    connect_with_retry,
    create_db_engine,
)
from app.database.models import Base
from app.entitlements import models as _entitlement_models  # noqa: F401
from app.identity import models as _identity_models  # noqa: F401
from app.secrets import models as _secret_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run() -> None:
    settings = DatabaseSettings.from_env()
    if (
        settings.app_env == "production"
        and os.environ.get("RPA_ALLOW_PRODUCTION_MIGRATIONS") != "yes"
    ):
        raise ConfigurationError(
            "Production migration access requires RPA_ALLOW_PRODUCTION_MIGRATIONS=yes."
        )
    if context.is_offline_mode():
        # A URL is unnecessary; offline SQL must never contain connection credentials.
        context.configure(
            dialect_name="postgresql",
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_db_engine(settings)
    try:
        with connect_with_retry(engine, attempts=settings.connect_attempts) as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


try:
    run()
except ConfigurationError as error:
    raise CommandError(str(error)) from None
except (SQLAlchemyError, DatabaseConnectionError):
    # Driver diagnostics can contain full row values. Do not print them in CLI output.
    raise CommandError(
        "Database migration failed; inspect database health and schema safely."
    ) from None
