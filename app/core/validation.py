"""Shared boundary validation independent of services and persistence."""

import json
import math
import re
from decimal import Decimal
from uuid import UUID

from app.core.exceptions import ValidationError

_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "cookie",
        "apikey",
        "privatekey",
        "connectionstring",
        "databaseurl",
        "dburl",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "dbpassword",
    }
)


def validate_text(value: str, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{label} must be a nonempty string of at most {limit} characters")
    if any(ord(character) < 32 for character in value):
        raise ValidationError(f"{label} must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValidationError(f"{label} must be valid Unicode") from None
    return value


def validate_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValidationError("An entity ID must be a UUID")
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("An entity ID must be a UUID") from None


def validate_json(value: object) -> dict[str, object]:
    """Validate bounded, caller-sanitized JSON and detach it from caller objects.

    This is defense in depth, not a PII/secret detector. Callers must sanitize
    values before submitting them, including secrets embedded in strings.
    """

    def inspect(item: object, depth: int = 0) -> int:
        if depth > 32:
            raise ValidationError("JSON nesting must not exceed 32 levels")
        if isinstance(item, dict):
            size = 2 + max(0, len(item) - 1) * 2
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValidationError("JSON object keys must be strings")
                normalized = re.sub(r"[^a-z]", "", key.lower())
                if normalized in _SECRET_KEYS or normalized.endswith(
                    ("password", "secret", "token")
                ):
                    raise ValidationError("JSON contains a credential-related key")
                size += inspect(key, depth + 1) + 2 + inspect(child, depth + 1)
            return size
        elif isinstance(item, list):
            return 2 + max(0, len(item) - 1) * 2 + sum(inspect(child, depth + 1) for child in item)
        elif isinstance(item, str):
            if "\x00" in item:
                raise ValidationError("JSON strings cannot contain null characters")
            return len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        elif item is None:
            return 4
        elif isinstance(item, bool):
            return 4 if item else 5
        elif isinstance(item, int):
            return len(str(item))
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValidationError("JSON numbers must be finite")
            # PostgreSQL JSONB expands exponents into numeric decimal text.
            return len(format(Decimal(str(item)), "f"))
        raise ValidationError("Values must be JSON serializable")

    if not isinstance(value, dict):
        raise ValidationError("JSON payload must be an object")
    try:
        if inspect(value) > 16_384:
            raise ValidationError("JSON payload must not exceed 16 KiB")
        # jsonb::text uses spaces after commas and colons, as these defaults do.
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > 16_384:
            raise ValidationError("JSON payload must not exceed 16 KiB")
        return json.loads(serialized)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ValidationError("Invalid, sensitive, or oversized JSON payload") from None


def validate_number(value: int | float, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a finite number")
    if value < minimum or value > maximum or not math.isfinite(value):
        raise ValidationError(f"{label} is outside the supported range")
    return float(value)
