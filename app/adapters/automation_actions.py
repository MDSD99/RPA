"""Concrete bounded runner adapters behind a declarative node contract."""

import base64
import importlib.util
import re
import sys
import time
from collections.abc import Callable
from typing import Any

from app.adapters.automation_files import (
    MAX_RESULT_BYTES,
    RootedFiles,
    bounded_result,
    decode_table,
    encode_table,
    relative_parts,
)
from app.adapters.automation_http import perform_http, prepare_http
from app.adapters.automation_policy import AutomationPolicy
from app.workflows.registry import WorkflowFailure


class AutomationActions:
    def __init__(self, policy: AutomationPolicy | None) -> None:
        self.policy = policy or AutomationPolicy()
        if not isinstance(self.policy, AutomationPolicy):
            raise WorkflowFailure("FLOW_AUTOMATION_POLICY_INVALID")
        self._credentials: dict[tuple[str, str], dict[str, str]] = {}
        self._protected_values: set[str] = set()

    def preflight(self, nodes: list[dict[str, Any]]) -> None:
        kinds = {node["type"] for node in nodes}
        for node in nodes:
            reference = node["config"].get("secret_ref")
            if reference is not None:
                if self.policy.secret_resolver is None:
                    raise WorkflowFailure("FLOW_CREDENTIALS_NOT_CONFIGURED")
                kind = (
                    "smtp"
                    if node["type"] == "email_send"
                    else "browser"
                    if node["type"].startswith("browser_")
                    else "http"
                )
                try:
                    self._credentials[(reference, kind)] = self.policy.secret_resolver(
                        reference, kind
                    )
                except WorkflowFailure:
                    raise
                except Exception:
                    raise WorkflowFailure("FLOW_CREDENTIAL_UNAVAILABLE") from None
                credentials = self._credentials[(reference, kind)]
                password = credentials.get("password")
                authorization = credentials.get("authorization")
                candidates = [password]
                if authorization:
                    candidates += [authorization, authorization.partition(" ")[2]]
                    if authorization.lower().startswith("basic "):
                        try:
                            decoded = base64.b64decode(
                                authorization.split(" ", 1)[1], validate=True
                            ).decode("utf-8")
                            candidates.append(decoded.partition(":")[2])
                        except (ValueError, UnicodeError):
                            pass
                self._protected_values.update(
                    value for value in candidates if isinstance(value, str) and len(value) >= 3
                )
        if any(kind.startswith(("file_", "csv_", "excel_")) for kind in kinds):
            if self.policy.root is None:
                raise WorkflowFailure("FLOW_FILES_NOT_CONFIGURED")
        if any(kind.startswith("excel_") for kind in kinds):
            if any(
                importlib.util.find_spec(module) is None for module in ("openpyxl", "defusedxml")
            ):
                raise WorkflowFailure("FLOW_EXCEL_NOT_INSTALLED")
        if any(kind.startswith("windows_") for kind in kinds):
            if not self.policy.windows_enabled or sys.platform != "win32":
                raise WorkflowFailure("FLOW_WINDOWS_DESKTOP_REQUIRED")
            if importlib.util.find_spec("pywinauto") is None:
                raise WorkflowFailure("FLOW_WINDOWS_NOT_INSTALLED")

    def assert_safe(self, value: Any) -> None:
        if isinstance(value, str):
            if any(secret in value for secret in self._protected_values):
                raise WorkflowFailure("FLOW_RESULT_CONTAINS_CREDENTIAL")
        elif isinstance(value, dict):
            for key, item in value.items():
                self.assert_safe(key)
                self.assert_safe(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_safe(item)

    def credential(self, reference: str, kind: str, field: str) -> str:
        value = self._credentials[(reference, kind)].get(field)
        if not isinstance(value, str) or not 1 <= len(value) <= 8192:
            raise WorkflowFailure("FLOW_CREDENTIAL_VALUE_INVALID")
        return value

    def prepare(self, kind: str, config: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": kind, "config": config}
        if "path" in config:
            relative_parts(config["path"])
        if kind.startswith("excel_"):
            sheet = config["sheet"]
            if (
                not isinstance(sheet, str)
                or not 1 <= len(sheet) <= 31
                or re.search(r"[\\/*?:\[\]]", sheet)
                or not config["path"].lower().endswith(".xlsx")
            ):
                raise WorkflowFailure("FLOW_EXCEL_CONFIGURATION_INVALID")
        if kind == "file_write":
            if not isinstance(config["value"], str):
                raise WorkflowFailure("FLOW_FILE_VALUE_INVALID")
            result["data"] = config["value"].encode("utf-8")
            if len(result["data"]) > MAX_RESULT_BYTES:
                raise WorkflowFailure("FLOW_FILE_TOO_LARGE")
        elif kind in {"csv_write", "excel_write"}:
            result["data"] = encode_table(kind, config["rows"], config.get("sheet"))
        elif kind == "http_request":
            reference = config.get("secret_ref")
            credentials = (
                self._credentials.get((reference, "http"), {}) if isinstance(reference, str) else {}
            )
            result["http"] = prepare_http(self.policy, config, credentials=credentials)
        elif kind == "email_send":
            from app.adapters.mail_automation import prepare_mail

            result["mail"] = prepare_mail(
                config, self._credentials[(config["secret_ref"], "smtp")], self.policy
            )
        elif kind.startswith("windows_"):
            Desktop = importlib.import_module("pywinauto").Desktop

            timeout = min(3.0, deadline - time.monotonic())
            if timeout <= 0:
                raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
            try:
                control = (
                    Desktop(backend="uia")
                    .window(title=config["window_title"])
                    .child_window(auto_id=config["auto_id"], control_type=config["control_type"])
                )
                control.wait("exists enabled visible", timeout=timeout, retry_interval=0.1)
                result["control"] = control.wrapper_object()
            except Exception:
                raise WorkflowFailure("FLOW_WINDOWS_TARGET_UNAVAILABLE") from None
            if kind == "windows_fill" and (
                not isinstance(config["value"], str) or len(config["value"]) > 8192
            ):
                raise WorkflowFailure("FLOW_WINDOWS_VALUE_INVALID")
        return result

    def perform(
        self, prepared: dict[str, Any], *, check_active: Callable[[], Any], deadline: float
    ) -> Any:
        kind, config = prepared["kind"], prepared["config"]
        check_active()
        if time.monotonic() >= deadline:
            raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
        if kind.startswith(("file_", "csv_", "excel_")):
            if self.policy.root is None:
                raise WorkflowFailure("FLOW_FILES_NOT_CONFIGURED")
            files = RootedFiles(self.policy.root)
            if kind.endswith("_write"):
                files.write(config["path"], prepared["data"], check_active)
                return None
            data = files.read(config["path"], check_active)
            if kind == "file_read":
                try:
                    return bounded_result(data.decode("utf-8"))
                except UnicodeError:
                    raise WorkflowFailure("FLOW_FILE_ENCODING_INVALID") from None
            return decode_table(kind, data, sheet=config.get("sheet"), check_active=check_active)
        if kind == "http_request":
            return perform_http(prepared["http"], check_active=check_active, deadline=deadline)
        if kind == "email_send":
            from app.adapters.mail_automation import perform_mail

            return perform_mail(prepared["mail"], check_active=check_active, deadline=deadline)
        if kind.startswith("windows_"):
            control = prepared["control"]
            if kind == "windows_click":
                control.click_input()
            elif kind == "windows_fill":
                control.set_edit_text(config["value"])
            else:
                return bounded_result(control.window_text())
            check_active()
            return None
        raise WorkflowFailure("FLOW_AUTOMATION_ACTION_INVALID")
