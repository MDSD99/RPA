"""Real browser journeys against the harness's isolated PostgreSQL database.

The server receives the already validated test session factory and binds only an
ephemeral loopback socket. Browser requests cannot leave that local server.
"""

import socket
import time
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest
import uvicorn

from app.web.api import create_app

pytestmark = [pytest.mark.postgres, pytest.mark.browser]
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def console_url(session_factory):
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(session_factory=session_factory, start_workers=True),
            host="127.0.0.1",
            port=0,
            access_log=False,
            log_level="critical",
        )
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, "Isolated browser test server did not start."
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            assert not thread.is_alive(), "Isolated browser test server did not shut down."


@pytest.fixture
def page(console_url, monkeypatch):
    # Optional dependency is imported only when browser tests are selected.
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(PROJECT_ROOT / ".browser-cache"))
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        str(PROJECT_ROOT / ".browser-cache/system/usr/lib/x86_64-linux-gnu"),
        prepend=":",
    )
    javascript_errors = []
    csp_errors = []
    external_requests = []

    def restrict_network(route):
        if route.request.url.startswith(console_url + "/"):
            route.continue_()
        else:
            external_requests.append(route.request.resource_type)
            route.abort()

    def inspect_console(message):
        if message.type == "error" and "content security policy" in message.text.lower():
            csp_errors.append(message.text)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="tr-TR",
            timezone_id="UTC",
            reduced_motion="reduce",
        )
        context.route("**/*", restrict_network)
        browser_page = context.new_page()
        browser_page.set_default_timeout(10_000)
        browser_page.on("pageerror", lambda error: javascript_errors.append(str(error)))
        browser_page.on("console", inspect_console)
        browser_page.goto(console_url)
        try:
            yield browser_page
        finally:
            context.close()
            browser.close()
            assert not javascript_errors, f"Browser JavaScript errors: {javascript_errors}"
            assert not csp_errors, f"Browser CSP errors: {csp_errors}"
            assert not external_requests, "Browser attempted to contact an external resource."


def _add_record(page, record_id, key, attempts=3):
    page.locator('[data-action="add-record"]').click()
    dialog = page.locator("#form-dialog")
    dialog.locator('[name="record_id"]').fill(record_id)
    dialog.locator('[name="idempotency_key"]').fill(key)
    dialog.locator('[name="max_attempts"]').fill(str(attempts))
    dialog.get_by_role("button", name="Kaydı ekle").click()


def _start_demo(page, scenario, record_count=1):
    from playwright.sync_api import expect

    page.get_by_role("button", name="Demo başlat", exact=True).click()
    dialog = page.locator("#form-dialog")
    dialog.locator(f'[name="scenario"][value="{scenario}"]').check()
    dialog.locator('[name="record_count"]').fill(str(record_count))
    dialog.get_by_role("button", name="Demoyu çalıştır").click()
    expect(dialog).not_to_be_visible()
    expect(page.locator(".detail-summary")).to_be_visible()


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth && "
        "document.body.scrollWidth <= window.innerWidth"
    ), "The console page overflows the viewport horizontally."


def test_custom_job_records_idempotency_and_cancel_confirmation(page):
    from playwright.sync_api import expect

    run_id = uuid4().hex
    job_name = f"browser-job-{run_id}"
    operation_key = f"browser-operation-{run_id}"
    page.get_by_role("button", name="Yeni iş", exact=True).click()
    dialog = page.locator("#form-dialog")
    dialog.locator('[name="job_id"]').fill(job_name)
    dialog.locator('[name="workflow_name"]').fill("browser-unregistered-workflow")
    dialog.get_by_role("button", name="İşi oluştur").click()
    expect(page.get_by_role("heading", level=1)).to_have_text(job_name)
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Bekliyor")

    _add_record(page, "browser-record-001", operation_key, attempts=2)
    expect(dialog).not_to_be_visible()
    expect(page.locator("tbody tr")).to_have_count(1)

    # Repeating the same operation resolves to the original row.
    _add_record(page, "browser-record-001", operation_key, attempts=2)
    expect(dialog).not_to_be_visible()
    expect(page.locator("tbody tr")).to_have_count(1)

    # Reusing the key for another record must surface the conflict in the form.
    _add_record(page, "browser-record-002", operation_key, attempts=2)
    expect(dialog.locator("#form-error")).to_have_text(
        "İşlem anahtarı farklı bir kayıtla eşleşiyor."
    )
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Vazgeç", exact=True).click()
    expect(page.locator("tbody tr")).to_have_count(1)

    page.locator('[data-action="start"]').click()
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Çalışıyor")
    page.locator('[data-action="refresh"]').click()
    expect(page.locator("tbody .status-badge")).to_have_text("Bekliyor")
    expect(page.locator(".callout")).to_contain_text("akışa özel otomasyon worker")

    page.locator('[data-action="cancel"]').click()
    expect(dialog.get_by_role("heading")).to_have_text("İş iptal edilsin mi?")
    dialog.get_by_role("button", name="Vazgeç", exact=True).click()
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Çalışıyor")
    page.locator('[data-action="cancel"]').click()
    dialog.get_by_role("button", name="Evet, işi iptal et").click()
    expect(page.locator(".detail-summary .status-badge")).to_have_text("İptal edildi")
    expect(page.locator("tbody .status-badge")).to_have_text("Atlandı")
    expect(page.locator('[data-action="start"]')).to_have_count(0)
    _assert_no_horizontal_overflow(page)


def test_duplicate_record_in_another_job_opens_the_existing_job(page, console_url):
    from playwright.sync_api import expect

    run_id = uuid4().hex
    operation_key = f"browser-cross-job-operation-{run_id}"
    workflow_name = "browser-cross-job-workflow"
    first_job_name = f"browser-first-job-{run_id}"
    second_job_name = f"browser-second-job-{run_id}"
    job_ids = []
    dialog = page.locator("#form-dialog")

    for job_name in (first_job_name, second_job_name):
        page.locator('[data-nav="jobs"]').click()
        expect(page.get_by_role("heading", level=1)).to_have_text("İşler")
        page.get_by_role("button", name="Yeni iş", exact=True).click()
        dialog.locator('[name="job_id"]').fill(job_name)
        dialog.locator('[name="workflow_name"]').fill(workflow_name)
        dialog.get_by_role("button", name="İşi oluştur").click()
        expect(page.get_by_role("heading", level=1)).to_have_text(job_name)
        job_ids.append(page.url.split("#jobs/", 1)[1])
        if job_name == first_job_name:
            _add_record(page, "browser-shared-record", operation_key)
            expect(dialog).not_to_be_visible()
            expect(page.locator("tbody tr")).to_have_count(1)

    _add_record(page, "browser-shared-record", operation_key)
    expect(dialog).not_to_be_visible()
    expect(page.locator("#toasts")).to_contain_text(
        "Bu kayıt başka bir işte zaten mevcut. Kayıtlı olduğu iş açıldı."
    )
    expect(page).to_have_url(f"{console_url}/#jobs/{job_ids[0]}")
    expect(page.get_by_role("heading", level=1)).to_have_text(first_job_name)
    expect(page.locator("tbody tr")).to_have_count(1)

    for job_id, expected_count in zip(job_ids, (1, 0), strict=True):
        job_response = page.request.get(f"{console_url}/api/jobs/{job_id}")
        assert job_response.status == 200
        assert job_response.json()["job"]["total_records"] == expected_count
        records_response = page.request.get(f"{console_url}/api/jobs/{job_id}/transactions")
        assert records_response.status == 200
        assert records_response.json()["total"] == expected_count


@pytest.mark.parametrize("scenario,attempts", [("success", 1), ("retry", 2)])
def test_demo_completes_and_displays_attempts_and_result(page, scenario, attempts):
    from playwright.sync_api import expect

    _start_demo(page, scenario)
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Tamamlandı", timeout=20_000)
    expect(page.locator("tbody .status-badge")).to_have_text("Başarılı")
    page.locator('[data-action="transaction"]').first.click()
    detail = page.locator("#transaction-dialog")
    expect(detail).to_be_visible()
    expect(detail.locator(".detail-meta")).to_contain_text(f"{attempts} / 3")
    expect(detail.locator(".checkpoints-list")).to_contain_text('"step": "completed"')
    expect(detail).to_contain_text('"external_action_performed": false')
    if scenario == "retry":
        expect(detail).to_contain_text("DEMO_SAFE_RETRY")


def test_recovery_reconciliation_desktop_and_mobile_console(page):
    from playwright.sync_api import expect

    _start_demo(page, "recovery")
    expect(page.locator("tbody .status-badge")).to_have_text("İnceleme bekliyor", timeout=20_000)
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Çalışıyor")
    page.locator('[data-action="transaction"]').first.click()
    detail = page.locator("#transaction-dialog")
    continue_button = detail.locator('[data-outcome="not_applied"]')
    expect(continue_button).to_be_disabled()
    expect(detail.locator(".checkpoints-list")).to_contain_text('"step": "prepared"')
    detail.locator("#reconcile-confirm").check()
    expect(continue_button).to_be_enabled()
    continue_button.click()
    expect(detail).not_to_be_visible()
    expect(page.locator(".detail-summary .status-badge")).to_have_text("Tamamlandı", timeout=20_000)
    expect(page.locator("tbody .status-badge")).to_have_text("Başarılı")
    page.locator('[data-action="transaction"]').first.click()
    expect(detail.locator(".detail-meta")).to_contain_text("2 / 3")
    expect(detail.locator(".timeline")).to_contain_text("işlem gerçekleşmedi")
    expect(detail.locator(".checkpoints-list")).to_contain_text('"step": "completed"')
    detail.locator('[data-action="close-transaction"]').click()

    page.locator('[data-nav="overview"]').click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Operasyon merkezi")
    expect(page.locator("#connection-status")).to_contain_text("Sistem çalışıyor")
    # Two demo workers and two workers for the reusable visual-flow runtime.
    expect(page.locator(".worker-mini")).to_have_count(4)
    _assert_no_horizontal_overflow(page)
    artifact_dir = PROJECT_ROOT / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    page.screenshot(path=str(artifact_dir / "console-desktop.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    menu = page.locator("#menu-button")
    expect(menu).to_be_visible()
    for route, heading in (
        ("jobs", "İşler"),
        ("queue", "İşlem kuyruğu"),
        ("workers", "Worker'lar"),
        ("overview", "Operasyon merkezi"),
    ):
        menu.click()
        expect(menu).to_have_attribute("aria-expanded", "true")
        page.locator(f'[data-nav="{route}"]').click()
        expect(page.get_by_role("heading", level=1)).to_have_text(heading)
        expect(menu).to_have_attribute("aria-expanded", "false")
        _assert_no_horizontal_overflow(page)
    page.screenshot(path=str(artifact_dir / "console-mobile.png"), full_page=True)
