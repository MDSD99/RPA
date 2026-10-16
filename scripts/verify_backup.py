#!/usr/bin/env python3
"""Restore a private backup only into a new disposable PostgreSQL test container.

The current development/production database is never a destination. Execution
row counts, identifiers and retained data are compared across the forward
migration; only aggregate hashes are printed, never restored row contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import DatabaseSettings  # noqa: E402
from app.database.connection import create_db_engine  # noqa: E402
from app.database.schema import EXPECTED_REVISION, schema_is_current  # noqa: E402
from scripts import test_postgres as harness  # noqa: E402

# Keys that existed before the tenant migration. The new tenant column must not
# change the identity or contents of any retained execution row.
EXECUTION_KEYS = {
    "rpa_jobs": ("id",),
    "rpa_transactions": ("id",),
    "rpa_errors": ("id",),
    "rpa_checkpoints": ("id",),
    "rpa_audit_log": ("id",),
    "rpa_flows": ("id",),
    "rpa_flow_versions": ("flow_id", "version"),
    "rpa_flow_runs": ("job_id",),
    "rpa_flow_schedules": ("id",),
}


class TableSnapshot(TypedDict):
    rows: int
    identifiers_sha256: str
    retained_data_sha256: str


def stage_backup(source: Path, destination: Path) -> str:
    """Copy a private regular custom-format dump through a verified descriptor.

    Reject symlinks, broad permissions and foreign ownership before Docker is
    contacted. A private snapshot prevents docker cp from following a replaced
    input path between validation and restore.
    """
    descriptor: int | None = None
    try:
        source = source.absolute()
        if source.resolve(strict=True) != source:
            raise harness.HarnessError("Yedek yolu symlink veya dolaylı yol içeremez.")
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
        ):
            raise harness.HarnessError(
                "Yedek, çalıştıran kullanıcıya ait 0600 izinli normal bir dosya olmalıdır."
            )
        with os.fdopen(descriptor, "rb") as input_file:
            descriptor = None
            if input_file.read(5) != b"PGDMP":
                raise harness.HarnessError("Yedek PostgreSQL custom-format arşivi olmalıdır.")
            input_file.seek(0)
            output_descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            digest = hashlib.sha256()
            with os.fdopen(output_descriptor, "wb") as output_file:
                while chunk := input_file.read(1024 * 1024):
                    output_file.write(chunk)
                    digest.update(chunk)
            after = os.fstat(input_file.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise harness.HarnessError("Yedek doğrulama sırasında değişti; yeniden deneyin.")
        return digest.hexdigest()
    except (OSError, RuntimeError):
        raise harness.HarnessError("Özel yedek dosyası güvenli biçimde okunamadı.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def isolated_engine(env: dict[str, str], run_id: str) -> Engine:
    """Require this invocation's exact disposable destination before connecting."""
    database = f"rpa_test_{run_id}"
    if (
        env.get("APP_ENV") != "test"
        or env.get("DB_HOST") != "127.0.0.1"
        or env.get("DB_NAME") != database
        or env.get("RPA_TEST_DB_CONFIRM") != database
    ):
        raise harness.HarnessError("İzole restore hedefi doğrulanamadı; bağlantı açılmadı.")
    engine = create_db_engine(DatabaseSettings.from_env(env))
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != database:
                raise harness.HarnessError(
                    "Restore bağlantısı beklenen test veritabanına ait değil."
                )
    except BaseException:
        engine.dispose()
        raise
    return engine


def revision(engine: Engine) -> str:
    with engine.connect() as connection:
        if connection.scalar(text("SELECT to_regclass('public.alembic_version')")) is None:
            raise harness.HarnessError("Restore edilen yedekte Alembic şema kaydı bulunamadı.")
        revisions = connection.scalars(text("SELECT version_num FROM public.alembic_version")).all()
    if len(revisions) != 1:
        raise harness.HarnessError("Restore edilen yedekte tek bir şema revision olmalıdır.")
    return revisions[0]


def execution_snapshot(engine: Engine) -> dict[str, TableSnapshot]:
    """Hash records in key order; sensitive columns never leave this process."""
    snapshots: dict[str, TableSnapshot] = {}
    with engine.connect().execution_options(stream_results=True) as connection:
        for table, keys in EXECUTION_KEYS.items():
            # Identifiers are fixed source constants, never supplied by the user.
            columns = ", ".join(f"original.{key}" for key in keys)
            rows = connection.execute(
                text(
                    f"SELECT {columns}, (to_jsonb(original) - 'tenant_id')::text "
                    f"FROM public.{table} AS original ORDER BY {columns}"
                )
            )
            identity_digest = hashlib.sha256()
            data_digest = hashlib.sha256()
            count = 0
            for row in rows:
                identity = json.dumps(row[: len(keys)], default=str, separators=(",", ":")).encode()
                retained_data = row[-1].encode()
                for digest, value in ((identity_digest, identity), (data_digest, retained_data)):
                    digest.update(len(value).to_bytes(8, "big"))
                    digest.update(value)
                count += 1
            snapshots[table] = {
                "rows": count,
                "identifiers_sha256": identity_digest.hexdigest(),
                "retained_data_sha256": data_digest.hexdigest(),
            }
    return snapshots


def assert_preserved(before: dict[str, TableSnapshot], after: dict[str, TableSnapshot]) -> None:
    if before != after:
        raise harness.HarnessError(
            "Forward migration yürütme satır sayısı, kimliği veya korunan veriyi değiştirdi."
        )


def start_container(container_name: str, run_id: str, env: dict[str, str], python: Path) -> None:
    harness.docker(
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--label",
        f"{harness.PURPOSE_LABEL}=isolated-test",
        "--label",
        f"{harness.RUN_LABEL}={run_id}",
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
        harness.IMAGE,
        env=env,
        timeout=180,
    )
    port = harness.docker(
        "container",
        "inspect",
        "--format",
        '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}',
        container_name,
        env=env,
    )
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise harness.HarnessError("İzole PostgreSQL loopback portu doğrulanamadı.")
    env["DB_PORT"] = port
    deadline = time.monotonic() + 60
    while True:
        try:
            harness.docker(
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
        except harness.HarnessError:
            if time.monotonic() >= deadline:
                raise harness.HarnessError(
                    "İzole PostgreSQL 60 saniye içinde hazır olmadı."
                ) from None
            time.sleep(0.5)
    harness.wait_for_host_connection(python, env)


def restore(container_name: str, staged_backup: Path, env: dict[str, str]) -> None:
    harness.docker("cp", str(staged_backup), f"{container_name}:/tmp/backup.dump", env=env)
    harness.docker(
        "exec",
        container_name,
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-acl",
        "--host",
        "127.0.0.1",
        "--username",
        env["DB_USER"],
        "--dbname",
        env["DB_NAME"],
        "/tmp/backup.dump",
        env=env,
        timeout=180,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="Private 0600 PostgreSQL custom-format dump")
    parser.add_argument("--expected-revision", default="0005_portal_rate_limits")
    arguments = parser.parse_args()
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        print(
            "APP_ENV=production altında izole restore doğrulaması çalıştırılmaz.", file=sys.stderr
        )
        return 2
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.is_file() or shutil.which("docker") is None:
        print("Proje .venv Python kurulumu ve Docker gereklidir.", file=sys.stderr)
        return 2
    run_id = harness.secrets.token_hex(8)
    container_name = f"rpa-db-restore-{run_id}"
    env, secret_values = harness.isolated_environment(run_id)
    exit_code = 0
    engine: Engine | None = None
    started = False
    try:
        with tempfile.TemporaryDirectory(prefix="multirpa-backup-verify-") as temporary:
            staged_backup = Path(temporary) / "backup.dump"
            backup_digest = stage_backup(arguments.backup, staged_backup)
            print(f"Private archive SHA-256: {backup_digest}", flush=True)
            print(
                "Restore target: new labelled PostgreSQL 17 container, loopback only.", flush=True
            )
            started = True  # Cleanup also verifies absence if docker run fails.
            start_container(container_name, run_id, env, python)
            engine = isolated_engine(env, run_id)
            with engine.connect() as connection:
                if connection.scalar(
                    text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                ):
                    raise harness.HarnessError("İzole restore veritabanı boş değil.")
            restore(container_name, staged_backup, env)
            if revision(engine) != arguments.expected_revision:
                raise harness.HarnessError(
                    "Yedek şeması beklenen başlangıç revision ile eşleşmiyor."
                )
            before = execution_snapshot(engine)
            print(f"Restored baseline revision: {arguments.expected_revision}", flush=True)
            print("Before migration: " + json.dumps(before, sort_keys=True), flush=True)
            harness.run_checked(
                [str(python), "-m", "alembic", "upgrade", "head"], env, secret_values
            )
            harness.run_checked([str(python), "-m", "alembic", "check"], env, secret_values)
            after = execution_snapshot(engine)
            assert_preserved(before, after)
            with Session(engine) as session:
                if not schema_is_current(session):
                    raise harness.HarnessError(
                        "Migration sonrası uygulama readiness şeması güncel değil."
                    )
            print("After migration: " + json.dumps(after, sort_keys=True), flush=True)
            print(f"Current revision: {EXPECTED_REVISION}; metadata/readiness: PASS", flush=True)
            print("Execution counts, identifiers and retained data: PRESERVED", flush=True)
    except harness.HarnessError as error:
        print(str(error), file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:
        print(
            "Restore doğrulaması kesildi; yalnız bu çalıştırmanın container'ı temizleniyor.",
            file=sys.stderr,
        )
        exit_code = 130
    except Exception:
        print(
            "İzole restore doğrulaması başarısız; hassas hata ayrıntıları bastırıldı.",
            file=sys.stderr,
        )
        exit_code = 1
    finally:
        if engine is not None:
            engine.dispose()
        if started:
            if harness.cleanup_container(container_name, run_id, env):
                print("This invocation's owned restore container: REMOVED", flush=True)
            else:
                print(
                    "İzole restore container temizliği doğrulanamadı; başka kaynak silmeyin.",
                    file=sys.stderr,
                )
                exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
