"""Entitlement persistence; tenant row serializes absent-state/quota/device races."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.entitlements.domain import EntitlementError
from app.entitlements.models import DeviceChallenge, Entitlement, FlowReservation, LicenseDevice
from app.identity.models import Account, IdentityAudit, Tenant


class EntitlementRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def now(self) -> datetime:
        return cast(datetime, self.session.scalar(select(func.clock_timestamp())))

    def owner(self, tenant_id: UUID, *, lock: bool = False) -> Account:
        query = (
            select(Account)
            .where(Account.tenant_id == tenant_id, Account.is_admin.is_(False))
            .order_by(Account.id)
        )
        if lock:
            query = query.with_for_update()
        rows = list(self.session.scalars(query.execution_options(populate_existing=True)))
        if len(rows) != 1:
            raise EntitlementError("ACCOUNT_UNAVAILABLE")
        return rows[0]

    def lock(self, tenant_id: UUID) -> Account:
        # Identity approval/suspension locks accounts first, so keep the same order.
        owner = self.owner(tenant_id, lock=True)
        if (
            self.session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
            is None
        ):
            raise EntitlementError("ACCOUNT_UNAVAILABLE")
        return owner

    def state(self, tenant_id: UUID, *, create: bool = False) -> Entitlement | None:
        row = self.session.get(Entitlement, tenant_id, populate_existing=True)
        if row is None and create:
            row = Entitlement(tenant_id=tenant_id)
            self.add(row)
            self.session.flush()
        return row

    def count_flows(self, tenant_id: UUID) -> int:
        return cast(
            int,
            self.session.scalar(
                select(func.count())
                .select_from(FlowReservation)
                .where(FlowReservation.tenant_id == tenant_id)
            ),
        )

    def reservation(self, tenant_id: UUID, flow_id: UUID) -> FlowReservation | None:
        return self.session.get(FlowReservation, (tenant_id, flow_id))

    def flow_ids(self, tenant_id: UUID) -> list[str]:
        return [
            str(value)
            for value in self.session.scalars(
                select(FlowReservation.flow_id)
                .where(FlowReservation.tenant_id == tenant_id)
                .order_by(FlowReservation.flow_id)
            )
        ]

    def device(self, tenant_id: UUID) -> LicenseDevice | None:
        return self.session.scalar(
            select(LicenseDevice)
            .where(LicenseDevice.tenant_id == tenant_id, LicenseDevice.revoked_at.is_(None))
            .execution_options(populate_existing=True)
        )

    def key_seen(self, thumbprint: str) -> bool:
        return (
            self.session.scalar(
                select(LicenseDevice.id).where(LicenseDevice.key_thumbprint == thumbprint)
            )
            is not None
        )

    def challenge(self, tenant_id: UUID, challenge_id: UUID) -> DeviceChallenge | None:
        return self.session.scalar(
            select(DeviceChallenge)
            .where(DeviceChallenge.id == challenge_id, DeviceChallenge.tenant_id == tenant_id)
            .with_for_update()
        )

    def pending_challenges(self, tenant_id: UUID, now: datetime) -> int:
        return cast(
            int,
            self.session.scalar(
                select(func.count())
                .select_from(DeviceChallenge)
                .where(
                    DeviceChallenge.tenant_id == tenant_id,
                    DeviceChallenge.consumed_at.is_(None),
                    DeviceChallenge.expires_at > now,
                )
            ),
        )

    def administrator(self, actor_id: UUID) -> Account:
        actor = self.session.scalar(select(Account).where(Account.id == actor_id).with_for_update())
        if (
            actor is None
            or not actor.is_admin
            or not actor.email_verified
            or actor.approval != "approved"
            or actor.suspended
        ):
            raise EntitlementError("ADMIN_REQUIRED")
        return actor

    def audit(
        self,
        owner: Account,
        action: str,
        *,
        actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        self.add(
            IdentityAudit(
                user_id=owner.id, actor_id=actor_id or owner.id, action=action, reason=reason
            )
        )

    def add(self, row: object) -> None:
        self.session.add(row)

    def job_tenant(self, job_id: UUID) -> UUID | None:
        from app.database.models import Job

        return self.session.scalar(select(Job.tenant_id).where(Job.id == job_id))
