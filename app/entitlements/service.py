"""Trial start, shared quota reporting, and audited manual paid entitlements."""

import unicodedata
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.database.unit_of_work import DatabaseUnitOfWork
from app.entitlements.access import active_window, assert_account, reserve_flow
from app.entitlements.domain import EntitlementError, calendar_month_after, timestamp
from app.entitlements.repository import EntitlementRepository


def status_in_session(repo: EntitlementRepository, tenant_id: UUID) -> dict[str, object]:
    owner = repo.owner(tenant_id)
    row = repo.state(tenant_id)
    now = repo.now()
    state, plan, limit, expiry = "not_started", "trial", 3, row.trial_ends_at if row else None
    if row is not None and row.paid_until is not None:
        plan, limit, expiry = "paid", row.paid_flow_limit or 3, row.paid_until
    try:
        expiry, limit, plan = active_window(row, now)
        state = "active"
    except EntitlementError as error:
        if error.code in {"TRIAL_EXPIRED", "LICENSE_EXPIRED", "LICENSE_REVOKED"}:
            state = "expired"
    if owner.suspended:
        state = "suspended"
    elif not owner.email_verified or owner.approval != "approved":
        state = "unapproved"
    device = repo.device(tenant_id)
    return {
        "state": state,
        "plan": plan,
        "started_at": timestamp(row.trial_started_at) if row else None,
        "expires_at": timestamp(expiry),
        "trial_expires_at": timestamp(row.trial_ends_at) if row else None,
        "flow_limit": limit,
        "flows_used": repo.count_flows(tenant_id),
        "device_id": str(device.id) if device else None,
        "device_blocked_until": timestamp(row.device_blocked_until) if row else None,
        "server_time": timestamp(now),
    }


def require_reason(reason: str) -> str:
    if (
        not isinstance(reason, str)
        or not 3 <= len(reason.strip()) <= 500
        or any(
            unicodedata.category(char).startswith("C") and char not in "\n\r\t" for char in reason
        )
    ):
        raise EntitlementError("INVALID_REASON")
    return reason.strip()


class TrialService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.uow = DatabaseUnitOfWork(session_factory)

    def status(self, tenant_id: UUID) -> dict[str, object]:
        with self.uow.transaction() as session:
            return status_in_session(EntitlementRepository(session), tenant_id)

    def start(self, tenant_id: UUID) -> dict[str, object]:
        with self.uow.transaction() as session:
            repo = EntitlementRepository(session)
            owner = repo.lock(tenant_id)
            assert_account(owner)
            row = repo.state(tenant_id, create=True)
            assert row is not None
            if row.trial_started_at is None and row.paid_until is None:
                row.trial_started_at = repo.now()
                row.trial_ends_at = calendar_month_after(row.trial_started_at)
                repo.audit(owner, "TRIAL_STARTED")
                session.flush()
            return status_in_session(repo, tenant_id)

    def reserve_for_desktop(self, tenant_id: UUID, flow_id: UUID) -> dict[str, object]:
        with self.uow.transaction() as session:
            reserve_flow(session, tenant_id, flow_id, source="desktop")
            return status_in_session(EntitlementRepository(session), tenant_id)

    def grant(
        self, actor_id: UUID, tenant_id: UUID, expires_at: datetime, flow_limit: int, reason: str
    ) -> dict[str, object]:
        reason = require_reason(reason)
        if (
            type(flow_limit) is not int
            or not 1 <= flow_limit <= 1000
            or not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
        ):
            raise EntitlementError("INVALID_GRANT")
        with self.uow.transaction() as session:
            repo = EntitlementRepository(session)
            repo.administrator(actor_id)
            owner = repo.lock(tenant_id)
            if expires_at <= repo.now() or flow_limit < repo.count_flows(tenant_id):
                raise EntitlementError("INVALID_GRANT")
            row = repo.state(tenant_id, create=True)
            assert row is not None
            row.paid_until = expires_at.astimezone(UTC)
            row.paid_flow_limit = flow_limit
            row.paid_revoked_at = None
            repo.audit(owner, "LICENSE_GRANTED", actor_id=actor_id, reason=reason)
            session.flush()
            return status_in_session(repo, tenant_id)

    def revoke(self, actor_id: UUID, tenant_id: UUID, reason: str) -> dict[str, object]:
        reason = require_reason(reason)
        with self.uow.transaction() as session:
            repo = EntitlementRepository(session)
            repo.administrator(actor_id)
            owner = repo.lock(tenant_id)
            row = repo.state(tenant_id, create=True)
            assert row is not None
            now = repo.now()
            # An explicit revoke is an execution stop, even during an old demo.
            # Retain the paid plan/history so start cannot fall back to a trial.
            if row.paid_until is None:
                row.paid_until = now
                row.paid_flow_limit = max(3, repo.count_flows(tenant_id))
            row.paid_revoked_at = now
            repo.audit(owner, "LICENSE_REVOKED", actor_id=actor_id, reason=reason)
            session.flush()
            return status_in_session(repo, tenant_id)
