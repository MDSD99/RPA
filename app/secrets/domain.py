"""Credential schemas shared by web and desktop, without persistence imports."""

import re
import unicodedata

from app.core.exceptions import ValidationError

KINDS = {"smtp", "http", "browser"}


def reference(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", value):
        raise ValidationError("Credential reference is invalid")
    return value


def validate_values(kind: str, values: dict[str, str]) -> dict[str, str]:
    fields = {
        "http": {"authorization"},
        "browser": {"username", "password"},
        "smtp": {"host", "port", "sender", "security", "username", "password"},
    }
    if (
        not isinstance(kind, str)
        or kind not in KINDS
        or not isinstance(values, dict)
        or set(values) != fields[kind]
    ):
        raise ValidationError("Credential fields do not match their kind")
    if any(
        not isinstance(value, str)
        or len(value) > 4096
        or any(unicodedata.category(c).startswith("C") for c in value)
        for value in values.values()
    ):
        raise ValidationError("Credential value is invalid")
    if kind == "http" and (not values["authorization"] or not values["authorization"].isascii()):
        raise ValidationError("HTTP authorization is invalid")
    if kind == "browser" and (not values["username"] or not values["password"]):
        raise ValidationError("Browser credentials are incomplete")
    if kind == "smtp":
        from app.core.portal_config import SMTPSettings

        try:
            port = int(values["port"])
            SMTPSettings(
                values["host"],
                port,
                values["sender"],
                values["security"],
                values["username"],
                values["password"],
            )
        except (ValueError, TypeError):
            raise ValidationError("SMTP credentials are invalid") from None
        if (values["security"], port) not in {("ssl", 465), ("starttls", 587)}:
            raise ValidationError("SMTP requires SSL465 or STARTTLS587")
    return dict(values)
