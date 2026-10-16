"""The workflow catalog is a code allowlist with immutable, bounded metadata."""

from dataclasses import FrozenInstanceError

import pytest

from app.core.exceptions import ValidationError
from app.workflows import SafeRetry, WorkflowDefinition, WorkflowFailure, WorkflowRegistry
from app.workflows.registered import build_registry


def _definition(**overrides):
    fields = {
        "name": "invoice_import.v1",
        "title": "Fatura aktarımı",
        "description": "Onaylanmış kayıtları hedef uygulamada işler.",
        "execute": lambda context: {},
    }
    return WorkflowDefinition(**(fields | overrides))


def test_registry_preserves_order_and_freezes_definitions():
    first = _definition()
    second = _definition(name="inventory-sync")
    source = [first, second]
    registry = WorkflowRegistry(source)
    source.clear()
    assert registry.names() == (first.name, second.name)
    assert registry.definitions() == (first, second)
    assert registry.get(first.name) is first
    assert registry.get("unregistered") is None
    with pytest.raises(FrozenInstanceError):
        first.name = "changed"
    with pytest.raises(TypeError):
        registry._entries["injected"] = second


@pytest.mark.parametrize(
    "name",
    ["", "infrastructure_demo", "CaseSensitive", " spaced", "module:call", "a" * 129, "a\n"],
)
def test_unstable_or_reserved_workflow_names_are_rejected(name):
    with pytest.raises(ValidationError):
        _definition(name=name)


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " "},
        {"title": "x" * 161},
        {"title": "hidden\x7f"},
        {"description": ""},
        {"description": "x" * 2001},
        {"description": "line\nbreak"},
        {"description": "invalid\ud800"},
        {"execute": None},
    ],
)
def test_invalid_metadata_and_non_callable_handler_are_rejected(overrides):
    with pytest.raises(ValidationError):
        _definition(**overrides)


def test_duplicate_names_and_untyped_entries_are_rejected():
    with pytest.raises(ValidationError):
        WorkflowRegistry([_definition(), _definition()])
    with pytest.raises(ValidationError):
        WorkflowRegistry([{"name": "untrusted"}])


def test_default_registry_provides_only_the_reusable_visual_flow_runtime():
    assert build_registry().names() == ("visual_flow",)


@pytest.mark.parametrize("exception_type", [SafeRetry, WorkflowFailure])
def test_handler_error_text_never_exposes_unvalidated_error_code(exception_type):
    error = exception_type("credential-like text must stay out of exception messages")
    assert "credential-like" not in str(error)
