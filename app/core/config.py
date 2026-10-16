"""Environment-only database configuration. Never interpolate secrets into errors."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigurationError(ValueError):
    """A safe, value-free explanation of invalid configuration."""


def _integer(env: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(env.get(key, str(default)))
    except (ValueError, TypeError):
        raise ConfigurationError(f"{key} must be an integer.") from None
    if not low <= value <= high:
        raise ConfigurationError(f"{key} is outside the allowed range.")
    return value


@dataclass(frozen=True, repr=False)
class DatabaseSettings:
    app_env: str
    host: str = field(repr=False)
    port: int
    name: str = field(repr=False)
    user: str = field(repr=False)
    password: str = field(repr=False)
    sslmode: str
    sslrootcert: str | None = field(default=None, repr=False)
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout: int = 30
    connect_timeout: int = 5
    connect_attempts: int = 3
    statement_timeout_ms: int = 30000
    lock_timeout_ms: int = 5000
    test_db_confirm: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"DatabaseSettings(app_env={self.app_env!r}, credentials=<redacted>)"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabaseSettings:
        source = os.environ if env is None else env
        app_env = source.get("APP_ENV", "development")
        required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
        if any(not source.get(key) for key in required):
            raise ConfigurationError("DB_HOST, DB_NAME, DB_USER and DB_PASSWORD are required.")
        settings = cls(
            app_env=app_env,
            host=source["DB_HOST"],
            port=_integer(source, "DB_PORT", 5432, 1, 65535),
            name=source["DB_NAME"],
            user=source["DB_USER"],
            password=source["DB_PASSWORD"],
            sslmode=source.get(
                "DB_SSLMODE", "verify-full" if app_env == "production" else "prefer"
            ),
            sslrootcert=source.get("DB_SSLROOTCERT") or None,
            pool_size=_integer(source, "DB_POOL_SIZE", 5, 1, 100),
            max_overflow=_integer(source, "DB_MAX_OVERFLOW", 5, 0, 100),
            pool_timeout=_integer(source, "DB_POOL_TIMEOUT", 30, 1, 300),
            connect_timeout=_integer(source, "DB_CONNECT_TIMEOUT", 5, 1, 60),
            connect_attempts=_integer(source, "DB_CONNECT_ATTEMPTS", 3, 1, 5),
            statement_timeout_ms=_integer(source, "DB_STATEMENT_TIMEOUT_MS", 30000, 1, 600000),
            lock_timeout_ms=_integer(source, "DB_LOCK_TIMEOUT_MS", 5000, 1, 60000),
            test_db_confirm=source.get("RPA_TEST_DB_CONFIRM"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Run before engine creation, including for manually constructed settings."""
        if self.app_env not in {"development", "test", "production"}:
            raise ConfigurationError("APP_ENV must be development, test or production.")
        if not all((self.host, self.name, self.user, self.password)):
            raise ConfigurationError("Database connection fields must not be empty.")
        limits = {
            "port": (1, 65535),
            "pool_size": (1, 100),
            "max_overflow": (0, 100),
            "pool_timeout": (1, 300),
            "connect_timeout": (1, 60),
            "connect_attempts": (1, 5),
            "statement_timeout_ms": (1, 600000),
            "lock_timeout_ms": (1, 60000),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ConfigurationError("Database numeric setting is outside the allowed range.")
        if self.sslmode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ConfigurationError("DB_SSLMODE is invalid.")
        if self.app_env == "test":
            if self.host not in {"127.0.0.1", "::1", "localhost"}:
                raise ConfigurationError("Test database must use a loopback host.")
            if len(self.name) > 63 or not re.fullmatch(r"rpa_test_[a-z0-9_]+", self.name):
                raise ConfigurationError("Test database name must begin with rpa_test_.")
            if self.test_db_confirm != self.name:
                raise ConfigurationError("RPA_TEST_DB_CONFIRM must exactly match DB_NAME.")
        if self.app_env == "production":
            if self.sslmode != "verify-full":
                raise ConfigurationError("Production requires DB_SSLMODE=verify-full.")
            if self.user.lower() in {"postgres", "root"}:
                raise ConfigurationError(
                    "Production requires a dedicated least-privileged DB_USER."
                )
            normalized_host = self.host.strip().rstrip(".").lower()
            local_hosts = {
                "localhost",
                socket.gethostname().strip().rstrip(".").lower(),
                socket.getfqdn().strip().rstrip(".").lower(),
            }
            if normalized_host in local_hosts:
                raise ConfigurationError("Production database must be on a separate server.")
            if "/" in self.host or "," in self.host:
                raise ConfigurationError("Production requires one remote database hostname.")
            try:
                address = ipaddress.ip_address(normalized_host)
            except ValueError:
                pass
            else:
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                    address = address.ipv4_mapped
                if address.is_loopback or address.is_unspecified or address.is_link_local:
                    raise ConfigurationError("Production database must be on a separate server.")
