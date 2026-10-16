"""Operator secret files must not be overwritten, shared or interpreted as shell code."""

import pytest
from cryptography.fernet import Fernet

from app.core.config import ConfigurationError
from app.core.portal_config import PortalSettings
from app.web.portal import create_portal_app
from scripts import portal


def test_init_keeps_key_private_and_never_replaces_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env.portal"
    monkeypatch.setattr(portal, "PORTAL_ENV", path)
    portal.initialize()
    original = path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    key = dict(line.split("=", 1) for line in original.splitlines() if "=" in line)[
        "MULTIRPA_IDENTITY_KEY"
    ]
    Fernet(key.encode())
    assert key not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        portal.initialize()
    assert path.read_text() == original


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "APP_ENV=development\nDB_HOST=127.0.0.1\nDB_PORT=55432\nDB_NAME=rpa_development\nDB_USER=fake_user\nDB_PASSWORD=fake_password\nDB_SSLMODE=disable\n"
    )
    env.chmod(0o600)
    path = tmp_path / ".env.portal"
    monkeypatch.setattr(portal, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(portal, "PORTAL_ENV", path)
    return path


def test_portal_env_is_data_and_does_not_execute_shell(local_config, tmp_path):
    sentinel = tmp_path / "never-created"
    password = f"$(touch {sentinel})"
    local_config.write_text(f"MULTIRPA_SMTP_PASSWORD={password}\n")
    local_config.chmod(0o600)
    assert portal.portal_environment()["MULTIRPA_SMTP_PASSWORD"] == password
    assert not sentinel.exists()


def test_adapter_policy_is_loaded_only_from_private_operator_settings(local_config, monkeypatch):
    monkeypatch.setenv("RPA_HTTP_ALLOWED_HOSTS", "unreviewed.example.com")
    monkeypatch.setenv("MULTIRPA_WORKSPACE_ROOT", "/unreviewed")
    local_config.write_text(
        "RPA_HTTP_ALLOWED_HOSTS=api.example.com\n"
        "RPA_MAIL_ALLOWED_HOSTS=smtp.example.com\n"
        "MULTIRPA_WORKSPACE_ROOT=/var/lib/multirpa/workspaces\n"
    )
    local_config.chmod(0o600)
    settings = portal.portal_environment()
    assert settings["RPA_HTTP_ALLOWED_HOSTS"] == "api.example.com"
    assert settings["RPA_MAIL_ALLOWED_HOSTS"] == "smtp.example.com"
    assert settings["MULTIRPA_WORKSPACE_ROOT"] == "/var/lib/multirpa/workspaces"


@pytest.mark.parametrize("unsafe", ["shared", "symlink", "duplicate", "unsupported"])
def test_portal_env_rejects_unsafe_file_or_keys(local_config, unsafe):
    local_config.write_text("MULTIRPA_PUBLIC_URL=http://127.0.0.1:8770\n")
    local_config.chmod(0o600)
    if unsafe == "shared":
        local_config.chmod(0o644)
    elif unsafe == "symlink":
        target = local_config.with_name("secret-target")
        local_config.rename(target)
        local_config.symlink_to(target)
    elif unsafe == "duplicate":
        local_config.write_text("MULTIRPA_IDENTITY_KEY=a\nMULTIRPA_IDENTITY_KEY=b\n")
    else:
        local_config.write_text("UNSUPPORTED_SETTING=value\n")
    with pytest.raises(ConfigurationError):
        portal.portal_environment()


def test_production_process_cannot_bypass_guard_with_development_settings(monkeypatch):
    settings = PortalSettings("http://127.0.0.1:8770", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ConfigurationError, match="production portal settings"):
        create_portal_app(settings=settings)


def test_local_port_must_match_generated_links():
    settings = PortalSettings("http://127.0.0.1:8770", Fernet.generate_key().decode())
    portal.validate_bind_port(settings, 8770)
    with pytest.raises(ConfigurationError, match="same local port"):
        portal.validate_bind_port(settings, 8771)
