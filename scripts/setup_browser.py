#!/usr/bin/env python3
"""Prepare Chromium entirely inside the project, without installing system packages."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".browser-cache"
PACKAGES = (
    "libatk1.0-0t64",
    "libatk-bridge2.0-0t64",
    "libatspi2.0-0t64",
    "libxcomposite1",
    "libxdamage1",
    "libxfixes3",
    "libxrandr2",
    "libgbm1",
    "libasound2t64",
    "libxrender1",
    "libxi6",
)


def main():
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(CACHE))
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"], env=env, cwd=ROOT, check=True
    )
    release = Path("/etc/os-release").read_text()
    if "ID=ubuntu" not in release or 'VERSION_ID="24.04"' not in release:
        raise SystemExit("Local library extraction requires Ubuntu 24.04; no system change made.")
    packages = CACHE / "system-debs"
    packages.mkdir(parents=True, exist_ok=True)
    subprocess.run(["apt-get", "download", *PACKAGES], cwd=packages, check=True)
    for package in packages.glob("*.deb"):
        subprocess.run(
            ["dpkg-deb", "-x", str(package), str(CACHE / "system")], cwd=ROOT, check=True
        )
    print("Chromium and browser libraries are ready inside /opt/rpa/.browser-cache.")


if __name__ == "__main__":
    main()
