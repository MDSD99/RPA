"""SMTP delivery over verified TLS; plaintext transport is loopback-only."""

import smtplib
import ssl
from email.message import EmailMessage

from app.core.portal_config import SMTPSettings


class MailDeliveryError(Exception):
    """Transport details and recipient addresses must never become public errors."""


class SMTPMailSender:
    def __init__(self, settings: SMTPSettings) -> None:
        self.settings = settings

    def send(self, message: EmailMessage) -> None:
        config = self.settings
        try:
            client: smtplib.SMTP
            if config.security == "ssl":
                client = smtplib.SMTP_SSL(
                    config.host,
                    config.port,
                    timeout=config.timeout,
                    context=ssl.create_default_context(),
                )
            else:
                client = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
            with client:
                client.ehlo()
                if config.security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if config.username:
                    client.login(config.username, config.password)
                if client.send_message(message):
                    raise MailDeliveryError("SMTP_DELIVERY_FAILED")
        except (smtplib.SMTPException, OSError):
            raise MailDeliveryError("SMTP_DELIVERY_FAILED") from None
