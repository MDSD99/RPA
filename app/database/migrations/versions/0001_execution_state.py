"""Create reusable RPA execution state, ownership, history, and checkpoints.

Revision ID: 0001_execution_state
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_execution_state"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "rpa_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("workflow_name", sa.String(128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "PARTIALLY_COMPLETED",
                "FAILED",
                "CANCELLED",
                native_enum=False,
                create_constraint=True,
                name="job_status",
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "total_records >= 0 AND success_count >= 0 "
            "AND failed_count >= 0 AND skipped_count >= 0",
            name=op.f("ck_rpa_jobs_nonnegative_counts"),
        ),
        sa.CheckConstraint(
            "success_count + failed_count + skipped_count <= total_records",
            name=op.f("ck_rpa_jobs_count_totals"),
        ),
        sa.CheckConstraint(
            "length(job_id) > 0 AND length(workflow_name) > 0",
            name=op.f("ck_rpa_jobs_identity_nonempty"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_jobs")),
        sa.UniqueConstraint("job_id", name=op.f("uq_rpa_jobs_job_id")),
    )
    op.create_index("ix_rpa_jobs_status_created", "rpa_jobs", ["status", "created_at"])
    op.create_table(
        "rpa_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_name", sa.String(128), nullable=False),
        sa.Column("record_id", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("external_reference", sa.String(256), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "SUCCESS",
                "FAILED",
                "SKIPPED",
                "RETRY",
                native_enum=False,
                create_constraint=True,
                name="transaction_status",
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "reconciliation_required", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column(
            "result_data", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 100 "
            "AND attempt_count <= max_attempts AND fencing_token >= 0",
            name=op.f("ck_rpa_transactions_attempt_limits"),
        ),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND owner_id IS NOT NULL "
            "AND length(owner_id) > 0 AND lease_expires_at IS NOT NULL "
            "AND processing_started_at IS NOT NULL AND NOT reconciliation_required) OR "
            "(status <> 'PROCESSING' AND owner_id IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_rpa_transactions_processing_ownership"),
        ),
        sa.CheckConstraint(
            "length(record_id) > 0 AND length(idempotency_key) > 0 AND length(workflow_name) > 0",
            name=op.f("ck_rpa_transactions_identity_nonempty"),
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR last_error ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name=op.f("ck_rpa_transactions_safe_error_code"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_data) = 'object' AND octet_length(result_data::text) <= 16384",
            name=op.f("ck_rpa_transactions_bounded_result"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_transactions_job_id_rpa_jobs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_transactions")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_rpa_transactions_idempotency_key")),
        sa.UniqueConstraint("job_id", "record_id", name="uq_rpa_transactions_job_record"),
        sa.UniqueConstraint("id", "job_id", name="uq_rpa_transactions_id_job"),
    )
    op.create_index(
        "ix_rpa_transactions_claim",
        "rpa_transactions",
        ["job_id", "available_at", "created_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'RETRY') AND NOT reconciliation_required"),
    )
    op.create_index(
        "ix_rpa_transactions_stale",
        "rpa_transactions",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )
    op.create_table(
        "rpa_errors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("error_type", sa.String(64), nullable=False),
        sa.Column("error_message", sa.String(64), nullable=False),
        sa.Column("step", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "error_type ~ '^[A-Z][A-Z0-9_]{0,63}$' AND error_message ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name=op.f("ck_rpa_errors_safe_error_codes"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_errors_job_id_rpa_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "job_id"],
            ["rpa_transactions.id", "rpa_transactions.job_id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_errors_transaction_id_rpa_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_errors")),
    )
    op.create_index(
        "ix_rpa_errors_transaction_created", "rpa_errors", ["transaction_id", "created_at"]
    )
    op.create_table(
        "rpa_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("workflow", sa.String(128), nullable=False),
        sa.Column("checkpoint_key", sa.String(128), nullable=False),
        sa.Column("checkpoint_value", postgresql.JSONB(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("fencing_token >= 0", name=op.f("ck_rpa_checkpoints_nonnegative_fence")),
        sa.CheckConstraint(
            "length(checkpoint_key) > 0", name=op.f("ck_rpa_checkpoints_key_nonempty")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(checkpoint_value) = 'object' "
            "AND octet_length(checkpoint_value::text) <= 16384",
            name=op.f("ck_rpa_checkpoints_bounded_checkpoint"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_checkpoints_job_id_rpa_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "job_id"],
            ["rpa_transactions.id", "rpa_transactions.job_id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_checkpoints_transaction_id_rpa_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_checkpoints")),
        sa.UniqueConstraint("transaction_id", "checkpoint_key", name="uq_rpa_checkpoints_tx_key"),
    )
    op.create_table(
        "rpa_audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("transaction_id", sa.Uuid(), nullable=True),
        sa.Column("record_id", sa.String(256), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("old_value", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("new_value", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "old_value IS NULL OR (jsonb_typeof(old_value) = 'object' "
            "AND octet_length(old_value::text) <= 16384)",
            name=op.f("ck_rpa_audit_log_bounded_old_value"),
        ),
        sa.CheckConstraint(
            "new_value IS NULL OR (jsonb_typeof(new_value) = 'object' "
            "AND octet_length(new_value::text) <= 16384)",
            name=op.f("ck_rpa_audit_log_bounded_new_value"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["rpa_jobs.id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_audit_log_job_id_rpa_jobs"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "job_id"],
            ["rpa_transactions.id", "rpa_transactions.job_id"],
            ondelete="RESTRICT",
            name=op.f("fk_rpa_audit_log_transaction_id_rpa_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rpa_audit_log")),
    )
    op.create_index("ix_rpa_audit_log_job_created", "rpa_audit_log", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_table("rpa_audit_log")
    op.drop_table("rpa_checkpoints")
    op.drop_table("rpa_errors")
    op.drop_table("rpa_transactions")
    op.drop_table("rpa_jobs")
