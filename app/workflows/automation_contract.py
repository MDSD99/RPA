"""Stable declarative file, table, HTTP, and Windows node configuration."""

import re
from typing import Any

from app.core.exceptions import ValidationError

AUTOMATION_FIELDS = {
    "file_read": ("path", "key"),
    "file_write": ("path", "value"),
    "csv_read": ("path", "key"),
    "csv_write": ("path", "rows"),
    "excel_read": ("path", "sheet", "key"),
    "excel_write": ("path", "sheet", "rows"),
    "http_request": ("url", "method", "headers", "body", "key"),
    "email_send": ("secret_ref", "to", "subject", "body"),
    "windows_click": ("window_title", "auto_id", "control_type"),
    "windows_fill": ("window_title", "auto_id", "control_type", "value"),
    "windows_extract": ("window_title", "auto_id", "control_type", "key"),
}
AUTOMATION_TYPES = tuple(AUTOMATION_FIELDS)
EFFECT_TYPES = frozenset(
    {
        "file_write",
        "csv_write",
        "excel_write",
        "http_request",
        "email_send",
        "windows_click",
        "windows_fill",
    }
)
PUBLIC_HTTP_HEADERS = frozenset({"accept", "content-type", "idempotency-key"})
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def validate_secret_reference(reference: Any) -> None:
    if not isinstance(reference, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", reference):
        raise ValidationError("Credential reference must be a literal identifier")


def validate_automation_config(kind: str, config: dict[str, Any]) -> None:
    required = set(AUTOMATION_FIELDS[kind])
    optional = {"secret_ref"} if kind == "http_request" else set()
    if not required <= set(config) or set(config) - required - optional:
        raise ValidationError("Automation node fields are missing or unknown")
    for name in ("path", "url", "sheet", "window_title", "auto_id", "control_type"):
        if name in config:
            value = config[name]
            limit = 2048 if name == "url" else 240
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > limit
                or any(ord(char) < 32 for char in value)
            ):
                raise ValidationError("Automation text field is invalid")
    if "rows" in config and not isinstance(config["rows"], (str, list)):
        raise ValidationError("Table rows must be an array or variable reference")
    if kind == "file_write" and not isinstance(config["value"], str):
        raise ValidationError("File value must be text or a variable reference")
    if kind.startswith("windows_"):
        for name in ("window_title", "auto_id", "control_type"):
            if "{{" in config[name] or "}}" in config[name]:
                raise ValidationError("Windows selectors must be literal")
        if config["control_type"] not in {"Button", "Edit", "Text", "Document", "ComboBox"}:
            raise ValidationError("Unsupported Windows control type")
        if kind == "windows_fill" and not isinstance(config["value"], str):
            raise ValidationError("Windows value must be text")
    if kind == "http_request":
        if not isinstance(config["method"], str) or config["method"] not in HTTP_METHODS:
            raise ValidationError("Unsupported HTTP method")
        if not config["url"].startswith("https://"):
            raise ValidationError("HTTP URLs must begin with https://")
        headers = config["headers"]
        if not isinstance(headers, dict) or len(headers) > 3:
            raise ValidationError("HTTP headers must be a public header object")
        for key, value in headers.items():
            if (
                key.lower() not in PUBLIC_HTTP_HEADERS
                or not isinstance(value, str)
                or len(value) > 512
                or re.search(r"[\x00-\x1f\x7f]", value)
            ):
                raise ValidationError("HTTP credentials and control headers are not permitted")
        if config["method"] == "GET" and config["body"] is not None:
            raise ValidationError("GET requests must not have a body")
    if "secret_ref" in config:
        validate_secret_reference(config["secret_ref"])
    if kind == "email_send":
        for field in ("to", "subject", "body"):
            if not isinstance(config[field], str) or len(config[field]) > 8192:
                raise ValidationError("Email fields must be bounded text")
