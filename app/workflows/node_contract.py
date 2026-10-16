"""Immutable node behavior contracts, independent of runner availability.

An omitted node type_version always means behavior version 1. Resolving that
default never rewrites a graph: stored definitions and checkpoint digests stay
identical. A required capability describes the adapter family, not whether a
particular runner is configured, authorized or ready to use it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypedDict

from app.core.exceptions import ValidationError

Capability = Literal["browser", "files", "excel", "http", "smtp", "windows_uia"]


@dataclass(frozen=True, slots=True)
class NodeContract:
    type: str
    type_version: int
    output_ports: tuple[str, ...]
    required_capability: Capability | None = None


_CONTRACTS = (
    NodeContract("start", 1, ("next",)),
    NodeContract("set", 1, ("next",)),
    NodeContract("condition", 1, ("true", "false")),
    NodeContract("delay", 1, ("next",)),
    NodeContract("browser_open", 1, ("next",), "browser"),
    NodeContract("browser_fill", 1, ("next",), "browser"),
    NodeContract("browser_click", 1, ("next",), "browser"),
    NodeContract("browser_extract", 1, ("next",), "browser"),
    NodeContract("end", 1, ()),
    NodeContract("file_read", 1, ("next",), "files"),
    NodeContract("file_write", 1, ("next",), "files"),
    NodeContract("csv_read", 1, ("next",), "files"),
    NodeContract("csv_write", 1, ("next",), "files"),
    NodeContract("excel_read", 1, ("next",), "excel"),
    NodeContract("excel_write", 1, ("next",), "excel"),
    NodeContract("http_request", 1, ("next",), "http"),
    NodeContract("email_send", 1, ("next",), "smtp"),
    NodeContract("windows_click", 1, ("next",), "windows_uia"),
    NodeContract("windows_fill", 1, ("next",), "windows_uia"),
    NodeContract("windows_extract", 1, ("next",), "windows_uia"),
)
NODE_CONTRACTS: Mapping[tuple[str, int], NodeContract] = MappingProxyType(
    {(contract.type, contract.type_version): contract for contract in _CONTRACTS}
)
NODE_TYPES = tuple(dict.fromkeys(contract.type for contract in _CONTRACTS))


def resolve_node_contract(kind: object, type_version: object = 1) -> NodeContract:
    """Resolve only explicitly supported versions; bool/float are not integers."""
    if not isinstance(kind, str) or kind not in NODE_TYPES:
        raise ValidationError("Unsupported flow node type")
    if type(type_version) is not int or (kind, type_version) not in NODE_CONTRACTS:
        raise ValidationError("Unsupported flow node type version")
    return NODE_CONTRACTS[(kind, type_version)]


class NodeCatalogEntry(TypedDict):
    type: str
    type_version: int
    output_ports: list[str]
    required_capability: Capability | None


def node_catalog() -> list[NodeCatalogEntry]:
    """Expose detached contract metadata, with no runner-readiness assertion."""
    return [
        {
            "type": contract.type,
            "type_version": contract.type_version,
            "output_ports": list(contract.output_ports),
            "required_capability": contract.required_capability,
        }
        for contract in _CONTRACTS
    ]
