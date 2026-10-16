"""Device permits and local execution use the shared graph contract."""

import base64
import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.exceptions import ValidationError
from app.desktop.license import LeaseGuard, LicenseDenied
from app.desktop.runner import LocalRunner
from app.desktop.store import LocalStore
from app.workflows.registry import WorkflowFailure


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class Permit:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.device_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        self.tenant = str(uuid4())
        self.wall, self.mono = 1_900_000_000.0, 1000.0

    def guard(self):
        return LeaseGuard(
            encoded(self.key.public_key().public_bytes_raw()),
            encoded(self.device_key),
            self.tenant,
            wall=lambda: self.wall,
            monotonic=lambda: self.mono,
        )

    def envelope(self, flows, **changes):
        body = {
            "version": 1,
            "issuer": "multirpa",
            "tenant_id": self.tenant,
            "key_thumbprint": hashlib.sha256(self.device_key).hexdigest(),
            "device_id": str(uuid4()),
            "lease_id": str(uuid4()),
            "issued_at": int(self.wall),
            "expires_at": int(self.wall) + 60,
            "entitlement_expires_at": int(self.wall) + 3600,
            "valid_for_ms": 60_000,
            "flow_ids": flows,
            **changes,
        }
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return {"payload": payload, "signature": encoded(self.key.sign(payload.encode()))}


def graph():
    nodes = [
        {"id": "start", "type": "start", "label": "Başla", "config": {}},
        {"id": "assign", "type": "set", "label": "Ata", "config": {"key": "answer", "value": 42}},
        {
            "id": "end",
            "type": "end",
            "label": "Bitir",
            "config": {"output": {"answer": "{{answer}}"}},
        },
    ]
    for node in nodes:
        node["position"] = {"x": 20, "y": 20}
    return {
        "nodes": nodes,
        "edges": [
            {"source": "start", "target": "assign", "port": "next"},
            {"source": "assign", "target": "end", "port": "next"},
        ],
    }


def flow(runner):
    return runner.save_flow({"name": "Yerel akış", "description": "", "graph": graph()})["flow"]


def run_body(version=1):
    return {"request_id": str(uuid4()), "version": version, "inputs": {}, "max_attempts": 1}


def test_permit_is_pinned_to_server_device_tenant_and_reserved_flow():
    permit = Permit()
    flow_id = str(uuid4())
    guard = permit.guard()
    envelope = permit.envelope([flow_id])
    guard.accept(envelope)
    guard.check(flow_id)
    with pytest.raises(LicenseDenied, match="FLOW_NOT_RESERVED"):
        guard.check(str(uuid4()))
    other = Permit().envelope([flow_id])
    # Even a response advertising another public key cannot replace the pin.
    other["public_key"] = encoded(Permit().key.public_key().public_bytes_raw())
    with pytest.raises(LicenseDenied, match="LICENSE_INVALID"):
        guard.accept(other)
    with pytest.raises(LicenseDenied):
        guard.check(flow_id)


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": str(uuid4())},
        {"key_thumbprint": "0" * 64},
        {"expires_at": 1_900_000_061},
        {"entitlement_expires_at": 1_900_000_030},
        {"valid_for_ms": True},
        {"valid_for_ms": 60_001},
        {"flow_ids": ["not-a-uuid"]},
    ],
)
def test_invalid_signed_permits_are_rejected(change):
    permit = Permit()
    with pytest.raises(LicenseDenied):
        permit.guard().accept(permit.envelope([], **change))


def test_delayed_response_replay_and_clock_rollback_never_extend_permit():
    permit = Permit()
    flow_id = str(uuid4())
    guard = permit.guard()
    envelope = permit.envelope([flow_id])
    guard.accept(envelope, elapsed_ms=59_000)
    permit.mono += 1.01
    permit.wall += 1.01
    with pytest.raises(LicenseDenied, match="EXPIRED"):
        guard.check(flow_id)
    with pytest.raises(LicenseDenied):
        guard.accept(envelope)
    guard.accept(permit.envelope([flow_id]))
    permit.wall -= 20
    with pytest.raises(LicenseDenied, match="EXPIRED"):
        guard.check(flow_id)


def test_large_paid_permit_has_bounded_capacity_and_remains_device_verified():
    permit = Permit()
    flows = [str(uuid4()) for _ in range(1000)]
    guard = permit.guard()
    guard.accept(permit.envelope(flows))
    guard.check(flows[-1])
    with pytest.raises(LicenseDenied):
        guard.accept(permit.envelope(flows + [str(uuid4())]))


def test_local_credential_snapshot_is_memory_only_and_kind_scoped(tmp_path):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    runner.configure_secrets(
        {"my_api": {"kind": "http", "values": {"authorization": "Bearer private-local"}}}
    )
    assert runner.resolve_secret("my_api", "http") == {"authorization": "Bearer private-local"}
    with pytest.raises(WorkflowFailure) as error:
        runner.resolve_secret("my_api", "smtp")
    assert error.value.error_code == "FLOW_CREDENTIALS_NOT_CONFIGURED"
    assert b"private-local" not in (tmp_path / "workspace.sqlite").read_bytes()
    runner.close()


def test_invalid_local_credentials_do_not_replace_valid_snapshot(tmp_path):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    runner.configure_secrets(
        {"my_api": {"kind": "http", "values": {"authorization": "Bearer valid"}}}
    )
    for snapshot in [
        {"my_api": {"kind": "http", "values": {"authorization": "Bearer bad\nheader"}}},
        {"my_api": {"kind": "browser", "values": {"username": "", "password": ""}}},
        {"not a reference": {"kind": "http", "values": {"authorization": "Bearer test"}}},
    ]:
        with pytest.raises(ValidationError):
            runner.configure_secrets(snapshot)
    assert runner.resolve_secret("my_api", "http") == {"authorization": "Bearer valid"}
    runner.configure_secrets({})
    with pytest.raises(WorkflowFailure):
        runner.resolve_secret("my_api", "http")
    runner.close()


def test_account_directories_keep_flows_separate_and_unbound_key_cannot_execute(tmp_path):
    permit = Permit()
    root = tmp_path / "accounts"
    first = LocalRunner(root / permit.tenant / "workspace.sqlite", permit.guard())
    saved = flow(first)
    first.close()
    other = Permit()
    second = LocalRunner(root / other.tenant / "workspace.sqlite", other.guard())
    assert second.store.all("flows") == []
    second.close()
    editing = LocalRunner(
        root / permit.tenant / "workspace.sqlite",
        LeaseGuard(
            encoded(permit.key.public_key().public_bytes_raw()), encoded(bytes(32)), permit.tenant
        ),
    )
    assert editing.store.get("flows", saved["id"])["name"] == "Yerel akış"
    changed = editing.save_flow(
        {"name": "Çevrimdışı düzenleme", "graph": graph(), "expected_version": 1}, saved["id"]
    )["flow"]
    with pytest.raises(LicenseDenied):
        editing.start_run(saved["id"], run_body(changed["version"]))
    with pytest.raises(LicenseDenied):
        editing.guard.accept(permit.envelope([saved["id"]]))
    assert editing.store.all("runs") == []
    editing.close()


def test_local_shared_flow_completes_and_run_request_is_idempotent(tmp_path):
    permit = Permit()
    runner = LocalRunner(tmp_path / "workspace.sqlite", permit.guard())
    saved = flow(runner)
    runner.guard.accept(permit.envelope([saved["id"]]))
    body = run_body()
    result = runner.start_run(saved["id"], body)
    with runner._lock:
        active = list(runner._active.values())
    for thread, _event in active:
        thread.join(timeout=5)
        assert not thread.is_alive()
    job_id = result["job"]["id"]
    run = runner.store.get("runs", job_id)
    assert run["status"] == "COMPLETED"
    assert run["result"] == {"answer": 42}
    assert run["checkpoints"]["flow_state"]["finished"] is True
    assert runner.start_run(saved["id"], body)["created"] is False
    assert runner.request("GET", f"/api/jobs/{job_id}")["job"]["status"] == "COMPLETED"
    runner.close()


def test_offline_editing_is_allowed_but_execution_and_retries_are_blocked(tmp_path):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    saved = flow(runner)
    with pytest.raises(LicenseDenied):
        runner.start_run(saved["id"], run_body())
    with pytest.raises(ValidationError, match="RETRY"):
        runner.start_run(saved["id"], {**run_body(), "max_attempts": 3})
    assert runner.store.all("runs") == []
    runner.close()


def test_flow_transfer_uses_latest_definition_and_new_local_identity_without_running(tmp_path):
    permit = Permit()
    source = LocalRunner(tmp_path / "source.sqlite", permit.guard())
    target = LocalRunner(tmp_path / "target.sqlite", Permit().guard())
    try:
        saved = flow(source)
        changed_graph = graph()
        changed_graph["nodes"][1]["config"]["value"] = 99
        changed = source.save_flow(
            {
                "name": "Güncel akış",
                "description": "Sürüm iki",
                "graph": changed_graph,
                "expected_version": 1,
            },
            saved["id"],
        )["flow"]
        exported = source.request("GET", f"/api/flows/{saved['id']}/export")
        assert exported["filename"] == f"MultiRPA-flow-{saved['id']}-v2.multirpa.json"
        assert set(exported["document"]) == {"format", "schema_version", "flow"}
        assert set(exported["document"]["flow"]) == {"name", "description", "graph"}
        imported = target.request("POST", "/api/flows/import", {"document": exported["document"]})[
            "flow"
        ]
        repeated = target.request("POST", "/api/flows/import", {"document": exported["document"]})[
            "flow"
        ]
        assert len({saved["id"], imported["id"], repeated["id"]}) == 3
        assert imported["version"] == repeated["version"] == 1
        assert imported["graph"] == repeated["graph"] == changed["graph"]
        assert imported["name"] == "Güncel akış" and imported["description"] == "Sürüm iki"
        assert source.store.all("runs") == target.store.all("runs") == []
        with pytest.raises(LicenseDenied):
            target.start_run(imported["id"], run_body())
        with pytest.raises(ValueError, match="DESKTOP_NOT_FOUND"):
            target.request("GET", f"/api/flows/{saved['id']}/export")
        # A copied definition never inherits the source identity's reserved permit.
        source.guard.accept(permit.envelope([saved["id"]]))
        copied = source.request("POST", "/api/flows/import", {"document": exported["document"]})[
            "flow"
        ]
        with pytest.raises(LicenseDenied, match="FLOW_NOT_RESERVED"):
            source.start_run(copied["id"], run_body())
    finally:
        source.close()
        target.close()


def test_flow_transfer_preserves_secret_aliases_without_reading_vault_values(tmp_path):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    try:
        runner.configure_secrets(
            {
                "my_login": {
                    "kind": "browser",
                    "values": {
                        "username": "private-account",
                        "password": "private-password",
                    },
                }
            }
        )
        browser_graph = graph()
        browser_graph["nodes"][1].update(type="browser_open", config={"url": "https://example.com"})
        browser_graph["nodes"].insert(
            2,
            {
                "id": "fill",
                "type": "browser_fill",
                "label": "Parola",
                "position": {"x": 20, "y": 20},
                "config": {
                    "selector": "#password",
                    "value": "",
                    "secret_ref": "my_login",
                    "secret_field": "password",
                },
            },
        )
        browser_graph["edges"][1]["target"] = "fill"
        browser_graph["edges"].append({"source": "fill", "target": "end", "port": "next"})
        browser_graph["nodes"][-1]["config"] = {"output": {}}
        saved = runner.save_flow({"name": "Kasalı akış", "graph": browser_graph})["flow"]
        exported = runner.request("GET", f"/api/flows/{saved['id']}/export")
        serialized = json.dumps(exported)
        assert "my_login" in serialized
        assert "private-account" not in serialized and "private-password" not in serialized
        runner.configure_secrets({})
        imported = runner.request("POST", "/api/flows/import", {"document": exported["document"]})[
            "flow"
        ]
        assert imported["graph"] == saved["graph"]
        assert runner.store.all("runs") == []
    finally:
        runner.close()


@pytest.mark.parametrize(
    "change",
    [
        lambda document: document.update(schema_version=3),
        lambda document: document["flow"].update(id=str(uuid4())),
        lambda document: document["flow"].update(version=10),
        lambda document: document["flow"].update(credentials={"password": "private"}),
        lambda document: document["flow"]["graph"]["nodes"][1]["config"].update(
            value={"__proto__": {"polluted": True}}
        ),
        lambda document: document["flow"]["graph"]["edges"][0].update(target="missing"),
    ],
)
def test_invalid_import_never_creates_local_flow_or_execution(tmp_path, change):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    try:
        document = {
            "format": "multirpa-flow",
            "schema_version": 1,
            "flow": {"name": "Yeni akış", "description": "", "graph": graph()},
        }
        change(document)
        with pytest.raises(ValidationError):
            runner.request("POST", "/api/flows/import", {"document": document})
        assert runner.store.all("flows") == runner.store.all("runs") == []
    finally:
        runner.close()


def test_local_import_rejects_execution_or_identity_fields_in_request(tmp_path):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    try:
        document = {
            "format": "multirpa-flow",
            "schema_version": 1,
            "flow": {"name": "Yeni akış", "description": "", "graph": graph()},
        }
        for extra in ({"run": True}, {"id": str(uuid4())}, {"expected_version": 1}):
            with pytest.raises(ValidationError):
                runner.request("POST", "/api/flows/import", {"document": document, **extra})
        assert runner.store.all("flows") == runner.store.all("runs") == []
    finally:
        runner.close()


def test_local_v2_transfer_preserves_mixed_versions_and_executes_shared_contract(tmp_path):
    permit = Permit()
    runner = LocalRunner(tmp_path / "workspace.sqlite", permit.guard())
    try:
        source = graph()
        source["nodes"][1]["type_version"] = 1
        document = {
            "format": "multirpa-flow",
            "schema_version": 2,
            "flow": {"name": "Sürümlü akış", "description": "", "graph": source},
        }
        flow = runner.request("POST", "/api/flows/import", {"document": document})["flow"]
        assert flow["version"] == 1 and flow["graph"] == source
        exported = runner.request("GET", f"/api/flows/{flow['id']}/export")
        assert exported["document"] == document
        assert runner.store.all("runs") == []
        runner.guard.accept(permit.envelope([flow["id"]]))
        result = runner.start_run(flow["id"], run_body())
        with runner._lock:
            active = list(runner._active.values())
        for thread, _event in active:
            thread.join(timeout=5)
            assert not thread.is_alive()
        run = runner.store.get("runs", result["job"]["id"])
        assert run["status"] == "COMPLETED" and run["result"] == {"answer": 42}
        assert run["graph"] == source
        metadata = runner.request("GET", "/api/workflows")["node_catalog"]
        assert len(metadata) == 20
        assert next(item for item in metadata if item["type"] == "set") == {
            "type": "set",
            "type_version": 1,
            "output_ports": ["next"],
            "required_capability": None,
        }
    finally:
        runner.close()


@pytest.mark.parametrize("version", [True, 1.0, "1", 2, None])
def test_local_bad_node_version_never_creates_definition_or_run(tmp_path, version):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    try:
        source = graph()
        source["nodes"][-1]["type_version"] = version
        definition = {"name": "Reddedilecek akış", "description": "", "graph": source}
        with pytest.raises(ValidationError, match="type version"):
            runner.save_flow(definition)
        with pytest.raises(ValidationError, match="type version"):
            runner.request(
                "POST",
                "/api/flows/import",
                {"document": {"format": "multirpa-flow", "schema_version": 2, "flow": definition}},
            )
        assert runner.store.all("flows") == runner.store.all("runs") == []
    finally:
        runner.close()


def test_local_future_stored_revision_is_rejected_before_new_run(tmp_path):
    permit = Permit()
    runner = LocalRunner(tmp_path / "workspace.sqlite", permit.guard())
    try:
        saved = flow(runner)
        runner.guard.accept(permit.envelope([saved["id"]]))
        # Simulate a definition written by a future application version.
        saved["graph"]["nodes"][-1]["type_version"] = 2
        with runner.store.transaction() as db:
            db.execute("UPDATE flows SET document=? WHERE id=?", (json.dumps(saved), saved["id"]))
        with pytest.raises(ValidationError, match="type version"):
            runner.start_run(saved["id"], run_body())
        assert runner.store.all("runs") == []
    finally:
        runner.close()


def test_license_expiry_stops_next_step_without_replaying_effects(tmp_path, monkeypatch):
    permit = Permit()
    runner = LocalRunner(tmp_path / "workspace.sqlite", permit.guard())
    saved = flow(runner)
    runner.guard.accept(permit.envelope([saved["id"]]))
    entered, released = threading.Event(), threading.Event()

    def executing(context, *_args, **_kwargs):
        context.checkpoint("flow_state", {"action_started": True})
        entered.set()
        assert released.wait(timeout=5)
        context.check_active()
        return {"unexpected": True}

    monkeypatch.setattr("app.desktop.runner.execute_graph", executing)
    result = runner.start_run(saved["id"], run_body())
    assert entered.wait(timeout=5)
    with pytest.raises(ValueError, match="BUSY"):
        runner.start_run(saved["id"], run_body())
    runner.guard.invalidate()
    released.set()
    with runner._lock:
        active = list(runner._active.values())
    for thread, _event in active:
        thread.join(timeout=5)
    run = runner.store.get("runs", result["job"]["id"])
    assert run["status"] == "FAILED"
    assert run["uncertain"] is True
    assert run["result"] == {}
    runner.close()


def test_restart_quarantines_interrupted_runs_and_preserves_local_flows(tmp_path):
    runner = LocalRunner(tmp_path / "workspace.sqlite", Permit().guard())
    saved = flow(runner)
    run = {
        "id": str(uuid4()),
        "request_id": str(uuid4()),
        "flow_id": saved["id"],
        "status": "RUNNING",
    }
    with runner.store.transaction() as db:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?)",
            (run["id"], run["request_id"], saved["id"], json.dumps(run)),
        )
    runner.close()
    store = LocalStore(tmp_path / "workspace.sqlite")
    assert store.get("flows", saved["id"])["name"] == "Yerel akış"
    interrupted = store.get("runs", run["id"])
    assert interrupted["status"] == "FAILED" and interrupted["uncertain"] is True
    store.close()


def test_private_child_jsonlines_has_no_http_or_arbitrary_operation(tmp_path):
    permit = Permit()
    root = Path(__file__).resolve().parents[1]
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.desktop",
            "--storage",
            str(tmp_path / "ipc.sqlite"),
            "--server-key",
            encoded(permit.key.public_key().public_bytes_raw()),
            "--device-key",
            encoded(permit.device_key),
            "--tenant-id",
            permit.tenant,
        ],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdin and child.stdout
    child.stdin.write(
        json.dumps(
            {
                "id": 1,
                "operation": "request",
                "method": "POST",
                "path": "/api/flows",
                "body": {"name": "Yerel", "graph": graph()},
            }
        )
        + "\n"
    )
    child.stdin.flush()
    result = json.loads(child.stdout.readline())
    assert result["ok"] is True
    child.stdin.write('{"id":2,"operation":"shell","command":"whoami"}\n')
    child.stdin.flush()
    result = json.loads(child.stdout.readline())
    assert result["ok"] is False and result["error"]["code"] == "DESKTOP_OPERATION_UNSUPPORTED"
    child.stdin.close()
    assert child.wait(timeout=5) == 0
