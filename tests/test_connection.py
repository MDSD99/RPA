"""Connection acquisition retry and secret-handling tests without network access."""

import traceback
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import ConfigurationError, DatabaseSettings
from app.database import connection


@pytest.fixture(autouse=True)
def local_hostname_without_dns(monkeypatch):
    monkeypatch.setattr("app.core.config.socket.gethostname", lambda: "rpa-host.example")
    monkeypatch.setattr("app.core.config.socket.getfqdn", lambda: "rpa-host.example")


def _settings(*, production=False):
    return DatabaseSettings.from_env(
        {
            "APP_ENV": "production" if production else "test",
            "DB_HOST": "postgres.internal.example" if production else "127.0.0.1",
            "DB_NAME": "rpa_test_connection",
            "DB_USER": "fake_user",
            "DB_PASSWORD": "fake-password-with-@:/#%characters",
            "DB_SSLMODE": "verify-full" if production else "disable",
            "RPA_TEST_DB_CONFIRM": "rpa_test_connection",
            "DB_CONNECT_ATTEMPTS": "2",
        }
    )


def _failure():
    return OperationalError(
        "SELECT fake_sensitive_statement",
        {"password": "fake_sensitive_parameter"},
        RuntimeError("fake_sensitive_driver_detail"),
    )


def test_engine_creation_is_lazy_and_hides_parameters(monkeypatch):
    engine = Mock()
    create_engine = Mock(return_value=engine)
    monkeypatch.setattr(connection, "create_engine", create_engine)
    settings = _settings()
    assert connection.create_db_engine(settings) is engine
    args, kwargs = create_engine.call_args
    assert args[0].drivername == "postgresql+psycopg"
    assert args[0].password == settings.password  # Special characters are not URL-interpolated.
    assert kwargs["hide_parameters"] is True
    assert kwargs["echo"] is False
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["execution_options"]["rpa_connect_attempts"] == 2
    engine.connect.assert_not_called()


def test_transient_acquisition_failure_retries_then_returns_one_connection(monkeypatch):
    acquired = Mock()
    engine = Mock()
    engine.connect.side_effect = [_failure(), _failure(), acquired]
    sleep = Mock()
    monkeypatch.setattr(connection.time, "sleep", sleep)
    assert connection.connect_with_retry(engine, attempts=3, delay_seconds=0.1) is acquired
    assert engine.connect.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.1, 0.2]
    acquired.begin.assert_not_called()
    acquired.execute.assert_not_called()
    acquired.commit.assert_not_called()


def test_exhausted_acquisition_retry_is_bounded_and_hides_driver_details(monkeypatch):
    engine = Mock()
    engine.connect.side_effect = _failure()
    sleep = Mock()
    monkeypatch.setattr(connection.time, "sleep", sleep)
    with pytest.raises(connection.DatabaseConnectionError) as caught:
        connection.connect_with_retry(engine, attempts=3, delay_seconds=0)
    assert engine.connect.call_count == 3
    assert sleep.call_count == 2
    rendered = "".join(traceback.format_exception(caught.value))
    assert "fake_sensitive" not in rendered
    assert caught.value.__suppress_context__ is True


def test_retry_disabled_still_attempts_acquisition_once(monkeypatch):
    engine = Mock()
    engine.connect.side_effect = _failure()
    sleep = Mock()
    monkeypatch.setattr(connection.time, "sleep", sleep)
    with pytest.raises(connection.DatabaseConnectionError):
        connection.connect_with_retry(engine, attempts=1)
    engine.connect.assert_called_once_with()
    sleep.assert_not_called()


def test_non_connection_failure_is_never_retried(monkeypatch):
    engine = Mock()
    engine.connect.side_effect = ConfigurationError("Database role is not permitted.")
    sleep = Mock()
    monkeypatch.setattr(connection.time, "sleep", sleep)
    with pytest.raises(ConfigurationError):
        connection.connect_with_retry(engine)
    engine.connect.assert_called_once_with()
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempts": 0},
        {"attempts": 6},
        {"attempts": True},
        {"attempts": 1.5},
        {"delay_seconds": -1},
        {"delay_seconds": 6},
        {"delay_seconds": True},
        {"delay_seconds": float("nan")},
        {"delay_seconds": float("inf")},
        {"delay_seconds": "fake"},
    ],
)
def test_invalid_retry_settings_do_not_start_acquisition(kwargs):
    engine = Mock()
    with pytest.raises(ValueError):
        connection.connect_with_retry(engine, **kwargs)
    engine.connect.assert_not_called()


@pytest.mark.parametrize(
    "role",
    [None, (True, False, False), (False, True, False), (False, False, True)],
)
def test_production_checks_actual_database_role_privileges(monkeypatch, role):
    listeners = []
    monkeypatch.setattr(connection, "create_engine", Mock(return_value=Mock()))

    def listens_for(_engine, event_name):
        assert event_name == "connect"

        def register(callback):
            listeners.append(callback)
            return callback

        return register

    monkeypatch.setattr(connection.event, "listens_for", listens_for)
    connection.create_db_engine(_settings(production=True))
    assert len(listeners) == 1
    dbapi = MagicMock()
    cursor = dbapi.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = role
    with pytest.raises(ConfigurationError) as caught:
        listeners[0](dbapi, Mock())
    assert "fake" not in str(caught.value)
    dbapi.close.assert_called_once_with()


def test_valid_production_role_check_leaves_no_open_transaction(monkeypatch):
    callbacks = []
    monkeypatch.setattr(connection, "create_engine", Mock(return_value=Mock()))

    def listens_for(_engine, _event):
        def register(callback):
            callbacks.append(callback)
            return callback

        return register

    monkeypatch.setattr(connection.event, "listens_for", listens_for)
    connection.create_db_engine(_settings(production=True))
    dbapi = MagicMock()
    dbapi.cursor.return_value.__enter__.return_value.fetchone.return_value = (False, False, False)
    callbacks[0](dbapi, Mock())
    dbapi.rollback.assert_called_once_with()
    dbapi.close.assert_not_called()


def test_session_scope_does_not_replay_work_when_commit_result_is_uncertain():
    session = MagicMock()
    session.__enter__.return_value = session
    session.begin.return_value.__exit__.side_effect = _failure()
    factory = Mock(return_value=session)
    operation = Mock()
    with pytest.raises(OperationalError):
        with connection.session_scope(factory):
            operation()
    factory.assert_called_once_with()
    operation.assert_called_once_with()
    session.begin.assert_called_once_with()
