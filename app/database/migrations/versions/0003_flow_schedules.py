"""Pinned, idempotent schedules and atomically consumed due occurrences.

Revision ID: 0003_flow_schedules
Revises: 0002_visual_flows
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_flow_schedules"
down_revision = "0002_visual_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rpa_flow_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("creation_digest", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", sa.Uuid(), nullable=True),
        sa.Column("run_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("missed_runs", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "length(name) > 0 AND revision >= 1", name=op.f("ck_rpa_flow_schedules_valid_identity")
        ),
        sa.CheckConstraint(
            "(kind = 'once' AND interval_seconds IS NULL) OR "
            "(kind = 'interval' AND interval_seconds IS NOT NULL "
            "AND interval_seconds BETWEEN 60 AND 2678400)",
            name=op.f("ck_rpa_flow_schedules_valid_frequency"),
        ),
        sa.CheckConstraint(
            "NOT enabled OR next_run_at IS NOT NULL",
            name=op.f("ck_rpa_flow_schedules_enabled_due_date"),
        ),
        sa.CheckConstraint(
            "run_count >= 0 AND missed_runs >= 0",
            name=op.f("ck_rpa_flow_schedules_nonnegative_counters"),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 100", name=op.f("ck_rpa_flow_schedules_attempt_limits")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inputs) = 'object' AND octet_length(inputs::text) <= 16384",
            name=op.f("ck_rpa_flow_schedules_bounded_inputs"),
        ),
        sa.CheckConstraint(
            "creation_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_rpa_flow_schedules_valid_creation_digest"),
        ),
        sa.ForeignKeyConstraint(
            ["flow_id", "version"],
            ["rpa_flow_versions.flow_id", "rpa_flow_versions.version"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_flow_schedules_flow_id_rpa_flow_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["last_job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_flow_schedules_last_job_id_rpa_jobs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_flow_schedules")),
    )
    op.create_index(
        "ix_rpa_flow_schedules_due",
        "rpa_flow_schedules",
        ["next_run_at", "id"],
        postgresql_where=sa.text("enabled AND next_run_at IS NOT NULL"),
    )
    op.create_index(
        "ix_rpa_flow_schedules_flow_created", "rpa_flow_schedules", ["flow_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("rpa_flow_schedules")
