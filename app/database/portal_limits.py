"""Persistent throttling shared by all portal processes."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base


class PortalRateLimit(Base):
    __tablename__ = "rpa_portal_rate_limits"
    __table_args__ = (CheckConstraint("hits > 0", name="positive_hits"),)

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer)
    resets_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
