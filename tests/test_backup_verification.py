"""Backup verification must never restore into inherited or unowned resources."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_backup.py"
SPEC = importlib.util.spec_from_file_location("rpa_backup_verification", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


@pytest.fixture
def private_dump(tmp_path):
    source = tmp_path / "backup.dump"
    source.write_bytes(b"PGDMP private archive fixture")
    source.chmod(0o600)
    return source


def test_private_backup_is_copied_without_changing_original(private_dump, tmp_path):
    destination = tmp_path / "staged.dump"
    digest = verification.stage_backup(private_dump, destination)
    assert destination.read_bytes() == private_dump.read_bytes()
    assert len(digest) == 64
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(private_dump.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400, 0o666])
def test_unprotected_backup_is_rejected(private_dump, tmp_path, mode):
    private_dump.chmod(mode)
    with pytest.raises(verification.harness.HarnessError, match="0600"):
        verification.stage_backup(private_dump, tmp_path / "staged.dump")
    assert not (tmp_path / "staged.dump").exists()


def test_symlink_backup_is_rejected(private_dump, tmp_path):
    alias = tmp_path / "alias.dump"
    alias.symlink_to(private_dump)
    with pytest.raises(verification.harness.HarnessError, match="symlink"):
        verification.stage_backup(alias, tmp_path / "staged.dump")


def test_backup_under_symlink_directory_is_rejected(private_dump, tmp_path):
    alias = tmp_path / "linked-directory"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(verification.harness.HarnessError, match="symlink"):
        verification.stage_backup(alias / private_dump.name, tmp_path / "staged.dump")


def test_foreign_owned_backup_is_rejected(private_dump, tmp_path, monkeypatch):
    actual_owner = private_dump.stat().st_uid
    monkeypatch.setattr(verification.os, "getuid", lambda: actual_owner + 1)
    with pytest.raises(verification.harness.HarnessError, match="0600"):
        verification.stage_backup(private_dump, tmp_path / "staged.dump")


def test_plain_sql_backup_is_rejected(private_dump, tmp_path):
    private_dump.write_bytes(b"CREATE DATABASE unrelated;")
    with pytest.raises(verification.harness.HarnessError, match="custom-format"):
        verification.stage_backup(private_dump, tmp_path / "staged.dump")


def test_production_shell_is_rejected_before_docker(private_dump, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(verification.sys, "argv", [str(SCRIPT), str(private_dump)])
    docker_lookup = Mock(side_effect=AssertionError("Must not inspect Docker"))
    monkeypatch.setattr(verification.shutil, "which", docker_lookup)
    assert verification.main() == 2
    docker_lookup.assert_not_called()


def test_bad_backup_never_starts_a_container(private_dump, monkeypatch, capsys):
    private_dump.chmod(0o644)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(verification.sys, "argv", [str(SCRIPT), str(private_dump)])
    monkeypatch.setattr(verification.shutil, "which", lambda _: "/fake/docker")
    start = Mock(side_effect=AssertionError("Must not start Docker"))
    monkeypatch.setattr(verification, "start_container", start)
    assert verification.main() == 1
    start.assert_not_called()
    assert str(private_dump) not in capsys.readouterr().err


@pytest.mark.parametrize(
    "changed",
    [
        {"APP_ENV": "development"},
        {"DB_HOST": "unrelated.example.invalid"},
        {"DB_NAME": "rpa_dev"},
        {"DB_NAME": "rpa_test_another_run", "RPA_TEST_DB_CONFIRM": "rpa_test_another_run"},
        {"RPA_TEST_DB_CONFIRM": "rpa_test_another_run"},
    ],
)
def test_inherited_or_different_database_is_rejected_before_connection(monkeypatch, changed):
    env, _ = verification.harness.isolated_environment("own_run")
    env.update(changed)
    create_engine = Mock(side_effect=AssertionError("Must not connect"))
    monkeypatch.setattr(verification, "create_db_engine", create_engine)
    with pytest.raises(verification.harness.HarnessError, match="hedefi"):
        verification.isolated_engine(env, "own_run")
    create_engine.assert_not_called()


def test_partial_container_start_still_uses_ownership_checked_cleanup(private_dump, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(verification.sys, "argv", [str(SCRIPT), str(private_dump)])
    monkeypatch.setattr(verification.shutil, "which", lambda _: "/fake/docker")
    monkeypatch.setattr(verification.harness.secrets, "token_hex", lambda _: "abcdef1234567890")
    monkeypatch.setattr(
        verification,
        "start_container",
        Mock(side_effect=verification.harness.HarnessError("partial startup failure")),
    )
    cleanup = Mock(return_value=True)
    monkeypatch.setattr(verification.harness, "cleanup_container", cleanup)
    assert verification.main() == 1
    assert cleanup.call_args.args[:2] == (
        "rpa-db-restore-abcdef1234567890",
        "abcdef1234567890",
    )


def test_restore_disables_original_ownership_and_stops_on_first_error(private_dump, monkeypatch):
    docker = Mock(return_value="")
    monkeypatch.setattr(verification.harness, "docker", docker)
    env = {"DB_USER": "fixture_user", "DB_NAME": "rpa_test_fixture"}
    verification.restore("owned-container", private_dump, env)
    assert docker.call_args_list[0].args == (
        "cp",
        str(private_dump),
        "owned-container:/tmp/backup.dump",
    )
    restore_arguments = docker.call_args.args
    assert restore_arguments[:3] == ("exec", "owned-container", "pg_restore")
    for option in ("--exit-on-error", "--single-transaction", "--no-owner", "--no-acl"):
        assert option in restore_arguments
    assert "--clean" not in restore_arguments
    assert "--create" not in restore_arguments


@pytest.mark.parametrize("changed_field", ["rows", "identifiers_sha256", "retained_data_sha256"])
def test_changed_execution_records_fail_preservation_check(changed_field):
    before = {
        "rpa_jobs": {
            "rows": 1,
            "identifiers_sha256": "a" * 64,
            "retained_data_sha256": "b" * 64,
        }
    }
    after = {"rpa_jobs": {**before["rpa_jobs"], changed_field: "changed"}}
    with pytest.raises(verification.harness.HarnessError, match="değiştirdi"):
        verification.assert_preserved(before, after)


def test_unchanged_records_pass_preservation_check():
    snapshot = {
        "rpa_jobs": {
            "rows": 1,
            "identifiers_sha256": "a" * 64,
            "retained_data_sha256": "b" * 64,
        }
    }
    verification.assert_preserved(snapshot, snapshot)
