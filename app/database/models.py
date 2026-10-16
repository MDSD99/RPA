"""Versioned PostgreSQL execution schema. Tables are created only by Alembic."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.states import JobStatus, TransactionStatus
from app.core.tenancy import LEGACY_TENANT_ID


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantOwned:
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_TENANT_ID,
        server_default=str(LEGACY_TENANT_ID),
        index=True,
    )


class Job(TenantOwned, Timestamps, Base):
    __tablename__ = "rpa_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rpa_jobs_tenant_id"),
        UniqueConstraint("tenant_id", "job_id", name="uq_rpa_jobs_tenant_job_id"),
        CheckConstraint(
            "total_records >= 0 AND success_count >= 0 AND failed_count >= 0 "
            "AND skipped_count >= 0",
            name="nonnegative_counts",
        ),
        CheckConstraint(
            "success_count + failed_count + skipped_count <= total_records",
            name="count_totals",
        ),
        CheckConstraint(
            "length(job_id) > 0 AND length(workflow_name) > 0", name="identity_nonempty"
        ),
        Index("ix_rpa_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[str] = mapped_column(String(128))
    workflow_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, create_constraint=True, name="job_status"),
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_records: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Transaction(TenantOwned, Timestamps, Base):
    __tablename__ = "rpa_transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", "job_id", name="uq_rpa_transactions_tenant_id_job"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_rpa_transactions_tenant_key"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["rpa_jobs.tenant_id", "rpa_jobs.id"],
            name="fk_rpa_transactions_tenant_job",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("job_id", "record_id", name="uq_rpa_transactions_job_record"),
        UniqueConstraint("id", "job_id", name="uq_rpa_transactions_id_job"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 100 "
            "AND attempt_count <= max_attempts AND fencing_token >= 0",
            name="attempt_limits",
        ),
        CheckConstraint(
            "(status = 'PROCESSING' AND owner_id IS NOT NULL "
            "AND length(owner_id) > 0 AND lease_expires_at IS NOT NULL "
            "AND processing_started_at IS NOT NULL AND NOT reconciliation_required) OR "
            "(status <> 'PROCESSING' AND owner_id IS NULL AND lease_expires_at IS NULL)",
            name="processing_ownership",
        ),
        CheckConstraint(
            "length(record_id) > 0 AND length(idempotency_key) > 0 AND length(workflow_name) > 0",
            name="identity_nonempty",
        ),
        CheckConstraint(
            "last_error IS NULL OR last_error ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="safe_error_code",
        ),
        CheckConstraint(
            "jsonb_typeof(result_data) = 'object' AND octet_length(result_data::text) <= 16384",
            name="bounded_result",
        ),
        Index(
            "ix_rpa_transactions_claim",
            "job_id",
            "available_at",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'RETRY') AND NOT reconciliation_required"),
        ),
        Index(
            "ix_rpa_transactions_stale",
            "lease_expires_at",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("rpa_jobs.id", ondelete="RESTRICT"))
    workflow_name: Mapped[str] = mapped_column(String(128))
    record_id: Mapped[str] = mapped_column(String(256))
    idempotency_key: Mapped[str] = mapped_column(String(256))
    external_reference: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            native_enum=False,
            create_constraint=True,
            name="transaction_status",
        ),
        default=TransactionStatus.PENDING,
        server_default=TransactionStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reconciliation_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    last_error: Mapped[str | None] = mapped_column(String(64))
    result_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class Error(TenantOwned, Base):
    __tablename__ = "rpa_errors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["rpa_jobs.tenant_id", "rpa_jobs.id"],
            name="fk_rpa_errors_tenant_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "transaction_id", "job_id"],
            ["rpa_transactions.tenant_id", "rpa_transactions.id", "rpa_transactions.job_id"],
            name="fk_rpa_errors_tenant_transaction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["transaction_id", "job_id"],
            ["rpa_transactions.id", "rpa_transactions.job_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "error_type ~ '^[A-Z][A-Z0-9_]{0,63}$' AND error_message ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="safe_error_codes",
        ),
        Index("ix_rpa_errors_transaction_created", "transaction_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("rpa_jobs.id", ondelete="RESTRICT"))
    transaction_id: Mapped[UUID] = mapped_column()
    error_type: Mapped[str] = mapped_column(String(64))
    error_message: Mapped[str] = mapped_column(String(64))
    step: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Checkpoint(TenantOwned, Timestamps, Base):
    __tablename__ = "rpa_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["rpa_jobs.tenant_id", "rpa_jobs.id"],
            name="fk_rpa_checkpoints_tenant_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "transaction_id", "job_id"],
            ["rpa_transactions.tenant_id", "rpa_transactions.id", "rpa_transactions.job_id"],
            name="fk_rpa_checkpoints_tenant_transaction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["transaction_id", "job_id"],
            ["rpa_transactions.id", "rpa_transactions.job_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("transaction_id", "checkpoint_key", name="uq_rpa_checkpoints_tx_key"),
        CheckConstraint("fencing_token >= 0", name="nonnegative_fence"),
        CheckConstraint("length(checkpoint_key) > 0", name="key_nonempty"),
        CheckConstraint(
            "jsonb_typeof(checkpoint_value) = 'object' "
            "AND octet_length(checkpoint_value::text) <= 16384",
            name="bounded_checkpoint",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("rpa_jobs.id", ondelete="RESTRICT"))
    transaction_id: Mapped[UUID] = mapped_column()
    workflow: Mapped[str] = mapped_column(String(128))
    checkpoint_key: Mapped[str] = mapped_column(String(128))
    checkpoint_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class AuditLog(TenantOwned, Base):
    __tablename__ = "rpa_audit_log"
    __table_args__ = (
        CheckConstraint(
            "transaction_id IS NULL OR job_id IS NOT NULL", name="transaction_requires_job"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["rpa_jobs.tenant_id", "rpa_jobs.id"],
            name="fk_rpa_audit_log_tenant_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "transaction_id", "job_id"],
            ["rpa_transactions.tenant_id", "rpa_transactions.id", "rpa_transactions.job_id"],
            name="fk_rpa_audit_log_tenant_transaction",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["transaction_id", "job_id"],
            ["rpa_transactions.id", "rpa_transactions.job_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "old_value IS NULL OR (jsonb_typeof(old_value) = 'object' "
            "AND octet_length(old_value::text) <= 16384)",
            name="bounded_old_value",
        ),
        CheckConstraint(
            "new_value IS NULL OR (jsonb_typeof(new_value) = 'object' "
            "AND octet_length(new_value::text) <= 16384)",
            name="bounded_new_value",
        ),
        Index("ix_rpa_audit_log_job_created", "job_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("rpa_jobs.id", ondelete="RESTRICT"))
    transaction_id: Mapped[UUID | None] = mapped_column()
    record_id: Mapped[str | None] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Flow(TenantOwned, Timestamps, Base):
    """Current editable metadata; graph revisions are append-only rows."""

    __tablename__ = "rpa_flows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rpa_flows_tenant_id"),
        CheckConstraint("length(name) > 0 AND version >= 1", name="valid_identity"),
        Index("ix_rpa_flows_updated", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class FlowVersion(TenantOwned, Base):
    """Immutable graph snapshot selected explicitly by every run."""

    __tablename__ = "rpa_flow_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "flow_id", "version", name="uq_rpa_flow_versions_tenant_flow_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "flow_id"],
            ["rpa_flows.tenant_id", "rpa_flows.id"],
            name="fk_rpa_flow_versions_tenant_flow",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1 AND node_count BETWEEN 2 AND 40", name="valid_version"),
        CheckConstraint(
            "jsonb_typeof(graph) = 'object' AND octet_length(graph::text) <= 16384",
            name="bounded_graph",
        ),
    )

    flow_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_flows.id", ondelete="RESTRICT"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(1000))
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB)
    node_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowRun(TenantOwned, Base):
    """One idempotent run binds a job to an immutable version and inputs."""

    __tablename__ = "rpa_flow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "request_id", name="uq_rpa_flow_runs_tenant_request"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["rpa_jobs.tenant_id", "rpa_jobs.id"],
            name="fk_rpa_flow_runs_tenant_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "flow_id", "version"],
            [
                "rpa_flow_versions.tenant_id",
                "rpa_flow_versions.flow_id",
                "rpa_flow_versions.version",
            ],
            name="fk_rpa_flow_runs_tenant_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["flow_id", "version"],
            ["rpa_flow_versions.flow_id", "rpa_flow_versions.version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="attempt_limits"),
        CheckConstraint(
            "jsonb_typeof(inputs) = 'object' AND octet_length(inputs::text) <= 16384",
            name="bounded_inputs",
        ),
        CheckConstraint("inputs_digest ~ '^[0-9a-f]{64}$'", name="valid_inputs_digest"),
        Index("ix_rpa_flow_runs_flow_created", "flow_id", "created_at"),
    )

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_jobs.id", ondelete="RESTRICT"), primary_key=True
    )
    request_id: Mapped[UUID] = mapped_column()
    flow_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs_digest: Mapped[str] = mapped_column(String(64))
    max_attempts: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowSchedule(TenantOwned, Timestamps, Base):
    """A pinned flow version and its next atomically consumed due occurrence."""

    __tablename__ = "rpa_flow_schedules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "last_job_id"],
            ["rpa_jobs.tenant_id", "rpa_jobs.id"],
            name="fk_rpa_flow_schedules_tenant_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "flow_id", "version"],
            [
                "rpa_flow_versions.tenant_id",
                "rpa_flow_versions.flow_id",
                "rpa_flow_versions.version",
            ],
            name="fk_rpa_flow_schedules_tenant_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["flow_id", "version"],
            ["rpa_flow_versions.flow_id", "rpa_flow_versions.version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(name) > 0 AND revision >= 1", name="valid_identity"),
        CheckConstraint(
            "(kind = 'once' AND interval_seconds IS NULL) OR "
            "(kind = 'interval' AND interval_seconds IS NOT NULL "
            "AND interval_seconds BETWEEN 60 AND 2678400)",
            name="valid_frequency",
        ),
        CheckConstraint("NOT enabled OR next_run_at IS NOT NULL", name="enabled_due_date"),
        CheckConstraint("run_count >= 0 AND missed_runs >= 0", name="nonnegative_counters"),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="attempt_limits"),
        CheckConstraint(
            "jsonb_typeof(inputs) = 'object' AND octet_length(inputs::text) <= 16384",
            name="bounded_inputs",
        ),
        CheckConstraint("creation_digest ~ '^[0-9a-f]{64}$'", name="valid_creation_digest"),
        Index(
            "ix_rpa_flow_schedules_due",
            "next_run_at",
            "id",
            postgresql_where=text("enabled AND next_run_at IS NOT NULL"),
        ),
        Index("ix_rpa_flow_schedules_flow_created", "flow_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("rpa_identity_tenants.id", ondelete="RESTRICT"),
        primary_key=True,
        default=lambda: LEGACY_TENANT_ID,
        server_default=str(LEGACY_TENANT_ID),
        index=True,
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    creation_digest: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    flow_id: Mapped[UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    revision: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_job_id: Mapped[UUID | None] = mapped_column(ForeignKey("rpa_jobs.id", ondelete="RESTRICT"))
    run_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    missed_runs: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    max_attempts: Mapped[int] = mapped_column(Integer)
