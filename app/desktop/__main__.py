"""Private JSON-lines runner child. No listening socket or shell commands."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.core.exceptions import ValidationError
from app.desktop.license import LeaseGuard, LicenseDenied
from app.desktop.runner import LocalRunner
from app.workflows.browser_actions import BrowserPolicy

MAX_LINE_BYTES = 262_144


def serve(path: Path, server_key: str, device_key: str, tenant_id: str) -> None:
    runner = LocalRunner(
        path, LeaseGuard(server_key, device_key, tenant_id), browser_policy=BrowserPolicy.from_env()
    )
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                # An oversized request desynchronizes the protocol; fail closed.
                break
            request_id: Any = None
            try:
                request = json.loads(line)
                request_id = request["id"]
                if type(request_id) is not int or not 0 <= request_id <= 2**53 - 1:
                    raise ValueError("DESKTOP_MESSAGE_INVALID")
                operation = request["operation"]
                if operation == "permit":
                    runner.guard.accept(
                        request["envelope"], elapsed_ms=request.get("elapsed_ms", 0)
                    )
                    result: dict[str, Any] = {"accepted": True}
                elif operation == "invalidate":
                    runner.guard.invalidate()
                    result = {"invalidated": True}
                elif operation == "secrets":
                    runner.configure_secrets(request["values"])
                    result = {"configured": True}
                elif operation == "request":
                    method, route = request["method"], request["path"]
                    if method not in {"GET", "POST", "PUT", "DELETE"} or not isinstance(route, str):
                        raise ValueError("DESKTOP_MESSAGE_INVALID")
                    body = request.get("body")
                    if body is not None and not isinstance(body, dict):
                        raise ValueError("DESKTOP_MESSAGE_INVALID")
                    result = runner.request(method, route, body)
                else:
                    raise ValueError("DESKTOP_OPERATION_UNSUPPORTED")
                response = {"id": request_id, "ok": True, "data": result}
            except (ValueError, KeyError, TypeError, ValidationError, LicenseDenied) as error:
                code = str(error)
                if not re.fullmatch(r"[A-Z_]+", code):
                    code = "DESKTOP_INPUT_INVALID"
                response = {"id": request_id, "ok": False, "error": {"code": code}}
            except Exception:
                response = {
                    "id": request_id,
                    "ok": False,
                    "error": {"code": "DESKTOP_OPERATION_FAILED"},
                }
            sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    finally:
        runner.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--server-key", required=True)
    parser.add_argument("--device-key", required=True)
    parser.add_argument("--tenant-id", required=True)
    arguments = parser.parse_args()
    serve(arguments.storage, arguments.server_key, arguments.device_key, arguments.tenant_id)


if __name__ == "__main__":
    main()
