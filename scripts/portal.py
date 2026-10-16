#!/usr/bin/env python3
"""Explicit local portal/admin/mail commands. No default account or credentials."""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import signal
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.fernet import Fernet  # noqa: E402

from app.core.config import ConfigurationError, DatabaseSettings  # noqa: E402
from app.core.portal_config import PortalSettings, SMTPSettings  # noqa: E402
from app.database.connection import create_db_engine, create_session_factory  # noqa: E402
from app.identity.domain import AuthError  # noqa: E402
from app.identity.service import IdentityService  # noqa: E402
from scripts.manage import ManageError, read_env, runtime_environment  # noqa: E402

PORTAL_ENV = PROJECT_ROOT / ".env.portal"
PORTAL_KEYS = {
    "MULTIRPA_PUBLIC_URL",
    "MULTIRPA_IDENTITY_KEY",
    "MULTIRPA_LICENSE_SIGNING_KEY",
    "MULTIRPA_RELEASE_DIR",
    "MULTIRPA_WORKSPACE_ROOT",
    "RPA_BROWSER_ALLOWED_HOSTS",
    "RPA_HTTP_ALLOWED_HOSTS",
    "RPA_MAIL_ALLOWED_HOSTS",
    "MULTIRPA_SMTP_HOST",
    "MULTIRPA_SMTP_PORT",
    "MULTIRPA_SMTP_FROM",
    "MULTIRPA_SMTP_SECURITY",
    "MULTIRPA_SMTP_USERNAME",
    "MULTIRPA_SMTP_PASSWORD",
    "MULTIRPA_SMTP_TIMEOUT",
}


def portal_environment() -> dict[str, str]:
    if os.environ.get("APP_ENV") == "production":
        # Production systemd supplies protected EnvironmentFile values. Never
        # merge the development database or project-local identity keys.
        return dict(os.environ)
    env = runtime_environment(read_env(PROJECT_ROOT / ".env"))
    for key in tuple(env):
        if key.startswith("MULTIRPA_"):
            del env[key]
    metadata = PORTAL_ENV.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_size > 16384:
        raise ConfigurationError(".env.portal must be a private regular file (chmod 600)")
    values: dict[str, str] = {}
    for line in PORTAL_ENV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or key not in PORTAL_KEYS
            or key in values
            or any(ord(c) < 32 for c in value)
        ):
            raise ConfigurationError(".env.portal contains an unsupported or duplicate setting")
        values[key] = value
    env.update(values)
    return env


def initialize() -> None:
    # Exclusive creation never replaces a key that decrypts existing MFA/outbox records.
    descriptor = os.open(PORTAL_ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(
            "# Local MultiRPA identity secrets. Back up securely; do not commit.\n"
            "MULTIRPA_PUBLIC_URL=http://127.0.0.1:8770\n"
            f"MULTIRPA_IDENTITY_KEY={Fernet.generate_key().decode('ascii')}\n"
            "MULTIRPA_LICENSE_SIGNING_KEY="
            f"{base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('=')}\n"
            "# Configure SMTP here before starting send-mail/mail-worker.\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    print("Yerel portal yapılandırması oluşturuldu; gizli anahtar ekrana yazılmadı.")


def validate_bind_port(settings: PortalSettings, port: int) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ConfigurationError("Portal port must be between 1024 and 65535")
    public = urlsplit(settings.public_url)
    if public.scheme == "http" and public.hostname in {"127.0.0.1", "localhost"}:
        if (public.port or 80) != port:
            raise ConfigurationError("--port and MULTIRPA_PUBLIC_URL must use the same local port")


@contextmanager
def worker_stop() -> Iterator[Event]:
    stopped = Event()
    previous = {}
    for name in (signal.SIGTERM, signal.SIGINT):
        previous[name] = signal.signal(name, lambda _number, _frame: stopped.set())
    try:
        yield stopped
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="MultiRPA yerel müşteri ve yönetici portalı")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help=".env.portal dosyasını bir kez oluştur")
    serve = commands.add_parser("serve", help="Portalı yalnız loopback üzerinde çalıştır")
    serve.add_argument("--port", type=int, default=8770)
    commands.add_parser("execution-worker", help="Müşteri akışlarını ve zamanlamalarını çalıştır")
    commands.add_parser(
        "bootstrap-admin", help="İlk yöneticiyi gizli terminal girdileriyle oluştur"
    )
    mail = commands.add_parser("send-mail", help="Yapılandırılmış SMTP ile sınırlı gönderim")
    mail.add_argument("--limit", type=int, default=20)
    commands.add_parser("mail-worker", help="Kalıcı outbox göndericisi (Ctrl+C ile kapanır)")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize()
            return 0
        env = portal_environment()
        settings = PortalSettings.from_env(env)
        if args.command == "serve":
            import uvicorn

            validate_bind_port(settings, args.port)
            os.environ.update(env)
            from app.web.portal import create_portal_app

            uvicorn.run(
                create_portal_app(
                    settings=settings,
                    start_workers=settings.app_env != "production",
                    external_workers=settings.app_env == "production",
                ),
                host="127.0.0.1",
                port=args.port,
                proxy_headers=settings.app_env == "production",
                forwarded_allow_ips="127.0.0.1",
                access_log=False,
                log_level="warning",
            )
            return 0
        engine = create_db_engine(DatabaseSettings.from_env(env))
        try:
            factory = create_session_factory(engine)
            if args.command == "execution-worker":
                from app.database.schema import schema_is_current
                from app.web.portal_runtime import build_customer_runtime

                with factory() as session:
                    if not schema_is_current(session):
                        raise ConfigurationError("Apply current migrations before starting workers")
                os.environ.update(env)
                with ExitStack() as resources, worker_stop() as stopped:
                    build_customer_runtime(
                        factory, resources, start_workers=True, secret_key=settings.secret_key
                    )
                    while not stopped.wait(1):
                        pass
                return 0
            if args.command == "bootstrap-admin":
                identity = IdentityService(
                    factory, secret_key=settings.secret_key, public_url=settings.public_url
                )
                email = input("Yönetici e-posta adresi: ").strip()
                name = input("Görünen ad: ").strip()
                password = getpass.getpass("Parola (15–128 karakter): ")
                if password != getpass.getpass("Parola tekrar: "):
                    raise ConfigurationError("Passwords do not match")
                secret = getpass.getpass("Doğrulayıcıya eklediğiniz Base32 TOTP anahtarı: ")
                identity.bootstrap_admin(email, password, name, secret)
                print(
                    "Yönetici hesabı oluşturuldu. /admin üzerinden parola ve TOTP ile giriş yapın."
                )
                return 0
            from app.adapters.smtp_mail import SMTPMailSender
            from app.services.mail_service import MailService
            from app.services.rate_limit_service import RateLimitService

            smtp = SMTPSettings.from_env(env)
            dispatcher = MailService(
                factory,
                secret_key=settings.secret_key,
                public_url=settings.public_url,
                sender_address=smtp.sender,
                sender=SMTPMailSender(smtp),
            )
            if args.command == "send-mail":
                if not 1 <= args.limit <= 100:
                    raise ConfigurationError("Mail batch must be between 1 and 100")
                processed = 0
                for _ in range(args.limit):
                    if dispatcher.dispatch_once() is None:
                        break
                    processed += 1
                print(
                    f"{processed} e-posta kuyruğu kaydı işlendi; teslim durumu outbox'ta tutulur."
                )
                return 0
            limiter = RateLimitService(factory, secret_key=settings.secret_key)
            next_prune = 0.0
            with worker_stop() as stopped:
                while not stopped.is_set():
                    if time.monotonic() >= next_prune:
                        limiter.prune()
                        next_prune = time.monotonic() + 3600
                    if dispatcher.dispatch_once() is None:
                        stopped.wait(2)
        finally:
            engine.dispose()
    except FileExistsError:
        print("Portal dosyası zaten var; mevcut anahtar değiştirilmedi.", file=sys.stderr)
        return 2
    except (ConfigurationError, ManageError, AuthError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Neither driver failures, email addresses nor credentials enter CLI logs.
        print(
            "İşlem tamamlanamadı. Yapılandırma ve veritabanı durumunu kontrol edin.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
