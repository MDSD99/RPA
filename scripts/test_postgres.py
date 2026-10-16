#!/usr/bin/env python3
"""Run migrations and tests against a disposable, isolated PostgreSQL container.

Never connects to an inherited database. No system or production service is
started/stopped. The only removal target is this invocation's labelled container.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE = "postgres:17@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675"
PURPOSE_LABEL = "com.rpa.purpose"
RUN_LABEL = "com.rpa.test-run"


class HarnessError(Exception):
    """An actionable message that cannot contain subprocess output or secrets."""


def docker(*arguments: str, env: dict[str, str], timeout: int = 60) -> str:
    """Do not echo Docker argv, stderr, environment or inspection details."""
    try:
        result = subprocess.run(
            ["docker", *arguments],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise HarnessError("Docker komutu başlatılamadı veya zaman aşımına uğradı.") from None
    if result.returncode:
        raise HarnessError(
            "Docker komutu başarısız. Docker erişimini, image indirme erişimini "
            "ve sunucu kapasitesini kontrol edin; gizli değer içerebilecek çıktı bastırıldı."
        )
    return result.stdout.strip()


def isolated_environment(run_id: str) -> tuple[dict[str, str], tuple[str, ...]]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DB_", "POSTGRES_", "RPA_TEST_", "PG"))
        and key not in {"DATABASE_URL", "SQLALCHEMY_DATABASE_URI", "APP_ENV"}
    }
    password = secrets.token_urlsafe(36)
    username = "rpa_test_runner"
    database = f"rpa_test_{run_id}"
    env.update(
        APP_ENV="test",
        DB_HOST="127.0.0.1",
        DB_NAME=database,
        DB_USER=username,
        DB_PASSWORD=password,
        DB_SSLMODE="disable",
        RPA_TEST_DB_CONFIRM=database,
        POSTGRES_USER=username,
        POSTGRES_PASSWORD=password,
        POSTGRES_DB=database,
        PYTHONUNBUFFERED="1",
    )
    return env, (password, username)


def redact_output(line: str, secret_values: tuple[str, ...]) -> str:
    for value in secret_values:
        line = line.replace(value, "[REDACTED]")
    return re.sub(
        r"postgres(?:ql)?(?:\+[a-zA-Z0-9_]+)?://[^\s\"'<>]+",
        "[DATABASE_URL_REDACTED]",
        line,
    )


def wait_for_host_connection(python: Path, env: dict[str, str]) -> None:
    """Verify the exact loopback port and database used by Alembic and workers."""
    probe = """
import sys
from sqlalchemy import text
from app.core.config import DatabaseSettings
from app.database.connection import create_db_engine

try:
    settings = DatabaseSettings.from_env()
    if settings.app_env != "test":
        sys.exit(1)
    engine = create_db_engine(settings)
    with engine.connect() as connection:
        actual_database = connection.scalar(text("SELECT current_database()"))
        if actual_database != settings.name:
            sys.exit(1)
    engine.dispose()
except Exception:
    sys.exit(1)
"""
    deadline = time.monotonic() + 60
    while True:
        try:
            result = subprocess.run(
                [str(python), "-c", probe],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                timeout=8,
                check=False,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        if time.monotonic() >= deadline:
            raise HarnessError("İzole PostgreSQL host portu ve hedef DB bağlantısı doğrulanamadı.")
        time.sleep(0.5)


def run_checked(command: list[str], env: dict[str, str], secret_values: tuple[str, ...]) -> None:
    """Stream useful validation output through redaction, including failure paths."""
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError:
        raise HarnessError(
            "Doğrulama komutu başlatılamadı; proje .venv kurulumunu kontrol edin."
        ) from None
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(redact_output(line, secret_values), end="", flush=True)
        if process.wait():
            raise HarnessError(
                "Migration veya test başarısız oldu; üstteki sansürlenmiş sonucu inceleyin."
            )
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        if process.stdout is not None:
            process.stdout.close()


def cleanup_container(container_name: str, run_id: str, env: dict[str, str]) -> bool:
    """Inspect only ownership metadata, then remove exactly the verified ID."""
    template = (
        '{{.Id}} {{index .Config.Labels "'
        + PURPOSE_LABEL
        + '"}} {{index .Config.Labels "'
        + RUN_LABEL
        + '"}}'
    )
    try:
        metadata = docker("container", "inspect", "--format", template, container_name, env=env)
    except HarnessError:
        # Missing is normal when docker run failed before creating a container.
        # A daemon failure is not silently treated as proof of successful cleanup.
        try:
            remaining = docker(
                "container",
                "ls",
                "--all",
                "--quiet",
                "--filter",
                f"label={RUN_LABEL}={run_id}",
                env=env,
            )
            return not remaining
        except HarnessError:
            return False
    fields = metadata.split()
    if len(fields) != 3 or fields[1:] != ["isolated-test", run_id]:
        return False
    container_id = fields[0]
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        return False
    try:
        docker("container", "rm", "--force", container_id, env=env)
    except HarnessError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pytest_args", nargs=argparse.REMAINDER, help="Optional pytest arguments after --"
    )
    arguments = parser.parse_args()
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        print(
            "APP_ENV=production altında test harness çalıştırılmaz. Ayrı bir test kabuğu kullanın.",
            file=sys.stderr,
        )
        return 2
    if shutil.which("docker") is None:
        print("Docker bulunamadı; testler çalıştırılmadı.", file=sys.stderr)
        return 2
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        print("Önce /opt/rpa/.venv içine proje test bağımlılıklarını kurun.", file=sys.stderr)
        return 2
    run_id = secrets.token_hex(8)
    container_name = f"rpa-db-test-{run_id}"
    env, secret_values = isolated_environment(run_id)
    exit_code = 0
    print(
        f"İzole PostgreSQL testi: {container_name}; yalnızca loopback ve geçici bellek depolaması.",
        flush=True,
    )
    try:
        docker(
            "run",
            "--detach",
            "--rm",
            "--name",
            container_name,
            "--label",
            f"{PURPOSE_LABEL}=isolated-test",
            "--label",
            f"{RUN_LABEL}={run_id}",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_USER",
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_DB",
            IMAGE,
            env=env,
            timeout=180,
        )
        port = docker(
            "container",
            "inspect",
            "--format",
            '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
            container_name,
            env=env,
        )
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise HarnessError("İzole PostgreSQL loopback portu doğrulanamadı.")
        env["DB_PORT"] = port
        deadline = time.monotonic() + 60
        while True:
            try:
                docker(
                    "exec",
                    container_name,
                    "pg_isready",
                    "--quiet",
                    "--host",
                    "127.0.0.1",
                    "--username",
                    env["DB_USER"],
                    "--dbname",
                    env["DB_NAME"],
                    env=env,
                    timeout=5,
                )
                break
            except HarnessError:
                if time.monotonic() >= deadline:
                    raise HarnessError("İzole PostgreSQL 60 saniye içinde hazır olmadı.") from None
                time.sleep(0.5)
        wait_for_host_connection(python, env)
        print("PostgreSQL hazır; migration upgrade/downgrade/upgrade doğrulanıyor.", flush=True)
        migration_checks = (
            ["upgrade", "head"],
            ["downgrade", "base"],
            ["upgrade", "head"],
            ["check"],
        )
        for migration_args in migration_checks:
            run_checked([str(python), "-m", "alembic", *migration_args], env, secret_values)
        pytest_args = arguments.pytest_args
        if pytest_args[:1] == ["--"]:
            pytest_args = pytest_args[1:]
        print("Gerçek PostgreSQL üzerinde pytest çalıştırılıyor.", flush=True)
        run_checked(
            [str(python), "-m", "pytest", "-q", "--tb=short", *pytest_args],
            env,
            secret_values,
        )
    except HarnessError as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:
        print("Test kesildi; bu çalıştırmanın container'ı temizleniyor.", file=sys.stderr)
        exit_code = 130
    finally:
        if cleanup_container(container_name, run_id, env):
            print("Bu çalıştırmanın izole test container'ı kaldırıldı.", flush=True)
        else:
            print(
                f"Temizlik doğrulanamadı: yalnızca {container_name} container'ını ve "
                f"{RUN_LABEL}={run_id} etiketini kontrol edin. Başka container/volume silmeyin.",
                file=sys.stderr,
            )
            exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
