"""Real loopback SMTP delivery, one-use links and recoverable outbox failures."""

import email
import re
import socketserver
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.policy import default
from threading import Thread
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select

from app.adapters.smtp_mail import MailDeliveryError, SMTPMailSender
from app.core.portal_config import SMTPSettings
from app.database.repositories.mail_repository import MailRepository
from app.identity.domain import AuthError
from app.identity.models import EmailOutbox
from app.identity.service import IdentityService
from app.services.mail_service import MailService

pytestmark = pytest.mark.postgres
PASSWORD = "Email delivery test passphrase!"


@pytest.fixture
def mail_context(session_factory):
    with session_factory.begin() as session:
        session.execute(delete(EmailOutbox))
    key = Fernet.generate_key().decode()
    identity = IdentityService(session_factory, secret_key=key, public_url="http://127.0.0.1:8770")
    address = f"mail-{uuid4().hex}@example.com"
    identity.register(address, PASSWORD, "E-posta alıcısı")
    return identity, key, address


class CaptureSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 loopback SMTP\r\n")
        while line := self.rfile.readline():
            verb = line.split(b" ", 1)[0].strip().upper()
            if verb in {b"EHLO", b"HELO", b"MAIL", b"RCPT", b"RSET", b"NOOP"}:
                self.wfile.write(b"250 OK\r\n")
            elif verb == b"DATA":
                self.wfile.write(b"354 Send data\r\n")
                parts = []
                while (part := self.rfile.readline()) not in {b".\r\n", b""}:
                    parts.append(part[1:] if part.startswith(b"..") else part)
                self.server.messages.append(b"".join(parts))
                self.wfile.write(b"250 Queued\r\n")
            elif verb == b"QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return
            else:
                self.wfile.write(b"500 Unsupported\r\n")


def dispatcher(session_factory, key, sender):
    return MailService(
        session_factory,
        secret_key=key,
        public_url="http://127.0.0.1:8770",
        sender_address="noreply@example.com",
        sender=sender,
    )


def test_real_smtp_delivers_one_use_verification_and_clears_payload(session_factory, mail_context):
    identity, key, address = mail_context
    with socketserver.TCPServer(("127.0.0.1", 0), CaptureSMTP) as server:
        server.messages = []
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            sender = SMTPMailSender(
                SMTPSettings("127.0.0.1", server.server_address[1], "noreply@example.com", "local")
            )
            service = dispatcher(session_factory, key, sender)
            assert service.dispatch_once() == "sent"
            assert service.dispatch_once() is None
            assert len(server.messages) == 1
            message = email.message_from_bytes(server.messages[0], policy=default)
            assert message["To"] == address
            body = message.get_content()
            token = re.search(r"/verify-email#token=([A-Za-z0-9_-]+)", body).group(1)
            identity.verify_email(token)
            with pytest.raises(AuthError):
                identity.verify_email(token)
            assert identity.login(address, PASSWORD).user.email_verified
            with session_factory() as session:
                row = session.scalar(select(EmailOutbox))
                assert row.status == "sent" and row.payload_ciphertext is None
                assert row.lease_until is None and row.claim_token is None
                assert row.sent_at is not None
                assert str(row.id) in message["Message-ID"]
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


class FailingSender:
    def send(self, _message):
        raise MailDeliveryError("Sensitive SMTP diagnostic must be discarded")


class CollectSender:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def test_retry_failure_is_sanitized_and_replaced_link_never_sent(session_factory, mail_context):
    identity, key, address = mail_context
    service = dispatcher(session_factory, key, FailingSender())
    assert service.dispatch_once() == "retry"
    with session_factory.begin() as session:
        row = session.scalar(select(EmailOutbox))
        assert row.last_error == "SMTP_DELIVERY_FAILED"
        assert row.status == "pending" and row.attempts == 1
        row.available_at = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    identity.request_verification(address)
    collector = CollectSender()
    service = dispatcher(session_factory, key, collector)
    assert service.dispatch_once() == "discarded"
    assert service.dispatch_once() == "sent"
    assert len(collector.messages) == 1


def test_parallel_claim_and_expired_claim_fencing(session_factory, mail_context):
    def claim():
        with session_factory.begin() as session:
            row = MailRepository(session).claim()
            return (row.id, row.claim_token) if row else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _n: claim(), range(2)))
    claimed = [result for result in results if result]
    assert len(claimed) == 1
    row_id, stale_token = claimed[0]
    with session_factory.begin() as session:
        session.get(EmailOutbox, row_id).lease_until = session.scalar(
            select(func.clock_timestamp())
        ) - timedelta(seconds=1)
    new_claim = claim()
    assert new_claim[0] == row_id and new_claim[1] != stale_token
    with session_factory.begin() as session:
        assert (
            MailRepository(session).finish(row_id, stale_token, error=None, terminal=False) is False
        )
        assert (
            MailRepository(session).finish(row_id, new_claim[1], error=None, terminal=False) is True
        )


def test_invalid_encryption_does_not_send_or_reveal_payload(session_factory, mail_context):
    _identity, key, _address = mail_context
    with session_factory.begin() as session:
        row = session.scalar(select(EmailOutbox))
        row.payload_ciphertext = "invalid-private-payload"
    collector = CollectSender()
    assert dispatcher(session_factory, key, collector).dispatch_once() == "discarded"
    assert collector.messages == []
    with session_factory() as session:
        row = session.scalar(select(EmailOutbox))
        assert row.last_error == "MAIL_PAYLOAD_INVALID" and row.payload_ciphertext is None
