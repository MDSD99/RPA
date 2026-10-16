"""Durable trial history, lifetime reservations, and device proof records."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base


class Entitlement(Base):
    __tablename__ = "rpa_entitlements"
    __table_args__ = (
        CheckConstraint(
            "(trial_started_at IS NULL AND trial_ends_at IS NULL) OR "
            "(trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL "
            "AND trial_ends_at > trial_started_at)",
            name="trial_window",
        ),
        CheckConstraint(
            "paid_flow_limit IS NULL OR paid_flow_limit BETWEEN 1 AND 1000", name="paid_limit"
        ),
        CheckConstraint("(paid_until IS NULL) = (paid_flow_limit IS NULL)", name="paid_pair"),
        CheckConstraint(
            "paid_revoked_at IS NULL OR paid_until IS NOT NULL", name="revoked_paid_plan"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_flow_limit: Mapped[int | None] = mapped_column(Integer)
    paid_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowReservation(Base):
    __tablename__ = "rpa_flow_reservations"
    __table_args__ = (CheckConstraint("source IN ('web', 'desktop')", name="source"),)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    flow_id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LicenseDevice(Base):
    __tablename__ = "rpa_license_devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rpa_license_devices_tenant_id"),
        Index(
            "ix_rpa_license_devices_active_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint("length(public_key) = 43", name="public_key_length"),
        CheckConstraint("key_thumbprint ~ '^[0-9a-f]{64}$'", name="thumbprint"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT")
    )
    public_key: Mapped[str] = mapped_column(String(43))
    key_thumbprint: Mapped[str] = mapped_column(String(64), unique=True)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceChallenge(Base):
    __tablename__ = "rpa_device_challenges"
    __table_args__ = (
        CheckConstraint("purpose IN ('activate', 'renew')", name="purpose"),
        CheckConstraint("length(public_key) = 43", name="public_key_length"),
        CheckConstraint("purpose <> 'renew' OR device_id IS NOT NULL", name="renewal_device"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            ["rpa_license_devices.tenant_id", "rpa_license_devices.id"],
            ondelete="RESTRICT",
            name="fk_rpa_device_challenges_tenant_device",
        ),
        Index("ix_rpa_device_challenges_tenant", "tenant_id", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT")
    )
    public_key: Mapped[str] = mapped_column(String(43))
    purpose: Mapped[str] = mapped_column(String(16))
    device_id: Mapped[UUID | None] = mapped_column()
    message: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
