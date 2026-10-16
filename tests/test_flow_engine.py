"""Visual DSL validation, branch execution, fencing and safe resume contracts."""

import copy
from types import SimpleNamespace

import pytest

from app.core.exceptions import OwnershipLostError, ValidationError
from app.workflows.browser_actions import BrowserPolicy
from app.workflows.flow_engine import FlowOutcomeUncertain, execute_graph, validate_graph
from app.workflows.registry import WorkflowFailure


def node(identifier, kind, config=None):
    return {
        "id": identifier,
        "type": kind,
        "label": identifier,
        "position": {"x": 20, "y": 20},
        "config": {} if config is None else config,
    }


def graph(*steps):
    nodes = [node("start", "start"), *steps, node("end", "end", {"output": {"ok": True}})]
    return {
        "nodes": nodes,
        "edges": [
            {"source": left["id"], "target": right["id"], "port": "next"}
            for left, right in zip(nodes, nodes[1:], strict=False)
        ],
    }


class Context:
    def __init__(self):
        self.claim = SimpleNamespace(attempt_count=1)
        self.saved = None
        self.checks = 0
        self.writes = []
        self.fail_at = None

    def check_active(self):
        self.checks += 1
        if self.fail_at == self.checks:
            raise OwnershipLostError("Ownership expired")

    def checkpoint(self, key, value):
        self.check_active()
        self.saved = copy.deepcopy(value)
        self.writes.append((key, copy.deepcopy(value)))

    def get_checkpoint(self, key):
        self.check_active()
        return copy.deepcopy(self.saved)


def test_validation_detaches_payload_and_requires_fully_connected_dag():
    source = graph(node("assign", "set", {"key": "message", "value": "Hello"}))
    saved = validate_graph(source)
    source["nodes"][1]["config"]["value"] = "Changed"
    assert saved["nodes"][1]["config"]["value"] == "Hello"
    saved["edges"].pop()
    with pytest.raises(ValidationError, match="connected"):
        validate_graph(saved)


@pytest.mark.parametrize("operator", [[], {}, None, True, "eval", "__import__"])
def test_invalid_condition_operator_is_always_safe_validation_error(operator):
    source = branch_graph()
    source["nodes"][1]["config"]["operator"] = operator
    with pytest.raises(ValidationError, match="operator"):
        validate_graph(source)


@pytest.mark.parametrize(
    "modify",
    [
        lambda value: value.update(arbitrary_code="print('example')"),
        lambda value: value["nodes"][0].update(extra=True),
        lambda value: value["nodes"][0].update(id="invalid id"),
        lambda value: value["nodes"][1].update(id="start"),
        lambda value: value["nodes"][1].update(type="python"),
        lambda value: value["nodes"][1].update(position={"x": -1, "y": 2}),
        lambda value: value["nodes"][1].update(position={"x": True, "y": 2}),
        lambda value: value["nodes"][1].update(config={"output": [], "unknown": 1}),
        lambda value: value["edges"][0].update(target="missing"),
        lambda value: value["edges"][0].update(port="true"),
        lambda value: value["edges"].append(dict(value["edges"][0])),
        lambda value: value["nodes"].append(node("unused", "end", {"output": {}})),
    ],
)
def test_invalid_graph_shape_rejected(modify):
    source = graph()
    modify(source)
    with pytest.raises(ValidationError):
        validate_graph(source)


def test_cycles_are_rejected_even_when_all_ports_are_connected():
    source = graph(node("first", "set", {"key": "value", "value": 1}))
    source["nodes"].insert(2, node("second", "set", {"key": "value", "value": 2}))
    source["edges"] = [
        {"source": "start", "target": "first", "port": "next"},
        {"source": "first", "target": "second", "port": "next"},
        {"source": "second", "target": "first", "port": "next"},
    ]
    with pytest.raises(ValidationError, match="cycles"):
        validate_graph(source)


@pytest.mark.parametrize(
    "step",
    [
        node("assign", "set", {"key": "password", "value": "fake-example"}),
        node("assign", "set", {"key": "message", "value": "{{__import__('os')}}"}),
        node("assign", "set", {"key": "message", "value": "x" * 17000}),
        node("wait", "delay", {"seconds": True}),
        node("wait", "delay", {"seconds": -1}),
        node("wait", "delay", {"seconds": 11}),
        node("open", "browser_open", {"url": "file:///etc/passwd"}),
        node("open", "browser_open", {"url": "http://example.com"}),
        node("fill", "browser_fill", {"selector": "xpath=//input", "value": "example"}),
        node("fill", "browser_fill", {"selector": "#input", "value": "example"}),
    ],
)
def test_invalid_configs_and_unopened_browser_action_rejected(step):
    with pytest.raises(ValidationError):
        validate_graph(graph(step))


def branch_graph():
    return {
        "nodes": [
            node("start", "start"),
            node(
                "choose", "condition", {"left": "{{count}}", "operator": "greater_than", "right": 5}
            ),
            node("yes", "end", {"output": {"branch": "yes", "customer": "{{customer.name}}"}}),
            node("no", "end", {"output": {"branch": "no"}}),
        ],
        "edges": [
            {"source": "start", "target": "choose", "port": "next"},
            {"source": "choose", "target": "yes", "port": "true"},
            {"source": "choose", "target": "no", "port": "false"},
        ],
    }


@pytest.mark.parametrize(
    "count,expected", [(9, {"branch": "yes", "customer": "Ada"}), (2, {"branch": "no"})]
)
def test_branches_execute_only_selected_path(count, expected):
    context = Context()
    assert (
        execute_graph(context, branch_graph(), {"count": count, "customer": {"name": "Ada"}})
        == expected
    )
    assert context.saved["finished"] is True
    assert context.checks >= 6


def test_literal_nested_values_and_typed_templates_without_eval():
    source = graph(
        node("assign", "set", {"key": "message", "value": "Hello {{customer.name}}: {{items.0}}"}),
        node("copy", "set", {"key": "selected", "value": "{{items}}"}),
    )
    source["nodes"][-1]["config"]["output"] = {"message": "{{message}}", "items": "{{selected}}"}
    assert execute_graph(Context(), source, {"customer": {"name": "Ada"}, "items": [3, 4]}) == {
        "message": "Hello Ada: 3",
        "items": [3, 4],
    }


@pytest.mark.parametrize(
    "config,inputs,code",
    [
        ({"left": "{{missing}}", "operator": "equals", "right": 1}, {}, "FLOW_VARIABLE_MISSING"),
        (
            {"left": "{{count}}", "operator": "greater_than", "right": 1},
            {"count": "2"},
            "FLOW_COMPARISON_TYPE_INVALID",
        ),
    ],
)
def test_known_pre_action_errors_are_terminal_and_sanitized(config, inputs, code):
    source = branch_graph()
    source["nodes"][1]["config"] = config
    with pytest.raises(WorkflowFailure) as error:
        execute_graph(Context(), source, inputs)
    assert error.value.error_code == code
    assert "missing" not in str(error.value)


def test_resume_uses_last_acknowledged_deterministic_checkpoint():
    source = graph(
        node("assign", "set", {"key": "count", "value": 7}), node("wait", "delay", {"seconds": 0})
    )
    source["nodes"][-1]["config"]["output"] = {"count": "{{count}}"}
    context = Context()
    context.fail_at = 7  # After the assignment was acknowledged, before delay.
    with pytest.raises(OwnershipLostError):
        execute_graph(context, source, {})
    assert context.saved["variables"] == {"count": 7}
    checkpoint_count = len(context.writes)
    context.fail_at = None
    context.claim.attempt_count = 2
    assert execute_graph(context, source, {"count": 99}) == {"count": 7}
    assert len(context.writes) - checkpoint_count == 2
    # A completed deterministic checkpoint is safe to return without redoing nodes.
    assert execute_graph(context, source, {}) == {"count": 7}


def test_graph_change_cannot_resume_an_old_checkpoint():
    context = Context()
    source = graph()
    execute_graph(context, source, {})
    source["nodes"][-1]["config"]["output"] = {"changed": True}
    with pytest.raises(WorkflowFailure) as error:
        execute_graph(context, source, {})
    assert error.value.error_code == "FLOW_CHECKPOINT_MISMATCH"


def test_delay_checks_lease_cooperatively_and_stops_on_loss():
    context = Context()
    context.fail_at = 6
    with pytest.raises(OwnershipLostError):
        execute_graph(context, graph(node("wait", "delay", {"seconds": 0.4})), {})
    assert context.saved["next"] == "wait"
    assert not context.saved["finished"]


def test_browser_errors_after_actions_quarantine_and_do_not_replay_same_attempt(monkeypatch):
    calls = []

    class Browser:
        opened = False

        def __init__(self, policy):
            pass

        def validate_url(self, url):
            pass

        def prepare(self):
            pass

        def perform(self, kind, config, **kwargs):
            calls.append(kind)
            self.opened = True
            if kind == "browser_click":
                raise RuntimeError("Sensitive target detail must not escape")

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("app.workflows.browser_actions.BrowserSession", Browser)
    source = graph(
        node("open", "browser_open", {"url": "https://example.com"}),
        node("click", "browser_click", {"selector": "#submit"}),
    )
    context = Context()
    with pytest.raises(FlowOutcomeUncertain) as error:
        execute_graph(context, source, {})
    assert "Sensitive" not in str(error.value)
    assert calls == ["browser_open", "browser_click", "closed"]
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {})
    assert calls == ["browser_open", "browser_click", "closed"]
    # Only a newly claimed attempt after runtime reconciliation can replay.
    context.claim.attempt_count = 2
    with pytest.raises(FlowOutcomeUncertain):
        execute_graph(context, source, {})
    assert calls.count("browser_click") == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://localhost",
        "https://example.com:8443",
        "https://user:fake@example.com",
        "file:///etc/passwd",
        "https://example.com\\@127.0.0.1",
    ],
)
def test_browser_policy_refuses_private_unlisted_or_credentialed_targets(url):
    with pytest.raises(WorkflowFailure):
        BrowserPolicy(hosts=("example.com",)).validate(url)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "224.0.0.1"]
)
def test_browser_dns_pinning_rejects_private_and_special_destinations(monkeypatch, address):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, (address, 443))]
    )
    with pytest.raises(WorkflowFailure) as error:
        BrowserPolicy(hosts=("example.com",)).resolver_rules()
    assert error.value.error_code == "FLOW_BROWSER_HOST_BLOCKED"


def test_browser_pins_validated_public_dns_and_blocks_unknown_resolutions(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    assert (
        BrowserPolicy(hosts=("example.com",)).resolver_rules()
        == "MAP example.com 93.184.216.34, MAP * ~NOTFOUND"
    )


def test_browser_network_disabled_without_allowlist(monkeypatch):
    monkeypatch.delenv("RPA_BROWSER_ALLOWED_HOSTS", raising=False)
    source = graph(node("open", "browser_open", {"url": "https://example.com"}))
    with pytest.raises(WorkflowFailure) as error:
        execute_graph(Context(), source, {})
    assert error.value.error_code == "FLOW_BROWSER_NOT_CONFIGURED"


@pytest.mark.parametrize(
    "operator,left,right,expected",
    [
        ("equals", {"flag": True}, {"flag": 1}, "no"),
        ("not_equals", {"flag": True}, {"flag": 1}, "yes"),
        ("contains", [{"flag": True}], {"flag": 1}, "no"),
    ],
)
def test_condition_equality_preserves_nested_json_types(operator, left, right, expected):
    source = branch_graph()
    source["nodes"][1]["config"] = {"left": left, "operator": operator, "right": right}
    result = execute_graph(Context(), source, {"customer": {"name": "Fixture"}})
    assert result["branch"] == expected


def test_maximum_graph_progress_and_variables_fit_real_checkpoint_payload_bound():
    from app.services.execution_service import _json_value

    class BoundedContext(Context):
        def checkpoint(self, key, value):
            super().checkpoint(key, _json_value(value))

    steps = [node("step" + str(index).zfill(60), "delay", {"seconds": 0}) for index in range(38)]
    source = graph(*steps)
    for item in source["nodes"]:
        item["label"] = "Step"
    source["nodes"][-1]["config"]["output"] = {"value": "{{value}}"}
    context = BoundedContext()
    assert execute_graph(context, source, {"value": "x" * 12000}) == {"value": "x" * 12000}
    assert len(context.saved["executed"]) == 40
    assert context.saved["current"] == "end"


def test_oversized_variable_state_fails_without_writing_unstorable_checkpoint():
    context = Context()
    with pytest.raises(WorkflowFailure) as error:
        execute_graph(context, graph(), {"value": "x" * 12300})
    assert error.value.error_code == "FLOW_VARIABLES_TOO_LARGE"
    assert context.writes == []
