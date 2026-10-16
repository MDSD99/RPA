"""Reviewed runner capabilities; workflow JSON cannot change these permissions."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from app.workflows.browser_actions import BrowserPolicy


@dataclass(frozen=True, slots=True)
class AutomationPolicy:
    root: Path | None = None
    allowed_http_hosts: tuple[str, ...] = ()
    windows_enabled: bool = False
    http_test_origin: str | None = None
    mail_hosts: tuple[str, ...] = ()
    mail_test_origin: str | None = None
    secret_resolver: Callable[[str, str], dict[str, str]] | None = field(
        default=None, repr=False, compare=False
    )

    @classmethod
    def for_tenant(cls, tenant_id: UUID) -> "AutomationPolicy":
        default = Path(__file__).resolve().parents[2] / ".runtime" / "workspaces"
        workspace_root = Path(os.environ.get("MULTIRPA_WORKSPACE_ROOT", str(default)))
        if not workspace_root.is_absolute():
            raise ValueError("MULTIRPA_WORKSPACE_ROOT must be an absolute operator-owned path")
        root = workspace_root / str(UUID(str(tenant_id)))
        hosts = tuple(
            dict.fromkeys(
                host.strip().lower()
                for host in os.environ.get("RPA_HTTP_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            )
        )
        mail_hosts = tuple(
            dict.fromkeys(
                host.strip().lower()
                for host in os.environ.get("RPA_MAIL_ALLOWED_HOSTS", "").split(",")
                if host.strip()
            )
        )
        return cls(root=root, allowed_http_hosts=hosts, mail_hosts=mail_hosts)

    def http_policy(self) -> BrowserPolicy:
        if self.http_test_origin is not None:
            return BrowserPolicy.for_test_origin(self.http_test_origin)
        return BrowserPolicy(hosts=self.allowed_http_hosts)
