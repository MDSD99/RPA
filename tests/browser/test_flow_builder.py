"""User-created graphs persist and execute through the real browser and PostgreSQL."""

from uuid import uuid4

import pytest
from test_console import (
    PROJECT_ROOT,
    _assert_no_horizontal_overflow,
)

pytestmark = [pytest.mark.postgres, pytest.mark.browser]


def _new_flow(page, template=None):
    from playwright.sync_api import expect

    page.locator('[data-nav="workflows"]').click()
    expect(page.locator(".flow-hero")).to_be_visible()
    selector = '[data-flow-action="new"]'
    selector += f'[data-template="{template}"]' if template else ":not([data-template])"
    page.locator(selector).first.click()
    expect(page.locator("#flow-name")).to_be_visible()


def _select(page, node_id):
    from playwright.sync_api import expect

    page.locator(f'[data-flow-action="select"][data-id="{node_id}"]').click()
    expect(page.locator("#node-settings")).to_have_attribute("data-node-id", node_id)


def _save(page, version):
    from playwright.sync_api import expect

    page.locator('[data-flow-action="save"]').click()
    expect(page.locator("#flow-save-state")).to_have_text(f"Sürüm {version} · Kaydedildi")
    expect(page.locator("#flow-validation")).not_to_be_visible()
    return page.url.split("#flows/", 1)[1]


def _run(page, console_url, *, value=None, expected):
    from playwright.sync_api import expect

    page.locator('[data-flow-action="run"]').click()
    form = page.locator("#flow-run-form")
    expect(form).to_be_visible()
    if value is not None:
        expect(form.locator('[name="input-key"]')).to_have_value("girdi")
        form.locator('[name="input-value"]').fill(value)
    form.get_by_role("button", name="Çalıştırmayı başlat", exact=True).click()
    expect(form).not_to_be_visible()
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Tamamlandı", timeout=20_000)
    job_id = page.url.split("#jobs/", 1)[1]
    records = page.request.get(f"{console_url}/api/jobs/{job_id}/transactions").json()
    assert records["total"] == 1
    transaction = records["items"][0]
    assert transaction["result_data"] == expected
    assert transaction["attempt_count"] == 1
    assert transaction["status"] == "SUCCESS"
    page.locator('[data-action="transaction"]').first.click()
    detail = page.locator("#transaction-dialog")
    expect(detail.locator(".checkpoints-list")).to_contain_text("flow_state")
    expect(detail.locator(".checkpoints-list")).to_contain_text('"finished": true')
    detail.locator('[data-action="close-transaction"]').click()
    return job_id


def test_build_connect_save_reload_and_run_a_user_configured_flow(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page)
    flow_name = f"Kullanıcı akışı {uuid4().hex[:10]}"
    page.locator("#flow-name").fill(flow_name)
    page.locator("#flow-description").fill("Tarayıcıdan oluşturulmuş kalıcı kullanıcı akışı.")
    page.locator('[data-flow-action="add"][data-type="set"]').click()
    step_id = page.locator("#node-settings").get_attribute("data-node-id")
    page.locator("#node-label").fill("Mesajı hazırla")
    page.locator("#flow-key").fill("mesaj")
    page.locator("#flow-value").fill("Kullanıcı tarafından ayarlandı")

    # An automatic overview poll must not replace focused editor inputs or the draft.
    page.wait_for_timeout(3500)
    expect(page.locator("#flow-value")).to_be_focused()
    expect(page.locator("#flow-value")).to_have_value("Kullanıcı tarafından ayarlandı")
    expect(page.locator("#flow-name")).to_have_value(flow_name)
    _select(page, "end")
    page.locator('[data-output-key="0"]').fill("mesaj")
    page.locator('[data-output-value="0"]').fill("{{mesaj}}")
    _select(page, step_id)
    page.locator("#edge-next").select_option("")
    page.locator(f'[data-flow-action="connect"][data-id="{step_id}"][data-port="next"]').click()
    page.locator('[data-flow-action="target"][data-id="end"]').click()
    expect(page.locator("#edge-next")).to_have_value("end")
    node = page.locator(f'[data-node="{step_id}"]')
    original_position = node.evaluate(
        "element => ({x: parseInt(element.style.left), y: parseInt(element.style.top)})"
    )
    title = node.locator(".node-title")
    title.scroll_into_view_if_needed()
    bounds = title.bounding_box()
    start_x, start_y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x - 45, start_y + 90, steps=6)
    page.mouse.up()
    flow_id = _save(page, 1)
    stored = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    assert stored["name"] == flow_name
    assert all(node["type_version"] == 1 for node in stored["graph"]["nodes"])
    assert stored["graph"]["edges"] == [
        {"source": "start", "target": step_id, "port": "next"},
        {"source": step_id, "target": "end", "port": "next"},
    ]
    assert next(node for node in stored["graph"]["nodes"] if node["id"] == step_id)["position"] == {
        "x": original_position["x"] - 45,
        "y": original_position["y"] + 90,
    }
    page.reload()
    expect(page.locator("#flow-name")).to_have_value(flow_name)
    expect(page.locator("[data-node]")).to_have_count(3)
    _select(page, step_id)
    expect(page.locator("#flow-key")).to_have_value("mesaj")
    expect(page.locator("#flow-value")).to_have_value("Kullanıcı tarafından ayarlandı")
    _run(page, console_url, expected={"mesaj": "Kullanıcı tarafından ayarlandı"})


def test_condition_branches_revisions_and_mobile_editor(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page, "condition")
    page.locator("#flow-name").fill(f"Dallanma {uuid4().hex[:10]}")
    _select(page, "yes")
    page.locator("#flow-value").fill("Kullanıcının olumlu sonucu")
    flow_id = _save(page, 1)
    first_job_id = _run(
        page, console_url, value="evet", expected={"sonuc": "Kullanıcının olumlu sonucu"}
    )
    page.goto(f"{console_url}/#flows/{flow_id}")
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 1 · Kaydedildi")
    _run(page, console_url, value="hayır", expected={"sonuc": "İncelemeye alındı"})
    page.goto(f"{console_url}/#flows/{flow_id}")
    _select(page, "yes")
    page.locator("#flow-value").fill("İkinci sürüm")
    _save(page, 2)
    frozen = page.request.get(f"{console_url}/api/flows/runs/by-job/{first_job_id}").json()
    assert frozen["version"] == 1
    assert (
        next(node for node in frozen["graph"]["nodes"] if node["id"] == "yes")["config"]["value"]
        == "Kullanıcının olumlu sonucu"
    )
    _run(page, console_url, value="evet", expected={"sonuc": "İkinci sürüm"})

    page.goto(f"{console_url}/#flows/{flow_id}")
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 2 · Kaydedildi")
    _assert_no_horizontal_overflow(page)
    expect(page.locator("#toasts > *")).to_have_count(0, timeout=10_000)
    page.locator('[data-flow-action="fit"]').click()
    artifacts = PROJECT_ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    page.screenshot(path=str(artifacts / "flow-builder-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    _select(page, "condition")
    expect(page.locator("#edge-true")).to_have_value("yes")
    expect(page.locator("#edge-false")).to_have_value("no")
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=str(artifacts / "flow-builder-mobile.png"), full_page=True)


def test_invalid_cycle_is_explained_and_unsaved_work_survives_navigation(page):
    from playwright.sync_api import expect

    _new_flow(page, "condition")
    name = f"Taslak {uuid4().hex[:10]}"
    page.locator("#flow-name").fill(name)
    _select(page, "yes")
    page.locator("#edge-next").select_option("condition")
    page.locator('[data-flow-action="save"]').click()
    expect(page.locator("#flow-validation")).to_contain_text("döngü oluşturuyor")
    expect(page.locator("#flow-save-state")).to_have_text("Kaydedilmemiş değişiklikler")
    page.locator('[data-nav="overview"]').click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Operasyon merkezi")
    page.evaluate("location.hash = 'flows/new'")
    expect(page.locator("#flow-name")).to_have_value(name)
    _select(page, "yes")
    expect(page.locator("#edge-next")).to_have_value("condition")
    page.locator("#edge-next").select_option("end")
    _save(page, 1)


def test_concurrent_revision_conflict_preserves_the_unsaved_editor(page, console_url):
    from playwright.sync_api import expect

    _new_flow(page)
    page.locator("#flow-name").fill(f"Sürüm çakışması {uuid4().hex[:10]}")
    flow_id = _save(page, 1)
    stored = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    page.locator("#flow-name").fill("Bu sekmedeki kaydedilmemiş değişiklik")
    changed = page.request.put(
        f"{console_url}/api/flows/{flow_id}",
        headers={"X-RPA-Request": "1", "Origin": console_url},
        data={
            "name": "Başka sekmenin ikinci sürümü",
            "description": stored["description"],
            "graph": stored["graph"],
            "expected_version": 1,
        },
    )
    assert changed.status == 200
    assert changed.json()["flow"]["version"] == 2
    page.locator('[data-flow-action="save"]').click()
    expect(page.locator("#flow-validation")).to_be_visible()
    expect(page.locator("#flow-save-state")).to_have_text("Kaydedilmemiş değişiklikler")
    expect(page.locator("#flow-name")).to_have_value("Bu sekmedeki kaydedilmemiş değişiklik")
    latest = page.request.get(f"{console_url}/api/flows/{flow_id}").json()["flow"]
    assert latest["version"] == 2
    assert latest["name"] == "Başka sekmenin ikinci sürümü"
    # Discarding the draft is an explicit browser-navigation decision.
    page.once("dialog", lambda dialog: dialog.accept())
    page.reload()
    expect(page.locator("#flow-name")).to_have_value("Başka sekmenin ikinci sürümü")
    expect(page.locator("#flow-save-state")).to_have_text("Sürüm 2 · Kaydedildi")
