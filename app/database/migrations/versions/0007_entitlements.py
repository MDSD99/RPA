"""Durable trial history, shared flow quota, and single-device challenge leases.

Revision ID: 0007_entitlements
Revises: 0006_execution_tenants
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_entitlements"
down_revision = "0006_execution_tenants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rpa_entitlements",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("trial_started_at", sa.DateTime(timezone=True)),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True)),
        sa.Column("paid_until", sa.DateTime(timezone=True)),
        sa.Column("paid_flow_limit", sa.Integer()),
        sa.Column("paid_revoked_at", sa.DateTime(timezone=True)),
        sa.Column("device_blocked_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(trial_started_at IS NULL AND trial_ends_at IS NULL) OR "
            "(trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL "
            "AND trial_ends_at > trial_started_at)",
            name=op.f("ck_rpa_entitlements_trial_window"),
        ),
        sa.CheckConstraint(
            "paid_flow_limit IS NULL OR paid_flow_limit BETWEEN 1 AND 1000",
            name=op.f("ck_rpa_entitlements_paid_limit"),
        ),
        sa.CheckConstraint(
            "(paid_until IS NULL) = (paid_flow_limit IS NULL)",
            name=op.f("ck_rpa_entitlements_paid_pair"),
        ),
        sa.CheckConstraint(
            "paid_revoked_at IS NULL OR paid_until IS NOT NULL",
            name=op.f("ck_rpa_entitlements_revoked_paid_plan"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["rpa_identity_tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_entitlements_tenant_id_rpa_identity_tenants"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_rpa_entitlements")),
    )
    op.create_table(
        "rpa_flow_reservations",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('web', 'desktop')", name=op.f("ck_rpa_flow_reservations_source")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["rpa_identity_tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_flow_reservations_tenant_id_rpa_identity_tenants"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "flow_id", name=op.f("pk_rpa_flow_reservations")),
    )
    op.create_table(
        "rpa_license_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("public_key", sa.String(43), nullable=False),
        sa.Column("key_thumbprint", sa.String(64), nullable=False),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "length(public_key) = 43", name=op.f("ck_rpa_license_devices_public_key_length")
        ),
        sa.CheckConstraint(
            "key_thumbprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_rpa_license_devices_thumbprint")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["rpa_identity_tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_license_devices_tenant_id_rpa_identity_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_license_devices")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rpa_license_devices_tenant_id"),
        sa.UniqueConstraint("key_thumbprint", name=op.f("uq_rpa_license_devices_key_thumbprint")),
    )
    op.create_index(
        "ix_rpa_license_devices_active_tenant",
        "rpa_license_devices",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "rpa_device_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("public_key", sa.String(43), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("device_id", sa.Uuid()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('activate', 'renew')", name=op.f("ck_rpa_device_challenges_purpose")
        ),
        sa.CheckConstraint(
            "length(public_key) = 43", name=op.f("ck_rpa_device_challenges_public_key_length")
        ),
        sa.CheckConstraint(
            "purpose <> 'renew' OR device_id IS NOT NULL",
            name=op.f("ck_rpa_device_challenges_renewal_device"),
        ),
        sa.CheckConstraint("expires_at > created_at", name=op.f("ck_rpa_device_challenges_expiry")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["rpa_identity_tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_device_challenges_tenant_id_rpa_identity_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            ["rpa_license_devices.tenant_id", "rpa_license_devices.id"],
            ondelete="RESTRICT",
            name="fk_rpa_device_challenges_tenant_device",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_device_challenges")),
    )
    op.create_index(
        "ix_rpa_device_challenges_tenant", "rpa_device_challenges", ["tenant_id", "expires_at"]
    )


def downgrade() -> None:
    op.drop_table("rpa_device_challenges")
    op.drop_table("rpa_license_devices")
    op.drop_table("rpa_flow_reservations")
    op.drop_table("rpa_entitlements")
