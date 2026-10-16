"""Build on the target OS; Linux cannot produce a genuine Windows runner."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
desktop = root / "desktop"
command = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name",
    "multirpa-runner",
    "--paths",
    str(root),
    "--distpath",
    str(desktop / "runner-dist"),
    "--workpath",
    str(desktop / "runner-build"),
    "--specpath",
    str(desktop),
    "--collect-all",
    "playwright",
    "--collect-all",
    "openpyxl",
    "--hidden-import",
    "defusedxml",
    "--hidden-import",
    "app.adapters.mail_automation",
    str(root / "app/desktop/__main__.py"),
]
if sys.platform == "win32":
    command = command[:-1] + ["--collect-all", "pywinauto", command[-1]]
subprocess.run(
    command,
    cwd=root,
    check=True,
)
browser_source = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", root / ".browser-cache"))
destination = desktop / "runner-dist/multirpa-runner/browsers"
if not browser_source.is_dir():
    raise RuntimeError("Install the target platform Playwright Chromium browser before packaging")
for directory in browser_source.iterdir():
    if directory.name.startswith(("chromium_headless_shell-", "ffmpeg-")):
        shutil.copytree(directory, destination / directory.name, dirs_exist_ok=True)
(desktop / "runner-dist/platform.json").write_text(
    json.dumps({"platform": "win32" if sys.platform == "win32" else sys.platform}) + "\n"
)
