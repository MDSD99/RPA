"""Bounded, versioned flow archives shared by server and local runners.

Archives contain editable metadata and the graph only. Credential aliases stay
in the graph; vault values, IDs and execution history are never projected into
the document. Literal customer data in a graph remains part of that graph.
"""

import json
from collections.abc import Iterator, Mapping
from typing import TypedDict

from app.core.exceptions import ValidationError
from app.core.validation import validate_text
from app.workflows.flow_engine import validate_graph

FORMAT = "multirpa-flow"
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 128 * 1024
MAX_DOCUMENT_DEPTH = 36
_FORBIDDEN_KEYS = frozenset({"__proto__", "constructor", "prototype"})


class FlowCreation(TypedDict):
    name: str
    description: str
    graph: dict[str, object]


def validate_metadata(name: object, description: object) -> tuple[str, str]:
    """Apply the same metadata rules to edits, creation and transferred flows."""
    if not isinstance(name, str):
        raise ValidationError("Flow name must be text")
    name = validate_text(name, "name", 160).strip()
    if not isinstance(description, str):
        raise ValidationError("Flow description must be text")
    if description:
        description = validate_text(description, "description", 1000).strip()
    return name, description


def _object_values(value: dict[object, object]) -> Iterator[object]:
    for key, child in value.items():
        if not isinstance(key, str) or key in _FORBIDDEN_KEYS:
            raise ValidationError("Flow archive contains an unsupported object key")
        yield key
        yield child


def _validate_document_bounds(document: object) -> None:
    """Count UTF-8 JSON bytes iteratively before recursive graph validation.

    Iterator frames bound memory independently of container breadth. Only the
    current ancestors are retained for cycle detection; repeated references to
    the same acyclic value still count toward the complete serialized size.
    """
    size = 0
    ancestors: set[int] = set()
    stack: list[tuple[Iterator[object], int, int | None]] = [(iter((document,)), 0, None)]
    while stack:
        values, depth, owner = stack[-1]
        try:
            value = next(values)
        except StopIteration:
            stack.pop()
            if owner is not None:
                ancestors.remove(owner)
            continue
        if depth > MAX_DOCUMENT_DEPTH:
            raise ValidationError("Flow archive nesting exceeds the supported limit")
        if isinstance(value, dict | list):
            if id(value) in ancestors:
                raise ValidationError("Flow archive must contain acyclic JSON")
            # Match json.dumps' default comma and colon spacing.
            size += 2 + max(0, len(value) - 1) * 2
            if isinstance(value, dict):
                size += 2 * len(value)
                children = _object_values(value)
            else:
                children = iter(value)
            ancestors.add(id(value))
            stack.append((children, depth + 1, id(value)))
        elif value is None or isinstance(value, str | bool | int | float):
            # Avoid allocating an encoded copy of an already oversized string.
            if isinstance(value, str) and len(value) > MAX_DOCUMENT_BYTES:
                raise ValidationError("Flow archive must not exceed 128 KiB")
            try:
                size += len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise ValidationError("Flow archive must contain valid JSON values") from None
        else:
            raise ValidationError("Flow archive must contain valid JSON values")
        if size > MAX_DOCUMENT_BYTES:
            raise ValidationError("Flow archive must not exceed 128 KiB")


def import_document(document: object) -> FlowCreation:
    """Validate raw JSON or a parsed archive and return detached creation data.

    File clients forward the original text so JavaScript number normalization
    cannot turn a forbidden floating-point version into a supported integer.
    Existing object callers use the same validation after this decoding boundary.
    """
    if isinstance(document, str):
        if len(document) > MAX_DOCUMENT_BYTES:
            raise ValidationError("Flow archive must not exceed 128 KiB")
        try:
            size = len(document.encode("utf-8"))
        except UnicodeError:
            raise ValidationError("Flow archive must contain valid JSON values") from None
        if size > MAX_DOCUMENT_BYTES:
            raise ValidationError("Flow archive must not exceed 128 KiB")
        try:
            document = json.loads(document.removeprefix("\ufeff"))
        except (ValueError, RecursionError):
            raise ValidationError("Flow archive fields or JSON text are invalid") from None
    _validate_document_bounds(document)
    if not isinstance(document, dict) or set(document) != {"format", "schema_version", "flow"}:
        raise ValidationError("Flow archive fields are invalid")
    if document["format"] != FORMAT:
        raise ValidationError("Flow archive format is unsupported")
    if type(document["schema_version"]) is not int or document["schema_version"] not in {
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
    }:
        raise ValidationError("Flow archive schema version is unsupported")
    flow = document["flow"]
    if not isinstance(flow, dict) or set(flow) != {"name", "description", "graph"}:
        raise ValidationError("Flow archive definition fields are invalid")
    name, description = validate_metadata(flow["name"], flow["description"])
    graph = validate_graph(flow["graph"])
    if document["schema_version"] == LEGACY_SCHEMA_VERSION and _has_node_versions(graph):
        raise ValidationError("Flow archive schema version 1 does not support node type versions")
    return {"name": name, "description": description, "graph": graph}


def _has_node_versions(graph: object) -> bool:
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return False
    return any(isinstance(node, dict) and "type_version" in node for node in graph["nodes"])


def export_document(flow: Mapping[str, object]) -> dict[str, object]:
    """Project only the editable definition, then enforce the import contract."""
    document = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "flow": {key: flow.get(key) for key in ("name", "description", "graph")},
    }
    definition = import_document(document)
    # Legacy-only graphs retain v1 interoperability; explicit versions require
    # a v2 reader. Neither exporting nor importing upgrades individual nodes.
    version = SCHEMA_VERSION if _has_node_versions(definition["graph"]) else LEGACY_SCHEMA_VERSION
    return {"format": FORMAT, "schema_version": version, "flow": definition}
