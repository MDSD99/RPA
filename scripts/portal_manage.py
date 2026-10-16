#!/usr/bin/env python3
"""Persist only this project's loopback development customer portal."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import ConfigurationError  # noqa: E402
from app.core.portal_config import PortalSettings  # noqa: E402
from scripts import manage, portal  # noqa: E402


def command(port: int) -> list[str]:
    return [
        str(PROJECT_ROOT / ".venv/bin/python"),
        "scripts/portal.py",
        "serve",
        "--port",
        str(port),
    ]


def owned(manifest: dict) -> bool:
    identity = manage.process_identity(manifest.get("pid"))
    return bool(
        identity
        and identity["start_time"] == manifest.get("start_time")
        and identity["cwd"] == str(PROJECT_ROOT)
        and identity["command"] == command(manifest.get("port"))
    )


def healthy(port: int, hostname: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/auth/me", headers={"Host": f"{hostname}:{port}"}
    )
    try:
        opener.open(request, timeout=2).close()
    except urllib.error.HTTPError as error:
        if error.code != 401:
            return False
        try:
            return json.loads(error.read(4096)).get("error", {}).get("code") == "SESSION_EXPIRED"
        except (ValueError, AttributeError):
            return False
    except (OSError, urllib.error.URLError):
        return False
    return False


def settings_for_port(port: int) -> tuple[dict[str, str], PortalSettings]:
    env = portal.portal_environment()
    settings = PortalSettings.from_env(env)
    parsed = urlsplit(settings.public_url)
    if (
        settings.app_env != "development"
        or parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
    ):
        raise ConfigurationError("Use the development loopback portal; production uses systemd")
    portal.validate_bind_port(settings, port)
    return env, settings


def start(path: Path, port: int, env: dict[str, str], settings: PortalSettings) -> None:
    previous = manage.read_manifest(path)
    if previous and owned(previous):
        print(f"MultiRPA müşteri portalı zaten çalışıyor: {settings.public_url}")
        return
    if previous and manage.process_identity(previous["pid"]) is not None:
        raise manage.ManageError("Registered PID belongs to another process; no action taken")
    manage.check_port(port)
    logs = PROJECT_ROOT / "logs"
    if logs.is_symlink():
        raise manage.ManageError("Project log directory must not be a symbolic link")
    logs.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(
        logs / "multirpa-portal.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "a", encoding="utf-8") as output:
        process = subprocess.Popen(
            command(port),
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    identity = manage.process_identity(process.pid)
    if identity is None:
        raise manage.ManageError("Customer portal process did not start")
    manifest = {"pid": process.pid, "port": port, "start_time": identity["start_time"]}
    try:
        manage.write_manifest(path, manifest)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if owned(manifest) and healthy(port, settings.hostname):
                print(
                    f"MultiRPA müşteri portalı çalışıyor: {settings.public_url} (PID {process.pid})"
                )
                return
            time.sleep(0.25)
        raise manage.ManageError(
            "Portal startup failed; apply current migrations and check settings"
        )
    except BaseException:
        if owned(manifest):
            manage.stop_process(manifest, owned)
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    try:
        env, settings = settings_for_port(args.port)
        with manage.runtime_lock():
            path = PROJECT_ROOT / ".runtime/portal.json"
            if args.action == "start":
                start(path, args.port, env, settings)
                return 0
            manifest = manage.read_manifest(path)
            if not manifest:
                print("Müşteri portalının process kaydı yok.")
                return 0
            if not owned(manifest):
                raise manage.ManageError(
                    "Customer portal ownership could not be verified; no action taken"
                )
            if args.action == "stop":
                manage.stop_process(manifest, owned)
                path.unlink(missing_ok=True)
                print("Projeye ait müşteri portalı durduruldu.")
            else:
                status = (
                    "sağlıklı"
                    if healthy(manifest["port"], settings.hostname)
                    else "sağlık kontrolü başarısız"
                )
                print(f"{settings.public_url} (PID {manifest['pid']}, {status})")
            return 0
    except (OSError, ConfigurationError, manage.ManageError) as error:
        print(
            str(error) if not isinstance(error, OSError) else "Portal process operation failed",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
