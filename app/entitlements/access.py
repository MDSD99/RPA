"""Same-transaction policy shared by API services, scheduler, and workers."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.tenancy import LEGACY_TENANT_ID
from app.entitlements.domain import EntitlementError
from app.entitlements.models import Entitlement, FlowReservation
from app.entitlements.repository import EntitlementRepository
from app.identity.models import Account


def assert_account(account: Account) -> None:
    if account.suspended:
        raise EntitlementError("ACCOUNT_SUSPENDED")
    if not account.email_verified or account.approval != "approved":
        raise EntitlementError("ACCOUNT_UNAPPROVED")


def active_window(state: Entitlement | None, now: datetime) -> tuple[datetime, int, str]:
    if state is not None and state.paid_until is not None:
        if state.paid_revoked_at is not None:
            raise EntitlementError("LICENSE_REVOKED")
        if state.paid_until <= now:
            raise EntitlementError("LICENSE_EXPIRED")
        return state.paid_until, state.paid_flow_limit or 3, "paid"
    if state is None or state.trial_started_at is None:
        raise EntitlementError("TRIAL_NOT_STARTED")
    if state.trial_ends_at is None or state.trial_ends_at <= now:
        raise EntitlementError("TRIAL_EXPIRED")
    return state.trial_ends_at, 3, "trial"


def assert_access(session: Session, tenant_id: UUID) -> None:
    if tenant_id == LEGACY_TENANT_ID:
        return
    repo = EntitlementRepository(session)
    assert_account(repo.owner(tenant_id))
    active_window(repo.state(tenant_id), repo.now())


def reserve_flow(session: Session, tenant_id: UUID, flow_id: UUID, *, source: str = "web") -> None:
    if tenant_id == LEGACY_TENANT_ID:
        return
    if source not in {"web", "desktop"}:
        raise EntitlementError("INVALID_RESERVATION_SOURCE")
    repo = EntitlementRepository(session)
    assert_account(repo.lock(tenant_id))
    _, limit, _ = active_window(repo.state(tenant_id), repo.now())
    if repo.reservation(tenant_id, flow_id) is not None:
        return
    if repo.count_flows(tenant_id) >= limit:
        raise EntitlementError("FLOW_LIMIT_REACHED")
    repo.add(FlowReservation(tenant_id=tenant_id, flow_id=flow_id, source=source))
    session.flush()


def assert_job_access(session: Session, job_id: UUID) -> None:
    tenant_id = EntitlementRepository(session).job_tenant(job_id)
    if tenant_id is None:
        raise EntitlementError("JOB_UNAVAILABLE")
    assert_access(session, tenant_id)


def eligible_tenant(tenant_column: ColumnElement[UUID]) -> ColumnElement[bool]:
    approved = exists(
        select(Account.id).where(
            Account.tenant_id == tenant_column,
            Account.is_admin.is_(False),
            Account.email_verified.is_(True),
            Account.approval == "approved",
            Account.suspended.is_(False),
        )
    )
    entitled = exists(
        select(Entitlement.tenant_id).where(
            Entitlement.tenant_id == tenant_column,
            or_(
                and_(
                    Entitlement.paid_until > func.clock_timestamp(),
                    Entitlement.paid_revoked_at.is_(None),
                ),
                and_(
                    Entitlement.paid_until.is_(None),
                    Entitlement.trial_ends_at > func.clock_timestamp(),
                ),
            ),
        )
    )
    return or_(tenant_column == LEGACY_TENANT_ID, and_(approved, entitled))
