"""Versioned visual flows and immutable per-job execution snapshots.

Revision ID: 0002_visual_flows
Revises: 0001_execution_state
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_visual_flows"
down_revision = "0001_execution_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rpa_flows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            "length(name) > 0 AND version >= 1", name=op.f("ck_rpa_flows_valid_identity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_flows")),
    )
    op.create_index("ix_rpa_flows_updated", "rpa_flows", ["updated_at"])
    op.create_table(
        "rpa_flow_versions",
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "version >= 1 AND node_count BETWEEN 2 AND 40",
            name=op.f("ck_rpa_flow_versions_valid_version"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(graph) = 'object' AND octet_length(graph::text) <= 16384",
            name=op.f("ck_rpa_flow_versions_bounded_graph"),
        ),
        sa.ForeignKeyConstraint(
            ["flow_id"],
            ["rpa_flows.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_flow_versions_flow_id_rpa_flows"),
        ),
        sa.PrimaryKeyConstraint("flow_id", "version", name=op.f("pk_rpa_flow_versions")),
    )
    op.create_table(
        "rpa_flow_runs",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("inputs_digest", sa.String(64), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 100", name=op.f("ck_rpa_flow_runs_attempt_limits")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inputs) = 'object' AND octet_length(inputs::text) <= 16384",
            name=op.f("ck_rpa_flow_runs_bounded_inputs"),
        ),
        sa.CheckConstraint(
            "inputs_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_rpa_flow_runs_valid_inputs_digest"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_flow_runs_job_id_rpa_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["flow_id", "version"],
            ["rpa_flow_versions.flow_id", "rpa_flow_versions.version"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_flow_runs_flow_id_rpa_flow_versions"),
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_rpa_flow_runs")),
        sa.UniqueConstraint("request_id", name=op.f("uq_rpa_flow_runs_request_id")),
    )
    op.create_index("ix_rpa_flow_runs_flow_created", "rpa_flow_runs", ["flow_id", "created_at"])
    # A running job must keep the graph and inputs it was submitted with, even
    # if another code path accidentally attempts an ORM or direct SQL update.
    op.execute("""
        CREATE FUNCTION rpa_reject_flow_snapshot_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'FLOW_SNAPSHOT_IMMUTABLE' USING ERRCODE = '23514';
        END;
        $$
    """)
    for table in ("rpa_flow_versions", "rpa_flow_runs"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION rpa_reject_flow_snapshot_mutation()"
        )


def downgrade() -> None:
    op.drop_table("rpa_flow_runs")
    op.drop_table("rpa_flow_versions")
    op.drop_table("rpa_flows")
    op.execute("DROP FUNCTION rpa_reject_flow_snapshot_mutation()")
