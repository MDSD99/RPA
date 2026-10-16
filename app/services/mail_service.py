"""Decrypt and deliver one leased identity email with a stable message identifier."""

import json
from email.message import EmailMessage
from typing import Protocol, cast
from urllib.parse import quote, urlsplit
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.smtp_mail import MailDeliveryError
from app.core.branding import PRODUCT_NAME
from app.database.repositories.mail_repository import MailRepository
from app.database.unit_of_work import DatabaseUnitOfWork
from app.identity.models import EmailOutbox


class MailSender(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class MailService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        secret_key: str,
        public_url: str,
        sender_address: str,
        sender: MailSender,
    ) -> None:
        self.unit_of_work = DatabaseUnitOfWork(session_factory)
        self._cipher = Fernet(secret_key.encode("ascii"))
        self.public_url = public_url.rstrip("/")
        self.sender_address = sender_address
        self.sender = sender

    def _message(self, row: EmailOutbox, payload: dict[str, str]) -> EmailMessage:
        verify = row.kind == "verify_email"
        path = "/verify-email" if verify else "/reset-password"
        # A fragment keeps the credential out of HTTP access logs and referrers.
        link = f"{self.public_url}{path}#token={quote(payload['token'], safe='')}"
        message = EmailMessage()
        message["From"] = f"{PRODUCT_NAME} <{self.sender_address}>"
        message["To"] = payload["email"]
        message["Subject"] = (
            f"{PRODUCT_NAME} e-posta doğrulaması" if verify else f"{PRODUCT_NAME} parola yenileme"
        )
        message["Message-ID"] = f"<{row.id}@{urlsplit(self.public_url).hostname}>"
        action = "e-posta adresinizi doğrulayın" if verify else "parolanızı yenileyin"
        message.set_content(
            f"Merhaba {payload['display_name']},\n\n"
            f"Aşağıdaki bağlantıyı açarak {action}:\n\n{link}\n\n"
            "Bu bağlantı süreli ve tek kullanımlıktır. İşlemi siz başlatmadıysanız "
            "bu e-postayı dikkate almayabilirsiniz.\n\nMultiRPA\n"
        )
        return message

    def dispatch_once(self) -> str | None:
        with self.unit_of_work.transaction() as session:
            row = MailRepository(session).claim()
        if row is None:
            return None
        error = None
        terminal = False
        try:
            if row.payload_ciphertext is None:
                raise ValueError
            payload = json.loads(self._cipher.decrypt(row.payload_ciphertext.encode("ascii")))
            if not isinstance(payload, dict) or not all(
                isinstance(payload.get(key), str) for key in ("token", "email", "display_name")
            ):
                raise ValueError
            with self.unit_of_work.transaction() as session:
                active = MailRepository(session).token_active(
                    row.user_id, row.kind, payload["token"]
                )
            if not active:
                error, terminal = "TOKEN_INACTIVE", True
            elif row.attempts > 5:
                error, terminal = "DELIVERY_ATTEMPTS_EXHAUSTED", True
            else:
                self.sender.send(self._message(row, payload))
        except MailDeliveryError:
            error = "SMTP_DELIVERY_FAILED"
        except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            error, terminal = "MAIL_PAYLOAD_INVALID", True
        with self.unit_of_work.transaction() as session:
            applied = MailRepository(session).finish(
                # The delivery_claim database constraint requires a UUID while sending.
                row.id,
                cast(UUID, row.claim_token),
                error=error,
                terminal=terminal,
            )
        if not applied:
            return "lease_lost"
        return (
            "sent" if error is None else "discarded" if terminal or row.attempts >= 5 else "retry"
        )
