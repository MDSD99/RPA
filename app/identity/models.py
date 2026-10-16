"""Identity storage. Alembic owns schema changes; no execution data is exposed here."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base


class Tenant(Base):
    __tablename__ = "rpa_identity_tenants"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "rpa_identity_accounts"
    __table_args__ = (
        CheckConstraint("approval IN ('pending', 'approved', 'rejected')", name="valid_approval"),
        CheckConstraint("email = lower(email)", name="normalized_email"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="valid_name"),
        CheckConstraint("NOT is_admin OR totp_secret_ciphertext IS NOT NULL", name="admin_mfa"),
        Index("ix_rpa_identity_accounts_created", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT")
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(256))
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    approval: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    totp_secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailToken(Base):
    __tablename__ = "rpa_identity_email_tokens"
    __table_args__ = (
        CheckConstraint("purpose IN ('verify_email', 'password_reset')", name="valid_purpose"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="valid_hash"),
        CheckConstraint("expires_at > created_at", name="valid_expiry"),
        Index("ix_rpa_identity_email_tokens_user", "user_id", "purpose"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_accounts.id", ondelete="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginSession(Base):
    __tablename__ = "rpa_identity_sessions"
    __table_args__ = (
        CheckConstraint("audience IN ('customer', 'admin')", name="valid_audience"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="valid_hash"),
        CheckConstraint("expires_at > created_at", name="valid_expiry"),
        Index("ix_rpa_identity_sessions_user", "user_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_accounts.id", ondelete="RESTRICT")
    )
    audience: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityAudit(Base):
    __tablename__ = "rpa_identity_audit"
    __table_args__ = (CheckConstraint("action ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="safe_action"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    correlation_id: Mapped[UUID] = mapped_column(default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rpa_identity_accounts.id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_accounts.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailOutbox(Base):
    __tablename__ = "rpa_identity_email_outbox"
    __table_args__ = (
        CheckConstraint("kind IN ('verify_email', 'password_reset')", name="valid_kind"),
        CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name="valid_status"),
        CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
        CheckConstraint(
            "last_error IS NULL OR last_error ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="safe_error"
        ),
        CheckConstraint(
            "(status = 'sending' AND lease_until IS NOT NULL AND claim_token IS NOT NULL) OR "
            "(status <> 'sending' AND lease_until IS NULL AND claim_token IS NULL)",
            name="delivery_claim",
        ),
        CheckConstraint(
            "status IN ('sent', 'failed') OR payload_ciphertext IS NOT NULL",
            name="delivery_payload",
        ),
        Index("ix_rpa_identity_email_outbox_due", "status", "available_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_accounts.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(32))
    payload_ciphertext: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
