"""Encrypted values have no plaintext read API; aliases are tenant-owned."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base


class Credential(Base):
    __tablename__ = "rpa_credentials"
    __table_args__ = (
        CheckConstraint("kind IN ('smtp', 'http', 'browser')", name="kind"),
        CheckConstraint("reference ~ '^[a-z][a-z0-9_-]{0,79}$'", name="reference"),
        CheckConstraint("version >= 1", name="version"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    reference: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    ciphertext: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
