"""Real files, XLSX/CSV, owned HTTPS, and adapter safety without a database."""

import copy
import hashlib
import io
import json
import os
import ssl
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.adapters.automation_files import RootedFiles, decode_table
from app.adapters.automation_http import PinnedHTTPSConnection, prepare_http
from app.adapters.automation_policy import AutomationPolicy
from app.core.exceptions import OwnershipLostError, ValidationError
from app.workflows.flow_engine import FlowOutcomeUncertain, execute_graph, validate_graph
from app.workflows.registry import WorkflowFailure


@contextmanager
def known_failure(code):
    with pytest.raises(WorkflowFailure) as error:
        yield
    assert error.value.error_code == code


class Context:
    def __init__(self):
        self.claim = SimpleNamespace(attempt_count=1)
        self.saved = None
        self.writes = []
        self.active = True

    def check_active(self):
        if not self.active:
            raise OwnershipLostError("Fixture ownership expired")

    def get_checkpoint(self, _key):
        self.check_active()
        return copy.deepcopy(self.saved)

    def checkpoint(self, _key, value):
        self.check_active()
        self.saved = copy.deepcopy(value)
        self.writes.append(copy.deepcopy(value))


def node(kind, config):
    return {"type": kind, "config": config}


def graph(*steps, output=None):
    nodes = [node("start", {}), *steps, node("end", {"output": output or {"done": True}})]
    for index, item in enumerate(nodes):
        item.update(id=f"step{index}", label=f"Step {index}", position={"x": index * 100, "y": 20})
    return {
        "nodes": nodes,
        "edges": [
            {"source": left["id"], "target": right["id"], "port": "next"}
            for left, right in zip(nodes, nodes[1:], strict=False)
        ],
    }


def test_real_file_write_read_and_completed_checkpoint_replay(tmp_path):
    policy = AutomationPolicy(root=tmp_path / "private")
    context = Context()
    source = graph(
        node("file_write", {"path": "reports/result.txt", "value": "Merhaba {{name}}\n"}),
        node("file_read", {"path": "reports/result.txt", "key": "result"}),
        output={"text": "{{result}}"},
    )
    assert execute_graph(context, source, {"name": "Dünya"}, automation_policy=policy) == {
        "text": "Merhaba Dünya\n"
    }
    saved = policy.root / "reports/result.txt"
    assert saved.read_text() == "Merhaba Dünya\n"
    assert saved.stat().st_mode & 0o777 == 0o600
    assert any(state["action_started"] for state in context.writes)
    saved.unlink()
    assert execute_graph(context, source, {}, automation_policy=policy)["text"] == "Merhaba Dünya\n"


def test_real_csv_and_excel_round_trip_reuse_table_variables(tmp_path):
    source = graph(
        node("csv_write", {"path": "table.csv", "rows": "{{rows}}"}),
        node("csv_read", {"path": "table.csv", "key": "csv_rows"}),
        node("excel_write", {"path": "table.xlsx", "sheet": "Sayfa1", "rows": "{{rows}}"}),
        node("excel_read", {"path": "table.xlsx", "sheet": "Sayfa1", "key": "excel_rows"}),
        output={"csv": "{{csv_rows}}", "excel": "{{excel_rows}}"},
    )
    rows = [["Ad", "Tutar"], ["Örnek", 12], ["Diğer", 34]]
    result = execute_graph(
        Context(), source, {"rows": rows}, automation_policy=AutomationPolicy(root=tmp_path)
    )
    assert result["csv"] == [["Ad", "Tutar"], ["Örnek", "12"], ["Diğer", "34"]]
    assert result["excel"] == rows
    assert zipfile.is_zipfile(tmp_path / "table.xlsx")


@pytest.mark.parametrize(
    "path", ["../outside", "/etc/passwd", "C:/secret", "a\\b", "a/../b", "NUL.txt", "a//b", "a./b"]
)
def test_file_paths_cannot_leave_the_private_workspace(tmp_path, path):
    with known_failure("FLOW_FILE_PATH_BLOCKED"):
        execute_graph(
            Context(),
            graph(node("file_read", {"path": path, "key": "result"})),
            {},
            automation_policy=AutomationPolicy(root=tmp_path),
        )


def test_symlinks_and_hardlinks_are_not_read_or_followed(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("Outside content")
    (private / "linked").symlink_to(outside)
    os.link(outside, private / "hardlinked")
    for name in ("linked", "hardlinked"):
        with pytest.raises(WorkflowFailure):
            RootedFiles(private).read(name, lambda: None)
        with pytest.raises(WorkflowFailure):
            RootedFiles(private).write(name, b"replacement", lambda: None)
    assert outside.read_text() == "Outside content"


def test_parent_symlink_does_not_create_any_file_outside_workspace(tmp_path):
    private, outside = tmp_path / "private", tmp_path / "outside"
    private.mkdir()
    outside.mkdir()
    (private / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkflowFailure):
        RootedFiles(private).write("escape/result.txt", b"content", lambda: None)
    assert not (outside / "result.txt").exists()


@pytest.mark.parametrize("kind", ["csv_write", "excel_write"])
def test_table_formula_text_is_rejected_before_an_effect(tmp_path, kind):
    config = {
        "path": "table.xlsx" if kind == "excel_write" else "table.csv",
        "rows": [['=HYPERLINK("x")']],
    }
    if kind == "excel_write":
        config["sheet"] = "Sayfa1"
    context = Context()
    with known_failure("FLOW_TABLE_FORMULA_BLOCKED"):
        execute_graph(
            context,
            graph(node(kind, config)),
            {},
            automation_policy=AutomationPolicy(root=tmp_path),
        )
    assert not any(state["action_started"] for state in context.writes)
    assert not list(tmp_path.iterdir())


def test_xlsx_archive_size_is_bounded_before_xml_processing():
    binary = io.BytesIO()
    with zipfile.ZipFile(binary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("huge.xml", b"x" * 2_097_153)
    with known_failure("FLOW_EXCEL_ARCHIVE_TOO_LARGE"):
        decode_table("excel_read", binary.getvalue(), sheet="Sheet1", check_active=lambda: None)


def test_mutation_ack_failure_and_same_attempt_are_quarantined(tmp_path, monkeypatch):
    actual_write = RootedFiles.write
    effects = []

    def lost_ack(self, path, data, check_active):
        actual_write(self, path, data, check_active)
        effects.append(path)
        raise OSError("Fixture acknowledgement lost")

    monkeypatch.setattr(RootedFiles, "write", lost_ack)
    source = graph(node("file_write", {"path": "once.txt", "value": "written"}))
    context = Context()
    policy = AutomationPolicy(root=tmp_path)
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {}, automation_policy=policy)
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {}, automation_policy=policy)
    assert effects == ["once.txt"]
    assert (tmp_path / "once.txt").read_text() == "written"


def test_missing_windows_capability_fails_before_an_earlier_file_write(tmp_path):
    source = graph(
        node("file_write", {"path": "must-not-exist.txt", "value": "content"}),
        node(
            "windows_click", {"window_title": "App", "auto_id": "button", "control_type": "Button"}
        ),
    )
    context = Context()
    with known_failure("FLOW_WINDOWS_DESKTOP_REQUIRED"):
        execute_graph(context, source, {}, automation_policy=AutomationPolicy(root=tmp_path))
    assert not context.writes
    assert not list(tmp_path.iterdir())


@pytest.fixture
def https_fixture(tmp_path):
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "fixture.crt", tmp_path / "fixture.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    state = {"requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state["requests"].append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/")
                self.end_headers()
                return
            body = json.dumps({"ok": True}).encode()
            if self.path == "/large":
                body = b"x" * 9000
            elif self.path == "/echo":
                body = json.dumps({"value": self.headers.get("Authorization")}).encode()
            elif self.path == "/echo-token":
                body = json.dumps(
                    {"value": self.headers.get("Authorization").partition(" ")[2]}
                ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def http_config(url):
    return {
        "url": url,
        "method": "GET",
        "headers": {"Accept": "application/json"},
        "body": None,
        "key": "response",
    }


def test_http_runs_real_owned_https_and_returns_bounded_json(https_fixture):
    origin, state = https_fixture
    policy = AutomationPolicy(http_test_origin=origin)
    result = execute_graph(
        Context(),
        graph(node("http_request", http_config(origin + "/ok")), output={"reply": "{{response}}"}),
        {},
        automation_policy=policy,
    )
    assert result == {"reply": {"status": 200, "body": {"ok": True}}}
    assert state["requests"] == ["/ok"]


@pytest.mark.parametrize("path", ["/redirect", "/large"])
def test_http_redirect_and_oversized_response_are_not_retried(https_fixture, path):
    origin, state = https_fixture
    context = Context()
    source = graph(node("http_request", http_config(origin + path)))
    policy = AutomationPolicy(http_test_origin=origin)
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {}, automation_policy=policy)
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {}, automation_policy=policy)
    assert state["requests"] == [path]


@pytest.mark.parametrize("path", ["/echo", "/echo-token"])
def test_authenticated_http_does_not_persist_a_credential_echo(https_fixture, path):
    origin, _ = https_fixture
    credential = "Bearer fixture-private-value-123456"
    policy = AutomationPolicy(
        http_test_origin=origin, secret_resolver=lambda ref, kind: {"authorization": credential}
    )
    config = {**http_config(origin + path), "secret_ref": "api"}
    context = Context()
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, graph(node("http_request", config)), {}, automation_policy=policy)
    assert credential not in json.dumps(context.writes)


def test_credential_echo_in_cached_checkpoint_is_rejected_before_return():
    credential = "private-cached-result-123"
    source = graph(
        node("http_request", {**http_config("https://example.com"), "secret_ref": "api"})
    )
    context = Context()
    context.saved = {
        "graph_digest": hashlib.sha256(
            json.dumps(validate_graph(source), sort_keys=True).encode()
        ).hexdigest(),
        "attempt": 1,
        "action_started": False,
        "finished": True,
        "result": {"leak": credential},
    }
    policy = AutomationPolicy(
        secret_resolver=lambda ref, kind: {"authorization": "Bearer " + credential}
    )
    with known_failure("FLOW_RESULT_CONTAINS_CREDENTIAL"):
        execute_graph(context, source, {}, automation_policy=policy)
    assert not context.writes


def test_browser_extract_cannot_put_a_resolved_password_into_a_checkpoint(monkeypatch):
    credential = "fixture-private-password-123"
    monkeypatch.setattr(
        "app.workflows.browser_actions.BrowserSession",
        lambda policy: SimpleNamespace(
            opened=True,
            validate_url=lambda url: None,
            prepare=lambda: None,
            close=lambda: None,
            perform=lambda kind, config, **kwargs: (
                credential if kind == "browser_extract" else None
            ),
        ),
    )
    source = graph(
        node("browser_open", {"url": "https://example.com"}),
        node(
            "browser_fill",
            {"selector": "#pass", "value": "", "secret_ref": "login", "secret_field": "password"},
        ),
        node("browser_extract", {"selector": "#echo", "key": "echo"}),
    )
    context = Context()
    policy = AutomationPolicy(secret_resolver=lambda ref, kind: {"password": credential})
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {}, automation_policy=policy)
    assert credential not in json.dumps(context.writes)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"]
)
def test_http_dns_pinning_rejects_private_and_special_addresses(monkeypatch, address):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *_args, **_kw: [(None, None, None, None, (address, 443))]
    )
    with known_failure("FLOW_HTTP_HOST_BLOCKED"):
        prepare_http(
            AutomationPolicy(allowed_http_hosts=("example.com",)),
            http_config("https://example.com"),
        )


def test_http_default_tls_verifies_the_original_host():
    connection = PinnedHTTPSConnection("example.com", "93.184.216.34", timeout=1)
    assert connection.host == "example.com"
    assert connection._context.check_hostname
    assert connection._context.verify_mode == ssl.CERT_REQUIRED


def test_browser_credentials_are_resolved_without_entering_checkpoint_variables(monkeypatch):
    observed = []
    credential = "Fixture browser private value 123"

    class Browser:
        opened = False

        def __init__(self, _policy):
            pass

        def validate_url(self, _url):
            pass

        def prepare(self):
            pass

        def perform(self, kind, config, **_kwargs):
            self.opened = True
            if kind == "browser_fill":
                observed.append(config["value"])

        def close(self):
            pass

    monkeypatch.setattr("app.workflows.browser_actions.BrowserSession", Browser)
    source = graph(
        node("browser_open", {"url": "https://example.com"}),
        node(
            "browser_fill",
            {"selector": "#pass", "value": "", "secret_ref": "login", "secret_field": "password"},
        ),
    )
    context = Context()
    assert execute_graph(
        context,
        source,
        {},
        automation_policy=AutomationPolicy(
            secret_resolver=lambda ref, kind: {"password": credential}
        ),
    ) == {"done": True}
    assert observed == [credential]
    assert credential not in json.dumps(context.writes)


@pytest.mark.parametrize("name", ["Authorization", "Cookie", "Host", "Proxy-Authorization"])
def test_flow_cannot_supply_credentials_or_control_http_routing(name):
    config = {**http_config("https://example.com"), "headers": {name: "value"}}
    with pytest.raises(ValidationError):
        validate_graph(graph(node("http_request", config)))


def test_unavailable_secret_fails_before_an_earlier_file_effect(tmp_path):
    source = graph(
        node("file_write", {"path": "not-created.txt", "value": "value"}),
        node("http_request", {**http_config("https://example.com"), "secret_ref": "api"}),
    )
    with known_failure("FLOW_CREDENTIALS_NOT_CONFIGURED"):
        execute_graph(Context(), source, {}, automation_policy=AutomationPolicy(root=tmp_path))
    assert not list(tmp_path.iterdir())
