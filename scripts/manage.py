#!/usr/bin/env python3
"""Manage only this project's persistent development database and loopback web app."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import select
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HOST = "srv1752189"
APP_TARGET = "app.web.api:create_app"
DEFAULT_PORT = 8765


class ManageError(Exception):
    """Safe messages only: never include driver, Docker or environment contents."""


def create_env_if_missing(path: Path) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(
            "# Development only. Generated locally; never commit this file.\n"
            "APP_ENV=development\nDB_HOST=127.0.0.1\nDB_PORT=55432\n"
            "DB_NAME=rpa_development\n"
            f"DB_USER=rpa_dev_{secrets.token_hex(4)}\n"
            f"DB_PASSWORD={secrets.token_urlsafe(36)}\n"
            "DB_SSLMODE=disable\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    return True


def read_env(path: Path) -> dict[str, str]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ManageError(".env normal bir dosya ve yalnızca sahibine açık olmalı: chmod 600 .env")
    if metadata.st_size > 32_768:
        raise ManageError(".env beklenenden büyük; yapılandırmayı kontrol edin.")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or not (
                key.startswith("DB_")
                or key
                in {
                    "APP_ENV",
                    "RPA_ALLOWED_HOSTS",
                    "RPA_BROWSER_ALLOWED_HOSTS",
                    "RPA_HTTP_ALLOWED_HOSTS",
                    "RPA_MAIL_ALLOWED_HOSTS",
                }
            )
        ):
            raise ManageError(
                ".env yalnızca benzersiz APP_ENV, DB_* ve izin verilen RPA host ayarlarını içerir."
            )
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ManageError(".env tırnakları eşleşmiyor.")
            value = value[1:-1]
        if any(ord(character) < 32 for character in value):
            raise ManageError(".env kontrol karakteri içeremez.")
        # No shell, substitutions, escape evaluation, or variable expansion.
        values[key] = value
    return values


def runtime_environment(values: dict[str, str]) -> dict[str, str]:
    if os.environ.get("APP_ENV", "").lower() == "production":
        raise ManageError("Production kabuğunda development başlatılmaz; ayrı oturum kullanın.")
    if (
        values.get("APP_ENV") != "development"
        or values.get("DB_HOST") != "127.0.0.1"
        or values.get("DB_NAME") != "rpa_development"
        or values.get("DB_SSLMODE", "disable") != "disable"
        or not values.get("DB_USER")
        or not values.get("DB_PASSWORD")
        or values.get("DB_PASSWORD") == "REPLACE_WITH_A_SECRET"
    ):
        raise ManageError(
            "Yalnızca yerel rpa_development DB için geçerli development .env kabul edilir."
        )
    try:
        database_port = int(values.get("DB_PORT", "55432"))
    except ValueError:
        raise ManageError("Development DB_PORT geçerli bir port olmalı.") from None
    if not 1024 <= database_port <= 65535:
        raise ManageError("Development DB_PORT 1024..65535 aralığında olmalı.")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DB_", "POSTGRES_", "PG", "RPA_", "COMPOSE_", "UVICORN_"))
        and key
        not in {"APP_ENV", "DATABASE_URL", "SQLALCHEMY_DATABASE_URI", "PYTHONPATH", "PYTHONHOME"}
    }
    env.update(values)
    env.update(DB_PORT=str(database_port), DB_SSLMODE="disable", PYTHONUNBUFFERED="1")
    return env


def web_command(port: int) -> list[str]:
    return [
        str(PROJECT_ROOT / ".venv/bin/python"),
        "-m",
        "uvicorn",
        APP_TARGET,
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
        "--log-level",
        "warning",
    ]


def process_identity(pid: int) -> dict | None:
    if type(pid) is not int or pid <= 1:
        return None
    try:
        process = Path(f"/proc/{pid}")
        raw_stat = (process / "stat").read_text()
        fields = raw_stat[raw_stat.rfind(")") + 2 :].split()
        if fields[0] == "Z":
            return None
        command = (process / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        return {
            "start_time": fields[19],
            "cwd": os.readlink(process / "cwd"),
            "command": [item.decode("utf-8") for item in command],
        }
    except (OSError, IndexError, UnicodeError):
        return None


def owned_process(manifest: dict) -> bool:
    identity = process_identity(manifest.get("pid"))
    return bool(
        identity
        and identity["start_time"] == manifest.get("start_time")
        and identity["cwd"] == str(PROJECT_ROOT)
        and identity["command"] == web_command(manifest.get("port"))
    )


def read_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 4096:
        raise ManageError("Runtime process kaydı güvenle okunamıyor.")
    try:
        manifest = json.loads(path.read_text())
        if (
            not isinstance(manifest, dict)
            or type(manifest.get("pid")) is not int
            or manifest["pid"] <= 1
            or type(manifest.get("port")) is not int
            or not 1024 <= manifest["port"] <= 65535
            or not isinstance(manifest.get("start_time"), str)
            or not manifest["start_time"].isdigit()
        ):
            raise ValueError
        return manifest
    except (ValueError, OSError):
        raise ManageError("Runtime process kaydı geçersiz; hiçbir process durdurulmadı.") from None


def write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_name(f".web-{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def stop_owned(manifest: dict) -> None:
    stop_process(manifest, owned_process)


def stop_process(manifest: dict, is_owned) -> None:
    """Stop only the verified process identity, keeping PID reuse protection."""
    if not is_owned(manifest):
        raise ManageError("PID/start-time/komut sahipliği doğrulanmadı; process durdurulmadı.")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ManageError(
            "Güvenli pidfd process yönetimi bu Python/Linux ortamında kullanılamıyor."
        )
    try:
        descriptor = os.pidfd_open(manifest["pid"])
    except ProcessLookupError:
        return
    try:
        # Verify after opening the pidfd as well, preventing PID reuse races.
        if not is_owned(manifest):
            raise ManageError("Process sahipliği değişti; sinyal gönderilmedi.")
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        if not select.select([descriptor], [], [], 10)[0]:
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            if not select.select([descriptor], [], [], 5)[0]:
                raise ManageError("Projeye ait web process'inin durduğu doğrulanamadı.")
    except ProcessLookupError:
        pass
    finally:
        os.close(descriptor)


def health(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            body = json.loads(response.read(4096))
            return response.status == 200 and isinstance(body, dict) and body.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def check_port(port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            # Match Uvicorn: a recently closed connection in TIME_WAIT is not
            # a running service. An active listener still makes bind fail.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
    except OSError:
        raise ManageError(
            "Web portu kullanımda; mevcut servise dokunulmadı. --port ile başka port seçin."
        ) from None


def checked_command(command: list[str], env: dict[str, str], purpose: str) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ManageError(f"{purpose} başlatılamadı veya zaman aşımına uğradı.") from None
    if result.returncode:
        raise ManageError(f"{purpose} başarısız; hassas bilgi içerebilecek ham çıktı bastırıldı.")
    return result.stdout.strip()


def validate_compose_ownership(env: dict[str, str]) -> None:
    identifiers = checked_command(
        [
            "docker",
            "container",
            "ls",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=rpa-dev",
        ],
        env,
        "Development container kontrolü",
    ).splitlines()
    for identifier in identifiers:
        if not re.fullmatch(r"[a-f0-9]{12,64}", identifier):
            raise ManageError("Development container kimliği doğrulanamadı.")
        metadata = checked_command(
            [
                "docker",
                "container",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.rpa.purpose"}} '
                '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
                identifier,
            ],
            env,
            "Development container sahiplik kontrolü",
        )
        if metadata != f"development {PROJECT_ROOT}":
            raise ManageError(
                "rpa-dev adı başka container tarafından kullanılıyor; işlem yapılmadı."
            )


def start_web(port: int, manifest_path: Path) -> None:
    if os.environ.get("APP_ENV", "").lower() == "production":
        raise ManageError("Production kabuğunda development başlatılmaz; ayrı oturum kullanın.")
    env_path = PROJECT_ROOT / ".env"
    create_env_if_missing(env_path)
    env = runtime_environment(read_env(env_path))
    manifest = read_manifest(manifest_path)
    if manifest and owned_process(manifest):
        show_status(manifest_path)
        return
    if manifest and process_identity(manifest["pid"]) is not None:
        raise ManageError("Kayıtlı PID başka process'e ait; process ve runtime kaydı korunuyor.")
    check_port(port)
    python = PROJECT_ROOT / ".venv/bin/python"
    if not python.is_file():
        raise ManageError("Önce proje .venv ve bağımlılıklarını kurun.")
    validate_compose_ownership(env)
    print("Projeye ait development PostgreSQL hazırlanıyor.", flush=True)
    checked_command(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(PROJECT_ROOT),
            "-f",
            str(PROJECT_ROOT / "compose.dev.yaml"),
            "--project-name",
            "rpa-dev",
            "up",
            "-d",
            "--wait",
            "--no-recreate",
            "postgres",
        ],
        env,
        "Development PostgreSQL başlangıcı",
    )
    checked_command([str(python), "-m", "alembic", "upgrade", "head"], env, "Development migration")
    logs = PROJECT_ROOT / "logs"
    if logs.is_symlink():
        raise ManageError("Proje logs dizini symbolic link olamaz.")
    logs.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(
        logs / "rpa-web.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "a", encoding="utf-8") as logfile:
        process = subprocess.Popen(
            web_command(port),
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    identity = process_identity(process.pid)
    if identity is None:
        raise ManageError("Web process'i başlatılamadı; yalnızca development DB açık kalabilir.")
    manifest = {"pid": process.pid, "start_time": identity["start_time"], "port": port}
    try:
        write_manifest(manifest_path, manifest)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if owned_process(manifest) and health(port):
                print(f"Uygulama çalışıyor: http://127.0.0.1:{port} (PID {process.pid})")
                return
            time.sleep(0.25)
        raise ManageError(
            "Web sağlık kontrolü başarısız; projeye ait web durduruldu, DB verisi korundu."
        )
    except BaseException:
        if owned_process(manifest):
            stop_owned(manifest)
        manifest_path.unlink(missing_ok=True)
        raise


def show_status(manifest_path: Path) -> None:
    manifest = read_manifest(manifest_path)
    if not manifest:
        print("Projeye ait web process kaydı yok.")
    elif owned_process(manifest):
        status = "sağlıklı" if health(manifest["port"]) else "sağlık kontrolü başarısız"
        print(f"http://127.0.0.1:{manifest['port']} (PID {manifest['pid']}, {status})")
    else:
        print("Kayıtlı web process çalışmıyor veya sahipliği doğrulanamıyor; işlem yapılmadı.")


@contextmanager
def runtime_lock():
    runtime = PROJECT_ROOT / ".runtime"
    if runtime.is_symlink():
        raise ManageError(".runtime symbolic link olamaz.")
    runtime.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(runtime / "manage.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ManageError(
                "Başka bir RPA yönetim komutu sürüyor; tamamlanmasını bekleyin."
            ) from None
        yield runtime / "web.json"
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    try:
        if socket.gethostname() != EXPECTED_HOST or PROJECT_ROOT != Path("/opt/rpa"):
            raise ManageError(
                "Beklenen srv1752189 /opt/rpa ortamı doğrulanamadı; değişiklik yapılmadı."
            )
        if Path.cwd().resolve() != PROJECT_ROOT:
            raise ManageError("Komutu /opt/rpa çalışma dizininden çalıştırın.")
        if not 1024 <= args.port <= 65535:
            raise ManageError("Web portu 1024..65535 aralığında olmalı.")
        with runtime_lock() as manifest_path:
            if args.action == "start":
                start_web(args.port, manifest_path)
            elif args.action == "status":
                show_status(manifest_path)
            else:
                manifest = read_manifest(manifest_path)
                if manifest and owned_process(manifest):
                    stop_owned(manifest)
                    manifest_path.unlink(missing_ok=True)
                    print("Projeye ait web process durduruldu. PostgreSQL ve verileri korundu.")
                elif manifest and process_identity(manifest["pid"]) is not None:
                    raise ManageError(
                        "Process sahipliği doğrulanamadı; hiçbir process durdurulmadı."
                    )
                else:
                    print("Projeye ait çalışan web process bulunamadı. PostgreSQL'e dokunulmadı.")
        return 0
    except ManageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print(
            "Proje dosyası veya process erişimi başarısız; ham hata çıktısı bastırıldı.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print(
            "Yönetim komutu kesildi; status komutuyla projeye ait process'i kontrol edin.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
