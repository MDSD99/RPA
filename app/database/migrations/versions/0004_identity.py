"""Tenant accounts, approval, single-use email tokens and encrypted delivery outbox.

Revision ID: 0004_identity
Revises: 0003_flow_schedules
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_identity"
down_revision = "0003_flow_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rpa_identity_tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_identity_tenants")),
    )
    op.create_table(
        "rpa_identity_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("approval", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("suspended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("totp_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_rpa_identity_accounts_valid_approval"),
        ),
        sa.CheckConstraint(
            "NOT is_admin OR totp_secret_ciphertext IS NOT NULL",
            name=op.f("ck_rpa_identity_accounts_admin_mfa"),
        ),
        sa.CheckConstraint(
            "email = lower(email)", name=op.f("ck_rpa_identity_accounts_normalized_email")
        ),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 120",
            name=op.f("ck_rpa_identity_accounts_valid_name"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["rpa_identity_tenants.id"],
            name=op.f("fk_rpa_identity_accounts_tenant_id_rpa_identity_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_identity_accounts")),
        sa.UniqueConstraint("email", name=op.f("uq_rpa_identity_accounts_email")),
    )
    op.create_index(
        "ix_rpa_identity_accounts_created",
        "rpa_identity_accounts",
        ["created_at", "id"],
        unique=False,
    )
    op.create_table(
        "rpa_identity_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action ~ '^[A-Z][A-Z0-9_]{0,63}$'", name=op.f("ck_rpa_identity_audit_safe_action")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["rpa_identity_accounts.id"],
            name=op.f("fk_rpa_identity_audit_actor_id_rpa_identity_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["rpa_identity_accounts.id"],
            name=op.f("fk_rpa_identity_audit_user_id_rpa_identity_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_identity_audit")),
    )
    op.create_table(
        "rpa_identity_email_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload_ciphertext", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'sending' AND lease_until IS NOT NULL AND claim_token IS NOT NULL) OR "
            "(status <> 'sending' AND lease_until IS NULL AND claim_token IS NULL)",
            name=op.f("ck_rpa_identity_email_outbox_delivery_claim"),
        ),
        sa.CheckConstraint(
            "kind IN ('verify_email', 'password_reset')",
            name=op.f("ck_rpa_identity_email_outbox_valid_kind"),
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR last_error ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name=op.f("ck_rpa_identity_email_outbox_safe_error"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed')",
            name=op.f("ck_rpa_identity_email_outbox_valid_status"),
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed') OR payload_ciphertext IS NOT NULL",
            name=op.f("ck_rpa_identity_email_outbox_delivery_payload"),
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_rpa_identity_email_outbox_nonnegative_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["rpa_identity_accounts.id"],
            name=op.f("fk_rpa_identity_email_outbox_user_id_rpa_identity_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_identity_email_outbox")),
    )
    op.create_index(
        "ix_rpa_identity_email_outbox_due",
        "rpa_identity_email_outbox",
        ["status", "available_at", "id"],
        unique=False,
    )
    op.create_table(
        "rpa_identity_email_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'password_reset')",
            name=op.f("ck_rpa_identity_email_tokens_valid_purpose"),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_rpa_identity_email_tokens_valid_hash")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_rpa_identity_email_tokens_valid_expiry")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["rpa_identity_accounts.id"],
            name=op.f("fk_rpa_identity_email_tokens_user_id_rpa_identity_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_identity_email_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_rpa_identity_email_tokens_token_hash")),
    )
    op.create_index(
        "ix_rpa_identity_email_tokens_user",
        "rpa_identity_email_tokens",
        ["user_id", "purpose"],
        unique=False,
    )
    op.create_table(
        "rpa_identity_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "audience IN ('customer', 'admin')",
            name=op.f("ck_rpa_identity_sessions_valid_audience"),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_rpa_identity_sessions_valid_hash")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_rpa_identity_sessions_valid_expiry")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["rpa_identity_accounts.id"],
            name=op.f("fk_rpa_identity_sessions_user_id_rpa_identity_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_identity_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_rpa_identity_sessions_token_hash")),
    )
    op.create_index(
        "ix_rpa_identity_sessions_user", "rpa_identity_sessions", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_rpa_identity_sessions_user", table_name="rpa_identity_sessions")
    op.drop_table("rpa_identity_sessions")
    op.drop_index("ix_rpa_identity_email_tokens_user", table_name="rpa_identity_email_tokens")
    op.drop_table("rpa_identity_email_tokens")
    op.drop_index("ix_rpa_identity_email_outbox_due", table_name="rpa_identity_email_outbox")
    op.drop_table("rpa_identity_email_outbox")
    op.drop_table("rpa_identity_audit")
    op.drop_index("ix_rpa_identity_accounts_created", table_name="rpa_identity_accounts")
    op.drop_table("rpa_identity_accounts")
    op.drop_table("rpa_identity_tenants")
