"""Immutable workflow metadata and an explicit synchronous handler contract.

Handlers receive a WorkflowContext and return a caller-sanitized JSON object.
Returning None explicitly means success with an empty result. Handlers must use
the claim's idempotency key at their target where supported, call check_active()
before external changes, and reconcile ambiguous external outcomes.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.core.exceptions import ValidationError

if TYPE_CHECKING:
    from app.workers.runtime import WorkflowContext


def _metadata_text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{label} must be nonempty and at most {limit} characters")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValidationError(f"{label} must not contain control characters")


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    title: str
    description: str
    execute: Callable[["WorkflowContext"], dict | None]

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{0,127}", self.name
        ):
            raise ValidationError("Workflow name must be a stable lowercase identifier")
        if self.name == "infrastructure_demo":
            raise ValidationError("The infrastructure demo workflow name is reserved")
        _metadata_text(self.title, "Workflow title", 160)
        _metadata_text(self.description, "Workflow description", 2000)
        if not callable(self.execute):
            raise ValidationError("Workflow execute must be callable")


class WorkflowRegistry:
    """A fixed allowlist; registering code never imports modules from job input."""

    def __init__(self, definitions: Iterable[WorkflowDefinition] = ()):
        entries = {}
        for definition in definitions:
            if not isinstance(definition, WorkflowDefinition):
                raise ValidationError("Registry entries must be workflow definitions")
            if definition.name in entries:
                raise ValidationError("Workflow names must be unique")
            entries[definition.name] = definition
        self._entries = MappingProxyType(entries)

    def get(self, name: str) -> WorkflowDefinition | None:
        return self._entries.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def definitions(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._entries.values())


class SafeRetry(Exception):
    """The handler has verified that this failed attempt applied no side effect.

    Use only a static uppercase error code. The execution service validates it;
    invalid codes are quarantined and never stored as arbitrary exception text.
    """

    def __init__(self, error_code: str):
        super().__init__("Workflow requested a verified safe retry")
        self.error_code = error_code


class WorkflowFailure(Exception):
    """A known terminal failure with a static, non-sensitive uppercase code."""

    def __init__(self, error_code: str):
        super().__init__("Workflow reported a known terminal failure")
        self.error_code = error_code
