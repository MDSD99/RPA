"""Exact public HTTPS hosts, pinned DNS, verified TLS, and bounded responses."""

import http.client
import json
import socket
import ssl
import time
from collections.abc import Callable
from typing import Any

from app.adapters.automation_policy import AutomationPolicy
from app.workflows.automation_contract import HTTP_METHODS, PUBLIC_HTTP_HEADERS
from app.workflows.browser_actions import BrowserPolicy
from app.workflows.registry import WorkflowFailure

MAX_HTTP_BYTES = 8192


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, address: str, *, port: int = 443, timeout: float, test_origin: bool = False
    ):
        context = ssl._create_unverified_context() if test_origin else ssl.create_default_context()
        self.tls_context = context
        super().__init__(host, port=port, timeout=timeout, context=context)
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def prepare_http(
    policy: AutomationPolicy, config: dict[str, Any], *, credentials: dict[str, str] | None = None
) -> dict[str, Any]:
    try:
        parsed = policy.http_policy().validate(config["url"])
        network_policy = (
            policy.http_policy()
            if policy.http_test_origin is not None
            else BrowserPolicy(hosts=(parsed.hostname,))
        )
        addresses = network_policy.destinations()
        address = addresses[(parsed.hostname, parsed.port or 443)]
    except WorkflowFailure as error:
        raise WorkflowFailure(error.error_code.replace("FLOW_BROWSER_", "FLOW_HTTP_")) from None
    method = config["method"]
    if method not in HTTP_METHODS or (method == "GET" and config["body"] is not None):
        raise WorkflowFailure("FLOW_HTTP_REQUEST_INVALID")
    headers = {"User-Agent": "MultiRPA/1.0", "Accept-Encoding": "identity"}
    for name, value in config["headers"].items():
        if (
            name.lower() not in PUBLIC_HTTP_HEADERS
            or not isinstance(value, str)
            or len(value) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise WorkflowFailure("FLOW_HTTP_HEADER_BLOCKED")
        headers[name] = value
    authorization = (credentials or {}).get("authorization")
    if authorization is not None:
        if (
            not isinstance(authorization, str)
            or not 1 <= len(authorization) <= 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in authorization)
        ):
            raise WorkflowFailure("FLOW_HTTP_CREDENTIAL_INVALID")
        headers["Authorization"] = authorization
    body = None
    if config["body"] is not None:
        body = json.dumps(config["body"], ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_HTTP_BYTES:
            raise WorkflowFailure("FLOW_HTTP_BODY_TOO_LARGE")
        headers.setdefault("Content-Type", "application/json")
    return {
        "parsed": parsed,
        "address": address,
        "headers": headers,
        "body": body,
        "method": method,
        "authorization": authorization,
        "test_origin": policy.http_test_origin is not None,
    }


def perform_http(
    prepared: dict[str, Any], *, check_active: Callable[[], Any], deadline: float
) -> dict[str, Any]:
    parsed = prepared["parsed"]
    remaining = min(5.0, deadline - time.monotonic())
    if remaining <= 0:
        raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
    connection = (
        http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=remaining)
        if parsed.scheme == "http"
        else PinnedHTTPSConnection(
            parsed.hostname,
            prepared["address"],
            port=parsed.port or 443,
            timeout=remaining,
            test_origin=prepared["test_origin"],
        )
    )
    try:
        check_active()
        connection.request(
            prepared["method"],
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
            body=prepared["body"],
            headers=prepared["headers"],
        )
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise WorkflowFailure("FLOW_HTTP_REDIRECT_BLOCKED")
        if response.status >= 400:
            raise WorkflowFailure("FLOW_HTTP_RESPONSE_FAILED")
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise WorkflowFailure("FLOW_HTTP_ENCODING_BLOCKED")
        chunks = bytearray()
        while True:
            check_active()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
            if connection.sock is not None:
                connection.sock.settimeout(min(1.0, remaining))
            # read1 performs at most one underlying read; a trickling peer
            # cannot hide for minutes inside an uninterruptible read(n).
            chunk = response.read1(min(1024, MAX_HTTP_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_HTTP_BYTES:
                raise WorkflowFailure("FLOW_HTTP_RESPONSE_TOO_LARGE")
        check_active()
        text = bytes(chunks).decode("utf-8")
        authorization = prepared.get("authorization")
        if authorization and any(
            candidate in text
            for candidate in {authorization, authorization.partition(" ")[2]}
            if len(candidate) >= 8
        ):
            raise WorkflowFailure("FLOW_HTTP_RESPONSE_SENSITIVE")
        body = json.loads(text) if "json" in response.getheader("Content-Type", "") else text
        return {"status": response.status, "body": body}
    except WorkflowFailure:
        raise
    except (OSError, UnicodeError, ValueError, http.client.HTTPException):
        raise WorkflowFailure("FLOW_HTTP_UNAVAILABLE") from None
    finally:
        connection.close()
