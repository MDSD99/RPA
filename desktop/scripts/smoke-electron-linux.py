"""Inspect the actual Linux package with a project-local Xvfb and isolated profile.

Root CI needs --no-sandbox to start Chromium. The application itself never adds
that flag. This proves renderer isolation and key-store failure behavior, not a
Windows installation or working Linux OS secret store.
"""

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--app", type=Path, required=True)
parser.add_argument("--date", default="2026-10-04")
parser.add_argument("--flow-transfer", action="store_true")
arguments = parser.parse_args()
tools = root / "desktop/.tools/system"
display = next(
    number for number in range(94, 120) if not Path(f"/tmp/.X11-unix/X{number}").exists()
)
with socket.socket() as available:
    available.bind(("127.0.0.1", 0))
    port = available.getsockname()[1]
env = {
    **os.environ,
    "DISPLAY": f":{display}",
    "LD_LIBRARY_PATH": ":".join(
        [
            str(tools / "usr/lib/x86_64-linux-gnu"),
            str(root / ".browser-cache/system/usr/lib/x86_64-linux-gnu"),
        ]
    ),
}
env.pop("ELECTRON_RUN_AS_NODE", None)
with tempfile.TemporaryDirectory(prefix="multirpa-electron-smoke-") as temporary:
    tenant_id = str(uuid4())
    if arguments.flow_transfer:
        # Model a previously opened local account in this disposable profile.
        # This is public identity metadata, not a central login or device permit.
        identity = Path(temporary) / "last-account.json"
        identity.write_text(
            json.dumps(
                {
                    "tenant_id": tenant_id,
                    "email": "offline-smoke@example.invalid",
                    "display_name": "Yerel aktarım kontrolü",
                }
            )
        )
        identity.chmod(0o600)
    # The distro binary hardcodes /usr/bin/xkbcomp. Relocate only this project
    # test copy so its compiler resolves inside the unpacked project tools.
    xvfb_data = (tools / "usr/bin/Xvfb").read_bytes()
    if xvfb_data.count(b"/usr/bin\x00") != 1:
        raise RuntimeError("Unexpected Xvfb layout; review the local tool relocation")
    xvfb_binary = Path(temporary) / "Xvfb"
    xvfb_binary.write_bytes(xvfb_data.replace(b"/usr/bin\x00", b"bin\x00" + bytes(5)))
    xvfb_binary.chmod(0o700)
    suffix = "-transfer" if arguments.flow_transfer else ""
    log_path = root / f"artifacts/multirpa-desktop-gui{suffix}-{arguments.date}.txt"
    with log_path.open("w") as log:
        xvfb = subprocess.Popen(
            [
                str(xvfb_binary),
                f":{display}",
                "-screen",
                "0",
                "1440x960x24",
                "-nolisten",
                "tcp",
                "-ac",
                "-xkbdir",
                str(tools / "usr/share/X11/xkb"),
            ],
            env=env,
            cwd=tools / "usr",
            stdout=log,
            stderr=log,
        )
        application = None
        try:
            deadline = time.monotonic() + 10
            while not Path(f"/tmp/.X11-unix/X{display}").exists():
                if xvfb.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Project-local Xvfb did not start")
                time.sleep(0.05)
            application = subprocess.Popen(
                [
                    str(arguments.app.resolve()),
                    "--no-sandbox",
                    "--disable-gpu",
                    "--password-store=basic",
                    f"--remote-debugging-port={port}",
                    "--remote-debugging-address=127.0.0.1",
                    f"--user-data-dir={temporary}",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            address = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(address + "/json/version", timeout=1) as response:
                        json.load(response)
                    break
                except (OSError, urllib.error.URLError):
                    if application.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Packaged Electron did not expose its local test CDP"
                        ) from None
                    time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(address)
                page = browser.contexts[0].pages[0]
                page.wait_for_load_state("domcontentloaded")
                if arguments.flow_transfer:
                    page.locator("#desktop-account-button").click()
                page.locator("#desktop-account-dialog").wait_for(state="visible")
                assert page.evaluate("typeof require") == "undefined"
                assert page.evaluate("typeof process") == "undefined"
                assert page.evaluate("typeof window.multirpaDesktop.exportFlow") == "function"
                denied = page.evaluate("window.multirpaDesktop.exportFlow({path:'/etc/passwd'})")
                assert denied["ok"] is False
                assert denied["error"]["code"] == "DESKTOP_FLOW_ID_INVALID"
                status = page.evaluate("window.multirpaDesktop.account()")
                assert status["ok"] and status["data"]["secure_storage"] is False
                denied = page.evaluate(
                    "window.multirpaDesktop.login('smoke@example.com','not-a-real-password')"
                )
                assert denied["ok"] is False
                assert denied["error"]["code"] == "DESKTOP_SECURE_STORAGE_REQUIRED"
                page.screenshot(
                    path=root / f"artifacts/multirpa-desktop-login{suffix}-{arguments.date}.png"
                )
                page.locator(".desktop-close").click()
                page.locator('[data-flow-action="new"]').first.click()
                page.locator(".node-library").wait_for(state="visible")
                assert not page.locator('[data-nav="schedules"]').is_visible()
                assert not page.locator('[data-flow-action="schedule"]').is_visible()
                assert page.locator('[data-type="email_send"]').count() == 1
                for path in ["/api/jobs", "https://attacker.invalid/api/flows"]:
                    denied = page.evaluate(
                        "path => window.multirpaDesktop.request(path, {}, 'POST')", path
                    )
                    assert denied["ok"] is False
                assert (
                    page.evaluate("fetch('https://example.com').then(()=>false).catch(()=>true)")
                    is True
                )
                page.screenshot(
                    path=root / f"artifacts/multirpa-desktop-editor{suffix}-{arguments.date}.png"
                )
                if arguments.flow_transfer:
                    page.evaluate("location.hash = 'workflows'")
                    page.locator('[data-flow-action="import"]').first.click()
                    document = {
                        "format": "multirpa-flow",
                        "schema_version": 1,
                        "flow": {
                            "name": "Çevrimdışı aktarım denemesi",
                            "description": "Paketli ortak editör kontrolü",
                            "graph": {
                                "nodes": [
                                    {
                                        "id": name,
                                        "type": kind,
                                        "label": name,
                                        "position": {"x": index * 180, "y": 40},
                                        "config": config,
                                    }
                                    for index, (name, kind, config) in enumerate(
                                        [
                                            ("start", "start", {}),
                                            (
                                                "write",
                                                "file_write",
                                                {"path": "should-not-exist.txt", "value": "smoke"},
                                            ),
                                            ("end", "end", {"output": {}}),
                                        ]
                                    )
                                ],
                                "edges": [
                                    {"source": "start", "target": "write", "port": "next"},
                                    {"source": "write", "target": "end", "port": "next"},
                                ],
                            },
                        },
                    }
                    page.locator("#flow-import-file").set_input_files(
                        {
                            "name": "offline.multirpa.json",
                            "mimeType": "application/json",
                            "buffer": json.dumps(document).encode(),
                        }
                    )
                    page.locator("#flow-import-preview").wait_for(state="visible")
                    assert (
                        page.locator("#flow-import-name").inner_text() == document["flow"]["name"]
                    )
                    page.locator('#flow-import-form [type="submit"]').click()
                    page.wait_for_function("location.hash.startsWith('#flows/')")
                    flow_id = page.evaluate("location.hash.split('/')[1]")
                    exported = page.evaluate(
                        "id => window.multirpaDesktop.request(`/api/flows/${id}/export`)",
                        flow_id,
                    )
                    assert exported["ok"]
                    assert exported["data"]["document"] == document
                    jobs = page.evaluate("window.multirpaDesktop.request('/api/jobs')")
                    assert jobs["ok"] and jobs["data"]["items"] == []
                    assert not (
                        Path(temporary) / "accounts" / tenant_id / "files/should-not-exist.txt"
                    ).exists()
                    assert not page.evaluate("window.multirpaDesktop.account()")["data"]["licensed"]
                    page.screenshot(
                        path=root / f"artifacts/multirpa-desktop-imported-{arguments.date}.png"
                    )
                    page.evaluate("location.hash = 'workflows'")
                    page.locator('[data-flow-action="new"]').first.click()
                    page.locator("#flow-name").fill("Sürümlü masaüstü akışı")
                    page.locator('[data-flow-action="add"][data-type="set"]').click()
                    page.locator('[data-flow-action="save"]').click()
                    page.wait_for_function(
                        "location.hash.startsWith('#flows/') && location.hash !== '#flows/new'"
                    )
                    versioned_id = page.evaluate("location.hash.split('/')[1]")
                    versioned_export = page.evaluate(
                        "id => window.multirpaDesktop.request(`/api/flows/${id}/export`)",
                        versioned_id,
                    )
                    assert versioned_export["ok"]
                    versioned_document = versioned_export["data"]["document"]
                    assert versioned_document["schema_version"] == 2
                    versioned_nodes = versioned_document["flow"]["graph"]["nodes"]
                    assert len(versioned_nodes) == 3
                    assert all(node["type_version"] == 1 for node in versioned_nodes)
                    page.evaluate("location.hash = 'workflows'")
                    page.locator('[data-flow-action="import"]').first.click()
                    page.locator("#flow-import-file").set_input_files(
                        {
                            "name": "versioned.multirpa.json",
                            "mimeType": "application/json",
                            "buffer": json.dumps(versioned_document).encode(),
                        }
                    )
                    page.locator("#flow-import-preview").wait_for(state="visible")
                    page.locator('#flow-import-form [type="submit"]').click()
                    page.wait_for_function("location.hash.startsWith('#flows/')")
                    versioned_import_id = page.evaluate("location.hash.split('/')[1]")
                    assert versioned_import_id not in {flow_id, versioned_id}
                    versioned_roundtrip = page.evaluate(
                        "id => window.multirpaDesktop.request(`/api/flows/${id}/export`)",
                        versioned_import_id,
                    )
                    assert versioned_roundtrip["ok"]
                    assert versioned_roundtrip["data"]["document"] == versioned_document
                    jobs = page.evaluate("window.multirpaDesktop.request('/api/jobs')")
                    assert jobs["ok"] and jobs["data"]["items"] == []
                    assert not (
                        Path(temporary) / "accounts" / tenant_id / "files/should-not-exist.txt"
                    ).exists()
                    assert not page.evaluate("window.multirpaDesktop.account()")["data"]["licensed"]
                    page.screenshot(
                        path=root / f"artifacts/multirpa-desktop-versioned-{arguments.date}.png"
                    )
                    print(
                        "Actual packaged Electron preserved a legacy schema 1 draft and "
                        "created explicit version 1 nodes through the shared editor; schema 2 "
                        "export/import retained the definition under a new identity. No jobs, "
                        "file effects or license permit were created."
                    )
                print(
                    "Actual packaged Electron opened the shared editor; Node/process absent, "
                    "network/generic-job IPC blocked, schedule hidden, key-store login blocked."
                )
                browser.close()
        finally:
            if application is not None:
                application.terminate()
                try:
                    application.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    application.kill()
                    application.wait(timeout=5)
            xvfb.terminate()
            xvfb.wait(timeout=5)
