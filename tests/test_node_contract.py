"""Node behavior versions preserve legacy execution identity and fail closed."""

import hashlib
import json
from copy import deepcopy

import pytest
from test_flow_engine import Context, branch_graph, graph, node

from app.core.exceptions import ValidationError
from app.workflows.flow_engine import execute_graph, validate_graph
from app.workflows.node_contract import node_catalog, resolve_node_contract
from app.workflows.registry import WorkflowFailure


def test_legacy_definition_and_acknowledged_checkpoint_keep_original_digest():
    source = graph(
        node("assign", "set", {"key": "count", "value": 7}),
        node("wait", "delay", {"seconds": 0}),
    )
    source["nodes"][-1]["config"]["output"] = {"count": "{{count}}"}
    original = deepcopy(source)
    old_digest = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    validated = validate_graph(source)
    assert source == validated == original
    assert all("type_version" not in step for step in validated["nodes"])
    assert hashlib.sha256(json.dumps(validated, sort_keys=True).encode()).hexdigest() == old_digest
    context = Context()
    context.claim.attempt_count = 2
    context.saved = {
        "graph_digest": old_digest,
        "attempt": 1,
        "next": "wait",
        "variables": {"count": 7},
        "browser": False,
        "finished": False,
        "action_started": False,
        "current": "assign",
        "executed": ["start", "assign"],
        "last_port": "next",
    }
    assert execute_graph(context, source, {"count": 99}) == {"count": 7}
    assert [state["current"] for _key, state in context.writes] == ["wait", "end"]
    assert all(state["graph_digest"] == old_digest for _key, state in context.writes)
    assert source == original


@pytest.mark.parametrize("all_explicit", [False, True])
@pytest.mark.parametrize("count,branch", [(9, "yes"), (2, "no")])
def test_explicit_v1_and_mixed_graphs_preserve_branch_behavior(all_explicit, count, branch):
    source = branch_graph()
    for step in source["nodes"]:
        if all_explicit or step["type"] == "condition":
            step["type_version"] = 1
    assert validate_graph(source) == source
    result = execute_graph(Context(), source, {"count": count, "customer": {"name": "Ada"}})
    assert result["branch"] == branch


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 0, -1, 2, None, [], {}])
def test_unknown_version_anywhere_is_rejected_before_adapter_or_effect(monkeypatch, version):
    source = graph(
        node("write", "file_write", {"path": "out.txt", "value": "must not write"}),
        node("open", "browser_open", {"url": "https://example.com"}),
    )
    source["nodes"][-1]["type_version"] = version

    def must_not_construct(*_args, **_kwargs):
        raise AssertionError("Unknown version reached an adapter")

    monkeypatch.setattr("app.adapters.automation_actions.AutomationActions", must_not_construct)
    monkeypatch.setattr("app.workflows.browser_actions.BrowserSession", must_not_construct)
    with pytest.raises(ValidationError, match="type version"):
        validate_graph(source)
    context = Context()
    with pytest.raises(WorkflowFailure) as error:
        execute_graph(context, source, {})
    assert error.value.error_code == "FLOW_DEFINITION_INVALID"
    assert context.checks == 0 and context.writes == []


def test_explicit_version_remains_part_of_checkpoint_identity():
    source = graph()
    context = Context()
    execute_graph(context, source, {})
    source["nodes"][0]["type_version"] = 1
    with pytest.raises(WorkflowFailure) as error:
        execute_graph(context, source, {})
    assert error.value.error_code == "FLOW_CHECKPOINT_MISMATCH"


def test_contract_metadata_is_detached_and_does_not_claim_runner_readiness():
    catalog = node_catalog()
    assert len(catalog) == 20
    assert {entry["type_version"] for entry in catalog} == {1}
    assert all(
        set(entry) == {"type", "type_version", "output_ports", "required_capability"}
        for entry in catalog
    )
    condition = next(entry for entry in catalog if entry["type"] == "condition")
    condition["output_ports"].clear()
    assert resolve_node_contract("condition").output_ports == ("true", "false")
    assert resolve_node_contract("end").output_ports == ()
    assert resolve_node_contract("windows_click").required_capability == "windows_uia"
