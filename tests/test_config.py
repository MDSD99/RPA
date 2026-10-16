"""Security boundaries for configuration; these tests never connect to a database."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from app.core.config import ConfigurationError, DatabaseSettings
from app.database import connection


@pytest.fixture(autouse=True)
def local_hostname_without_dns(monkeypatch):
    monkeypatch.setattr("app.core.config.socket.gethostname", lambda: "rpa-host.example")
    monkeypatch.setattr("app.core.config.socket.getfqdn", lambda: "rpa-host.example")


@pytest.fixture
def test_env():
    return {
        "APP_ENV": "test",
        "DB_HOST": "127.0.0.1",
        "DB_NAME": "rpa_test_config",
        "DB_USER": "fake_sensitive_user",
        "DB_PASSWORD": "fake_sensitive_password",
        "DB_SSLMODE": "disable",
        "RPA_TEST_DB_CONFIRM": "rpa_test_config",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"DB_NAME": "rpa_production", "RPA_TEST_DB_CONFIRM": "rpa_production"},
        {"DB_NAME": "postgres", "RPA_TEST_DB_CONFIRM": "postgres"},
        {"DB_NAME": "rpa_test_", "RPA_TEST_DB_CONFIRM": "rpa_test_"},
        {"DB_HOST": "production.internal.example"},
        {"DB_HOST": "/var/run/postgresql"},
        {"RPA_TEST_DB_CONFIRM": "rpa_test_other"},
        {"RPA_TEST_DB_CONFIRM": ""},
    ],
)
def test_unsafe_test_target_is_rejected_before_engine_creation(monkeypatch, test_env, changes):
    create_engine = Mock(side_effect=AssertionError("A database engine must not be created."))
    monkeypatch.setattr(connection, "create_engine", create_engine)
    with pytest.raises(ConfigurationError):
        connection.create_db_engine(DatabaseSettings.from_env(test_env | changes))
    create_engine.assert_not_called()


def test_manually_constructed_settings_cannot_bypass_test_guard(monkeypatch, test_env):
    settings = replace(
        DatabaseSettings.from_env(test_env), name="production", test_db_confirm="production"
    )
    create_engine = Mock(side_effect=AssertionError("A database engine must not be created."))
    monkeypatch.setattr(connection, "create_engine", create_engine)
    with pytest.raises(ConfigurationError):
        connection.create_db_engine(settings)
    create_engine.assert_not_called()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_explicitly_confirmed_loopback_database_is_accepted(test_env, host):
    settings = DatabaseSettings.from_env(test_env | {"DB_HOST": host})
    assert settings.app_env == "test"
    assert settings.name == settings.test_db_confirm == "rpa_test_config"


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_credentials_have_no_implicit_defaults(test_env, missing):
    test_env.pop(missing)
    with pytest.raises(ConfigurationError) as caught:
        DatabaseSettings.from_env(test_env)
    assert "fake_sensitive_" not in str(caught.value)


def test_database_url_is_not_an_unsafe_configuration_fallback():
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_env(
            {"APP_ENV": "test", "DATABASE_URL": "postgresql://fake:fake@fake/production"}
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DB_PORT", "0"),
        ("DB_PORT", "65536"),
        ("DB_PORT", "fake_sensitive_numeric_value"),
        ("DB_POOL_SIZE", "0"),
        ("DB_MAX_OVERFLOW", "-1"),
        ("DB_CONNECT_ATTEMPTS", "0"),
        ("DB_CONNECT_ATTEMPTS", "6"),
        ("DB_CONNECT_TIMEOUT", "61"),
        ("DB_LOCK_TIMEOUT_MS", "0"),
        ("DB_STATEMENT_TIMEOUT_MS", "600001"),
    ],
)
def test_numeric_environment_bounds_fail_without_leaking_values(test_env, field, value):
    with pytest.raises(ConfigurationError) as caught:
        DatabaseSettings.from_env(test_env | {field: value})
    assert "fake_sensitive_" not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"port": 0},
        {"connect_attempts": 6},
        {"connect_attempts": True},
        {"statement_timeout_ms": "1 -c search_path=unsafe"},
    ],
)
def test_direct_constructor_cannot_bypass_numeric_validation(monkeypatch, test_env, changes):
    settings = replace(DatabaseSettings.from_env(test_env), **changes)
    create_engine = Mock()
    monkeypatch.setattr(connection, "create_engine", create_engine)
    with pytest.raises(ConfigurationError):
        connection.create_db_engine(settings)
    create_engine.assert_not_called()


def test_environment_defaults_and_redacted_representation(test_env):
    test_env.pop("APP_ENV")
    test_env.pop("DB_SSLMODE")
    settings = DatabaseSettings.from_env(test_env)
    assert settings.app_env == "development"
    assert settings.port == 5432
    assert settings.sslmode == "prefer"
    assert settings.connect_attempts == 3
    assert settings.pool_size > 0
    assert "fake_sensitive_" not in repr(settings)
    assert settings.name not in repr(settings)
    assert settings.host not in repr(settings)


def test_unknown_environment_error_does_not_echo_input(test_env):
    with pytest.raises(ConfigurationError) as caught:
        DatabaseSettings.from_env(test_env | {"APP_ENV": "fake_sensitive_environment"})
    assert "fake_sensitive_" not in str(caught.value)


def _production_env(test_env):
    env = test_env | {"APP_ENV": "production", "DB_HOST": "postgres.internal.example"}
    env.pop("DB_SSLMODE")
    return env


def test_production_requires_verified_tls_by_default(test_env):
    assert DatabaseSettings.from_env(_production_env(test_env)).sslmode == "verify-full"


@pytest.mark.parametrize("sslmode", ["disable", "prefer", "require", "verify-ca"])
def test_production_refuses_tls_without_hostname_verification(test_env, sslmode):
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_env(_production_env(test_env) | {"DB_SSLMODE": sslmode})


@pytest.mark.parametrize("user", ["postgres", "root", "POSTGRES"])
def test_production_refuses_administrative_role_names(test_env, user):
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_env(_production_env(test_env) | {"DB_USER": user})


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost.",
        " LOCALHOST. ",
        "127.0.0.1",
        "127.0.0.1.",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.1.1",
        "0.0.0.0",
        "169.254.1.1",
        "/var/run/postgresql",
        "postgres.internal.example,localhost",
        "rpa-host.example",
        "RPA-HOST.EXAMPLE.",
    ],
)
def test_production_refuses_embedded_or_ambiguous_database_hosts(monkeypatch, test_env, host):
    monkeypatch.setattr("app.core.config.socket.gethostname", lambda: "rpa-host.example")
    monkeypatch.setattr("app.core.config.socket.getfqdn", lambda: "rpa-host.example")
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_env(_production_env(test_env) | {"DB_HOST": host})
