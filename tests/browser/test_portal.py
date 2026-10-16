"""Real customer and admin journeys on an isolated loopback portal and PostgreSQL.

Only the test account's encrypted outbox is read to obtain its verification link.
Mail transport itself is covered by the separate loopback SMTP integration tests.
"""

import json
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pyotp
import pytest
import uvicorn
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.portal_config import PortalSettings
from app.identity.models import Account, EmailOutbox, EmailToken, IdentityAudit
from app.identity.service import IdentityService
from app.web.portal import create_portal_app

pytestmark = [pytest.mark.postgres, pytest.mark.browser]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Browser portal test passphrase 2026!"
DESKTOP = {"width": 1440, "height": 1000}
MOBILE = {"width": 390, "height": 844}


@dataclass(frozen=True)
class PortalServer:
    url: str
    key: str = field(repr=False)
    identity: IdentityService = field(repr=False)


@pytest.fixture(scope="module")
def portal_server(session_factory):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        key = Fernet.generate_key().decode("ascii")
        settings = PortalSettings(url, key, "test")
        identity = IdentityService(session_factory, secret_key=key, public_url=url)
        server = uvicorn.Server(
            uvicorn.Config(
                create_portal_app(
                    session_factory=session_factory, settings=settings, start_workers=True
                ),
                host="127.0.0.1",
                port=0,
                access_log=False,
                log_level="critical",
            )
        )
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, "Isolated portal browser test server did not start."
            yield PortalServer(url, key, identity)
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            assert not thread.is_alive(), "Isolated portal browser test server did not shut down."


@pytest.fixture
def portal_pages(portal_server, monkeypatch):
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
        if route.request.url.startswith(portal_server.url + "/"):
            route.continue_()
        else:
            external_requests.append(route.request.resource_type)
            route.abort()

    def inspect_console(message):
        if message.type == "error" and "content security policy" in message.text.lower():
            csp_errors.append(message.text)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        contexts = []
        pages = []
        try:
            # Customer and administrator must not share cookies or browser storage.
            for _ in range(2):
                context = browser.new_context(
                    viewport=DESKTOP,
                    locale="tr-TR",
                    timezone_id="UTC",
                    reduced_motion="reduce",
                )
                contexts.append(context)
                context.route("**/*", restrict_network)
                page = context.new_page()
                page.set_default_timeout(10_000)
                page.on("pageerror", lambda error: javascript_errors.append(str(error)))
                page.on("console", inspect_console)
                pages.append(page)
            yield tuple(pages)
        finally:
            for context in contexts:
                context.close()
            browser.close()
            assert not javascript_errors, f"Browser JavaScript errors: {javascript_errors}"
            assert not csp_errors, f"Browser CSP errors: {csp_errors}"
            assert not external_requests, "Browser attempted to contact an external resource."


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth && "
        "document.body.scrollWidth <= window.innerWidth"
    ), "The portal page overflows the viewport horizontally."


def _screenshot(page, filename, *, full_page=True):
    artifacts = PROJECT_ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    page.screenshot(path=str(artifacts / filename), full_page=full_page)


def _verification_token(session_factory, portal_server, email):
    with session_factory() as session:
        row = session.scalar(
            select(EmailOutbox)
            .join(Account)
            .where(Account.email == email, EmailOutbox.kind == "verify_email")
        )
        assert row is not None and row.status == "pending"
        payload = Fernet(portal_server.key.encode("ascii")).decrypt(
            row.payload_ciphertext.encode("ascii")
        )
        return json.loads(payload)["token"]


def test_customer_registration_verification_and_admin_approval(
    portal_pages, portal_server, session_factory
):
    from playwright.sync_api import expect

    customer, admin = portal_pages
    customer_email = f"browser-customer-{uuid4().hex}@example.com"
    admin_email = f"browser-admin-{uuid4().hex}@example.com"

    customer.goto(portal_server.url)
    expect(customer).to_have_title("MultiRPA — İşler aksın.")
    _assert_no_horizontal_overflow(customer)
    _screenshot(customer, "multirpa-marketing.png")
    customer.get_by_role("link", name="Hesap oluştur", exact=True).click()
    expect(customer).to_have_url(portal_server.url + "/account?mode=register")
    expect(customer.locator("#register-view")).to_be_visible()
    customer.get_by_label("Ad soyad", exact=True).fill("Deniz Tarayıcı")
    customer.locator("#register-email").fill(customer_email)
    customer.locator("#register-password").fill(PASSWORD)
    customer.locator("#register-form").get_by_role("button", name="Hesap oluştur").click()
    expect(customer.locator("#login-view")).to_be_visible()
    expect(customer.locator("#notice")).to_contain_text("e-posta gönderim sırasına alındı")
    expect(customer.locator("#login-email")).to_have_value(customer_email)
    customer.locator("#login-password").fill(PASSWORD)
    customer.locator("#login-form").get_by_role("button", name="Giriş yap").click()
    expect(customer.locator("#account-state-label")).to_have_text("E-posta doğrulaması bekleniyor")
    expect(customer.locator("#email-progress-label")).to_have_text("Bekliyor")
    expect(customer.locator("#approval-progress-label")).to_have_text("Bekliyor")

    token = _verification_token(session_factory, portal_server, customer_email)
    customer.goto(portal_server.url + "/verify-email#token=" + token)
    expect(customer.locator("#verify-view")).to_be_visible()
    expect(customer).to_have_url(portal_server.url + "/verify-email")
    # Opening a mail link (including by a scanner) must not consume its token.
    with session_factory() as session:
        account = session.scalar(select(Account).where(Account.email == customer_email))
        assert account is not None and not account.email_verified
        verification = session.scalar(
            select(EmailToken).where(
                EmailToken.user_id == account.id, EmailToken.purpose == "verify_email"
            )
        )
        assert verification is not None and verification.consumed_at is None
    customer.get_by_role("button", name="E-posta adresimi doğrula", exact=True).click()
    expect(customer.locator("#login-view")).to_be_visible()
    expect(customer.locator("#notice")).to_contain_text("E-posta adresiniz doğrulandı")
    customer.locator("#login-email").fill(customer_email)
    customer.locator("#login-password").fill(PASSWORD)
    customer.locator("#login-form").get_by_role("button", name="Giriş yap").click()
    expect(customer.locator("#account-state-label")).to_have_text("Yönetici onayı bekleniyor")
    expect(customer.locator("#email-progress-label")).to_have_text("Tamamlandı")
    expect(customer.locator("#approval-progress-label")).to_have_text("Bekliyor")
    assert customer.request.get(portal_server.url + "/api/admin/me").status == 401

    secret = pyotp.random_base32()
    portal_server.identity.bootstrap_admin(admin_email, PASSWORD, "Portal Yöneticisi", secret)
    admin.goto(portal_server.url + "/admin")
    expect(admin.locator("#admin-login-view")).to_be_visible()
    admin.get_by_label("E-posta adresi", exact=True).fill(admin_email)
    admin.get_by_label("Parola", exact=True).fill(PASSWORD)
    admin.get_by_label("Doğrulama kodu", exact=True).fill(pyotp.TOTP(secret).now())
    admin.get_by_role("button", name="Yönetim alanına giriş yap").click()
    expect(admin.locator("#admin-accounts-view")).to_be_visible()
    row = admin.locator("#accounts-body tr").filter(has_text=customer_email)
    expect(row).to_contain_text("Doğrulandı")
    expect(row).to_contain_text("Onay bekliyor")
    assert admin.request.get(portal_server.url + "/api/auth/me").status == 401
    _assert_no_horizontal_overflow(admin)
    _screenshot(admin, "multirpa-admin.png", full_page=False)
    admin.set_viewport_size(MOBILE)
    _assert_no_horizontal_overflow(admin)
    _screenshot(admin, "multirpa-admin-mobile.png", full_page=False)
    row.get_by_role("button", name=f"{customer_email}: Onayla", exact=True).click()
    expect(admin.locator("#decision-panel")).to_be_visible()
    _assert_no_horizontal_overflow(admin)
    reason = "Başvuru ve kurum bilgileri incelendi."
    admin.get_by_label("İşlem gerekçesi", exact=True).fill(reason)
    admin.locator("#confirm-decision").click()
    expect(admin.locator("#decision-panel")).not_to_be_visible()
    expect(row).to_contain_text("Onaylandı")
    expect(row.get_by_role("button", name=f"{customer_email}: Askıya al")).to_be_visible()

    customer.get_by_role("button", name="Durumu yenile", exact=True).click()
    expect(customer.locator("#account-state-label")).to_have_text("Hesabınız onaylandı")
    expect(customer.locator("#approval-progress-label")).to_have_text("Onaylandı")
    expect(customer.locator("#start-trial-button")).to_be_visible()
    customer.locator("#start-trial-button").click()
    expect(customer.locator("#trial-title")).to_have_text("Demo sürümünüz etkin.")
    expect(customer.locator("#trial-quota")).to_contain_text("0 / 3")
    customer.locator("#workspace-link").click()
    expect(customer.locator(".flow-hero")).to_be_visible()
    expect(customer.locator('[data-action="demo"]')).not_to_be_visible()
    customer.locator('[data-flow-action="new"]:not([data-template])').first.click()
    expect(customer.locator("#flow-name")).to_be_visible()
    customer.locator("#flow-name").fill("Onaylı müşterinin ilk akışı")
    customer.locator('[data-flow-action="save"]').click()
    expect(customer.locator("#flow-save-state")).to_have_text("Sürüm 1 · Kaydedildi")
    customer.locator('[data-flow-action="run"]').click()
    customer.locator("#flow-run-form").get_by_role(
        "button", name="Çalıştırmayı başlat", exact=True
    ).click()
    expect(customer.locator(".detail-summary .status-badge")).to_have_text(
        "Tamamlandı", timeout=20_000
    )
    _screenshot(customer, "multirpa-customer-workspace.png")
    customer.locator('[data-action="secrets"]').click()
    expect(customer.locator("#credential-form")).to_be_visible()
    customer.locator("#credential-reference").fill("ornek_api")
    customer.locator("#credential-authorization").fill("Bearer browser-private-fixture")
    customer.locator("#credential-form").get_by_role(
        "button", name="Bağlantıyı kaydet", exact=True
    ).click()
    expect(customer.locator(".credential-list")).to_contain_text("ornek_api")
    assert "browser-private-fixture" not in customer.locator("#dialog-content").inner_text()
    expect(customer.locator("#credential-authorization")).to_have_value("")
    customer.locator('[data-action="close-dialog"]').click()
    customer.goto(portal_server.url + "/account")
    expect(customer.locator("#trial-quota")).to_contain_text("1 / 3")
    customer.set_viewport_size(MOBILE)
    _assert_no_horizontal_overflow(customer)
    with session_factory() as session:
        account = session.scalar(select(Account).where(Account.email == customer_email))
        actor = session.scalar(select(Account).where(Account.email == admin_email))
        audit = session.scalar(
            select(IdentityAudit).where(
                IdentityAudit.user_id == account.id, IdentityAudit.action == "ACCOUNT_APPROVE"
            )
        )
        assert account.email_verified and account.approval == "approved"
        assert audit is not None and audit.actor_id == actor.id and audit.reason == reason

    admin.get_by_role("button", name="Çıkış yap", exact=True).click()
    expect(admin.locator("#admin-login-view")).to_be_visible()
    customer.get_by_role("button", name="Durumu yenile", exact=True).click()
    expect(customer.locator("#account-state-label")).to_have_text("Hesabınız onaylandı")
    customer.get_by_role("button", name="Çıkış yap", exact=True).click()
    expect(customer.locator("#login-view")).to_be_visible()


def test_marketing_login_and_recovery_pages_fit_mobile(portal_pages, portal_server):
    from playwright.sync_api import expect

    customer, admin = portal_pages
    customer.set_viewport_size(MOBILE)
    customer.goto(portal_server.url)
    expect(customer.get_by_role("heading", level=1)).to_contain_text("Tekrarlayan işler")
    _assert_no_horizontal_overflow(customer)
    _screenshot(customer, "multirpa-marketing-mobile.png")
    customer.get_by_role("link", name="Giriş yap", exact=True).click()
    expect(customer.locator("#login-view")).to_be_visible()
    _assert_no_horizontal_overflow(customer)
    _screenshot(customer, "multirpa-account-mobile.png")
    customer.set_viewport_size(DESKTOP)
    _assert_no_horizontal_overflow(customer)
    _screenshot(customer, "multirpa-account.png")
    customer.set_viewport_size(MOBILE)
    customer.get_by_role("button", name="Parolamı unuttum", exact=True).click()
    expect(customer.locator("#password-request-view")).to_be_visible()
    _assert_no_horizontal_overflow(customer)
    customer.get_by_role("button", name="Girişe dön", exact=True).click()
    customer.get_by_role("button", name="Hesap oluşturun", exact=True).click()
    expect(customer.locator("#register-view")).to_be_visible()
    _assert_no_horizontal_overflow(customer)

    admin.set_viewport_size(MOBILE)
    admin.goto(portal_server.url + "/admin")
    expect(admin.locator("#admin-login-view")).to_be_visible()
    _assert_no_horizontal_overflow(admin)
