"""PostgreSQL proves one-time calendar trials and web/desktop lifetime quota."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, literal, select, update
from sqlalchemy.exc import IntegrityError

from app.entitlements.access import assert_access, assert_job_access, eligible_tenant, reserve_flow
from app.entitlements.domain import EntitlementError, calendar_month_after
from app.entitlements.models import Entitlement, FlowReservation
from app.entitlements.service import TrialService
from app.identity.models import Account, IdentityAudit, Tenant

pytestmark = pytest.mark.postgres


@pytest.fixture
def trials(session_factory):
    return TrialService(session_factory)


@pytest.fixture
def customer(session_factory):
    with session_factory.begin() as session:
        tenant = Tenant(id=uuid4(), name="Entitlement customer")
        session.add(tenant)
        session.flush()
        account = Account(
            id=uuid4(),
            tenant_id=tenant.id,
            email=f"entitlement-{uuid4().hex}@example.com",
            display_name="Deneme müşterisi",
            password_hash="unused-by-entitlement-service",
            email_verified=True,
            approval="approved",
        )
        session.add(account)
        session.flush()
        return account


@pytest.fixture
def administrator(session_factory):
    with session_factory.begin() as session:
        tenant = Tenant(id=uuid4(), name="Entitlement administrator")
        session.add(tenant)
        session.flush()
        account = Account(
            id=uuid4(),
            tenant_id=tenant.id,
            email=f"entitlement-admin-{uuid4().hex}@example.com",
            display_name="Lisans yöneticisi",
            password_hash="unused-by-entitlement-service",
            email_verified=True,
            approval="approved",
            is_admin=True,
            totp_secret_ciphertext="unused-by-entitlement-service",
        )
        session.add(account)
        session.flush()
        return account


def assert_error(code, action):
    with pytest.raises(EntitlementError) as error:
        action()
    assert error.value.code == code
    assert str(error.value) == code


@pytest.mark.parametrize(
    ("started", "expected"),
    [
        ("2026-01-31T12:34:56+00:00", "2026-02-28T12:34:56+00:00"),
        ("2028-01-31T12:34:56+00:00", "2028-02-29T12:34:56+00:00"),
        ("2026-03-31T12:34:56+00:00", "2026-04-30T12:34:56+00:00"),
        ("2026-12-15T12:34:56+00:00", "2027-01-15T12:34:56+00:00"),
        ("2026-02-28T12:34:56+00:00", "2026-03-28T12:34:56+00:00"),
        ("2026-01-31T01:00:00+03:00", "2026-02-28T22:00:00+00:00"),
    ],
)
def test_trial_uses_one_calendar_month_in_utc(started, expected):
    assert calendar_month_after(datetime.fromisoformat(started)) == datetime.fromisoformat(expected)


def test_calendar_policy_refuses_ambiguous_naive_timestamp():
    with pytest.raises(ValueError):
        calendar_month_after(datetime(2026, 1, 31))
    value = datetime(2026, 1, 31, 3, tzinfo=timezone(timedelta(hours=3)))
    assert calendar_month_after(value) == datetime(2026, 2, 28, tzinfo=UTC)


def test_status_does_not_start_trial_or_reserve_rights(trials, customer, session_factory):
    status = trials.status(customer.tenant_id)
    assert status["state"] == "not_started"
    assert status["started_at"] is None
    assert status["expires_at"] is None
    assert status["flows_used"] == 0
    assert status["flow_limit"] == 3
    with session_factory() as session:
        assert session.get(Entitlement, customer.tenant_id) is None
    assert_error(
        "TRIAL_NOT_STARTED", lambda: trials.reserve_for_desktop(customer.tenant_id, uuid4())
    )


@pytest.mark.parametrize(
    ("changes", "code", "state"),
    [
        ({"email_verified": False}, "ACCOUNT_UNAPPROVED", "unapproved"),
        ({"approval": "pending"}, "ACCOUNT_UNAPPROVED", "unapproved"),
        ({"approval": "rejected"}, "ACCOUNT_UNAPPROVED", "unapproved"),
        ({"suspended": True}, "ACCOUNT_SUSPENDED", "suspended"),
    ],
)
def test_current_account_approval_required_before_trial(
    trials, customer, session_factory, changes, code, state
):
    with session_factory.begin() as session:
        session.execute(update(Account).where(Account.id == customer.id).values(**changes))
    assert trials.status(customer.tenant_id)["state"] == state
    assert_error(code, lambda: trials.start(customer.tenant_id))
    with session_factory() as session:
        assert session.get(Entitlement, customer.tenant_id) is None


def test_parallel_trial_start_is_one_time_and_audited_once(trials, customer, session_factory):
    barrier = Barrier(6)

    def start(_):
        barrier.wait()
        return trials.start(customer.tenant_id)

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(start, range(6)))
    assert {row["state"] for row in outcomes} == {"active"}
    assert len({row["started_at"] for row in outcomes}) == 1
    assert len({row["expires_at"] for row in outcomes}) == 1
    with session_factory() as session:
        row = session.get(Entitlement, customer.tenant_id)
        assert row.trial_ends_at == calendar_month_after(row.trial_started_at)
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdentityAudit)
                .where(
                    IdentityAudit.user_id == customer.id, IdentityAudit.action == "TRIAL_STARTED"
                )
            )
            == 1
        )


def test_expired_trial_never_restarts_after_reinstall_or_repeated_start(
    trials, customer, session_factory
):
    trials.start(customer.tenant_id)
    trials.reserve_for_desktop(customer.tenant_id, uuid4())
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        began, ended = now - timedelta(days=62), now - timedelta(days=31)
        session.execute(
            update(Entitlement)
            .where(Entitlement.tenant_id == customer.tenant_id)
            .values(trial_started_at=began, trial_ends_at=ended)
        )
    # A newly constructed service models a fresh installation/session.
    result = TrialService(session_factory).start(customer.tenant_id)
    assert result["state"] == "expired"
    assert result["flows_used"] == 1
    with session_factory() as session:
        row = session.get(Entitlement, customer.tenant_id)
        assert row.trial_started_at == began
        assert row.trial_ends_at == ended
        assert_error("TRIAL_EXPIRED", lambda: assert_access(session, customer.tenant_id))
    assert_error("TRIAL_EXPIRED", lambda: trials.reserve_for_desktop(customer.tenant_id, uuid4()))


def test_web_and_desktop_share_three_lifetime_flow_identities(trials, customer, session_factory):
    trials.start(customer.tenant_id)
    first, second, third = uuid4(), uuid4(), uuid4()
    with session_factory.begin() as session:
        reserve_flow(session, customer.tenant_id, first)
    trials.reserve_for_desktop(customer.tenant_id, second)
    trials.reserve_for_desktop(customer.tenant_id, third)
    # Reusing a reserved identity across adapters charges no additional slot.
    assert trials.reserve_for_desktop(customer.tenant_id, first)["flows_used"] == 3
    with session_factory.begin() as session:
        reserve_flow(session, customer.tenant_id, second)
    assert_error(
        "FLOW_LIMIT_REACHED", lambda: trials.reserve_for_desktop(customer.tenant_id, uuid4())
    )
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(FlowReservation).where(FlowReservation.tenant_id == customer.tenant_id)
            )
        )
        assert {row.flow_id for row in rows} == {first, second, third}
        assert {row.source for row in rows} == {"web", "desktop"}
        # Reservations deliberately have no FK to a deletable local/web flow.
        assert len(rows) == 3


def test_concurrent_web_desktop_quota_requests_cannot_overdraw(trials, customer, session_factory):
    trials.start(customer.tenant_id)
    barrier = Barrier(8)

    def reserve(index):
        barrier.wait()
        try:
            with session_factory.begin() as session:
                reserve_flow(
                    session, customer.tenant_id, uuid4(), source="web" if index % 2 else "desktop"
                )
            return "reserved"
        except EntitlementError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(reserve, range(8)))
    assert outcomes.count("reserved") == 3
    assert outcomes.count("FLOW_LIMIT_REACHED") == 5
    assert trials.status(customer.tenant_id)["flows_used"] == 3


def test_failed_flow_transaction_rolls_back_quota(trials, customer, session_factory):
    trials.start(customer.tenant_id)
    flow_id = uuid4()
    with pytest.raises(RuntimeError), session_factory.begin() as session:
        reserve_flow(session, customer.tenant_id, flow_id)
        raise RuntimeError("Interrupted flow assembly")
    assert trials.status(customer.tenant_id)["flows_used"] == 0
    assert trials.reserve_for_desktop(customer.tenant_id, flow_id)["flows_used"] == 1


def test_access_checks_current_suspension_even_after_trial_start(trials, customer, session_factory):
    trials.start(customer.tenant_id)
    with session_factory.begin() as session:
        session.execute(update(Account).where(Account.id == customer.id).values(suspended=True))
    with session_factory() as session:
        assert_error("ACCOUNT_SUSPENDED", lambda: assert_access(session, customer.tenant_id))
    assert_error(
        "ACCOUNT_SUSPENDED", lambda: trials.reserve_for_desktop(customer.tenant_id, uuid4())
    )


def test_missing_account_and_job_do_not_receive_access(trials, session_factory):
    assert_error("ACCOUNT_UNAVAILABLE", lambda: trials.status(uuid4()))
    with session_factory() as session:
        assert_error("JOB_UNAVAILABLE", lambda: assert_job_access(session, uuid4()))


def test_admin_paid_grant_and_revoke_preserve_trial_history(
    trials, customer, administrator, session_factory
):
    original = trials.start(customer.tenant_id)
    for _ in range(3):
        trials.reserve_for_desktop(customer.tenant_id, uuid4())
    with session_factory() as session:
        expiry = session.scalar(select(func.clock_timestamp())) + timedelta(days=90)
    granted = trials.grant(administrator.id, customer.tenant_id, expiry, 5, "Plan yükseltme")
    assert granted["plan"] == "paid"
    assert granted["flow_limit"] == 5
    assert granted["started_at"] == original["started_at"]
    trials.reserve_for_desktop(customer.tenant_id, uuid4())
    revoked = trials.revoke(administrator.id, customer.tenant_id, "Ücretli plan iptali")
    assert revoked["plan"] == "paid"
    assert revoked["state"] == "expired"
    assert revoked["started_at"] == original["started_at"]
    assert revoked["trial_expires_at"] == original["expires_at"]
    assert revoked["flows_used"] == 4
    assert_error("LICENSE_REVOKED", lambda: trials.reserve_for_desktop(customer.tenant_id, uuid4()))
    assert trials.start(customer.tenant_id)["state"] == "expired"
    with session_factory() as session:
        assert_error("LICENSE_REVOKED", lambda: assert_access(session, customer.tenant_id))
        assert session.scalar(select(eligible_tenant(literal(customer.tenant_id)))) is False
    with session_factory() as session:
        audits = list(
            session.scalars(
                select(IdentityAudit).where(
                    IdentityAudit.user_id == customer.id,
                    IdentityAudit.action.in_(["LICENSE_GRANTED", "LICENSE_REVOKED"]),
                )
            )
        )
        assert len(audits) == 2
        assert {row.actor_id for row in audits} == {administrator.id}
        assert all(row.reason and row.correlation_id for row in audits)


def test_expired_paid_plan_cannot_fall_back_to_active_demo_or_overdrawn_flow_history(
    trials, customer, administrator, session_factory
):
    original = trials.start(customer.tenant_id)
    trials.grant(
        administrator.id,
        customer.tenant_id,
        datetime.now(UTC) + timedelta(days=1),
        5,
        "Ücretli kullanım",
    )
    for _ in range(5):
        trials.reserve_for_desktop(customer.tenant_id, uuid4())
    with session_factory.begin() as session:
        now = session.scalar(select(func.clock_timestamp()))
        session.execute(
            update(Entitlement)
            .where(Entitlement.tenant_id == customer.tenant_id)
            .values(paid_until=now - timedelta(seconds=1))
        )
    status = trials.start(customer.tenant_id)
    assert status["state"] == "expired"
    assert status["plan"] == "paid"
    assert status["flows_used"] == status["flow_limit"] == 5
    assert status["started_at"] == original["started_at"]
    assert status["trial_expires_at"] == original["expires_at"]
    with session_factory() as session:
        assert_error("LICENSE_EXPIRED", lambda: assert_access(session, customer.tenant_id))
        assert session.scalar(select(eligible_tenant(literal(customer.tenant_id)))) is False
    assert_error("LICENSE_EXPIRED", lambda: trials.reserve_for_desktop(customer.tenant_id, uuid4()))


def test_renewed_paid_grant_restores_access_without_resetting_demo_or_quota(
    trials, customer, administrator, session_factory
):
    original = trials.start(customer.tenant_id)
    trials.reserve_for_desktop(customer.tenant_id, uuid4())
    trials.revoke(administrator.id, customer.tenant_id, "Geçici lisans iptali")
    renewed = trials.grant(
        administrator.id,
        customer.tenant_id,
        datetime.now(UTC) + timedelta(days=30),
        3,
        "Lisans yeniden etkin",
    )
    assert renewed["state"] == "active"
    assert renewed["plan"] == "paid"
    assert renewed["started_at"] == original["started_at"]
    assert renewed["flows_used"] == 1
    with session_factory() as session:
        assert_access(session, customer.tenant_id)
        assert session.scalar(select(eligible_tenant(literal(customer.tenant_id)))) is True


def test_paid_customer_without_demo_cannot_start_demo_after_revocation(
    trials, customer, administrator
):
    trials.grant(
        administrator.id,
        customer.tenant_id,
        datetime.now(UTC) + timedelta(days=30),
        3,
        "Doğrudan ücretli lisans",
    )
    trials.revoke(administrator.id, customer.tenant_id, "Lisans iptali")
    status = trials.start(customer.tenant_id)
    assert status["state"] == "expired"
    assert status["started_at"] is None
    assert status["trial_expires_at"] is None


@pytest.mark.parametrize("flow_limit", [0, -1, 1001, True, 1.5])
def test_invalid_paid_limit_is_rejected(trials, customer, administrator, flow_limit):
    assert_error(
        "INVALID_GRANT",
        lambda: trials.grant(
            administrator.id,
            customer.tenant_id,
            datetime.now(UTC) + timedelta(days=1),
            flow_limit,
            "Plan talebi",
        ),
    )


def test_admin_grant_rejects_expiry_and_limit_below_reserved_usage(trials, customer, administrator):
    trials.start(customer.tenant_id)
    trials.reserve_for_desktop(customer.tenant_id, uuid4())
    trials.reserve_for_desktop(customer.tenant_id, uuid4())
    for expiry, limit in [
        (datetime.now(UTC) - timedelta(days=1), 3),
        (datetime.now(UTC) + timedelta(days=1), 1),
        (datetime.now(), 3),
    ]:
        assert_error(
            "INVALID_GRANT",
            lambda expiry=expiry, limit=limit: trials.grant(
                administrator.id, customer.tenant_id, expiry, limit, "Plan talebi"
            ),
        )


@pytest.mark.parametrize("reason", ["", "ab", "x" * 501, "Gerekçe\x00", "Gerekçe\u202e"])
def test_admin_actions_reject_harmful_or_missing_reason(trials, customer, administrator, reason):
    assert_error(
        "INVALID_REASON", lambda: trials.revoke(administrator.id, customer.tenant_id, reason)
    )


def test_customer_or_suspended_admin_cannot_grant_license(
    trials, customer, administrator, session_factory
):
    expiry = datetime.now(UTC) + timedelta(days=1)
    assert_error(
        "ADMIN_REQUIRED",
        lambda: trials.grant(customer.id, customer.tenant_id, expiry, 3, "Yetkisiz talep"),
    )
    with session_factory.begin() as session:
        session.execute(
            update(Account).where(Account.id == administrator.id).values(suspended=True)
        )
    assert_error(
        "ADMIN_REQUIRED",
        lambda: trials.revoke(administrator.id, customer.tenant_id, "Askıda yönetici"),
    )


@pytest.mark.parametrize(
    "values",
    [
        {"trial_started_at": datetime(2026, 1, 1, tzinfo=UTC), "trial_ends_at": None},
        {"trial_started_at": None, "trial_ends_at": datetime(2026, 1, 1, tzinfo=UTC)},
        {
            "trial_started_at": datetime(2026, 1, 2, tzinfo=UTC),
            "trial_ends_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
        {"paid_until": datetime(2026, 1, 1, tzinfo=UTC), "paid_flow_limit": None},
        {"paid_until": datetime(2026, 1, 1, tzinfo=UTC), "paid_flow_limit": 1001},
    ],
)
def test_database_rejects_partial_or_invalid_entitlement_windows(customer, session_factory, values):
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        session.add(Entitlement(tenant_id=customer.tenant_id, **values))
        session.flush()
