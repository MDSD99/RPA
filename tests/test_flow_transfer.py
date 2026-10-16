"""Portable flow archives preserve definitions without trusting runtime state."""

from copy import deepcopy

import pytest

from app.core.exceptions import ValidationError
from app.workflows.flow_transfer import MAX_DOCUMENT_BYTES, export_document, import_document


def transfer_graph():
    steps = [
        ("start", "start", {}),
        ("open", "browser_open", {"url": "https://example.com/login"}),
        (
            "fill",
            "browser_fill",
            {"selector": "#pass", "value": "", "secret_ref": "login", "secret_field": "password"},
        ),
        ("end", "end", {"output": {"label": "Müşteri verisi"}}),
    ]
    return {
        "nodes": [
            {"id": key, "type": kind, "label": key, "position": {"x": 0, "y": 0}, "config": config}
            for key, kind, config in steps
        ],
        "edges": [
            {"source": left[0], "target": right[0], "port": "next"}
            for left, right in zip(steps, steps[1:], strict=False)
        ],
    }


def transfer_document():
    return {
        "format": "multirpa-flow",
        "schema_version": 1,
        "flow": {"name": "Taşınabilir akış", "description": "Açıklama", "graph": transfer_graph()},
    }


def test_export_projects_only_the_definition_and_preserves_aliases_and_literal_data():
    source = {
        **transfer_document()["flow"],
        "id": "private-source-id",
        "tenant_id": "private-tenant-id",
        "version": 17,
        "created_at": "old-date",
        "runs": [{"inputs": {"private": "history"}}],
        "credentials": {"login": {"password": "private-vault-value"}},
    }
    document = export_document(source)
    assert document == transfer_document()
    imported = import_document(document)
    assert set(imported) == {"name", "description", "graph"}
    assert imported["graph"]["nodes"][2]["config"]["secret_ref"] == "login"
    assert imported["graph"]["nodes"][3]["config"]["output"]["label"] == "Müşteri verisi"
    source["graph"]["nodes"][0]["label"] = "Changed source"
    document["flow"]["graph"]["nodes"][0]["label"] = "Changed archive"
    assert imported["graph"]["nodes"][0]["label"] == "start"


def test_import_metadata_uses_the_same_normalization_as_creation():
    document = transfer_document()
    document["flow"].update(name="  Örnek  ", description="  Açıklama  ")
    result = import_document(document)
    assert result["name"] == "Örnek" and result["description"] == "Açıklama"


@pytest.mark.parametrize("version", [True, False, 1.0, 2.0, "1", "2", 0, 3, None])
def test_import_requires_the_exact_supported_integer_schema_version(version):
    document = transfer_document()
    document["schema_version"] = version
    with pytest.raises(ValidationError, match="schema version"):
        import_document(document)


@pytest.mark.parametrize("all_explicit", [False, True])
def test_v2_roundtrip_keeps_explicit_and_implicit_node_versions(all_explicit):
    document = transfer_document()
    document["schema_version"] = 2
    for step in document["flow"]["graph"]["nodes"]:
        if all_explicit or step["type"] == "browser_fill":
            step["type_version"] = 1
    original = deepcopy(document)
    imported = import_document(document)
    assert imported == document["flow"]
    assert export_document(imported) == original
    assert document == original


def test_v2_accepts_legacy_graph_without_upgrading_its_nodes():
    document = transfer_document()
    document["schema_version"] = 2
    definition = import_document(document)
    assert definition == transfer_document()["flow"]
    assert export_document(definition) == transfer_document()


def test_v1_rejects_explicit_node_versions_instead_of_silently_downgrading():
    document = transfer_document()
    document["flow"]["graph"]["nodes"][0]["type_version"] = 1
    with pytest.raises(ValidationError, match="schema version 1"):
        import_document(document)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2, None])
def test_v2_rejects_unsupported_node_versions(version):
    document = transfer_document()
    document["schema_version"] = 2
    document["flow"]["graph"]["nodes"][0]["type_version"] = version
    with pytest.raises(ValidationError, match="type version"):
        import_document(document)
    with pytest.raises(ValidationError, match="type version"):
        export_document(document["flow"])


@pytest.mark.parametrize(
    "change",
    [
        lambda document: document.update(id="private-marker"),
        lambda document: document.update(format="private-marker"),
        lambda document: document.pop("schema_version"),
        lambda document: document["flow"].pop("description"),
        lambda document: document["flow"].update(id="private-marker"),
        lambda document: document["flow"].update(tenant_id="private-marker"),
        lambda document: document["flow"].update(version=3),
        lambda document: document["flow"].update(inputs={"value": "private-marker"}),
        lambda document: document["flow"].update(runs=[]),
        lambda document: document["flow"].update(credentials={"password": "private-marker"}),
        lambda document: document["flow"].update(name=""),
        lambda document: document["flow"].update(description=123),
        lambda document: document["flow"]["graph"]["edges"].clear(),
        lambda document: document["flow"]["graph"]["nodes"][0].update(type="private-marker"),
    ],
)
def test_tampered_archives_are_rejected_without_echoing_values(change):
    document = transfer_document()
    change(document)
    with pytest.raises(ValidationError) as error:
        import_document(document)
    assert "private-marker" not in str(error.value)


@pytest.mark.parametrize("value", [None, [], "private-marker", 123, True])
def test_import_requires_an_object(value):
    with pytest.raises(ValidationError, match="fields"):
        import_document(value)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), "\ud800", b"data", {1}, (1, 2)]
)
def test_non_json_or_invalid_unicode_values_are_safely_rejected(value):
    document = transfer_document()
    document["flow"]["graph"]["nodes"][-1]["config"]["output"]["value"] = value
    with pytest.raises(ValidationError, match="JSON values"):
        import_document(document)


@pytest.mark.parametrize("key", ["__proto__", "constructor", "prototype", 123])
def test_nested_unsafe_object_keys_are_rejected_on_import_and_export(key):
    document = transfer_document()
    document["flow"]["graph"]["nodes"][-1]["config"]["output"]["nested"] = [{key: "value"}]
    with pytest.raises(ValidationError, match="object key"):
        import_document(document)
    with pytest.raises(ValidationError, match="object key"):
        export_document(document["flow"])


def test_oversize_limit_counts_utf8_bytes_before_recursive_graph_validation(monkeypatch):
    def must_not_validate(_graph):
        raise AssertionError("Oversized document reached recursive graph validation")

    monkeypatch.setattr("app.workflows.flow_transfer.validate_graph", must_not_validate)
    document = transfer_document()
    document["flow"]["description"] = "ç" * (MAX_DOCUMENT_BYTES // 2)
    with pytest.raises(ValidationError, match="128 KiB"):
        import_document(document)


def test_extreme_depth_and_cyclic_objects_fail_without_recursive_traversal():
    document = transfer_document()
    nested = {}
    document["flow"]["graph"]["nodes"][-1]["config"]["output"] = nested
    for _ in range(1500):
        child = {}
        nested["child"] = child
        nested = child
    with pytest.raises(ValidationError, match="nesting"):
        import_document(document)
    document = transfer_document()
    document["flow"]["graph"]["nodes"][-1]["config"]["output"]["cycle"] = document
    with pytest.raises(ValidationError, match="acyclic JSON"):
        import_document(document)


def test_inline_credential_fields_still_use_shared_graph_validation():
    document = deepcopy(transfer_document())
    document["flow"]["graph"]["nodes"][-1]["config"]["output"]["password"] = "private-marker"
    with pytest.raises(ValidationError, match="sensitive") as error:
        import_document(document)
    assert "private-marker" not in str(error.value)
