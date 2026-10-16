"""Safety boundaries of the disposable PostgreSQL runner; no Docker is contacted."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_postgres.py"
SPEC = importlib.util.spec_from_file_location("rpa_test_harness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_production_environment_is_rejected_before_docker(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(harness.sys, "argv", [str(SCRIPT)])
    docker_lookup = Mock(side_effect=AssertionError("Docker must not be contacted"))
    monkeypatch.setattr(harness.shutil, "which", docker_lookup)
    assert harness.main() == 2
    docker_lookup.assert_not_called()
    assert "production" in capsys.readouterr().err


def test_inherited_database_configuration_is_not_a_test_destination(monkeypatch):
    inherited = {
        "APP_ENV": "development",
        "DB_HOST": "unrelated-db.example.invalid",
        "DB_PORT": "5432",
        "DB_NAME": "unrelated_database",
        "DB_USER": "FAKE_OLD_USER",
        "DB_PASSWORD": "FAKE_OLD_PASSWORD",
        "DB_SSLROOTCERT": "/not/the/test/ca",
        "PGHOST": "unrelated-libpq.example.invalid",
        "POSTGRES_PASSWORD": "FAKE_OLD_CONTAINER_PASSWORD",
        "DATABASE_URL": "postgresql://FAKE_USER:FAKE_PASSWORD@invalid/unused",
        "SQLALCHEMY_DATABASE_URI": "postgresql://invalid/unused",
        "RPA_TEST_DB_CONFIRM": "unrelated_database",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(harness.secrets, "token_urlsafe", lambda size: "FAKE_NEW_PASSWORD")
    env, secrets = harness.isolated_environment("abcd1234")
    assert env["APP_ENV"] == "test"
    assert env["DB_HOST"] == "127.0.0.1"
    assert env["DB_NAME"] == env["RPA_TEST_DB_CONFIRM"] == "rpa_test_abcd1234"
    assert env["DB_PASSWORD"] == env["POSTGRES_PASSWORD"] == "FAKE_NEW_PASSWORD"
    assert env["DB_SSLMODE"] == "disable"
    for excluded in (
        "DB_PORT",
        "DB_SSLROOTCERT",
        "PGHOST",
        "DATABASE_URL",
        "SQLALCHEMY_DATABASE_URI",
    ):
        assert excluded not in env
    assert "FAKE_NEW_PASSWORD" in secrets
    # The caller's existing environment remains unchanged.
    assert harness.os.environ["DB_NAME"] == "unrelated_database"


@pytest.mark.parametrize(
    "metadata",
    [
        "a" * 64 + " production own-run",
        "a" * 64 + " isolated-test another-run",
        "short-id isolated-test own-run",
        "a" * 64,
    ],
)
def test_cleanup_refuses_foreign_or_unverified_container(monkeypatch, metadata):
    docker = Mock(return_value=metadata)
    monkeypatch.setattr(harness, "docker", docker)
    assert harness.cleanup_container("rpa-db-test-own-run", "own-run", {}) is False
    assert docker.call_count == 1
    assert docker.call_args.args[:2] == ("container", "inspect")


def test_cleanup_removes_only_verified_full_container_id(monkeypatch):
    container_id = "b" * 64
    docker = Mock(side_effect=[f"{container_id} isolated-test own-run", ""])
    monkeypatch.setattr(harness, "docker", docker)
    assert harness.cleanup_container("rpa-db-test-own-run", "own-run", {}) is True
    assert docker.call_count == 2
    assert docker.call_args.args == ("container", "rm", "--force", container_id)


def test_cleanup_requires_reachable_daemon_to_confirm_missing_container(monkeypatch):
    docker = Mock(side_effect=harness.HarnessError("unavailable"))
    monkeypatch.setattr(harness, "docker", docker)
    assert harness.cleanup_container("rpa-db-test-own-run", "own-run", {}) is False
    assert docker.call_count == 2
    assert docker.call_args.args[:2] == ("container", "ls")


def test_cleanup_accepts_verified_absence_after_failed_start(monkeypatch):
    docker = Mock(side_effect=[harness.HarnessError("missing"), ""])
    monkeypatch.setattr(harness, "docker", docker)
    assert harness.cleanup_container("rpa-db-test-own-run", "own-run", {}) is True
    assert docker.call_args.args[-1] == "label=com.rpa.test-run=own-run"


def test_docker_failure_does_not_disclose_raw_stderr(monkeypatch):
    raw_error = "FAKE_PASSWORD postgresql://FAKE_USER:FAKE_PASSWORD@invalid/unused"
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess(["docker"], 1, "", raw_error)),
    )
    with pytest.raises(harness.HarnessError) as error:
        harness.docker("run", env={})
    assert "FAKE_PASSWORD" not in str(error.value)
    assert "postgresql://" not in str(error.value)
    assert "invalid/unused" not in str(error.value)


def test_validation_output_redacts_password_user_and_database_url():
    line = (
        "password=FAKE_PASSWORD user=FAKE_USER "
        "postgresql+psycopg://FAKE_USER:FAKE_PASSWORD@invalid/unused failure\n"
    )
    result = harness.redact_output(line, ("FAKE_PASSWORD", "FAKE_USER"))
    assert "FAKE_PASSWORD" not in result
    assert "FAKE_USER" not in result
    assert "postgresql" not in result
    assert "invalid/unused" not in result
    assert "failure\n" in result


def test_docker_password_is_passed_in_environment_not_command(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess(["docker"], 0, "container-id", ""))
    monkeypatch.setattr(harness.subprocess, "run", run)
    env = {"POSTGRES_PASSWORD": "FAKE_ENV_ONLY_PASSWORD"}
    harness.docker("run", "--env", "POSTGRES_PASSWORD", env=env)
    assert "FAKE_ENV_ONLY_PASSWORD" not in run.call_args.args[0]
    assert run.call_args.kwargs["env"] == env
