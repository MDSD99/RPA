"""Browser and configuration boundaries for the isolated onboarding portal."""

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import ConfigurationError
from app.core.portal_config import PortalSettings, SMTPSettings
from app.entitlements.crypto import encode
from app.web.portal import create_portal_app

_PASSWORD = "correct horse battery staple"
_WRITE_HEADERS = {"X-RPA-Request": "1"}


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example.com",
        "//example.com",
        "ftp://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com/?redirect=elsewhere",
        "https://example.com/#fragment",
        "https://example.com:0",
        "https://example.com:bad",
        "https://",
    ],
)
def test_portal_rejects_untrusted_public_urls(url):
    with pytest.raises(ConfigurationError):
        PortalSettings(public_url=url, secret_key=Fernet.generate_key().decode())


def test_production_requires_dedicated_signing_key():
    with pytest.raises(ConfigurationError, match="dedicated license signing key"):
        PortalSettings(
            public_url="https://app.example.com",
            secret_key=Fernet.generate_key().decode(),
            app_env="production",
        )


def test_production_portal_requires_https_and_separate_workers():
    key = encode(b"a" * 32)
    with pytest.raises(ConfigurationError):
        PortalSettings("http://127.0.0.1:8770", Fernet.generate_key().decode(), "production", key)
    settings = PortalSettings(
        "https://app.example.com", Fernet.generate_key().decode(), "production", key
    )
    assert settings.secure_cookie
    with pytest.raises(ConfigurationError, match="separate execution worker"):
        create_portal_app(settings=settings, start_workers=True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"security": "local", "host": "mail.example.com"},
        {"security": "none"},
        {"host": "mail.example.com\r\nInjected"},
        {"port": 0},
        {"port": 65536},
        {"port": True},
        {"sender": "not-an-email"},
        {"username": "operator", "password": ""},
        {"username": "", "password": "secret"},
        {"timeout": 0},
        {"timeout": 61},
    ],
)
def test_smtp_rejects_unsafe_configuration(overrides):
    settings = {"host": "mail.example.com", "port": 587, "sender": "hello@example.com"}
    settings.update(overrides)
    with pytest.raises(ConfigurationError):
        SMTPSettings(**settings)


@pytest.fixture
def portal(session_factory):
    # Every test has its own rate-limit HMAC namespace while sharing only the
    # explicitly isolated PostgreSQL fixture.
    settings = PortalSettings(
        public_url="HTTPS://app.example.com",
        secret_key=Fernet.generate_key().decode(),
        app_env="test",
    )
    application = create_portal_app(session_factory=session_factory, settings=settings)
    with TestClient(application, base_url="https://app.example.com") as client:
        yield client, application.state.portal


def _login_customer(client, services):
    email = f"portal-security-{uuid4().hex}@example.com"
    services.identity.register(email, _PASSWORD, "Portal security test")
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": _PASSWORD},
        headers=_WRITE_HEADERS,
    )
    assert response.status_code == 200
    return response


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({"Host": "attacker.example.com", **_WRITE_HEADERS}, "REQUEST_REJECTED"),
        ({"Origin": "https://attacker.example.com", **_WRITE_HEADERS}, "REQUEST_REJECTED"),
        ({"Origin": "http://app.example.com", **_WRITE_HEADERS}, "REQUEST_REJECTED"),
        ({"Sec-Fetch-Site": "cross-site", **_WRITE_HEADERS}, "REQUEST_REJECTED"),
        ({}, "REQUEST_REJECTED"),
    ],
)
def test_portal_rejects_cross_origin_writes_and_missing_application_header(portal, headers, code):
    client, _ = portal
    response = client.post(
        "/api/auth/password/request", json={"email": "nobody@example.com"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == code
    assert response.headers["cache-control"] == "no-store"


def test_portal_requires_json_and_allows_external_marketing_navigation(portal):
    client, _ = portal
    response = client.post(
        "/api/auth/password/request",
        content="email=nobody%40example.com",
        headers={**_WRITE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415
    response = client.get("/", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200
    assert "MultiRPA" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    response = client.get("/api/auth/me", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_uppercase_https_still_sets_host_only_secure_session_cookie(portal):
    client, services = portal
    response = _login_customer(client, services)
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("__host-multirpa_customer=")
    assert "; secure" in cookie
    assert "; httponly" in cookie
    assert "; samesite=strict" in cookie
    assert "; path=/" in cookie
    assert "domain=" not in cookie
    assert "token" not in response.json()
    assert client.get("/api/auth/me").status_code == 200


def test_logout_requires_session_bound_csrf_and_revokes_cookie(portal):
    client, services = portal
    login = _login_customer(client, services)
    csrf = login.json()["csrf_token"]
    raw = client.cookies.get(services.cookie_name("customer"))
    for supplied in (None, "0" * 64, services.csrf(raw, "admin")):
        headers = dict(_WRITE_HEADERS)
        if supplied is not None:
            headers["X-CSRF-Token"] = supplied
        response = client.post("/api/auth/logout", json={}, headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_REJECTED"
        assert client.get("/api/auth/me").status_code == 200
    response = client.post(
        "/api/auth/logout", json={}, headers={**_WRITE_HEADERS, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.cookies.get(services.cookie_name("customer")) is None


def test_customer_cookie_cannot_be_relabelled_as_admin_session(portal):
    client, services = portal
    _login_customer(client, services)
    raw = client.cookies.get(services.cookie_name("customer"))
    # Direct clients can forge cookie names, but cannot change the audience
    # bound to the token by the database.
    response = client.get(
        "/api/admin/accounts",
        headers={"Cookie": f"{services.cookie_name('admin')}={raw}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_EXPIRED"


@pytest.mark.parametrize(
    "aliases",
    [
        ("\u00fcser@example.com", "u\u0308ser@example.com"),
        ("user@b\u00fccher.example.com", "user@xn--bcher-kva.example.com"),
    ],
)
def test_canonical_email_aliases_share_the_same_mail_throttle(portal, aliases):
    client, _ = portal
    for email in (aliases[0], aliases[1], aliases[0], aliases[1], aliases[0]):
        response = client.post(
            "/api/auth/verification/request", json={"email": email}, headers=_WRITE_HEADERS
        )
        assert response.status_code == 202
    response = client.post(
        "/api/auth/password/request", json={"email": aliases[1]}, headers=_WRITE_HEADERS
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_forwarded_for_header_cannot_reset_the_request_limit(portal):
    client, services = portal
    # Prime the same persistent bucket used by requests, without manufacturing
    # many password hashes or hundreds of redundant HTTP requests.
    for _ in range(120):
        assert services.limiter.allow("request", "testclient", limit=120, seconds=60)
    response = client.get("/api/auth/me", headers={"X-Forwarded-For": "203.0.113.51"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
