"""Workflow discovery and record linkage journeys in the isolated console."""

from uuid import uuid4

import pytest
from test_console import (
    _assert_no_horizontal_overflow,
)

pytestmark = [pytest.mark.postgres, pytest.mark.browser]


def test_catalog_identifies_demo_and_runs_it_on_mobile(page):
    from playwright.sync_api import expect

    page.locator('[data-nav="workflows"]').click()
    expect(page.get_by_role("heading", level=1)).to_have_text("İş akışları")
    expect(page.locator(".flow-hero")).to_be_visible()
    card = page.locator('.flow-template[data-action="demo"]')
    expect(card).to_contain_text("Altyapı demosu")
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    card.click()
    dialog = page.locator("#form-dialog")
    dialog.locator('[name="record_count"]').fill("1")
    dialog.get_by_role("button", name="Demoyu çalıştır").click()
    expect(dialog).not_to_be_visible()
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Tamamlandı", timeout=20_000)
    _assert_no_horizontal_overflow(page)


@pytest.mark.parametrize("workers_enabled", [True, False])
def test_registered_catalog_prefills_jobs_and_explains_worker_state(page, workers_enabled):
    """Exercise catalog response contracts without inventing an external adapter."""
    from playwright.sync_api import expect

    workflow_name = f"browser-registered-{uuid4().hex}"

    def registered_catalog(route):
        response = route.fetch()
        data = response.json()
        data["workers_enabled"] = workers_enabled
        for item in data["items"]:
            item["workers_enabled"] = workers_enabled
        data["items"].append(
            {
                "name": workflow_name,
                "title": "Kayıtlı test akışı",
                "description": "Tarayıcı sözleşme testi için kayıtlı akış tanımı.",
                "kind": "automation",
                "registered": True,
                "workers_enabled": workers_enabled,
            }
        )
        route.fulfill(response=response, json=data)

    page.route("**/api/workflows", registered_catalog)
    page.locator('[data-nav="workflows"]').click()
    expect(page.get_by_role("heading", level=1)).to_have_text("İş akışları")
    card = page.locator(f'[data-workflow-name="{workflow_name}"]')
    expect(card).to_contain_text("Tarayıcı sözleşme testi için kayıtlı akış tanımı.")
    if not workers_enabled:
        expect(page.locator(".live-label")).to_contain_text("Worker’lar kapalı")
        expect(page.locator('.flow-template[data-action="demo"]')).to_be_disabled()
    card.get_by_role("button", name="Yeni iş", exact=True).click()
    dialog = page.locator("#form-dialog")
    expect(dialog.locator('[name="workflow_name"]')).to_have_value(workflow_name)
    expect(dialog.locator(f'#workflow-choices option[value="{workflow_name}"]')).to_have_count(1)
    dialog.locator('[name="job_id"]').fill(f"catalog-job-{uuid4().hex}")
    dialog.get_by_role("button", name="İşi oluştur").click()
    expect(dialog).not_to_be_visible()
    expect(page.locator(".detail-summary")).to_be_visible()
    expect(page.locator(".page-heading .eyebrow")).to_have_text("Kayıtlı test akışı")
    if workers_enabled:
        expect(page.locator(".callout")).to_have_count(0)
    else:
        expect(page.locator(".callout")).to_contain_text(
            "Bu iş akışı kayıtlı; bu uygulamanın worker’ları kapalı."
        )


def test_catalog_refresh_failure_is_explicit_and_can_recover(page):
    from playwright.sync_api import expect

    page.locator('[data-nav="workflows"]').click()
    expect(page.locator(".flow-hero")).to_be_visible()
    page.route(
        "**/api/workflows",
        lambda route: route.fulfill(
            status=503, json={"error": {"message": "Katalog geçici olarak kullanılamıyor."}}
        ),
    )
    # The automatic refresh must remove stale readiness claims as well.
    expect(page.locator(".offline-banner")).to_contain_text(
        "İş akışı kataloğu alınamadı.", timeout=10_000
    )
    expect(page.locator(".flow-hero")).to_have_count(0)
    expect(page.locator("#connection-status")).to_contain_text("Bağlantı yok")
    page.unroute("**/api/workflows")
    page.get_by_role("button", name="Yeniden dene", exact=True).click()
    expect(page.locator(".flow-hero")).to_be_visible()
    expect(page.locator("#connection-status")).to_contain_text("Sistem çalışıyor")


@pytest.mark.parametrize("external_reference", ["  target-record-019  ", ""])
def test_record_external_reference_and_original_job_navigation(page, external_reference):
    from playwright.sync_api import expect

    job_name = f"reference-job-{uuid4().hex}"
    page.get_by_role("button", name="Yeni iş", exact=True).click()
    dialog = page.locator("#form-dialog")
    dialog.locator('[name="job_id"]').fill(job_name)
    dialog.locator('[name="workflow_name"]').fill("browser-reference-workflow")
    dialog.get_by_role("button", name="İşi oluştur").click()
    expect(page.get_by_role("heading", level=1)).to_have_text(job_name)
    original_job_url = page.url
    page.get_by_role("button", name="Kayıt ekle", exact=True).click()
    dialog.locator('[name="record_id"]').fill("reference-record")
    dialog.locator('[name="idempotency_key"]').fill(f"reference-operation-{uuid4().hex}")
    dialog.locator('[name="external_reference"]').fill(external_reference)
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith("/transactions")
    ) as sent:
        dialog.get_by_role("button", name="Kaydı ekle", exact=True).click()
    if external_reference:
        assert sent.value.post_data_json["external_reference"] == external_reference.strip()
    else:
        assert "external_reference" not in sent.value.post_data_json
    expect(dialog).not_to_be_visible()
    page.locator('[data-action="transaction"]').first.click()
    detail = page.locator("#transaction-dialog")
    expect(detail.locator(".external-reference")).to_have_text(external_reference.strip() or "—")
    detail.get_by_role("link", name="Kayıtlı olduğu işi aç").click()
    expect(detail).not_to_be_visible()
    expect(page).to_have_url(original_job_url)
    expect(page.get_by_role("heading", level=1)).to_have_text(job_name)
