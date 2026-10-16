"""Shared schema readiness for all application entry points."""

from sqlalchemy import column, func, select, table
from sqlalchemy.orm import Session

EXPECTED_REVISION = "0008_secret_vault"


def schema_is_current(session: Session) -> bool:
    if session.scalar(select(func.to_regclass("alembic_version"))) is None:
        return False
    revisions = table("alembic_version", column("version_num"))
    return session.scalars(select(revisions.c.version_num)).all() == [EXPECTED_REVISION]
