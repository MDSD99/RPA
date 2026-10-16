"""Framework-independent identity values and safe, stable failure codes."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Audience = Literal["customer", "admin"]
Approval = Literal["pending", "approved", "rejected"]
Decision = Literal["approve", "reject", "suspend", "restore"]


class AuthError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AccountView:
    id: str
    tenant_id: str
    email: str
    display_name: str
    email_verified: bool
    approval: Approval
    suspended: bool
    is_admin: bool


@dataclass(frozen=True)
class LoginResult:
    token: str = field(repr=False)
    user: AccountView
    expires_at: datetime
