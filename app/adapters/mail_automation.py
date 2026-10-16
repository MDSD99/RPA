"""Bounded SMTP effects with explicit hosts, pinned public DNS and TLS verification."""

import ipaddress
import smtplib
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email

from app.adapters.automation_policy import AutomationPolicy
from app.core.config import ConfigurationError
from app.core.portal_config import SMTPSettings
from app.workflows.registry import WorkflowFailure


@dataclass(frozen=True)
class PreparedMail:
    settings: SMTPSettings = field(repr=False)
    destination: str = field(repr=False)
    message: EmailMessage = field(repr=False)


def prepare_mail(
    config: dict[str, object], credentials: dict[str, str], policy: AutomationPolicy
) -> PreparedMail:
    try:
        sender, recipient = credentials["sender"], config["to"]
        if not isinstance(recipient, str):
            raise ValueError
        sender = validate_email(sender, check_deliverability=False).normalized
        recipient = validate_email(recipient, check_deliverability=False).normalized
        subject, body = config["subject"], config["body"]
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 200
            or any(ord(c) < 32 or ord(c) == 127 for c in subject)
            or not isinstance(body, str)
            or len(body.encode("utf-8")) > 16384
        ):
            raise ValueError
        settings = SMTPSettings(
            credentials["host"],
            int(credentials["port"]),
            sender,
            credentials["security"],
            credentials["username"],
            credentials["password"],
            timeout=5,
        )
        if policy.mail_test_origin is not None:
            test = urlsplit(policy.mail_test_origin)
            if (
                test.scheme != "smtp"
                or test.hostname != "127.0.0.1"
                or test.username is not None
                or test.password is not None
                or test.path not in {"", "/"}
                or test.query
                or test.fragment
                or not test.port
                or test.port < 1024
                or (settings.host, settings.port, settings.security)
                != ("127.0.0.1", test.port, "local")
            ):
                raise ValueError
            destination = "127.0.0.1"
        else:
            if settings.host not in policy.mail_hosts or (settings.security, settings.port) not in {
                ("ssl", 465),
                ("starttls", 587),
            }:
                raise WorkflowFailure("FLOW_MAIL_HOST_BLOCKED")
            addresses = socket.getaddrinfo(settings.host, settings.port, type=socket.SOCK_STREAM)
            if not addresses:
                raise ValueError
            for record in addresses:
                address = ipaddress.ip_address(record[4][0])
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                    address = address.ipv4_mapped
                if not address.is_global:
                    raise WorkflowFailure("FLOW_MAIL_HOST_BLOCKED")
            destination = str(addresses[0][4][0])
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, recipient, subject
        message["Date"] = format_datetime(datetime.now(UTC))
        message["Message-ID"] = make_msgid(domain="multirpa.local")
        message.set_content(body)
        return PreparedMail(settings, destination, message)
    except (KeyError, ValueError, TypeError, OSError, EmailNotValidError, ConfigurationError):
        raise WorkflowFailure("FLOW_MAIL_CONFIGURATION_INVALID") from None


def perform_mail(
    prepared: PreparedMail, *, check_active: Callable[[], None], deadline: float
) -> None:
    settings = prepared.settings

    def active() -> None:
        check_active()
        if time.monotonic() >= deadline:
            raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
        if smtp is not None and smtp.sock is not None:
            smtp.sock.settimeout(min(5, max(0.001, deadline - time.monotonic())))

    def pinned_socket() -> socket.socket:
        active()
        return socket.create_connection(
            (prepared.destination, settings.port),
            timeout=min(5, max(0.001, deadline - time.monotonic())),
        )

    class PinnedSMTP(smtplib.SMTP):
        def _get_socket(self, _host: str, _port: int, _timeout: float) -> socket.socket:
            return pinned_socket()

    class PinnedSSL(smtplib.SMTP_SSL):
        def _get_socket(self, host: str, _port: int, _timeout: float) -> ssl.SSLSocket:
            connection = pinned_socket()
            try:
                return ssl.create_default_context().wrap_socket(connection, server_hostname=host)
            except BaseException:
                connection.close()
                raise

    smtp: smtplib.SMTP | None = None
    try:
        active()
        client = PinnedSSL if settings.security == "ssl" else PinnedSMTP
        smtp = client(settings.host, settings.port, timeout=5, local_hostname="multirpa.local")
        active()
        smtp.ehlo()
        if settings.security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
            active()
            smtp.ehlo()
        if settings.username:
            active()
            smtp.login(settings.username, settings.password)
        active()
        rejected = smtp.send_message(prepared.message)
        if rejected:
            raise WorkflowFailure("FLOW_MAIL_SEND_FAILED")
        # Recording a known successful outcome is still allowed when a demo
        # expires during DATA; the engine handles an unacknowledged checkpoint.
    except (OSError, smtplib.SMTPException):
        raise WorkflowFailure("FLOW_MAIL_SEND_FAILED") from None
    finally:
        if smtp is not None:
            smtp.close()
