"""Tenant-bound encrypted automation credentials.

Revision ID: 0008_secret_vault
Revises: 0007_entitlements
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_secret_vault"
down_revision = "0007_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rpa_credentials",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
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
            "kind IN ('smtp', 'http', 'browser')", name=op.f("ck_rpa_credentials_kind")
        ),
        sa.CheckConstraint(
            "reference ~ '^[a-z][a-z0-9_-]{0,79}$'", name=op.f("ck_rpa_credentials_reference")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_rpa_credentials_version")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["rpa_identity_tenants.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_credentials_tenant_id_rpa_identity_tenants"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "reference", name=op.f("pk_rpa_credentials")),
    )


def downgrade() -> None:
    op.drop_table("rpa_credentials")
