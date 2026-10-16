"""Production profiles cannot inject directives or replace configs/secrets."""

import json

import pytest

from scripts.deployment import DeploymentError, DeploymentProfile, generate


@pytest.mark.parametrize(
    "root_url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?x=1",
        "https://example.com#x",
        "https://example.com:8443",
        "https://example.com;include-conf",
        "https://127.0.0.1",
        "https://example.com\nserver_name injected;",
    ],
)
def test_invalid_origin_cannot_inject_nginx_directives(root_url):
    with pytest.raises(DeploymentError):
        DeploymentProfile(root_url, "https://app.example.com")


@pytest.mark.parametrize(
    "app_url", ["https://example.com", "https://app.example.org", "https://appexample.com"]
)
def test_application_origin_requires_actual_root_subdomain(app_url):
    with pytest.raises(DeploymentError):
        DeploymentProfile("https://example.com", app_url)


@pytest.mark.parametrize(
    "changes",
    [
        {"user": "root"},
        {"user": "svc\nExecStart=bad"},
        {"install_dir": "/opt/rpa;bad"},
        {"settings_dir": "/etc/multirpa\nEnvironment=bad"},
        {"install_dir": "/opt/../etc"},
        {"port": 80},
        {"port": True},
    ],
)
def test_invalid_account_path_or_port_rejected(changes):
    with pytest.raises(DeploymentError):
        DeploymentProfile("https://example.com", "https://app.example.com", **changes)


def test_profile_separates_root_static_app_proxy_and_private_processes(tmp_path):
    profile = DeploymentProfile("https://example.com", "https://app.example.com")
    output = generate(profile, tmp_path / "profile")
    nginx = (output / "nginx.conf").read_text()
    assert "default_server" not in nginx.replace(
        "# Rendered for explicitly supplied MultiRPA domains. No default_server is changed.", ""
    )
    assert "proxy_pass http://127.0.0.1:8770" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in nginx
    assert 'proxy_set_header Forwarded "";' in nginx
    assert "TLSv1.2 TLSv1.3" in nginx
    assert "Strict-Transport-Security" in nginx
    assert "proxy_next_upstream off;" in nginx
    api, execution, mail = [
        (output / "systemd" / f"multirpa-{name}.service").read_text()
        for name in ("api", "execution", "mail")
    ]
    assert "scripts.portal serve --port 8770" in api
    assert "execution-worker" in execution
    assert "mail-worker" in mail
    assert "mail.env" not in api
    assert "mail.env" not in execution
    assert "mail.env" in mail
    for service in (api, execution, mail):
        assert "User=multirpa" in service
        assert "NoNewPrivileges=true" in service
        assert "ProtectSystem=strict" in service
        assert "KillSignal=SIGTERM" in service
    manifest = json.loads((output / "profile.json").read_text())
    assert manifest["deployed"] is False


def test_marketing_export_uses_absolute_app_origin_and_brand_assets(tmp_path):
    output = generate(
        DeploymentProfile("https://example.com", "https://app.example.com"), tmp_path / "profile"
    )
    source = (output / "marketing/index.html").read_text()
    assert "https://app.example.com/account?mode=register" in source
    assert "__APP_URL__" not in source
    assert "__PRODUCT_NAME__" not in source
    assert "MultiRPA" in source
    assert (output / "marketing/static/brand.svg").is_file()
    assert (output / "marketing/static/marketing.css").is_file()


def test_profile_generation_never_overwrites_existing_secrets(tmp_path):
    profile = DeploymentProfile("https://example.com", "https://app.example.com")
    output = generate(profile, tmp_path / "profile")
    secret = output / "portal.env.example"
    secret.write_text("existing operator secret")
    with pytest.raises(FileExistsError):
        generate(profile, output)
    assert secret.read_text() == "existing operator secret"
    for filename in ("database.env.example", "portal.env.example", "mail.env.example"):
        assert (output / filename).stat().st_mode & 0o777 == 0o600
