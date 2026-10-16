"""Shared authentication throttles without plaintext identifiers.

Revision ID: 0005_portal_rate_limits
Revises: 0004_identity
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_portal_rate_limits"
down_revision = "0004_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rpa_portal_rate_limits",
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("resets_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("hits > 0", name=op.f("ck_rpa_portal_rate_limits_positive_hits")),
        sa.PrimaryKeyConstraint("key_hash", name=op.f("pk_rpa_portal_rate_limits")),
    )
    op.create_index("ix_rpa_portal_rate_limits_resets_at", "rpa_portal_rate_limits", ["resets_at"])


def downgrade() -> None:
    op.drop_index("ix_rpa_portal_rate_limits_resets_at", table_name="rpa_portal_rate_limits")
    op.drop_table("rpa_portal_rate_limits")
