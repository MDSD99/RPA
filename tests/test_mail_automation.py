"""Bounded SMTP adapter and real loopback DATA ambiguity without external mail."""

import copy
import email
import socket
import socketserver
import ssl
import time
from contextlib import contextmanager
from email.policy import default
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.adapters import mail_automation
from app.adapters.automation_policy import AutomationPolicy
from app.adapters.mail_automation import perform_mail, prepare_mail
from app.core.exceptions import OwnershipLostError
from app.workflows.flow_engine import FlowOutcomeUncertain, execute_graph
from app.workflows.registry import WorkflowFailure


def values(*, host="smtp.example.com", port="587", security="starttls"):
    return {
        "host": host,
        "port": port,
        "security": security,
        "sender": "sender@example.com",
        "username": "",
        "password": "",
    }


def config():
    return {
        "secret_ref": "mail_main",
        "to": "recipient@example.com",
        "subject": "MultiRPA test",
        "body": "Örnek ileti gövdesi.",
    }


def addresses(*items):
    return [
        (socket.AF_INET6 if ":" in item else socket.AF_INET, socket.SOCK_STREAM, 6, "", (item, 587))
        for item in items
    ]


def assert_code(code, action):
    with pytest.raises(WorkflowFailure) as error:
        action()
    assert error.value.error_code == code
    return error.value


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.2.3",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "fe80::1",
    ],
)
def test_private_dns_results_are_rejected_before_smtp_connect(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_args, **_kwargs: addresses("8.8.8.8", address)
    )
    assert_code(
        "FLOW_MAIL_HOST_BLOCKED",
        lambda: prepare_mail(
            config(), values(), AutomationPolicy(mail_hosts=("smtp.example.com",))
        ),
    )


def test_public_dns_is_pinned_once_and_exact_host_allowlist_required(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_args, **_kwargs: addresses("8.8.8.8", "1.1.1.1")
    )
    prepared = prepare_mail(config(), values(), AutomationPolicy(mail_hosts=("smtp.example.com",)))
    assert prepared.destination == "8.8.8.8"
    assert prepared.settings.host == "smtp.example.com"
    assert "smtp.example.com" not in repr(prepared)
    assert "recipient@example.com" not in repr(prepared)
    assert_code(
        "FLOW_MAIL_HOST_BLOCKED",
        lambda: prepare_mail(
            config(), values(), AutomationPolicy(mail_hosts=("other.example.com",))
        ),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("to", "victim@example.com\r\nBcc: attacker@example.com"),
        ("subject", "Hello\r\nBcc: attacker@example.com"),
        ("subject", "bad\x7fvalue"),
        ("subject", "x" * 201),
        ("body", "😀" * 4097),
    ],
)
def test_message_header_injection_and_oversized_body_rejected(field, value):
    altered = config() | {field: value}
    assert_code(
        "FLOW_MAIL_CONFIGURATION_INVALID",
        lambda: prepare_mail(altered, values(), AutomationPolicy(mail_hosts=("smtp.example.com",))),
    )


@pytest.mark.parametrize(
    ("port", "security"),
    [("25", "starttls"), ("587", "ssl"), ("465", "starttls"), ("2525", "local")],
)
def test_production_smtp_protocol_and_port_pairs_are_fixed(port, security):
    assert_code(
        "FLOW_MAIL_CONFIGURATION_INVALID" if security == "local" else "FLOW_MAIL_HOST_BLOCKED",
        lambda: prepare_mail(
            config(),
            values(port=port, security=security),
            AutomationPolicy(mail_hosts=("smtp.example.com",)),
        ),
    )


@pytest.mark.parametrize(
    "origin",
    [
        "smtp://10.1.2.3:2525",
        "smtp://127.0.0.1:25",
        "smtp://127.0.0.1:2525/path",
        "smtp://user:pass@127.0.0.1:2525",
        "http://127.0.0.1:2525",
    ],
)
def test_local_test_override_only_accepts_explicit_loopback_smtp(origin):
    assert_code(
        "FLOW_MAIL_CONFIGURATION_INVALID",
        lambda: prepare_mail(
            config(),
            values(host="127.0.0.1", port="2525", security="local"),
            AutomationPolicy(mail_test_origin=origin),
        ),
    )


def test_cancel_or_expired_deadline_prevents_network_effect(monkeypatch):
    prepared = prepare_mail(
        config(),
        values(host="127.0.0.1", port="2525", security="local"),
        AutomationPolicy(mail_test_origin="smtp://127.0.0.1:2525"),
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Cancellation must prevent the initial network connection")

    monkeypatch.setattr(socket, "create_connection", forbidden)

    def cancel():
        raise OwnershipLostError("Cancelled")

    with pytest.raises(OwnershipLostError):
        perform_mail(prepared, check_active=cancel, deadline=time.monotonic() + 10)
    assert_code(
        "FLOW_RUNTIME_EXCEEDED",
        lambda: perform_mail(prepared, check_active=lambda: None, deadline=time.monotonic() - 1),
    )


def test_transport_failure_does_not_expose_server_diagnostic(monkeypatch):
    prepared = prepare_mail(
        config(),
        values(host="127.0.0.1", port="2525", security="local"),
        AutomationPolicy(mail_test_origin="smtp://127.0.0.1:2525"),
    )

    def failed(*_args, **_kwargs):
        raise OSError("SMTP diagnostic with private-password")

    monkeypatch.setattr(socket, "create_connection", failed)
    error = assert_code(
        "FLOW_MAIL_SEND_FAILED",
        lambda: perform_mail(prepared, check_active=lambda: None, deadline=time.monotonic() + 10),
    )
    assert "private-password" not in str(error)


def test_tls_connection_uses_pinned_ip_but_original_certificate_hostname(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: addresses("8.8.8.8"))
    prepared = prepare_mail(
        config(),
        values(port="465", security="ssl"),
        AutomationPolicy(mail_hosts=("smtp.example.com",)),
    )
    observed = {}

    class DummySocket:
        def settimeout(self, timeout):
            observed["timeout"] = timeout

        def close(self):
            observed["closed"] = True

    connection = DummySocket()

    def connect(address, *, timeout):
        observed["address"], observed["connect_timeout"] = address, timeout
        return connection

    def wrap(sock, *, server_hostname):
        assert sock is connection
        observed["server_hostname"] = server_hostname
        return connection

    context = ssl.create_default_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(
        mail_automation.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=wrap)
    )

    class FakeSMTPSSL:
        def __init__(self, host, port, *, timeout, local_hostname):
            self.sock = self._get_socket(host, port, timeout)

        def ehlo(self):
            pass

        def send_message(self, message):
            observed["subject"] = message["Subject"]
            return {}

        def close(self):
            self.sock.close()

    monkeypatch.setattr(mail_automation.smtplib, "SMTP_SSL", FakeSMTPSSL)
    perform_mail(prepared, check_active=lambda: None, deadline=time.monotonic() + 1)
    assert observed["address"] == ("8.8.8.8", 465)
    assert observed["server_hostname"] == "smtp.example.com"
    assert 0 < observed["timeout"] <= 1
    assert observed["closed"] is True


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
                if self.server.drop_ack:
                    return
                self.wfile.write(b"250 Queued\r\n")
            else:
                return


@contextmanager
def smtp_server(*, drop_ack=False):
    with socketserver.TCPServer(("127.0.0.1", 0), CaptureSMTP) as server:
        server.messages, server.drop_ack = [], drop_ack
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def local_policy(server, *, resolver=None):
    return AutomationPolicy(
        mail_test_origin=f"smtp://127.0.0.1:{server.server_address[1]}", secret_resolver=resolver
    )


def local_values(server):
    return values(host="127.0.0.1", port=str(server.server_address[1]), security="local")


def test_real_local_smtp_delivers_one_message_with_bounded_protocol():
    with smtp_server() as server:
        prepared = prepare_mail(config(), local_values(server), local_policy(server))
        checks = []
        perform_mail(
            prepared, check_active=lambda: checks.append(True), deadline=time.monotonic() + 5
        )
        assert len(server.messages) == 1
        message = email.message_from_bytes(server.messages[0], policy=default)
        assert message["From"] == "sender@example.com"
        assert message["To"] == "recipient@example.com"
        assert message["Subject"] == "MultiRPA test"
        assert message.get_content().strip() == config()["body"]
        assert len(checks) >= 4


def test_smtp_banner_wait_respects_remaining_execution_deadline():
    class SilentSMTP(socketserver.StreamRequestHandler):
        def handle(self):
            self.server.release.wait(2)

    with socketserver.TCPServer(("127.0.0.1", 0), SilentSMTP) as server:
        server.release = Event()
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            prepared = prepare_mail(config(), local_values(server), local_policy(server))
            started = time.monotonic()
            assert_code(
                "FLOW_MAIL_SEND_FAILED",
                lambda: perform_mail(prepared, check_active=lambda: None, deadline=started + 0.05),
            )
            assert time.monotonic() - started < 0.5
        finally:
            server.release.set()
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


class Context:
    def __init__(self):
        self.claim = SimpleNamespace(attempt_count=1)
        self.saved = None

    def check_active(self):
        pass

    def get_checkpoint(self, _key):
        return copy.deepcopy(self.saved)

    def checkpoint(self, _key, value):
        self.saved = copy.deepcopy(value)


def mail_graph():
    return {
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "label": "start",
                "position": {"x": 0, "y": 0},
                "config": {},
            },
            {
                "id": "mail",
                "type": "email_send",
                "label": "mail",
                "position": {"x": 100, "y": 0},
                "config": config(),
            },
            {
                "id": "end",
                "type": "end",
                "label": "end",
                "position": {"x": 200, "y": 0},
                "config": {"output": {}},
            },
        ],
        "edges": [
            {"source": "start", "target": "mail", "port": "next"},
            {"source": "mail", "target": "end", "port": "next"},
        ],
    }


def test_dropped_data_acknowledgement_is_uncertain_and_never_replayed():
    with smtp_server(drop_ack=True) as server:
        context = Context()
        policy = local_policy(server, resolver=lambda _ref, _kind: local_values(server))
        with pytest.raises(FlowOutcomeUncertain):
            execute_graph(context, mail_graph(), {}, automation_policy=policy)
        assert len(server.messages) == 1
        assert context.saved["action_started"] is True
        with pytest.raises(FlowOutcomeUncertain):
            execute_graph(context, mail_graph(), {}, automation_policy=policy)
        assert len(server.messages) == 1
