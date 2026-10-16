"""Exercise the packaged native runner and local file/CSV/Excel adapters."""

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

desktop = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--runner", type=Path)
arguments = parser.parse_args()
binary = arguments.runner or (
    desktop
    / "runner-dist/multirpa-runner"
    / ("multirpa-runner.exe" if sys.platform == "win32" else "multirpa-runner")
)


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


with tempfile.TemporaryDirectory(prefix="multirpa-packaged-") as temporary:
    signing = Ed25519PrivateKey.generate()
    device = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    tenant = str(uuid4())
    child = subprocess.Popen(
        [
            str(binary),
            "--storage",
            str(Path(temporary) / "workspace.sqlite"),
            "--server-key",
            encode(signing.public_key().public_bytes_raw()),
            "--device-key",
            encode(device),
            "--tenant-id",
            tenant,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sequence = 0

    def request(operation: str, **values):
        global sequence
        sequence += 1
        assert child.stdin and child.stdout
        child.stdin.write(json.dumps({"id": sequence, "operation": operation, **values}) + "\n")
        child.stdin.flush()
        line = child.stdout.readline()
        if not line:
            raise RuntimeError("Packaged runner exited unexpectedly")
        result = json.loads(line)
        assert result["ok"], result
        return result["data"]

    nodes = [
        ("start", {}),
        ("file_write", {"path": "result.txt", "value": "MultiRPA native runner"}),
        ("file_read", {"path": "result.txt", "key": "text"}),
        ("csv_write", {"path": "table.csv", "rows": [["name", "value"], ["MultiRPA", 42]]}),
        ("csv_read", {"path": "table.csv", "key": "csv_data"}),
        ("excel_write", {"path": "table.xlsx", "sheet": "Results", "rows": "{{csv_data}}"}),
        ("excel_read", {"path": "table.xlsx", "sheet": "Results", "key": "excel_data"}),
        ("end", {"output": {"text": "{{text}}", "rows": "{{excel_data}}"}}),
    ]
    graph = {
        "nodes": [
            {
                "id": f"step{index}",
                "type": kind,
                "label": kind,
                "position": {"x": 20, "y": 20},
                "config": config,
            }
            for index, (kind, config) in enumerate(nodes)
        ],
        "edges": [
            {"source": f"step{index}", "target": f"step{index + 1}", "port": "next"}
            for index in range(len(nodes) - 1)
        ],
    }
    try:
        saved = request(
            "request",
            method="POST",
            path="/api/flows",
            body={"name": "Native smoke", "graph": graph},
        )["flow"]
        exported = request("request", method="GET", path=f"/api/flows/{saved['id']}/export")
        imported = request(
            "request",
            method="POST",
            path="/api/flows/import",
            body={"document": exported["document"]},
        )["flow"]
        assert imported["id"] != saved["id"] and imported["version"] == 1
        assert imported["graph"] == saved["graph"]
        assert exported["document"]["schema_version"] == 1
        versioned_document = json.loads(json.dumps(exported["document"]))
        versioned_document["schema_version"] = 2
        versioned_document["flow"]["name"] = "Native versioned smoke"
        for node in versioned_document["flow"]["graph"]["nodes"]:
            node["type_version"] = 1
        versioned = request(
            "request",
            method="POST",
            path="/api/flows/import",
            body={"document": versioned_document},
        )["flow"]
        assert versioned["id"] not in {saved["id"], imported["id"]}
        assert versioned["version"] == 1
        assert versioned["graph"] == versioned_document["flow"]["graph"]
        versioned_export = request(
            "request", method="GET", path=f"/api/flows/{versioned['id']}/export"
        )
        assert versioned_export["document"] == versioned_document
        assert request("request", method="GET", path="/api/jobs")["items"] == []
        assert not (Path(temporary) / "files/result.txt").exists()
        issued = int(time.time())
        payload = json.dumps(
            {
                "version": 1,
                "issuer": "multirpa",
                "tenant_id": tenant,
                "key_thumbprint": hashlib.sha256(device).hexdigest(),
                "device_id": str(uuid4()),
                "lease_id": str(uuid4()),
                "issued_at": issued,
                "expires_at": issued + 60,
                "entitlement_expires_at": issued + 3600,
                "valid_for_ms": 55_000,
                "flow_ids": [imported["id"], versioned["id"]],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        request(
            "permit",
            envelope={"payload": payload, "signature": encode(signing.sign(payload.encode()))},
            elapsed_ms=0,
        )
        for runnable in (imported, versioned):
            result = request(
                "request",
                method="POST",
                path=f"/api/flows/{runnable['id']}/runs",
                body={"request_id": str(uuid4()), "version": 1, "inputs": {}, "max_attempts": 1},
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                transaction = request(
                    "request", method="GET", path=f"/api/transactions/{result['job']['id']}"
                )["transaction"]
                if transaction["status"] != "PROCESSING":
                    break
                time.sleep(0.01)
            assert transaction["status"] == "SUCCESS", transaction["last_error"]
            assert transaction["result_data"] == {
                "text": "MultiRPA native runner",
                "rows": [["name", "value"], ["MultiRPA", "42"]],
            }
        print(
            "Packaged runner: legacy schema 1 and explicit node type_version schema 2 "
            "roundtrips created fresh drafts without running; signed device permit + "
            "local file/CSV/Excel execution succeeded for both versions."
        )
    finally:
        assert child.stdin
        child.stdin.close()
        if child.wait(timeout=25) != 0:
            raise RuntimeError("Packaged runner did not close cleanly")
