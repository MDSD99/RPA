"""Bounded, declarative visual flows; no evaluated Python or user-supplied code.

A graph is immutable for a run. Deterministic nodes checkpoint their variables
and next node. Browser sessions cannot survive crashes: a later attempt starts
at the beginning only after the worker's explicit outcome reconciliation. Any
browser failure after navigation begins is ambiguous and must not auto-retry.
"""

import hashlib
import json
import math
import re
import time
from copy import deepcopy

from app.core.exceptions import DatabaseOperationError, OwnershipLostError, ValidationError
from app.core.validation import validate_json
from app.workflows.automation_contract import (
    AUTOMATION_TYPES,
    EFFECT_TYPES,
    validate_automation_config,
    validate_secret_reference,
)
from app.workflows.node_contract import NODE_TYPES as NODE_TYPES
from app.workflows.node_contract import resolve_node_contract
from app.workflows.registry import WorkflowFailure

_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z")
_REFERENCE = re.compile(r"{{\s*([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*|\.[0-9]+)*)\s*}}")
_OPERATORS = {"equals", "not_equals", "contains", "greater_than"}
_CHECKPOINT = "flow_state"
MAX_RUNTIME_SECONDS = 120
MAX_VARIABLE_BYTES = 12_288


class FlowOutcomeUncertain(Exception):
    """An external operation may have happened; the worker must quarantine it."""

    def __init__(self):
        super().__init__("FLOW_EXTERNAL_OUTCOME_UNCERTAIN")


def _fail_validation(message="Flow graph is invalid"):
    raise ValidationError(message)


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value):
        _fail_validation("Flow fields are missing")
    if set(value) - set(required) - set(optional):
        _fail_validation("Flow contains unknown fields")


def _string(value, limit, *, empty=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value.strip())
        or any(ord(character) < 32 for character in value)
    ):
        _fail_validation("Flow text is invalid or too long")


def _key(value):
    if not isinstance(value, str) or not _ID.fullmatch(value) or "-" in value:
        _fail_validation("Variable keys must be simple identifiers")
    # Reject secret-looking variable names, including names used in key fields.
    validate_json({value: None})


def _template(value):
    if isinstance(value, str):
        remainder = _REFERENCE.sub("", value)
        if "{{" in remainder or "}}" in remainder:
            _fail_validation("Variable references must use {{variable}} syntax")
    elif isinstance(value, list):
        for item in value:
            _template(item)
    elif isinstance(value, dict):
        for item in value.values():
            _template(item)


def _validate_config(kind, config):
    if kind in AUTOMATION_TYPES:
        if not isinstance(config, dict):
            _fail_validation("Automation configuration must be an object")
        validate_automation_config(kind, config)
        if "key" in config:
            _key(config["key"])
        _template(config)
        return
    fields = {
        "start": (),
        "set": ("key", "value"),
        "condition": ("left", "operator", "right"),
        "delay": ("seconds",),
        "browser_open": ("url",),
        "browser_fill": ("selector", "value"),
        "browser_click": ("selector",),
        "browser_extract": ("selector", "key"),
        "end": ("output",),
    }
    _fields(config, fields[kind], ("secret_ref", "secret_field") if kind == "browser_fill" else ())
    if kind == "browser_fill" and ("secret_ref" in config or "secret_field" in config):
        if set(config) != {"selector", "value", "secret_ref", "secret_field"}:
            _fail_validation("Browser credentials require a reference and field")
        validate_secret_reference(config["secret_ref"])
        if config["secret_field"] not in {"username", "password"} or config["value"] != "":
            _fail_validation("Browser credentials use username/password and an empty value")
    if "key" in config:
        _key(config["key"])
    if kind == "condition" and (
        not isinstance(config["operator"], str) or config["operator"] not in _OPERATORS
    ):
        _fail_validation("Unsupported condition operator")
    if kind == "delay":
        seconds = config["seconds"]
        if (
            type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or not 0 <= seconds <= 10
        ):
            _fail_validation("Delay must be between zero and ten seconds")
    if "selector" in config:
        _string(config["selector"], 512)
        # Selectors are literal Playwright CSS selectors. No JavaScript engine,
        # text engine, XPath expression or template is accepted here.
        if "=" in config["selector"].split("[", 1)[0] or ">>" in config["selector"]:
            _fail_validation("Use a CSS selector")
        if "{{" in config["selector"] or "}}" in config["selector"]:
            _fail_validation("Selectors must be literal CSS")
    if kind == "browser_open":
        _string(config["url"], 2048)
        if not config["url"].startswith("https://"):
            _fail_validation("Browser URLs must begin with https://")
    if kind == "browser_fill":
        _string(config["value"], 8192, empty=True)
    if kind == "end" and not isinstance(config["output"], dict):
        _fail_validation("End output must be an object")
    _template(config)


def validate_graph(graph):
    """Return a detached graph without injecting defaults or changing its digest."""
    graph = validate_json(graph)
    _fields(graph, ("nodes", "edges"))
    nodes, edges = graph["nodes"], graph["edges"]
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 40 or not isinstance(edges, list):
        _fail_validation("A flow must have two to forty nodes")
    if len(edges) > 78:
        _fail_validation("Too many flow connections")
    by_id = {}
    contracts = {}
    for node in nodes:
        _fields(node, ("id", "type", "label", "position", "config"), ("type_version",))
        node_id, kind = node["id"], node["type"]
        if not isinstance(node_id, str) or not _ID.fullmatch(node_id) or node_id in by_id:
            _fail_validation("Flow node identifiers must be unique")
        contracts[node_id] = resolve_node_contract(kind, node.get("type_version", 1))
        _string(node["label"], 100)
        _fields(node["position"], ("x", "y"))
        for coordinate in node["position"].values():
            if type(coordinate) not in (int, float) or not math.isfinite(coordinate):
                _fail_validation("Flow node positions must be numbers")
            if not 0 <= coordinate <= 10000:
                _fail_validation("Flow node position is outside the canvas")
        _validate_config(kind, node["config"])
        by_id[node_id] = node
    starts = [node["id"] for node in nodes if node["type"] == "start"]
    if len(starts) != 1 or not any(node["type"] == "end" for node in nodes):
        _fail_validation("Flow needs exactly one start and at least one end")
    links = {node_id: {} for node_id in by_id}
    incoming = {node_id: 0 for node_id in by_id}
    for edge in edges:
        _fields(edge, ("source", "target", "port"))
        source, target, port = edge["source"], edge["target"], edge["port"]
        if not all(isinstance(value, str) for value in (source, target, port)):
            _fail_validation("Flow connections must use string identifiers")
        if source not in by_id or target not in by_id or source == target:
            _fail_validation("Flow connection target is invalid")
        expected = contracts[source].output_ports
        if port not in expected or port in links[source]:
            _fail_validation("Flow output ports must connect exactly once")
        links[source][port] = target
        incoming[target] += 1
    for node_id in by_id:
        expected = set(contracts[node_id].output_ports)
        if set(links[node_id]) != expected:
            _fail_validation("Every flow output must be connected")
    if incoming[starts[0]]:
        _fail_validation("Start cannot have an incoming connection")
    visited, visiting = set(), set()

    def visit(node_id):
        if node_id in visiting:
            _fail_validation("Flow cycles are not supported")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in links[node_id].values():
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    visit(starts[0])
    if visited != set(by_id):
        _fail_validation("Every node must be reachable from start")
    browser_states = set()

    def browser_path(node_id, opened=False):
        if (node_id, opened) in browser_states:
            return
        browser_states.add((node_id, opened))
        kind = by_id[node_id]["type"]
        if kind.startswith("browser_") and kind != "browser_open" and not opened:
            _fail_validation("Open a browser page before using browser actions")
        opened = opened or kind == "browser_open"
        for target in links[node_id].values():
            browser_path(target, opened)

    browser_path(starts[0])
    # A finite DAG with all non-end ports connected necessarily ends.
    return graph


def _resolve(value, variables):
    def lookup(match):
        result = variables
        for part in match.group(1).split("."):
            if isinstance(result, dict) and part in result:
                result = result[part]
            elif isinstance(result, list) and part.isdigit() and int(part) < len(result):
                result = result[int(part)]
            else:
                raise WorkflowFailure("FLOW_VARIABLE_MISSING")
        return deepcopy(result)

    if isinstance(value, str):
        full = _REFERENCE.fullmatch(value)
        if full:
            return lookup(full)

        def interpolate(match):
            resolved = lookup(match)
            if isinstance(resolved, (dict, list)):
                raise WorkflowFailure("FLOW_VARIABLE_TYPE_INVALID")
            if isinstance(resolved, str):
                return resolved
            return json.dumps(resolved, ensure_ascii=False)

        return _REFERENCE.sub(interpolate, value)
    if isinstance(value, list):
        return [_resolve(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, variables) for key, item in value.items()}
    return value


def _condition(config, variables):
    left, right = _resolve(config["left"], variables), _resolve(config["right"], variables)
    operator = config["operator"]
    if operator in ("equals", "not_equals"):
        equal = json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)
        return equal if operator == "equals" else not equal
    if operator == "contains":
        if isinstance(left, str) and isinstance(right, str):
            return right in left
        if isinstance(left, list):
            return any(
                json.dumps(item, sort_keys=True) == json.dumps(right, sort_keys=True)
                for item in left
            )
        raise WorkflowFailure("FLOW_COMPARISON_TYPE_INVALID")
    if type(left) in (int, float) and type(right) in (int, float):
        return left > right
    raise WorkflowFailure("FLOW_COMPARISON_TYPE_INVALID")


def _variables(value):
    value = validate_json(value)
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_VARIABLE_BYTES:
        raise WorkflowFailure("FLOW_VARIABLES_TOO_LARGE")
    return value


def execute_graph(context, graph, inputs, *, browser_policy=None, automation_policy=None):
    """Execute an immutable graph using only the fenced WorkflowContext API.

    The optional browser_policy is a Python-only injection for isolated browser
    tests. The web/API layer never accepts an override from workflow JSON.
    """
    try:
        graph = validate_graph(graph)
        variables = _variables(inputs)
    except ValidationError:
        raise WorkflowFailure("FLOW_DEFINITION_INVALID") from None
    deadline = time.monotonic() + MAX_RUNTIME_SECONDS
    digest = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    by_id = {node["id"]: node for node in graph["nodes"]}
    links = {(edge["source"], edge["port"]): edge["target"] for edge in graph["edges"]}
    current = next(node["id"] for node in graph["nodes"] if node["type"] == "start")
    uses_browser = any(node["type"].startswith("browser_") for node in graph["nodes"])
    automation_kinds = {node["type"] for node in graph["nodes"] if node["type"] in AUTOMATION_TYPES}
    credential_nodes = [node for node in graph["nodes"] if "secret_ref" in node["config"]]
    automation = None
    if automation_kinds or credential_nodes:
        from app.adapters.automation_actions import AutomationActions

        automation = AutomationActions(automation_policy)
        # Reject unsupported runner capabilities before any earlier step can
        # mutate a target (especially Windows graphs submitted to a web runner).
        context.check_active()
        automation.preflight(
            [
                node
                for node in graph["nodes"]
                if node["type"] in automation_kinds or "secret_ref" in node["config"]
            ]
        )
        context.check_active()
        if time.monotonic() >= deadline:
            raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
        automation.assert_safe(variables)
    attempt = context.claim.attempt_count
    executed = []
    saved = context.get_checkpoint(_CHECKPOINT)
    if saved is not None:
        if saved.get("graph_digest") != digest:
            raise WorkflowFailure("FLOW_CHECKPOINT_MISMATCH")
        if uses_browser and saved.get("attempt", 0) >= attempt:
            raise FlowOutcomeUncertain()
        if saved.get("action_started") and saved.get("attempt", 0) >= attempt:
            raise FlowOutcomeUncertain()
        if not uses_browser:
            if saved.get("finished"):
                if automation is not None:
                    automation.assert_safe(saved["result"])
                return validate_json(saved["result"])
            current, variables = saved["next"], _variables(saved["variables"])
            if automation is not None:
                automation.assert_safe(variables)
            executed = saved.get("executed", [])
            if current not in by_id:
                raise WorkflowFailure("FLOW_CHECKPOINT_INVALID")
    browser = None
    external_started = False

    def active():
        context.check_active()
        if time.monotonic() >= deadline:
            raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")

    def persist(next_id, *, result=None, action_started=False, last_port=None):
        if automation is not None:
            automation.assert_safe(variables if result is None else result)
        state = {
            "graph_digest": digest,
            "attempt": attempt,
            "next": next_id,
            "variables": variables if result is None else {},
            "browser": uses_browser,
            "finished": result is not None,
            "action_started": action_started,
            "current": current,
            "executed": executed,
            "last_port": last_port,
        }
        if result is not None:
            state["result"] = result
        context.checkpoint(_CHECKPOINT, state)

    try:
        for _ in range(len(by_id)):
            active()
            node = by_id[current]
            kind, config = node["type"], node["config"]
            port = "next"
            if kind == "set":
                variables[config["key"]] = _resolve(config["value"], variables)
                variables = _variables(variables)
            elif kind == "condition":
                port = "true" if _condition(config, variables) else "false"
            elif kind == "delay":
                until = time.monotonic() + config["seconds"]
                while time.monotonic() < until:
                    active()
                    time.sleep(min(0.1, max(0, until - time.monotonic())))
            elif kind.startswith("browser_"):
                if browser is None:
                    from app.workflows.browser_actions import BrowserSession

                    browser = BrowserSession(browser_policy)
                if kind != "browser_open" and not browser.opened:
                    raise WorkflowFailure("FLOW_BROWSER_PAGE_REQUIRED")
                resolved = _resolve(config, variables)
                if kind == "browser_fill" and "secret_ref" in config:
                    resolved["value"] = automation.credential(
                        config["secret_ref"], "browser", config["secret_field"]
                    )
                    resolved.pop("secret_ref")
                    resolved.pop("secret_field")
                if kind == "browser_open":
                    browser.validate_url(resolved["url"])
                if kind == "browser_fill" and not isinstance(resolved["value"], str):
                    raise WorkflowFailure("FLOW_VARIABLE_TYPE_INVALID")
                browser.prepare()
                # Acknowledged intent is fenced before contacting the target.
                persist(current, action_started=True)
                active()
                external_started = True
                result = browser.perform(kind, resolved, check_active=active, deadline=deadline)
                if kind == "browser_extract":
                    variables[config["key"]] = result
                    variables = _variables(variables)
            elif kind in AUTOMATION_TYPES:
                resolved = _resolve(config, variables)
                automation.assert_safe(
                    {key: value for key, value in resolved.items() if key != "secret_ref"}
                )
                prepared = automation.prepare(kind, resolved, deadline=deadline)
                if kind in EFFECT_TYPES:
                    persist(current, action_started=True)
                    active()
                    external_started = True
                result = automation.perform(prepared, check_active=active, deadline=deadline)
                if "key" in config:
                    automation.assert_safe(result)
                    variables[config["key"]] = result
                    variables = _variables(variables)
            elif kind == "end":
                result = _variables(_resolve(config["output"], variables))
                executed.append(current)
                persist(None, result=result)
                return result
            executed.append(current)
            next_id = links[(current, port)]
            persist(next_id, last_port=port)
            current = next_id
        raise WorkflowFailure("FLOW_RUNTIME_EXCEEDED")
    except (DatabaseOperationError, OwnershipLostError):
        raise
    except Exception as error:
        if external_started:
            raise FlowOutcomeUncertain() from None
        if isinstance(error, WorkflowFailure):
            raise
        raise WorkflowFailure("FLOW_EXECUTION_INVALID") from None
    finally:
        if browser is not None:
            browser.close()
