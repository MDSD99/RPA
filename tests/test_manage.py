"""Runtime manager safety tests: no Docker command or real process is started."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/manage.py"
SPEC = importlib.util.spec_from_file_location("rpa_runtime_manager", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


def development_values():
    return {
        "APP_ENV": "development",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "55432",
        "DB_NAME": "rpa_development",
        "DB_USER": "FAKE_DEV_USER",
        "DB_PASSWORD": "FAKE_DEV_PASSWORD",
        "DB_SSLMODE": "disable",
    }


def test_missing_env_is_created_privately_without_replacing_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(manager.secrets, "token_hex", lambda count: "fakeuser")
    monkeypatch.setattr(manager.secrets, "token_urlsafe", lambda count: "FAKE_GENERATED_PASSWORD")
    path = tmp_path / ".env"
    assert manager.create_env_if_missing(path) is True
    assert path.stat().st_mode & 0o777 == 0o600
    values = manager.read_env(path)
    assert values["APP_ENV"] == "development"
    assert values["DB_NAME"] == "rpa_development"
    assert values["DB_PASSWORD"] == "FAKE_GENERATED_PASSWORD"
    original = path.read_bytes()
    assert manager.create_env_if_missing(path) is False
    assert path.read_bytes() == original


def test_existing_production_env_is_preserved_and_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    path = tmp_path / ".env"
    content = "APP_ENV=production\nDB_PASSWORD=FAKE_PRODUCTION_PASSWORD\n"
    path.write_text(content)
    path.chmod(0o600)
    assert manager.create_env_if_missing(path) is False
    with pytest.raises(manager.ManageError):
        manager.runtime_environment(manager.read_env(path))
    assert path.read_text() == content


def test_env_symlink_is_neither_replaced_nor_followed(tmp_path):
    target = tmp_path / "unrelated.env"
    target.write_text("DB_PASSWORD=FAKE_UNRELATED_PASSWORD\n")
    path = tmp_path / ".env"
    path.symlink_to(target)
    assert manager.create_env_if_missing(path) is False
    with pytest.raises(manager.ManageError):
        manager.read_env(path)
    assert target.read_text() == "DB_PASSWORD=FAKE_UNRELATED_PASSWORD\n"


def test_env_parser_preserves_literals_without_shell_expansion(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DB_PASSWORD='$(never-execute-this)${VARIABLE}#literal'\n")
    path.chmod(0o600)
    assert manager.read_env(path)["DB_PASSWORD"] == "$(never-execute-this)${VARIABLE}#literal"


@pytest.mark.parametrize(
    "content",
    [
        "APP_ENV=development\nAPP_ENV=production\n",
        "export APP_ENV=development\n",
        "RPA_ALLOW_PRODUCTION_MIGRATIONS=yes\n",
        "LD_PRELOAD=/unrelated/library\n",
        "DB_PASSWORD='unclosed\n",
    ],
)
def test_env_parser_rejects_unsafe_or_ambiguous_configuration(tmp_path, content):
    path = tmp_path / ".env"
    path.write_text(content)
    path.chmod(0o600)
    with pytest.raises(manager.ManageError):
        manager.read_env(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("APP_ENV", "production"),
        ("APP_ENV", "test"),
        ("DB_HOST", "production.example.invalid"),
        ("DB_NAME", "production"),
        ("DB_SSLMODE", "verify-full"),
        ("DB_PORT", "invalid"),
        ("DB_PORT", "0"),
        ("DB_PASSWORD", "REPLACE_WITH_A_SECRET"),
    ],
)
def test_runtime_refuses_any_non_development_destination(monkeypatch, key, value):
    monkeypatch.delenv("APP_ENV", raising=False)
    values = development_values()
    values[key] = value
    with pytest.raises(manager.ManageError):
        manager.runtime_environment(values)


def test_inherited_database_and_migration_configuration_is_removed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_PASSWORD", "FAKE_INHERITED_PASSWORD")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake.example.invalid/unused")
    monkeypatch.setenv("PGHOST", "fake.example.invalid")
    monkeypatch.setenv("RPA_ALLOW_PRODUCTION_MIGRATIONS", "yes")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unrelated")
    env = manager.runtime_environment(development_values())
    assert env["APP_ENV"] == "development"
    assert env["DB_PASSWORD"] == "FAKE_DEV_PASSWORD"
    assert not {
        "DATABASE_URL",
        "PGHOST",
        "RPA_ALLOW_PRODUCTION_MIGRATIONS",
        "COMPOSE_PROJECT_NAME",
    }.intersection(env)


def test_production_shell_cannot_launch_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(manager.ManageError):
        manager.runtime_environment(development_values())


def test_production_start_is_rejected_before_env_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    create = Mock()
    monkeypatch.setattr(manager, "create_env_if_missing", create)
    with pytest.raises(manager.ManageError):
        manager.start_web(8765, tmp_path / "web.json")
    create.assert_not_called()


def test_wrong_hostname_is_rejected_before_runtime_directory_or_process_changes(monkeypatch):
    monkeypatch.setattr(manager.sys, "argv", [str(SCRIPT), "start"])
    monkeypatch.setattr(manager.socket, "gethostname", lambda: "unrelated-server")
    lock = Mock()
    monkeypatch.setattr(manager, "runtime_lock", lock)
    assert manager.main() == 1
    lock.assert_not_called()


def test_busy_web_port_fails_without_attempting_to_stop_a_process(monkeypatch):
    monkeypatch.setattr(manager.socket, "socket", Mock(side_effect=OSError("already occupied")))
    stop = Mock()
    monkeypatch.setattr(manager, "stop_owned", stop)
    with pytest.raises(manager.ManageError):
        manager.check_port(8765)
    stop.assert_not_called()


def test_command_failure_does_not_expose_raw_secret_output(monkeypatch):
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 1, "", "FAKE_SECRET_PASSWORD")),
    )
    with pytest.raises(manager.ManageError) as error:
        manager.checked_command(["docker"], {}, "Development kontrolü")
    assert "FAKE_SECRET_PASSWORD" not in str(error.value)


@pytest.mark.parametrize("changed", ["start_time", "cwd", "command"])
def test_process_ownership_requires_start_time_cwd_and_exact_command(monkeypatch, changed):
    manifest = {"pid": 12345, "start_time": "123456", "port": 8765}
    identity = {
        "start_time": "123456",
        "cwd": str(manager.PROJECT_ROOT),
        "command": manager.web_command(8765),
    }
    monkeypatch.setattr(manager, "process_identity", lambda pid: identity)
    assert manager.owned_process(manifest) is True
    identity[changed] = "unrelated"
    assert manager.owned_process(manifest) is False


def test_stop_does_not_signal_an_unverified_process(monkeypatch):
    monkeypatch.setattr(manager, "owned_process", lambda manifest: False)
    open_pidfd = Mock(side_effect=AssertionError("No process operation is authorized"))
    monkeypatch.setattr(manager.os, "pidfd_open", open_pidfd)
    with pytest.raises(manager.ManageError):
        manager.stop_owned({"pid": os.getpid(), "start_time": "invalid", "port": 8765})
    open_pidfd.assert_not_called()


def test_stop_rechecks_identity_after_pidfd_open_before_signaling(monkeypatch):
    monkeypatch.setattr(manager, "owned_process", Mock(side_effect=[True, False]))
    monkeypatch.setattr(manager.os, "pidfd_open", lambda pid: 9876)
    close = Mock()
    send_signal = Mock()
    monkeypatch.setattr(manager.os, "close", close)
    monkeypatch.setattr(manager.signal, "pidfd_send_signal", send_signal)
    with pytest.raises(manager.ManageError):
        manager.stop_owned({"pid": 12345, "start_time": "123456", "port": 8765})
    send_signal.assert_not_called()
    close.assert_called_once_with(9876)


def test_compose_refuses_project_name_collision_with_unrelated_working_directory(monkeypatch):
    commands = Mock(side_effect=["a" * 12, "development /opt/unrelated-project"])
    monkeypatch.setattr(manager, "checked_command", commands)
    with pytest.raises(manager.ManageError):
        manager.validate_compose_ownership({})
    assert commands.call_count == 2
    assert all("up" not in call.args[0] for call in commands.call_args_list)


def test_manifest_contains_only_process_metadata(tmp_path):
    path = tmp_path / "web.json"
    expected = {"pid": 12345, "start_time": "987654", "port": 8765}
    manager.write_manifest(path, expected)
    assert manager.read_manifest(path) == expected
    assert path.stat().st_mode & 0o777 == 0o600


def test_port_probe_reuses_time_wait_but_refuses_an_active_listener():
    import socket

    with socket.socket() as listener, socket.socket() as client:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        with pytest.raises(manager.ManageError):
            manager.check_port(address[1])
        client.connect(address)
        accepted, _ = listener.accept()
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""
        client.shutdown(socket.SHUT_WR)
        assert accepted.recv(1) == b""
        accepted.close()
    # The active close leaves this owned server port in TIME_WAIT on Linux.
    manager.check_port(address[1])
