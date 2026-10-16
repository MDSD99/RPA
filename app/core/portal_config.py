"""Explicit identity configuration; secrets never appear in representations."""

import base64
import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from email_validator import EmailNotValidError, validate_email

from app.core.config import ConfigurationError, _integer


@dataclass(frozen=True)
class PortalSettings:
    public_url: str
    secret_key: str = field(repr=False)
    app_env: str = "development"
    license_signing_key: str = field(default="", repr=False)
    release_dir: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.app_env not in {"development", "test", "production"}:
            raise ConfigurationError("Portal environment must be development, test or production")
        if self.app_env == "production" and not self.license_signing_key:
            raise ConfigurationError("Production requires a dedicated license signing key")
        try:
            url = urlsplit(self.public_url)
            valid = (
                bool(url.hostname)
                and url.username is None
                and url.password is None
                and url.path in {"", "/"}
                and not url.query
                and not url.fragment
                and url.port != 0
                and (
                    url.scheme == "https"
                    or (
                        url.scheme == "http"
                        and (
                            (
                                self.app_env != "production"
                                and url.hostname in {"127.0.0.1", "localhost"}
                            )
                            or (self.app_env == "test" and url.hostname == "testserver")
                        )
                    )
                )
            )
            Fernet(self.secret_key.encode("ascii"))
            if self.license_signing_key:
                from app.entitlements.crypto import decode
                from app.entitlements.domain import EntitlementError

                try:
                    decode(self.license_signing_key, 32)
                except EntitlementError:
                    raise ValueError("Invalid signing key") from None
        except (ValueError, TypeError, UnicodeError, AttributeError):
            raise ConfigurationError("Portal URL or encryption key is invalid") from None
        if not valid:
            raise ConfigurationError("Portal URL must be a trusted HTTPS origin or local test URL")

    @property
    def secure_cookie(self) -> bool:
        return urlsplit(self.public_url).scheme == "https"

    @property
    def hostname(self) -> str:
        return urlsplit(self.public_url).hostname or ""

    @property
    def signing_key(self) -> str:
        if self.license_signing_key:
            return self.license_signing_key
        # Existing installations retain a stable, domain-separated key until
        # operators provision a dedicated signing key. Never generate per boot.
        raw = hmac.new(
            base64.urlsafe_b64decode(self.secret_key),
            b"MultiRPA Ed25519 license signing v1",
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PortalSettings":
        source = os.environ if env is None else env
        return cls(
            public_url=source.get("MULTIRPA_PUBLIC_URL", "http://127.0.0.1:8770"),
            secret_key=source.get("MULTIRPA_IDENTITY_KEY", ""),
            app_env=source.get("APP_ENV", "development"),
            license_signing_key=source.get("MULTIRPA_LICENSE_SIGNING_KEY", ""),
            release_dir=source.get("MULTIRPA_RELEASE_DIR", ""),
        )


@dataclass(frozen=True)
class SMTPSettings:
    host: str = field(repr=False)
    port: int
    sender: str = field(repr=False)
    security: str = "starttls"
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    timeout: int = 15

    def __post_init__(self) -> None:
        try:
            validate_email(self.sender, check_deliverability=False)
        except EmailNotValidError:
            raise ConfigurationError("SMTP sender must be a valid email address") from None
        if (
            not self.host
            or any(ord(char) < 33 for char in self.host)
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
            or self.security not in {"ssl", "starttls", "local"}
            or bool(self.username) != bool(self.password)
            or not 1 <= self.timeout <= 60
        ):
            raise ConfigurationError("SMTP configuration is invalid")
        if self.security == "local" and self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigurationError("Plain SMTP is allowed only on loopback")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SMTPSettings":
        source = os.environ if env is None else env
        return cls(
            host=source.get("MULTIRPA_SMTP_HOST", ""),
            port=_integer(source, "MULTIRPA_SMTP_PORT", 587, 1, 65535),
            sender=source.get("MULTIRPA_SMTP_FROM", ""),
            security=source.get("MULTIRPA_SMTP_SECURITY", "starttls"),
            username=source.get("MULTIRPA_SMTP_USERNAME", ""),
            password=source.get("MULTIRPA_SMTP_PASSWORD", ""),
            timeout=_integer(source, "MULTIRPA_SMTP_TIMEOUT", 15, 1, 60),
        )
