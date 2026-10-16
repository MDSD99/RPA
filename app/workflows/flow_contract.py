"""Shared flow identity and revision rules without persistence dependencies."""

import hashlib
import json

from app.core.exceptions import ValidationError

VISUAL_WORKFLOW = "visual_flow"


def validate_flow_version(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise ValidationError("Flow version must be a positive integer")
    return value


def configuration_digest(values: dict) -> str:
    """Preserve JSON type identity for flow and schedule request replays."""
    # Python considers True == 1, including inside nested dictionaries. The
    # graph interpreter distinguishes their JSON types, so replay checks must.
    # Retain request identity independently of JSONB normalizing exponent-form
    # numbers during its text round trip, e.g. 1e20 becomes an integer literal.
    canonical = json.dumps(values, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
