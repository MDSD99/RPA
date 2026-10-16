"""Execute genuine Chromium actions against owned, isolated HTTPS fixtures.

No application database or external site is contacted. The Python-only test
policy permits exactly one fixture origin and is never an API/environment knob.
"""

import copy
import json
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from app.core.exceptions import OwnershipLostError
from app.workflows.browser_actions import BrowserPolicy
from app.workflows.flow_engine import FlowOutcomeUncertain, execute_graph

pytestmark = pytest.mark.browser


class Context:
    def __init__(self):
        self.claim = SimpleNamespace(attempt_count=1)
        self.saved = None
        self.writes = []
        self.lost = False

    def check_active(self):
        if self.lost:
            raise OwnershipLostError("Fixture worker ownership expired")

    def checkpoint(self, key, value):
        self.check_active()
        self.saved = copy.deepcopy(value)
        self.writes.append(copy.deepcopy(value))

    def get_checkpoint(self, key):
        self.check_active()
        return copy.deepcopy(self.saved)


def _node(identifier, kind, config=None):
    return {
        "id": identifier,
        "type": kind,
        "label": identifier,
        "position": {"x": 0, "y": 0},
        "config": config or {},
    }


def _graph(origin, path="/"):
    nodes = [
        _node("start", "start"),
        _node("open", "browser_open", {"url": origin + path}),
        _node("fill", "browser_fill", {"selector": "#name", "value": "{{name}}"}),
        _node("submit", "browser_click", {"selector": "#submit"}),
        _node("read", "browser_extract", {"selector": "#result:not(:empty)", "key": "result"}),
        _node("end", "end", {"output": {"value": "{{result}}"}}),
    ]
    return {
        "nodes": nodes,
        "edges": [
            {"source": left["id"], "target": right["id"], "port": "next"}
            for left, right in zip(nodes, nodes[1:], strict=False)
        ],
    }


@pytest.fixture
def fixture_servers(tmp_path):
    certificate = tmp_path / "fixture.crt"
    key = tmp_path / "fixture.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    key.chmod(0o600)
    state = {"submitted": [], "blocked_requests": 0}
    servers = []
    threads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.server.is_blocked_fixture:
                state["blocked_requests"] += 1
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", state["blocked_origin"] + "/")
                self.end_headers()
                return
            html = b"""<!doctype html><html><body>
              <form id="form"><input id="name"><button id="submit">Save</button></form>
              <div id="result"></div><script>
              document.querySelector('#form').addEventListener('submit', async event => {
                event.preventDefault();
                const response = await fetch('/submit', {method: 'POST',
                  headers: {'Content-Type':'application/json'},
                  body:JSON.stringify({name:document.querySelector('#name').value})});
                document.querySelector('#result').textContent = (await response.json()).result;
              });</script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            if self.server.is_blocked_fixture:
                state["blocked_requests"] += 1
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            state["submitted"].append(data["name"])
            payload = json.dumps({"result": "Saved: " + data["name"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    try:
        for blocked in (False, True):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.is_blocked_fixture = blocked
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(certificate, key)
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
        origin = f"https://127.0.0.1:{servers[0].server_port}"
        state["blocked_origin"] = f"https://127.0.0.1:{servers[1].server_port}"
        yield origin, state
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_real_browser_opens_fills_clicks_and_extracts_result(fixture_servers):
    origin, state = fixture_servers
    context = Context()
    result = execute_graph(
        context,
        _graph(origin),
        {"name": "Fixture record"},
        browser_policy=BrowserPolicy.for_test_origin(origin),
    )
    assert result == {"value": "Saved: Fixture record"}
    assert state["submitted"] == ["Fixture record"]
    assert state["blocked_requests"] == 0
    assert context.saved["finished"] is True
    assert any(item["next"] == "submit" and item["action_started"] for item in context.writes)


def test_real_browser_blocks_redirect_to_other_owned_fixture_origin(fixture_servers):
    origin, state = fixture_servers
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(
            Context(),
            _graph(origin, "/redirect"),
            {"name": "Fixture record"},
            browser_policy=BrowserPolicy.for_test_origin(origin),
        )
    assert state["blocked_requests"] == 0
    assert state["submitted"] == []


def test_real_browser_lost_ownership_prevents_click(fixture_servers):
    origin, state = fixture_servers

    class LostBeforeClick(Context):
        def checkpoint(self, key, value):
            super().checkpoint(key, value)
            if value["next"] == "submit" and value["action_started"]:
                self.lost = True

    context = LostBeforeClick()
    with pytest.raises(OwnershipLostError):
        execute_graph(
            context,
            _graph(origin),
            {"name": "Fixture record"},
            browser_policy=BrowserPolicy.for_test_origin(origin),
        )
    assert state["submitted"] == []
    assert context.saved["finished"] is False


@pytest.mark.postgres
def test_saved_flow_real_worker_browser_and_idempotent_run(
    service, session_factory, fixture_servers
):
    """Persisted revision → leased PG worker → real browser → fenced result."""
    import time
    from uuid import uuid4

    from app.core.states import JobStatus
    from app.services.flow_service import FlowService
    from app.services.management_service import ManagementService
    from app.workers.runtime import WorkflowWorkerPool
    from app.workflows.registry import WorkflowDefinition, WorkflowRegistry

    origin, state = fixture_servers
    flows = FlowService(session_factory)
    flow = flows.create_flow(name="Isolated browser fixture", graph=_graph(origin))
    request_id = uuid4()
    arguments = {
        "request_id": request_id,
        "version": flow["version"],
        "inputs": {"name": "Database fixture"},
    }
    run = flows.start_run(flow["id"], **arguments)
    policy = BrowserPolicy.for_test_origin(origin)

    def handler(context):
        spec = flows.execution_spec(context.claim.job_id)
        return execute_graph(context, spec["graph"], spec["inputs"], browser_policy=policy)

    registry = WorkflowRegistry(
        [
            WorkflowDefinition(
                "visual_flow", "Visual fixture", "Isolated real browser fixture", handler
            )
        ]
    )
    pool = WorkflowWorkerPool(
        service,
        ManagementService(session_factory),
        registry,
        worker_count=2,
        poll_seconds=0.03,
        lease_seconds=10,
        heartbeat_seconds=1,
    )
    try:
        pool.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            saved = service.get_job(run["job"]["id"])
            if saved.status == JobStatus.COMPLETED:
                break
            time.sleep(0.05)
        assert saved.status == JobStatus.COMPLETED
        assert saved.success_count == 1
        rows = ManagementService(session_factory).transactions(job_id=saved.id)
        assert rows["total"] == 1
        transaction = service.get_transaction(rows["items"][0]["id"])
        assert transaction.result_data == {"value": "Saved: Database fixture"}
        assert transaction.attempt_count == 1
        assert state["submitted"] == ["Database fixture"]
        repeated = flows.start_run(flow["id"], **arguments)
        assert repeated["job"]["id"] == run["job"]["id"]
        assert repeated["job"]["total_records"] == 1
        assert state["submitted"] == ["Database fixture"]
        checkpoints = service.get_checkpoints(transaction.id)
        progress = next(
            item.checkpoint_value for item in checkpoints if item.checkpoint_key == "flow_state"
        )
        assert progress["executed"] == ["start", "open", "fill", "submit", "read", "end"]
    finally:
        pool.stop(timeout=15)
