"""CLI safety must hold before an engine or network connection is created."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["production", "unconfirmed_test"])
def test_migration_refuses_unsafe_target_without_exposing_secrets(mode):
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("DB_", "PG", "RPA_"))
    }
    env.update(
        APP_ENV="production" if mode == "production" else "test",
        DB_HOST="postgres.example.invalid" if mode == "production" else "127.0.0.1",
        DB_NAME="rpa_main" if mode == "production" else "rpa_test_guard",
        DB_USER="fake_guard_user",
        DB_PASSWORD="fake_guard_password",
        DB_SSLMODE="verify-full",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "fake_guard_password" not in output
    assert "fake_guard_user" not in output
    assert "postgres.example.invalid" not in output
    assert (
        "RPA_ALLOW_PRODUCTION_MIGRATIONS" in output
        if mode == "production"
        else ("RPA_TEST_DB_CONFIRM" in output)
    )
