"""Atomic account, email, session and operator approval use cases."""

import unicodedata
from datetime import timedelta
from ipaddress import ip_address
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session, sessionmaker

from app.database.unit_of_work import DatabaseUnitOfWork
from app.identity.domain import AccountView, Approval, Audience, AuthError, Decision, LoginResult
from app.identity.models import Account, EmailOutbox, EmailToken, LoginSession, Tenant
from app.identity.repository import IdentityRepository
from app.identity.security import (
    accepted_totp_counter,
    encrypt_payload,
    hash_password,
    new_token,
    normalize_email,
    password_matches,
    token_digest,
    validate_name,
    validate_totp_secret,
)


def _view(account: Account) -> AccountView:
    return AccountView(
        id=str(account.id),
        tenant_id=str(account.tenant_id),
        email=account.email,
        display_name=account.display_name,
        email_verified=account.email_verified,
        approval=cast(Approval, account.approval),
        suspended=account.suspended,
        is_admin=account.is_admin,
    )


def _audience(value: str) -> Audience:
    if value not in ("customer", "admin"):
        raise AuthError("INVALID_AUDIENCE")
    return cast(Audience, value)


def _approved(account: Account) -> None:
    if account.suspended:
        raise AuthError("ACCOUNT_SUSPENDED")
    if not account.email_verified:
        raise AuthError("EMAIL_UNVERIFIED")
    if account.approval != "approved":
        raise AuthError("APPROVAL_REQUIRED")


class IdentityService:
    def __init__(
        self, session_factory: sessionmaker[Session], *, secret_key: str, public_url: str
    ) -> None:
        self.uow = DatabaseUnitOfWork(session_factory)
        self.cipher = Fernet(secret_key.encode("ascii"))
        parsed = urlsplit(public_url)
        local = parsed.hostname in ("localhost", "testserver")
        if parsed.hostname:
            try:
                local = local or ip_address(parsed.hostname).is_loopback
            except ValueError:
                pass
        if (
            parsed.scheme not in ("https", "http")
            or (parsed.scheme == "http" and not local)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or any(unicodedata.category(char).startswith("C") for char in public_url)
        ):
            raise ValueError("Identity public URL must be an HTTPS origin or local test origin")
        self.public_url = public_url.rstrip("/")

    def register(self, email: str, password: str, display_name: str) -> None:
        email = normalize_email(email)
        display_name = validate_name(display_name)
        encoded = hash_password(password)
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            repo.lock_email(email)
            if repo.by_email(email) is not None:
                return
            tenant = Tenant(id=uuid4(), name=display_name)
            repo.add(tenant)
            repo.flush()
            account = Account(
                id=uuid4(),
                tenant_id=tenant.id,
                email=email,
                display_name=display_name,
                password_hash=encoded,
            )
            repo.add(account)
            repo.flush()
            self._issue(repo, account, "verify_email")
            repo.audit(account.id, "REGISTERED")

    def request_verification(self, email: str) -> None:
        self._request_email(email, "verify_email")

    def request_password_reset(self, email: str) -> None:
        self._request_email(email, "password_reset")

    def _request_email(
        self, email: str, purpose: Literal["verify_email", "password_reset"]
    ) -> None:
        email = normalize_email(email)
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            account = repo.by_email(email, lock=True)
            if account is None or (purpose == "verify_email" and account.email_verified):
                return
            self._issue(repo, account, purpose)

    def _issue(
        self,
        repo: IdentityRepository,
        account: Account,
        purpose: Literal["verify_email", "password_reset"],
    ) -> None:
        now = repo.now()
        raw = new_token()
        repo.revoke_email_tokens(account.id, purpose, now)
        repo.add(
            EmailToken(
                user_id=account.id,
                purpose=purpose,
                token_hash=token_digest(raw),
                created_at=now,
                expires_at=now + timedelta(hours=24)
                if purpose == "verify_email"
                else now + timedelta(minutes=30),
            ),
            EmailOutbox(
                user_id=account.id,
                kind=purpose,
                payload_ciphertext=encrypt_payload(
                    self.cipher,
                    {
                        "email": account.email,
                        "display_name": account.display_name,
                        "token": raw,
                        "public_url": self.public_url,
                    },
                ),
            ),
        )
        repo.audit(
            account.id,
            "VERIFICATION_REQUESTED" if purpose == "verify_email" else "PASSWORD_RESET_REQUESTED",
        )

    def _consume(self, repo: IdentityRepository, raw: str, purpose: str) -> Account:
        digest = token_digest(raw)
        token = repo.email_token(digest, purpose)
        if token is None:
            raise AuthError("INVALID_TOKEN")
        account = repo.account(token.user_id, lock=True)
        # Re-read after the shared account lock: parallel consumers see the committed result.
        token = repo.email_token(digest, purpose)
        now = repo.now()
        if (
            account is None
            or token is None
            or token.consumed_at is not None
            or token.expires_at <= now
        ):
            raise AuthError("INVALID_TOKEN")
        token.consumed_at = now
        return account

    def verify_email(self, token: str) -> None:
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            account = self._consume(repo, token, "verify_email")
            account.email_verified = True
            repo.audit(account.id, "EMAIL_VERIFIED")

    def reset_password(self, token: str, password: str) -> None:
        token_digest(token)
        encoded = hash_password(password)
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            account = self._consume(repo, token, "password_reset")
            account.password_hash = encoded
            repo.revoke_sessions(account.id, repo.now())
            repo.audit(account.id, "PASSWORD_RESET")

    def login(
        self, email: str, password: str, *, audience: Audience = "customer", totp: str | None = None
    ) -> LoginResult:
        audience = _audience(audience)
        try:
            email = normalize_email(email)
        except AuthError:
            password_matches(password, None)
            raise AuthError("INVALID_CREDENTIALS") from None
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            account = repo.by_email(email, lock=True)
            if (
                not password_matches(password, account.password_hash if account else None)
                or account is None
            ):
                raise AuthError("INVALID_CREDENTIALS")
            if account.suspended:
                raise AuthError("ACCOUNT_SUSPENDED")
            if audience == "admin":
                if not account.is_admin:
                    raise AuthError("ADMIN_REQUIRED")
                _approved(account)
                now = repo.now()
                account.totp_last_counter = accepted_totp_counter(
                    self.cipher,
                    # The admin_mfa database constraint guarantees a secret here.
                    cast(str, account.totp_secret_ciphertext),
                    totp,
                    now,
                    account.totp_last_counter,
                )
            now = repo.now()
            expires_at = now + (
                timedelta(minutes=30) if audience == "admin" else timedelta(hours=8)
            )
            raw = new_token()
            repo.add(
                LoginSession(
                    user_id=account.id,
                    token_hash=token_digest(raw),
                    audience=audience,
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            repo.audit(
                account.id,
                "ADMIN_LOGIN" if audience == "admin" else "CUSTOMER_LOGIN",
                actor_id=account.id,
            )
            return LoginResult(raw, _view(account), expires_at)

    def _principal(
        self, repo: IdentityRepository, raw_token: str, audience: str
    ) -> tuple[Account, LoginSession]:
        audience = _audience(audience)
        try:
            digest = token_digest(raw_token)
        except AuthError:
            raise AuthError("SESSION_EXPIRED") from None
        token = repo.session_token(digest, audience)
        if token is None:
            raise AuthError("SESSION_EXPIRED")
        account = repo.account(token.user_id, lock=True)
        token = repo.session_token(digest, audience)
        if (
            account is None
            or token is None
            or token.revoked_at is not None
            or token.expires_at <= repo.now()
        ):
            raise AuthError("SESSION_EXPIRED")
        if account.suspended:
            raise AuthError("ACCOUNT_SUSPENDED")
        if audience == "admin":
            if not account.is_admin:
                raise AuthError("ADMIN_REQUIRED")
            _approved(account)
        return account, token

    def authenticate(self, raw_token: str, *, audience: Audience = "customer") -> AccountView:
        with self.uow.transaction() as session:
            account, _ = self._principal(IdentityRepository(session), raw_token, audience)
            return _view(account)

    def require_approved(self, raw_token: str, *, audience: Audience = "customer") -> AccountView:
        with self.uow.transaction() as session:
            account, _ = self._principal(IdentityRepository(session), raw_token, audience)
            _approved(account)
            return _view(account)

    def logout(self, raw_token: str, *, audience: Audience = "customer") -> None:
        _audience(audience)
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            try:
                account, token = self._principal(repo, raw_token, audience)
            except AuthError as error:
                if error.code in ("SESSION_EXPIRED", "ACCOUNT_SUSPENDED"):
                    return
                raise
            token.revoked_at = repo.now()
            repo.audit(account.id, "LOGOUT", actor_id=account.id)

    def list_accounts(
        self, admin_token: str, *, limit: int = 50, offset: int = 0
    ) -> dict[str, object]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 100000
        ):
            raise AuthError("INVALID_PAGINATION")
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            self._principal(repo, admin_token, "admin")
            accounts, count = repo.accounts(limit, offset)
            return {
                "items": [_view(account) for account in accounts],
                "total": count,
                "limit": limit,
                "offset": offset,
            }

    def decide(
        self, admin_token: str, user_id: str, *, decision: Decision, reason: str
    ) -> AccountView:
        if decision not in ("approve", "reject", "suspend", "restore"):
            raise AuthError("INVALID_DECISION")
        if (
            not isinstance(reason, str)
            or not 3 <= len(reason.strip()) <= 500
            or any(
                unicodedata.category(char).startswith("C") and char not in "\n\r\t"
                for char in reason
            )
        ):
            raise AuthError("INVALID_REASON")
        try:
            target_id = UUID(user_id)
        except (ValueError, TypeError, AttributeError):
            raise AuthError("ACCOUNT_NOT_FOUND") from None
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            actor, _ = self._principal(repo, admin_token, "admin")
            if actor.id == target_id:
                raise AuthError("SELF_ACTION_FORBIDDEN")
            account = repo.account(target_id)
            if account is None:
                raise AuthError("ACCOUNT_NOT_FOUND")
            if account.is_admin:
                raise AuthError("ADMIN_ACTION_FORBIDDEN")
            account = repo.account(target_id, lock=True)
            if account is None:
                raise AuthError("ACCOUNT_NOT_FOUND")
            if decision == "approve":
                if not account.email_verified:
                    raise AuthError("EMAIL_UNVERIFIED")
                account.approval = "approved"
            elif decision == "reject":
                account.approval = "rejected"
            elif decision == "suspend":
                account.suspended = True
            elif decision == "restore":
                account.suspended = False
            if decision in ("reject", "suspend"):
                repo.revoke_sessions(account.id, repo.now())
            repo.audit(
                account.id, f"ACCOUNT_{decision.upper()}", actor_id=actor.id, reason=reason.strip()
            )
            return _view(account)

    def bootstrap_admin(
        self, email: str, password: str, display_name: str, totp_secret: str
    ) -> None:
        email = normalize_email(email)
        display_name = validate_name(display_name)
        secret = validate_totp_secret(totp_secret)
        encoded = hash_password(password)
        with self.uow.transaction() as session:
            repo = IdentityRepository(session)
            repo.lock_email(email)
            if repo.by_email(email) is not None:
                raise AuthError("ACCOUNT_EXISTS")
            tenant = Tenant(id=uuid4(), name=display_name)
            repo.add(tenant)
            repo.flush()
            account = Account(
                id=uuid4(),
                tenant_id=tenant.id,
                email=email,
                display_name=display_name,
                password_hash=encoded,
                email_verified=True,
                approval="approved",
                is_admin=True,
                totp_secret_ciphertext=self.cipher.encrypt(secret.encode("ascii")).decode("ascii"),
            )
            repo.add(account)
            repo.flush()
            repo.audit(account.id, "ADMIN_BOOTSTRAPPED")
