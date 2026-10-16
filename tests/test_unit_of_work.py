"""Session acquisition may retry; entered business transactions must never replay."""

import traceback
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseOperationError, ValidationError
from app.database.unit_of_work import DatabaseUnitOfWork


def _failure(error_type=OperationalError):
    return error_type(
        "SELECT fake_sensitive_statement",
        {"password": "fake_sensitive_parameter"},
        RuntimeError("fake_sensitive_driver_detail"),
    )


def test_transient_acquisition_closes_failed_sessions_before_running_business_once(monkeypatch):
    first, second, ready = [Mock(spec=Session) for _ in range(3)]
    first.connection.side_effect = _failure()
    second.connection.side_effect = _failure()
    factory = Mock(kw={"info": {"connection_attempts": 3}}, side_effect=[first, second, ready])
    sleep = Mock()
    monkeypatch.setattr("app.database.unit_of_work.time.sleep", sleep)
    work = Mock()

    with DatabaseUnitOfWork(factory).transaction() as session:
        work(session)

    work.assert_called_once_with(ready)
    assert factory.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.1, 0.2]
    for failed in (first, second):
        failed.close.assert_called_once_with()
        failed.commit.assert_not_called()
    ready.commit.assert_called_once_with()
    ready.close.assert_called_once_with()


@pytest.mark.parametrize("error_type, attempts", [(OperationalError, 3), (DBAPIError, 1)])
def test_acquisition_failure_is_bounded_sanitized_and_never_enters_business(
    monkeypatch, error_type, attempts
):
    sessions = [Mock(spec=Session) for _ in range(3)]
    for session in sessions:
        session.connection.side_effect = _failure(error_type)
    factory = Mock(kw={"info": {"connection_attempts": 3}}, side_effect=sessions)
    monkeypatch.setattr("app.database.unit_of_work.time.sleep", Mock())
    work = Mock()

    with pytest.raises(DatabaseOperationError) as caught:
        with DatabaseUnitOfWork(factory).transaction() as session:
            work(session)

    assert factory.call_count == attempts
    work.assert_not_called()
    assert "fake_sensitive" not in "".join(traceback.format_exception(caught.value))
    for session in sessions[:attempts]:
        session.close.assert_called_once_with()
        session.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["body", "flush", "commit"])
def test_failure_after_acquisition_never_repeats_business_or_reopens_session(stage):
    session = Mock(spec=Session)
    factory = Mock(kw={}, return_value=session)
    work = Mock()
    if stage == "body":
        work.side_effect = _failure()
    else:
        getattr(session, stage).side_effect = _failure()

    with pytest.raises(DatabaseOperationError, match="re-read execution state") as caught:
        with DatabaseUnitOfWork(factory).transaction() as acquired:
            work(acquired)

    factory.assert_called_once_with()
    work.assert_called_once_with(session)
    session.close.assert_called_once_with()
    assert session.commit.call_count == (1 if stage == "commit" else 0)
    assert "fake_sensitive" not in "".join(traceback.format_exception(caught.value))


def test_domain_rejection_closes_transaction_without_committing_or_replacing_error():
    session = Mock(spec=Session)
    factory = Mock(kw={}, return_value=session)
    rejection = ValidationError("Business configuration rejected")

    with pytest.raises(ValidationError) as caught:
        with DatabaseUnitOfWork(factory).transaction():
            raise rejection

    assert caught.value is rejection
    factory.assert_called_once_with()
    session.commit.assert_not_called()
    session.close.assert_called_once_with()


@pytest.mark.parametrize("attempts", [0, 6, True, 1.5, "3"])
def test_invalid_acquisition_policy_is_rejected_before_session_creation(attempts):
    factory = Mock(kw={"info": {"connection_attempts": attempts}})

    with pytest.raises(ValidationError, match="Connection attempts"):
        DatabaseUnitOfWork(factory)

    factory.assert_not_called()
