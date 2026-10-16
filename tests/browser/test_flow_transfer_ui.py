"""Portable flow files preserve authored graphs without running imported work."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_console import PROJECT_ROOT, _assert_no_horizontal_overflow
from test_flow_builder import _new_flow, _save

pytestmark = [pytest.mark.postgres, pytest.mark.browser]


def _file(document):
    return {
        "name": "ornek.multirpa.json",
        "mimeType": "application/json",
        "buffer": json.dumps(document, ensure_ascii=False).encode(),
    }


def _import_dialog(page):
    from playwright.sync_api import expect

    if page.viewport_size["width"] <= 720:
        page.locator("#menu-button").click()
    page.locator('[data-nav="workflows"]').click()
    page.locator('[data-flow-action="import"]').click()
    expect(page.locator("#flow-import-form")).to_be_visible()


def test_export_saved_revision_and_import_new_flow_on_mobile(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page, "condition")
    name = f"Taşınabilir akış <özel> {uuid4().hex[:8]}"
    page.locator("#flow-name").fill(name)
    exporter = page.locator('[data-flow-action="export"]')
    expect(exporter).to_be_disabled()
    flow_id = _save(page, 1)
    page.locator("#flow-description").fill("Sabit metinler dosyada korunur.")
    expect(exporter).to_be_disabled()
    _save(page, 2)
    expect(exporter).to_be_enabled()
    original = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    jobs_before = page.request.get(f"{console_url}/api/jobs").json()["total"]

    with page.expect_download() as downloading:
        exporter.click()
    download = downloading.value
    assert download.suggested_filename == f"MultiRPA-flow-{flow_id}-v2.multirpa.json"
    document = json.loads(Path(download.path()).read_text(encoding="utf-8"))
    assert set(document) == {"format", "schema_version", "flow"}
    assert document["schema_version"] == 2
    assert all(node["type_version"] == 1 for node in document["flow"]["graph"]["nodes"])
    assert set(document["flow"]) == {"name", "description", "graph"}
    assert document["flow"]["graph"] == original["graph"]

    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    _import_dialog(page)
    page.locator("#flow-import-file").set_input_files(_file(document))
    expect(page.locator("#flow-import-name")).to_have_text(name)
    expect(page.locator("#flow-import-preview img")).to_have_count(0)
    _assert_no_horizontal_overflow(page)
    artifacts = PROJECT_ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    page.screenshot(path=str(artifacts / "multirpa-flow-import-mobile-2026-10-13.png"))
    page.locator('#flow-import-form [type="submit"]').click()
    expect(page.locator("#flow-import-form")).not_to_be_visible()
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 1 · Kaydedildi")
    imported_id = page.url.split("#flows/", 1)[1]
    assert imported_id != flow_id
    imported = page.request.get(f"{console_url}/api/flows/{imported_id}").json()["flow"]
    assert imported["graph"] == original["graph"]
    assert imported["description"] == original["description"]
    assert page.request.get(f"{console_url}/api/jobs").json()["total"] == jobs_before
    assert page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]["version"] == 2
    _assert_no_horizontal_overflow(page)


def test_legacy_edit_preserves_nodes_and_new_step_exports_mixed_schema_two(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page)
    flow_id = _save(page, 1)
    flow = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    for node in flow["graph"]["nodes"]:
        node.pop("type_version")
    legacy = flow["graph"]
    response = page.request.put(
        f"{console_url}/api/flows/{flow_id}",
        headers={"X-RPA-Request": "1"},
        data={"name": flow["name"], "description": "", "graph": legacy, "expected_version": 1},
    )
    assert response.status == 200
    page.reload()
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 2 · Kaydedildi")
    page.locator("#flow-description").fill("Eski düğümler korunmalı.")
    _save(page, 3)
    persisted = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    assert persisted["graph"] == legacy
    with page.expect_download() as downloading:
        page.locator('[data-flow-action="export"]').click()
    document = json.loads(Path(downloading.value.path()).read_text())
    assert document["schema_version"] == 1
    assert document["flow"]["graph"] == legacy

    page.locator('[data-flow-action="add"][data-type="set"]').click()
    added_id = page.locator("#node-settings").get_attribute("data-node-id")
    _save(page, 4)
    with page.expect_download() as downloading:
        page.locator('[data-flow-action="export"]').click()
    mixed = json.loads(Path(downloading.value.path()).read_text())
    assert mixed["schema_version"] == 2
    for node in mixed["flow"]["graph"]["nodes"]:
        if node["id"] == added_id:
            assert node["type_version"] == 1
        else:
            assert node in legacy["nodes"] and "type_version" not in node
    _import_dialog(page)
    page.locator("#flow-import-file").set_input_files(_file(mixed))
    page.locator('#flow-import-form [type="submit"]').click()
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 1 · Kaydedildi")
    imported_id = page.url.split("#flows/", 1)[1]
    assert imported_id != flow_id
    imported = page.request.get(f"{console_url}/api/flows/{imported_id}").json()["flow"]
    assert imported["graph"] == mixed["flow"]["graph"]


def test_unsupported_step_cannot_import_or_open_in_an_older_editor(page, console_url):
    from copy import deepcopy

    from playwright.sync_api import expect

    _new_flow(page)
    flow_id = _save(page, 1)
    current = page.request.get(f"{console_url}/api/flows/{flow_id}").json()
    document = page.request.get(f"{console_url}/api/flows/{flow_id}/export").json()["document"]
    flows_before = page.request.get(f"{console_url}/api/flows").json()["total"]
    jobs_before = page.request.get(f"{console_url}/api/jobs").json()["total"]
    _import_dialog(page)
    submit = page.locator('#flow-import-form [type="submit"]')
    field = page.locator("#flow-import-file")
    field.set_input_files(_file({**document, "schema_version": 1}))
    expect(submit).to_be_disabled()
    expect(page.locator("#flow-import-error")).to_contain_text("biçimi adımlarıyla uyuşmuyor")
    future_document = deepcopy(document)
    future_document["flow"]["graph"]["nodes"][-1]["type_version"] = 2
    field.set_input_files(_file(future_document))
    expect(submit).to_be_disabled()
    expect(page.locator("#flow-import-error")).to_contain_text("Uygulamayı güncelleyin")
    assert page.request.get(f"{console_url}/api/flows").json()["total"] == flows_before
    assert page.request.get(f"{console_url}/api/jobs").json()["total"] == jobs_before
    page.locator('#flow-import-form [data-action="close-dialog"]').click()

    # Simulate a newer server serving a node unknown to this editor. The older
    # editor must not silently drop the version or save its previous draft.
    current["flow"]["graph"]["nodes"][-1]["type_version"] = 2
    future_id = str(uuid4())
    current["flow"]["id"] = future_id
    writes = []
    page.on(
        "request", lambda request: writes.append(request.url) if request.method == "PUT" else None
    )
    page.route(f"**/api/flows/{future_id}", lambda route: route.fulfill(json=current))
    page.goto(f"{console_url}/#flows/{future_id}")
    expect(page.locator(".offline-banner")).to_contain_text("Uygulamayı güncelleyin")
    expect(page.locator("#flow-name")).to_have_count(0)
    page.keyboard.press("Control+s")
    expect(page.locator(".offline-banner")).to_be_visible()
    assert writes == []


def test_invalid_files_and_late_import_do_not_replace_current_draft(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page)
    flow_id = _save(page, 1)
    document = page.request.get(f"{console_url}/api/flows/{flow_id}/export").json()["document"]
    _import_dialog(page)
    field = page.locator("#flow-import-file")
    submit = page.locator('#flow-import-form [type="submit"]')
    field.set_input_files(_file({**document, "schema_version": 999}))
    expect(page.locator("#flow-import-error")).to_contain_text("desteklenen")
    expect(submit).to_be_disabled()
    field.set_input_files(
        {"name": "large.json", "mimeType": "application/json", "buffer": b" " * (128 * 1024 + 1)}
    )
    expect(page.locator("#flow-import-error")).to_contain_text("128 KB")
    expect(submit).to_be_disabled()

    held = []
    page.route("**/api/flows/import", lambda route: held.append(route))
    field.set_input_files(_file(document))
    expect(submit).to_be_enabled()
    submit.click()
    expect(submit).to_be_disabled()
    assert len(held) == 1
    page.locator('#flow-import-form [data-action="close-dialog"]').click()
    _new_flow(page)
    fresh_name = "Yanıtı beklerken yazılmış taslak"
    page.locator("#flow-name").fill(fresh_name)
    response = held[0].fetch()
    assert response.status == 201
    imported_id = response.json()["flow"]["id"]
    held[0].fulfill(response=response)
    expect(page.locator("#toasts")).to_contain_text("yeni bir kopya")
    expect(page.locator("#flow-name")).to_have_value(fresh_name)
    expect(page.locator("#flow-save-state")).to_have_text("Kaydedilmemiş değişiklikler")
    assert page.url.endswith("#flows/new")
    assert page.request.get(f"{console_url}/api/flows/{imported_id}").status == 200


def test_nested_graph_exports_as_bounded_reimportable_json(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page)
    flow_id = _save(page, 1)
    original = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    nested = [0] * 4000
    for _ in range(14):
        nested = {"rows": nested}
    original["graph"]["nodes"][-1]["config"]["output"] = {"nested": nested}
    updated = page.request.put(
        f"{console_url}/api/flows/{flow_id}",
        headers={"X-RPA-Request": "1"},
        data={
            "name": original["name"],
            "description": original["description"],
            "graph": original["graph"],
            "expected_version": 1,
        },
    )
    assert updated.status == 200
    page.reload()
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 2 · Kaydedildi")
    with page.expect_download() as downloading:
        page.locator('[data-flow-action="export"]').click()
    contents = Path(downloading.value.path()).read_bytes()
    assert len(contents) <= 128 * 1024
    document = json.loads(contents)
    assert len(json.dumps(document, indent=2).encode()) > 128 * 1024
    _import_dialog(page)
    page.locator("#flow-import-file").set_input_files(_file(document))
    page.locator('#flow-import-form [type="submit"]').click()
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 1 · Kaydedildi")
    imported_id = page.url.split("#flows/", 1)[1]
    assert imported_id != flow_id
    imported = page.request.get(f"{console_url}/api/flows/{imported_id}").json()["flow"]
    assert imported["graph"] == original["graph"]
