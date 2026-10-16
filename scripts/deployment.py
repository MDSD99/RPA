#!/usr/bin/env python3
"""Render a reviewable production profile; never reads secrets or changes services."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.branding import PRODUCT_NAME  # noqa: E402


class DeploymentError(ValueError):
    """Safe, value-free configuration failure."""


def origin(value: str) -> tuple[str, str]:
    try:
        if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
            raise ValueError
        url = urlsplit(value)
        hostname = url.hostname or ""
        labels = hostname.split(".")
        if (
            url.scheme != "https"
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.port not in {None, 443}
            or len(hostname) > 253
            or len(labels) < 2
            or all(label.isdigit() for label in labels)
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise DeploymentError(
            "Use an explicit HTTPS DNS origin without credentials or URL paths"
        ) from None
    return "https://" + hostname, hostname


def directory(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
        or ".." in Path(value).parts
        or Path(value) == Path("/")
        or value.endswith("/")
    ):
        raise DeploymentError(
            "Deployment paths must be absolute and contain no directives or spaces"
        )
    return value


@dataclass(frozen=True)
class DeploymentProfile:
    root_url: str
    app_url: str
    install_dir: str = "/opt/multirpa/current"
    settings_dir: str = "/etc/multirpa"
    marketing_dir: str = "/var/www/multirpa"
    user: str = "multirpa"
    port: int = 8770

    def __post_init__(self) -> None:
        root_url, root_host = origin(self.root_url)
        app_url, app_host = origin(self.app_url)
        if not app_host.endswith("." + root_host):
            raise DeploymentError(
                "The application origin must be a subdomain of the marketing origin"
            )
        for name in ("install_dir", "settings_dir", "marketing_dir"):
            directory(getattr(self, name))
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.user) or self.user == "root":
            raise DeploymentError("Use a dedicated non-root service account")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise DeploymentError("The private application port must be between 1024 and 65535")
        object.__setattr__(self, "root_url", root_url)
        object.__setattr__(self, "app_url", app_url)

    def variables(self) -> dict[str, str]:
        return {
            "ROOT_HOST": origin(self.root_url)[1],
            "APP_HOST": origin(self.app_url)[1],
            "ROOT_URL": self.root_url,
            "APP_URL": self.app_url,
            "INSTALL_DIR": self.install_dir,
            "MARKETING_DIR": self.marketing_dir,
            "USER": self.user,
            "PORT": str(self.port),
        }


def render(template: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        template = template.replace("__" + name + "__", value)
    if re.search(r"__[A-Z_]+__", template):
        raise DeploymentError("Deployment template has an unresolved setting")
    return template


def generate(profile: DeploymentProfile, output: Path) -> Path:
    # A new output directory is mandatory: never replace deployed configs or keys.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    templates = PROJECT_ROOT / "deploy"
    variables = profile.variables()
    (output / "nginx.conf").write_text(
        render((templates / "nginx.conf.template").read_text(), variables), encoding="utf-8"
    )
    systemd = output / "systemd"
    systemd.mkdir()
    for name, description, command, mail in (
        ("api", "customer API", f"serve --port {profile.port}", False),
        ("execution", "workflow workers and scheduler", "execution-worker", False),
        ("mail", "email outbox worker", "mail-worker", True),
    ):
        files = ["database.env", "portal.env"] + (["mail.env"] if mail else [])
        service_variables = variables | {
            "DESCRIPTION": description,
            "COMMAND": command,
            "ENVIRONMENT_FILES": "\n".join(
                f"EnvironmentFile={profile.settings_dir}/{filename}" for filename in files
            ),
        }
        (systemd / f"multirpa-{name}.service").write_text(
            render((templates / "systemd.service.template").read_text(), service_variables),
            encoding="utf-8",
        )
    examples = {
        "database.env.example": (
            "APP_ENV=production\nDB_HOST=postgres.internal.example\nDB_PORT=5432\n"
            "DB_NAME=multirpa\nDB_USER=multirpa_runtime\n"
            "DB_PASSWORD=REPLACE_WITH_DATABASE_SECRET\nDB_SSLMODE=verify-full\n"
            f"DB_SSLROOTCERT={profile.settings_dir}/postgres-ca.pem\n"
            "DB_POOL_SIZE=5\nDB_MAX_OVERFLOW=5\nRPA_BROWSER_ALLOWED_HOSTS=\n"
            "RPA_HTTP_ALLOWED_HOSTS=\nRPA_MAIL_ALLOWED_HOSTS=\n"
        ),
        "portal.env.example": (
            f"MULTIRPA_PUBLIC_URL={profile.app_url}\n"
            "MULTIRPA_WORKSPACE_ROOT=/var/lib/multirpa/workspaces\n"
            "MULTIRPA_IDENTITY_KEY=REPLACE_WITH_PERSISTENT_FERNET_KEY\n"
            "MULTIRPA_LICENSE_SIGNING_KEY=REPLACE_WITH_PERSISTENT_BASE64URL_ED25519_SEED\n"
        ),
        "mail.env.example": (
            "MULTIRPA_SMTP_HOST=REPLACE_WITH_SMTP_HOST\nMULTIRPA_SMTP_PORT=587\n"
            "MULTIRPA_SMTP_SECURITY=starttls\nMULTIRPA_SMTP_FROM=REPLACE_WITH_SENDER\n"
            "MULTIRPA_SMTP_USERNAME=REPLACE_WITH_SMTP_USERNAME\n"
            "MULTIRPA_SMTP_PASSWORD=REPLACE_WITH_SMTP_SECRET\nMULTIRPA_SMTP_TIMEOUT=15\n"
        ),
    }
    for filename, source in examples.items():
        target = output / filename
        target.write_text(source, encoding="utf-8")
        target.chmod(0o600)
    marketing = output / "marketing"
    static = marketing / "static"
    static.mkdir(parents=True)
    source = (PROJECT_ROOT / "app/web/templates/marketing.html").read_text(encoding="utf-8")
    source = render(
        source,
        {
            "PRODUCT_NAME": escape(PRODUCT_NAME),
            "APP_URL": escape(profile.app_url + "/account", quote=True),
        },
    )
    (marketing / "index.html").write_text(source, encoding="utf-8")
    for filename in ("marketing.css", "brand.svg", "favicon.svg"):
        (static / filename).write_bytes((PROJECT_ROOT / "app/web/static" / filename).read_bytes())
    manifest = {
        "product": PRODUCT_NAME,
        "root_url": profile.root_url,
        "app_url": profile.app_url,
        "install_dir": profile.install_dir,
        "settings_dir": profile.settings_dir,
        "marketing_dir": profile.marketing_dir,
        "service_user": profile.user,
        "private_port": profile.port,
        "deployed": False,
        "required_operator_steps": [
            "DNS/TLS",
            "private PostgreSQL and secrets",
            "SMTP",
            "first admin",
            "install and validate profile",
        ],
    }
    (output / "profile.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-url", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install-dir", default="/opt/multirpa/current")
    parser.add_argument("--settings-dir", default="/etc/multirpa")
    parser.add_argument("--marketing-dir", default="/var/www/multirpa")
    parser.add_argument("--user", default="multirpa")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args(argv)
    try:
        profile = DeploymentProfile(
            args.root_url,
            args.app_url,
            args.install_dir,
            args.settings_dir,
            args.marketing_dir,
            args.user,
            args.port,
        )
        generate(profile, args.output)
    except (DeploymentError, OSError):
        print(
            "Yayın profili oluşturulamadı; HTTPS adreslerini, yolları "
            "ve yeni çıktı dizinini kontrol edin.",
            file=sys.stderr,
        )
        return 2
    print("MultiRPA yayın profili hazırlandı; servis veya yayın yapılandırması değiştirilmedi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
