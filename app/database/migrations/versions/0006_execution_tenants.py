"""Scope execution data to accounts while preserving the existing development data.

Revision ID: 0006_execution_tenants
Revises: 0005_portal_rate_limits

The reserved legacy tenant has no login account. Existing execution rows retain
all identifiers and state. Downgrade refuses ambiguous global identities instead
of deleting customer data.
"""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0006_execution_tenants"
down_revision = "0005_portal_rate_limits"
branch_labels = None
depends_on = None

LEGACY = "00000000-0000-0000-0000-000000000001"
TABLES = [
    "rpa_jobs",
    "rpa_transactions",
    "rpa_errors",
    "rpa_checkpoints",
    "rpa_audit_log",
    "rpa_flows",
    "rpa_flow_versions",
    "rpa_flow_runs",
    "rpa_flow_schedules",
]
UNIQUES = [
    ("uq_rpa_jobs_tenant_id", "rpa_jobs", ["tenant_id", "id"]),
    ("uq_rpa_jobs_tenant_job_id", "rpa_jobs", ["tenant_id", "job_id"]),
    ("uq_rpa_transactions_tenant_key", "rpa_transactions", ["tenant_id", "idempotency_key"]),
    ("uq_rpa_transactions_tenant_id_job", "rpa_transactions", ["tenant_id", "id", "job_id"]),
    ("uq_rpa_flows_tenant_id", "rpa_flows", ["tenant_id", "id"]),
    (
        "uq_rpa_flow_versions_tenant_flow_version",
        "rpa_flow_versions",
        ["tenant_id", "flow_id", "version"],
    ),
    ("uq_rpa_flow_runs_tenant_request", "rpa_flow_runs", ["tenant_id", "request_id"]),
]
FOREIGN_KEYS = [
    (
        "fk_rpa_transactions_tenant_job",
        "rpa_transactions",
        ["tenant_id", "job_id"],
        ["rpa_jobs.tenant_id", "rpa_jobs.id"],
    ),
    (
        "fk_rpa_errors_tenant_transaction",
        "rpa_errors",
        ["tenant_id", "transaction_id", "job_id"],
        ["rpa_transactions.tenant_id", "rpa_transactions.id", "rpa_transactions.job_id"],
    ),
    (
        "fk_rpa_errors_tenant_job",
        "rpa_errors",
        ["tenant_id", "job_id"],
        ["rpa_jobs.tenant_id", "rpa_jobs.id"],
    ),
    (
        "fk_rpa_checkpoints_tenant_job",
        "rpa_checkpoints",
        ["tenant_id", "job_id"],
        ["rpa_jobs.tenant_id", "rpa_jobs.id"],
    ),
    (
        "fk_rpa_checkpoints_tenant_transaction",
        "rpa_checkpoints",
        ["tenant_id", "transaction_id", "job_id"],
        ["rpa_transactions.tenant_id", "rpa_transactions.id", "rpa_transactions.job_id"],
    ),
    (
        "fk_rpa_audit_log_tenant_job",
        "rpa_audit_log",
        ["tenant_id", "job_id"],
        ["rpa_jobs.tenant_id", "rpa_jobs.id"],
    ),
    (
        "fk_rpa_audit_log_tenant_transaction",
        "rpa_audit_log",
        ["tenant_id", "transaction_id", "job_id"],
        ["rpa_transactions.tenant_id", "rpa_transactions.id", "rpa_transactions.job_id"],
    ),
    (
        "fk_rpa_flow_versions_tenant_flow",
        "rpa_flow_versions",
        ["tenant_id", "flow_id"],
        ["rpa_flows.tenant_id", "rpa_flows.id"],
    ),
    (
        "fk_rpa_flow_runs_tenant_job",
        "rpa_flow_runs",
        ["tenant_id", "job_id"],
        ["rpa_jobs.tenant_id", "rpa_jobs.id"],
    ),
    (
        "fk_rpa_flow_runs_tenant_version",
        "rpa_flow_runs",
        ["tenant_id", "flow_id", "version"],
        ["rpa_flow_versions.tenant_id", "rpa_flow_versions.flow_id", "rpa_flow_versions.version"],
    ),
    (
        "fk_rpa_flow_schedules_tenant_job",
        "rpa_flow_schedules",
        ["tenant_id", "last_job_id"],
        ["rpa_jobs.tenant_id", "rpa_jobs.id"],
    ),
    (
        "fk_rpa_flow_schedules_tenant_version",
        "rpa_flow_schedules",
        ["tenant_id", "flow_id", "version"],
        ["rpa_flow_versions.tenant_id", "rpa_flow_versions.flow_id", "rpa_flow_versions.version"],
    ),
]


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO rpa_identity_tenants (id, name) VALUES "
            "('00000000-0000-0000-0000-000000000001', 'MultiRPA geliştirme verileri') "
            "ON CONFLICT (id) DO NOTHING"
        )
    )
    for table in TABLES:
        op.add_column(
            table, sa.Column("tenant_id", sa.Uuid(), server_default=LEGACY, nullable=False)
        )
        op.create_foreign_key(
            f"fk_{table}_tenant_id_rpa_identity_tenants",
            table,
            "rpa_identity_tenants",
            ["tenant_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    for name, table, columns in UNIQUES:
        op.create_unique_constraint(name, table, columns)
    for name, table, columns, targets in FOREIGN_KEYS:
        target_table = targets[0].split(".")[0]
        op.create_foreign_key(
            name,
            table,
            target_table,
            columns,
            [target.split(".")[1] for target in targets],
            ondelete="RESTRICT",
        )
    op.drop_constraint("uq_rpa_jobs_job_id", "rpa_jobs", type_="unique")
    op.drop_constraint("uq_rpa_transactions_idempotency_key", "rpa_transactions", type_="unique")
    op.drop_constraint("uq_rpa_flow_runs_request_id", "rpa_flow_runs", type_="unique")
    op.drop_constraint("pk_rpa_flow_schedules", "rpa_flow_schedules", type_="primary")
    op.create_primary_key("pk_rpa_flow_schedules", "rpa_flow_schedules", ["tenant_id", "id"])
    # A nullable parent would bypass the composite FK under MATCH SIMPLE.
    op.create_check_constraint(
        op.f("ck_rpa_audit_log_transaction_requires_job"),
        "rpa_audit_log",
        "transaction_id IS NULL OR job_id IS NOT NULL",
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        if connection.scalar(
            sa.text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE tenant_id <> :legacy)").bindparams(
                legacy=UUID(LEGACY)
            )
        ):
            raise RuntimeError("Customer execution data exists; use a forward migration")
    for table, column in [
        ("rpa_jobs", "job_id"),
        ("rpa_transactions", "idempotency_key"),
        ("rpa_flow_runs", "request_id"),
        ("rpa_flow_schedules", "id"),
    ]:
        if connection.scalar(
            sa.text(f"SELECT EXISTS(SELECT 1 FROM {table} GROUP BY {column} HAVING count(*) > 1)")
        ):
            raise RuntimeError(
                "Tenant migration downgrade would merge identities; use a forward migration"
            )
    for name, table, _, _ in reversed(FOREIGN_KEYS):
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_constraint(
        op.f("ck_rpa_audit_log_transaction_requires_job"), "rpa_audit_log", type_="check"
    )
    for name, table, _ in reversed(UNIQUES):
        op.drop_constraint(name, table, type_="unique")
    op.drop_constraint("pk_rpa_flow_schedules", "rpa_flow_schedules", type_="primary")
    op.create_primary_key("pk_rpa_flow_schedules", "rpa_flow_schedules", ["id"])
    op.create_unique_constraint("uq_rpa_jobs_job_id", "rpa_jobs", ["job_id"])
    op.create_unique_constraint(
        "uq_rpa_transactions_idempotency_key", "rpa_transactions", ["idempotency_key"]
    )
    op.create_unique_constraint("uq_rpa_flow_runs_request_id", "rpa_flow_runs", ["request_id"])
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_constraint(f"fk_{table}_tenant_id_rpa_identity_tenants", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")
