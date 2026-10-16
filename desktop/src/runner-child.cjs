"use strict";
const { spawn } = require("node:child_process");
const readline = require("node:readline");
const path = require("node:path");
const fs = require("node:fs");
class RunnerChild {
  constructor({
    packaged,
    resourcesPath,
    projectRoot,
    storage,
    serverKey,
    deviceKey,
    tenantId,
    browserHosts = [],
    httpHosts = [],
    mailHosts = [],
  }) {
    const binary =
      process.platform === "win32" ? "multirpa-runner.exe" : "multirpa-runner";
    const command = packaged
      ? path.join(resourcesPath, "runner", binary)
      : path.join(
          projectRoot,
          ".venv",
          process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
        );
    const prefix = packaged ? [] : ["-m", "app.desktop"];
    fs.mkdirSync(path.dirname(storage), { recursive: true, mode: 0o700 });
    this.child = spawn(
      command,
      [
        ...prefix,
        "--storage",
        storage,
        "--server-key",
        serverKey,
        "--device-key",
        deviceKey,
        "--tenant-id",
        tenantId,
      ],
      {
        cwd: packaged ? resourcesPath : projectRoot,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          PATH: process.env.PATH,
          SYSTEMROOT: process.env.SYSTEMROOT,
          LANG: "C.UTF-8",
          PYTHONUNBUFFERED: "1",
          PLAYWRIGHT_BROWSERS_PATH: packaged
            ? path.join(resourcesPath, "runner/browsers")
            : path.join(projectRoot, ".browser-cache"),
          RPA_BROWSER_ALLOWED_HOSTS: browserHosts.join(","),
          MULTIRPA_HTTP_ALLOWED_HOSTS: httpHosts.join(","),
          MULTIRPA_MAIL_ALLOWED_HOSTS: mailHosts.join(","),
        },
      },
    );
    this.pending = new Map();
    this.sequence = 0;
    readline
      .createInterface({ input: this.child.stdout })
      .on("line", (line) => {
        if (Buffer.byteLength(line) > 2_000_000) return this.close();
        let result;
        try {
          result = JSON.parse(line);
        } catch {
          return this.close();
        }
        const pending = this.pending.get(result.id);
        if (!pending) return;
        this.pending.delete(result.id);
        clearTimeout(pending.timer);
        result.ok
          ? pending.resolve(result.data)
          : pending.reject(
              Error(result.error?.code || "DESKTOP_RUNNER_FAILED"),
            );
      });
    this.child.stderr.on("data", () => {}); // Never echo customer documents or secrets.
    this.child.on("error", () => this.rejectAll());
    this.child.on("exit", () => this.rejectAll());
  }
  rejectAll() {
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(Error("DESKTOP_RUNNER_UNAVAILABLE"));
    }
    this.pending.clear();
  }
  request(message) {
    if (this.child.exitCode !== null || this.child.killed)
      return Promise.reject(Error("DESKTOP_RUNNER_UNAVAILABLE"));
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Error("DESKTOP_RUNNER_TIMEOUT"));
      }, 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(
        JSON.stringify({ id, ...message }) + "\n",
        (error) => {
          if (error) {
            this.pending.delete(id);
            clearTimeout(timer);
            reject(Error("DESKTOP_RUNNER_UNAVAILABLE"));
          }
        },
      );
    });
  }
  close() {
    this.rejectAll();
    this.child.stdin.end();
    setTimeout(() => {
      if (this.child.exitCode === null) this.child.kill();
    }, 22000).unref();
  }
}
module.exports = { RunnerChild };
