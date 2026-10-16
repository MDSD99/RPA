"""Local execution adapter for the same graph engine used by web workers."""

import hashlib
import json
import os
import re
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from app.adapters.automation_policy import AutomationPolicy
from app.core.exceptions import OwnershipLostError, ValidationError
from app.core.validation import validate_json
from app.desktop.license import LeaseGuard
from app.desktop.store import LocalStore
from app.secrets.domain import reference, validate_values
from app.workflows.browser_actions import BrowserPolicy
from app.workflows.flow_engine import FlowOutcomeUncertain, execute_graph, validate_graph
from app.workflows.flow_transfer import export_document, import_document, validate_metadata
from app.workflows.node_contract import node_catalog
from app.workflows.registry import WorkflowFailure


def now() -> str:
    return datetime.now(UTC).isoformat()


def identity(value: str) -> str:
    return str(UUID(value))


class LocalContext:
    def __init__(self, runner: "LocalRunner", run: dict[str, Any], cancel: threading.Event):
        self.runner, self.run, self.cancel = runner, run, cancel
        self.claim = SimpleNamespace(attempt_count=1)

    def check_active(self) -> None:
        if self.cancel.is_set():
            raise OwnershipLostError("DESKTOP_CANCELLED")
        self.runner.guard.check(self.run["flow_id"])

    def get_checkpoint(self, key: str) -> dict[str, Any] | None:
        return self.run["checkpoints"].get(key)

    def checkpoint(self, key: str, value: dict[str, Any]) -> None:
        self.check_active()
        self.run["checkpoints"][key] = value
        self.run["updated_at"] = now()
        self.runner.store.save_run(self.run)


class LocalRunner:
    def __init__(
        self, path: Path, guard: LeaseGuard, *, browser_policy: BrowserPolicy | None = None
    ) -> None:
        self.store = LocalStore(path)
        self.guard = guard
        self.browser_policy = browser_policy or BrowserPolicy()
        self._lock = threading.Lock()
        self._launch_lock = threading.Lock()
        self._active: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._secrets: dict[str, dict[str, Any]] = {}
        self.automation_policy = AutomationPolicy(
            root=path.parent / "files",
            allowed_http_hosts=tuple(
                part
                for part in os.environ.get("MULTIRPA_HTTP_ALLOWED_HOSTS", "").split(",")
                if part
            ),
            mail_hosts=tuple(
                part
                for part in os.environ.get("MULTIRPA_MAIL_ALLOWED_HOSTS", "").split(",")
                if part
            ),
            windows_enabled=sys.platform == "win32",
            secret_resolver=self.resolve_secret,
        )

    def configure_secrets(self, values: dict[str, dict[str, Any]]) -> None:
        if (
            not isinstance(values, dict)
            or len(values) > 100
            or len(json.dumps(values).encode()) > 120_000
        ):
            raise ValidationError("DESKTOP_SECRET_INPUT_INVALID")
        validated = {}
        for ref, stored in values.items():
            if not isinstance(stored, dict):
                raise ValidationError("DESKTOP_SECRET_INPUT_INVALID")
            kind, credentials = stored.get("kind"), stored.get("values")
            if not isinstance(kind, str) or not isinstance(credentials, dict):
                raise ValidationError("DESKTOP_SECRET_INPUT_INVALID")
            validated[reference(ref)] = {
                "kind": kind,
                "values": validate_values(kind, credentials),
            }
        with self._lock:
            self._secrets = validated

    def resolve_secret(self, reference: str, kind: str) -> dict[str, str]:
        with self._lock:
            stored = self._secrets.get(reference)
            if stored is None or stored.get("kind") != kind:
                raise WorkflowFailure("FLOW_CREDENTIALS_NOT_CONFIGURED")
            values = stored.get("values")
            if not isinstance(values, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in values.items()
            ):
                raise WorkflowFailure("FLOW_CREDENTIALS_INVALID")
            return dict(values)

    def save_flow(self, body: dict[str, Any], flow_id: str | None = None) -> dict[str, Any]:
        allowed = {"name", "description", "graph", "expected_version"}
        if set(body) - allowed:
            raise ValidationError("Flow contains unknown fields")
        graph = validate_graph(body["graph"])
        name, description = validate_metadata(body["name"], body.get("description", ""))
        with self.store.transaction() as db:
            if flow_id is None:
                flow = {"id": str(uuid4()), "version": 1, "created_at": now()}
            else:
                flow_id = identity(flow_id)
                row = db.execute("SELECT document FROM flows WHERE id=?", (flow_id,)).fetchone()
                if row is None:
                    raise ValueError("DESKTOP_NOT_FOUND")
                flow = json.loads(row[0])
                if (
                    type(body.get("expected_version")) is not int
                    or body["expected_version"] != flow["version"]
                ):
                    raise ValueError("DESKTOP_FLOW_VERSION_CONFLICT")
                flow["version"] += 1
            flow.update(
                name=name,
                description=description,
                graph=graph,
                updated_at=now(),
                node_count=len(graph["nodes"]),
            )
            db.execute(
                "INSERT INTO flows(id,document) VALUES(?,?) "
                "ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                (flow["id"], json.dumps(flow)),
            )
        return {"flow": flow}

    def start_run(self, flow_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._launch_lock:
            return self._start_run(flow_id, body)

    def _start_run(self, flow_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) - {"request_id", "version", "inputs", "max_attempts"}:
            raise ValidationError("DESKTOP_RUN_INPUT_INVALID")
        if type(body.get("max_attempts", 1)) is not int or body.get("max_attempts", 1) != 1:
            raise ValidationError("DESKTOP_RETRY_UNSUPPORTED")
        flow_id = identity(flow_id)
        self.guard.check(flow_id)
        inputs = validate_json(body.get("inputs", {}))
        request_id = identity(body["request_id"])
        if type(body.get("version")) is not int or not isinstance(inputs, dict):
            raise ValidationError("DESKTOP_RUN_INPUT_INVALID")
        digest = hashlib.sha256(
            json.dumps(
                {"flow_id": flow_id, "version": body["version"], "inputs": inputs}, sort_keys=True
            ).encode()
        ).hexdigest()
        with self._lock, self.store.transaction() as db:
            prior = db.execute(
                "SELECT document FROM runs WHERE request_id=?", (request_id,)
            ).fetchone()
            if prior:
                run = json.loads(prior[0])
                if run["digest"] != digest:
                    raise ValueError("DESKTOP_REQUEST_CONFLICT")
                return {"job": self.job(run), "created": False}
            if self._active:
                raise ValueError("DESKTOP_RUNNER_BUSY")
            flow = self.store.get("flows", flow_id)
            if flow["version"] != body["version"]:
                raise ValueError("DESKTOP_FLOW_VERSION_CONFLICT")
            graph = validate_graph(flow["graph"])
            run = {
                "id": str(uuid4()),
                "request_id": request_id,
                "flow_id": flow_id,
                "version": flow["version"],
                "name": flow["name"],
                "graph": graph,
                "inputs": inputs,
                "digest": digest,
                "status": "RUNNING",
                "result": {},
                "error": None,
                "uncertain": False,
                "checkpoints": {},
                "created_at": now(),
                "updated_at": now(),
            }
            db.execute(
                "INSERT INTO runs(id,request_id,flow_id,document) VALUES(?,?,?,?)",
                (run["id"], request_id, flow_id, json.dumps(run)),
            )
        cancel = threading.Event()
        thread = threading.Thread(target=self._execute, args=(run, cancel), daemon=True)
        with self._lock:
            self._active[run["id"]] = (thread, cancel)
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._active.pop(run["id"], None)
            run.update(status="FAILED", error="DESKTOP_RUNNER_START_FAILED")
            self.store.save_run(run)
            raise
        return {"job": self.job(run), "created": True}

    def _execute(self, run: dict[str, Any], cancel: threading.Event) -> None:
        context = LocalContext(self, run, cancel)
        try:
            run["result"] = execute_graph(
                context,
                run["graph"],
                run["inputs"],
                browser_policy=self.browser_policy,
                automation_policy=self.automation_policy,
            )
            run["status"] = "COMPLETED"
        except FlowOutcomeUncertain:
            run.update(status="FAILED", error="WORKFLOW_OUTCOME_UNCERTAIN", uncertain=True)
        except OwnershipLostError:
            checkpoint = run["checkpoints"].get("flow_state", {})
            run.update(
                status="CANCELLED" if cancel.is_set() else "FAILED",
                error="DESKTOP_CANCELLED" if cancel.is_set() else "DESKTOP_LICENSE_EXPIRED",
                uncertain=bool(checkpoint.get("action_started")),
            )
        except WorkflowFailure as error:
            code = error.error_code
            run.update(
                status="FAILED", error=code if re.fullmatch(r"[A-Z_]+", code) else "FLOW_FAILED"
            )
        except Exception:
            run.update(status="FAILED", error="DESKTOP_RUN_FAILED", uncertain=True)
        finally:
            run["updated_at"] = now()
            self.store.save_run(run)
            with self._lock:
                self._active.pop(run["id"], None)

    @staticmethod
    def job(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": run["id"],
            "job_id": run["name"],
            "workflow_name": "visual_flow",
            "status": run["status"],
            "version": run["version"],
            "total_records": 1,
            "success_count": int(run["status"] == "COMPLETED"),
            "failed_count": int(run["status"] == "FAILED"),
            "skipped_count": int(run["status"] == "CANCELLED"),
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
        }

    @staticmethod
    def transaction(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": run["id"],
            "job_id": run["id"],
            "record_id": "flow",
            "workflow_name": "visual_flow",
            "status": {
                "RUNNING": "PROCESSING",
                "COMPLETED": "SUCCESS",
                "FAILED": "FAILED",
                "CANCELLED": "SKIPPED",
            }[run["status"]],
            "attempt_count": 1,
            "max_attempts": 1,
            "owner_id": "desktop-worker-1",
            "fencing_token": 1,
            "reconciliation_required": run["uncertain"],
            "result_data": run["result"],
            "last_error": run["error"],
            "idempotency_key": run["request_id"],
            "updated_at": run["updated_at"],
        }

    def request(
        self, method: str, raw_path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = body or {}
        parsed = urlsplit(raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if parsed.scheme or parsed.netloc or parsed.fragment or len(raw_path) > 4096:
            raise ValueError("DESKTOP_ROUTE_INVALID")
        parts = path.strip("/").split("/")
        if path == "/api/flows/import" and method == "POST":
            if set(body) != {"document"}:
                raise ValidationError("FLOW_DOCUMENT_INVALID")
            return self.save_flow(dict(import_document(body["document"])))
        if path == "/api/flows" and method == "POST":
            return self.save_flow(body)
        if (
            len(parts) == 4
            and parts[:2] == ["api", "flows"]
            and parts[3] == "export"
            and method == "GET"
        ):
            flow = self.store.get("flows", identity(parts[2]))
            return {
                "document": export_document(flow),
                "filename": f"MultiRPA-flow-{flow['id']}-v{flow['version']}.multirpa.json",
            }
        if len(parts) == 3 and parts[:2] == ["api", "flows"]:
            if method == "PUT":
                return self.save_flow(body, parts[2])
            if method == "GET":
                return {"flow": self.store.get("flows", identity(parts[2]))}
        if len(parts) == 4 and parts[:2] == ["api", "flows"] and parts[3] == "runs":
            if method == "POST":
                return self.start_run(parts[2], body)
            if method == "GET":
                return self._page(
                    [
                        self.job(run)
                        for run in self.store.all("runs")
                        if run["flow_id"] == identity(parts[2])
                    ],
                    query,
                )
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "jobs"]:
            run_id = identity(parts[2])
            self.store.get("runs", run_id)
            if parts[3] == "cancel":
                with self._lock:
                    if run_id in self._active:
                        self._active[run_id][1].set()
                return {"job": self.job(self.store.get("runs", run_id))}
        if method != "GET":
            raise ValueError("DESKTOP_OPERATION_UNSUPPORTED")
        if path == "/api/workflows":
            return {
                "node_catalog": node_catalog(),
                "workers_enabled": True,
                "schedules_enabled": False,
                "items": [
                    {
                        "name": "visual_flow",
                        "title": "Görsel akış",
                        "kind": "automation",
                        "registered": True,
                        "workers_enabled": True,
                    }
                ],
            }
        if path == "/api/flows":
            return self._page(self.store.all("flows"), query)
        runs = self.store.all("runs")
        if path == "/api/workers":
            return {"items": self._workers(runs)}
        if path == "/api/overview":
            completed = sum(run["status"] == "COMPLETED" for run in runs)
            failed = sum(run["status"] == "FAILED" for run in runs)
            cancelled = sum(run["status"] == "CANCELLED" for run in runs)
            running = sum(run["status"] == "RUNNING" for run in runs)
            return {
                "counts": {
                    "jobs": len(runs),
                    "transactions": len(runs),
                    "success": completed,
                    "failed": failed,
                    "skipped": cancelled,
                    "running_jobs": running,
                    "processing": running,
                    "pending": 0,
                    "retry": 0,
                    "reconciliation_required": sum(run["uncertain"] for run in runs),
                },
                "workers": self._workers(runs),
            }
        if path == "/api/jobs":
            return self._page([self.job(run) for run in runs], query)
        if path == "/api/transactions":
            return self._page([self.transaction(run) for run in runs], query)
        if len(parts) == 5 and parts[:4] == ["api", "flows", "runs", "by-job"]:
            run = self.store.get("runs", identity(parts[4]))
            return {key: run[key] for key in ("flow_id", "version", "name", "graph", "inputs")}
        if len(parts) in (3, 4) and parts[:2] == ["api", "jobs"]:
            run = self.store.get("runs", identity(parts[2]))
            if len(parts) == 3:
                return {"job": self.job(run)}
            if parts[3] == "transactions":
                return self._page([self.transaction(run)], query)
        if len(parts) == 3 and parts[:2] == ["api", "transactions"]:
            run = self.store.get("runs", identity(parts[2]))
            return {
                "transaction": self.transaction(run),
                "checkpoints": [
                    {
                        "checkpoint_key": key,
                        "checkpoint_value": value,
                        "updated_at": run["updated_at"],
                    }
                    for key, value in run["checkpoints"].items()
                ],
                "errors": [{"error_type": run["error"], "created_at": run["updated_at"]}]
                if run["error"]
                else [],
                "audit": [],
            }
        raise ValueError("DESKTOP_ROUTE_INVALID")

    @staticmethod
    def _workers(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "worker_id": "desktop-worker-1",
                "state": "processing"
                if any(run["status"] == "RUNNING" for run in runs)
                else "idle",
                "processed_count": sum(run["status"] == "COMPLETED" for run in runs),
                "error_count": sum(run["status"] == "FAILED" for run in runs),
                "workflow_names": ["visual_flow"],
                "last_heartbeat": now(),
            }
        ]

    @staticmethod
    def _page(items: list[dict[str, Any]], query: dict[str, list[str]]) -> dict[str, Any]:
        status, text = query.get("status", [""])[0], query.get("q", [""])[0].casefold()
        items = [
            item
            for item in items
            if (not status or item.get("status") == status)
            and (not text or text in json.dumps(item, ensure_ascii=False).casefold())
        ]
        if query.get("reconciliation_required") == ["true"]:
            items = [item for item in items if item.get("reconciliation_required")]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        limit = max(1, min(500, int(query.get("limit", ["100"])[0])))
        offset = max(0, int(query.get("offset", ["0"])[0]))
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "limit": limit,
            "offset": offset,
        }

    def close(self) -> None:
        self.guard.invalidate()
        with self._lock:
            threads = list(self._active.values())
            for _thread, event in threads:
                event.set()
        for thread, _event in threads:
            thread.join(timeout=20)
        if not any(thread.is_alive() for thread, _event in threads):
            self.store.close()
