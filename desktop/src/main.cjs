"use strict";
const {
  app,
  BrowserWindow,
  ipcMain,
  safeStorage,
  shell,
  powerMonitor,
  session,
  Menu,
  dialog,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { pathToFileURL } = require("node:url");
const { localRequest, serverOrigin } = require("./contracts.cjs");
const { ServerClient } = require("./server-client.cjs");
const { RunnerChild } = require("./runner-child.cjs");
const { LocalVault } = require("./local-vault.cjs");
const { saveFlowExport } = require("./flow-export.cjs");
// Editing remains possible when an existing account's OS key store is locked.
// This placeholder cannot obtain a server lease: login and activation require
// the real private key, and the runner starts with no execution permit.
const editingOnlyDeviceKey = Buffer.alloc(32).toString("base64url");
let window,
  runner,
  server,
  renewTimer,
  lastUser = null,
  permitActive = false,
  secureKeyReady = false,
  renewQueue = Promise.resolve();
const projectRoot = path.resolve(__dirname, "../..");
const rendererFile = path.join(__dirname, "../renderer/index.html");
const configPath = path.join(__dirname, "../deployment.json");
let config;
function privateKey() {
  if (
    !safeStorage.isEncryptionAvailable() ||
    (process.platform === "linux" &&
      safeStorage.getSelectedStorageBackend() === "basic_text")
  )
    throw Error("DESKTOP_SECURE_STORAGE_REQUIRED");
  const filename = path.join(app.getPath("userData"), "device-key.bin");
  let pem;
  if (fs.existsSync(filename))
    pem = safeStorage.decryptString(fs.readFileSync(filename));
  else {
    const pair = crypto.generateKeyPairSync("ed25519");
    pem = pair.privateKey.export({ type: "pkcs8", format: "pem" }).toString();
    fs.writeFileSync(filename, safeStorage.encryptString(pem), {
      mode: 0o600,
      flag: "wx",
    });
  }
  const key = crypto.createPrivateKey(pem);
  const publicKey = crypto
    .createPublicKey(key)
    .export({ type: "spki", format: "der" })
    .subarray(-32)
    .toString("base64url");
  return { key, publicKey };
}
function publicIdentity() {
  const filename = path.join(app.getPath("userData"), "last-account.json");
  if (!lastUser && fs.existsSync(filename)) {
    try {
      const saved = JSON.parse(fs.readFileSync(filename, "utf8"));
      if (/^[a-f0-9-]{36}$/.test(saved.tenant_id)) lastUser = saved;
    } catch {}
  }
  return filename;
}
function ensureRunner(user) {
  if (
    !user?.tenant_id ||
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(
      user.tenant_id,
    )
  )
    throw Error("DESKTOP_LOGIN_REQUIRED");
  if (runner && runner.tenantId === user.tenant_id) return runner;
  if (runner) runner.close();
  runner = new RunnerChild({
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    projectRoot,
    storage: path.join(
      app.getPath("userData"),
      "accounts",
      user.tenant_id,
      "workspace.sqlite",
    ),
    serverKey: config.server_public_key,
    deviceKey: server?.publicKey || editingOnlyDeviceKey,
    tenantId: user.tenant_id,
    browserHosts: config.browser_allowed_hosts || [],
    httpHosts: config.http_allowed_hosts || [],
    mailHosts: config.mail_allowed_hosts || [],
  });
  runner.tenantId = user.tenant_id;
  if (secureKeyReady)
    runner
      .request({ operation: "secrets", values: vaultFor(user).snapshot() })
      .catch(invalidate);
  return runner;
}
function vaultFor(user) {
  if (!secureKeyReady || !user) throw Error("DESKTOP_SECURE_STORAGE_REQUIRED");
  return new LocalVault(
    path.join(
      app.getPath("userData"),
      "accounts",
      user.tenant_id,
      "secret-vault.bin",
    ),
    safeStorage,
  );
}
async function invalidate() {
  permitActive = false;
  if (runner)
    try {
      await runner.request({ operation: "invalidate" });
    } catch {}
}
async function renew(purpose = "renew") {
  const operation = renewQueue.then(async () => {
    try {
      const permit = await server.lease(purpose);
      await ensureRunner(server.user).request({
        operation: "permit",
        ...permit,
      });
      permitActive = true;
    } catch (error) {
      await invalidate();
      throw error;
    }
  });
  renewQueue = operation.catch(() => {});
  return operation;
}
function trusted(event) {
  if (
    event.sender !== window?.webContents ||
    event.senderFrame !== window.webContents.mainFrame ||
    event.senderFrame.url.split("#")[0] !== pathToFileURL(rendererFile).href
  )
    throw Error("DESKTOP_UNTRUSTED_SENDER");
}
function handle(name, operation) {
  ipcMain.handle("multirpa:" + name, async (event, value) => {
    try {
      trusted(event);
      return { ok: true, data: await operation(value) };
    } catch (error) {
      const code = String(error.message || "");
      return {
        ok: false,
        error: {
          code: /^[A-Z_]+$/.test(code) ? code : "DESKTOP_OPERATION_FAILED",
        },
      };
    }
  });
}
async function account() {
  let trial = null,
    online = false;
  if (server?.cookie) {
    try {
      await server.request("/api/auth/me");
      trial = await server.request("/api/trial");
      online = true;
    } catch {
      await invalidate();
    }
  }
  return {
    user: server?.user || lastUser,
    online,
    trial,
    licensed: permitActive,
    secure_storage: secureKeyReady,
    server_url: config.server_url,
  };
}
app
  .whenReady()
  .then(async () => {
    config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    serverOrigin(config.server_url);
    fs.mkdirSync(app.getPath("userData"), { recursive: true, mode: 0o700 });
    const accountPath = publicIdentity();
    try {
      const keys = privateKey();
      server = new ServerClient(config, keys.key, keys.publicKey);
      secureKeyReady = true;
      if (lastUser) ensureRunner(lastUser);
    } catch {}
    handle("account", account);
    handle("login", async (value) => {
      if (!server) throw Error("DESKTOP_SECURE_STORAGE_REQUIRED");
      if (
        !value ||
        typeof value.email !== "string" ||
        value.email.length > 254 ||
        typeof value.password !== "string" ||
        value.password.length > 128
      )
        throw Error("DESKTOP_INPUT_INVALID");
      await invalidate();
      await server.login(value.email, value.password);
      lastUser = {
        tenant_id: server.user.tenant_id,
        email: server.user.email,
        display_name: server.user.display_name,
      };
      fs.writeFileSync(accountPath, JSON.stringify(lastUser), { mode: 0o600 });
      ensureRunner(lastUser);
      try {
        await renew();
      } catch {}
      return account();
    });
    handle("logout", async () => {
      await invalidate();
      if (server?.cookie)
        try {
          await server.request("/api/auth/logout", {});
        } catch {}
      if (server) {
        server.cookie = "";
        server.csrf = "";
        server.user = null;
      }
      return account();
    });
    handle("start-trial", async () => {
      if (!server) throw Error("DESKTOP_LOGIN_REQUIRED");
      await server.request("/api/trial/start", {});
      return account();
    });
    handle("activate", async () => {
      await renew("activate");
      return account();
    });
    handle("open-account", async () => {
      await shell.openExternal(serverOrigin(config.server_url) + "/account");
      return { opened: true };
    });
    handle("open-workspace", async () => {
      const user = server?.user || lastUser;
      if (!user) throw Error("DESKTOP_LOGIN_REQUIRED");
      const directory = path.join(
        app.getPath("userData"),
        "accounts",
        user.tenant_id,
        "files",
      );
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
      const error = await shell.openPath(directory);
      if (error) throw Error("DESKTOP_FOLDER_UNAVAILABLE");
      return { opened: true };
    });
    handle("import-file", async () => {
      const user = server?.user || lastUser;
      if (!user) throw Error("DESKTOP_LOGIN_REQUIRED");
      const chosen = await dialog.showOpenDialog(window, {
        properties: ["openFile"],
        filters: [
          { name: "Akış verileri", extensions: ["csv", "xlsx", "txt", "json"] },
        ],
      });
      if (chosen.canceled) return { cancelled: true };
      const source = chosen.filePaths[0],
        name = path.basename(source);
      if (
        !/^[a-zA-Z0-9][a-zA-Z0-9_. -]{0,119}\.(csv|xlsx|txt|json)$/i.test(name)
      )
        throw Error("DESKTOP_FILE_NAME_INVALID");
      const info = fs.statSync(source);
      if (!info.isFile() || info.size > 1024 * 1024)
        throw Error("DESKTOP_FILE_TOO_LARGE");
      const directory = path.join(
        app.getPath("userData"),
        "accounts",
        user.tenant_id,
        "files",
      );
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
      fs.copyFileSync(
        source,
        path.join(directory, name),
        fs.constants.COPYFILE_EXCL,
      );
      return { name };
    });
    handle("export-flow", (flowId) =>
      saveFlowExport(flowId, {
        request: (message) =>
          ensureRunner(server?.user || lastUser).request(message),
        chooseFile: (options) => dialog.showSaveDialog(window, options),
      }),
    );
    handle("request", async (value) => {
      if (
        value &&
        typeof value.path === "string" &&
        value.path.startsWith("/api/secrets")
      ) {
        if (Buffer.byteLength(JSON.stringify(value)) > 24000)
          throw Error("DESKTOP_MESSAGE_TOO_LARGE");
        const user = server?.user || lastUser,
          local = vaultFor(user);
        const data = local.request(
          value.method || "GET",
          value.path,
          value.body,
        );
        if (value.method !== "GET")
          await ensureRunner(user).request({
            operation: "secrets",
            values: local.snapshot(),
          });
        return data;
      }
      const request = localRequest(value);
      if (
        request.method === "POST" &&
        /^\/api\/flows\/[a-f0-9-]+\/runs$/.test(
          new URL(request.path, "https://local.invalid").pathname,
        )
      ) {
        if (!server?.user) throw Error("DESKTOP_LOGIN_REQUIRED");
        const flowId = request.path.split("/")[3];
        await server.request("/api/trial/flows", { flow_id: flowId });
        await renew();
      }
      return ensureRunner(server?.user || lastUser).request({
        operation: "request",
        ...request,
      });
    });
    window = new BrowserWindow({
      width: 1440,
      height: 960,
      minWidth: 850,
      minHeight: 650,
      title: "MultiRPA",
      icon: path.join(__dirname, "../renderer/static/desktop-icon.png"),
      backgroundColor: "#f7f8f6",
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webSecurity: true,
        spellcheck: false,
      },
    });
    Menu.setApplicationMenu(null);
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => {
      if (url.split("#")[0] !== pathToFileURL(rendererFile).href)
        event.preventDefault();
    });
    session.defaultSession.setPermissionRequestHandler(
      (_contents, _permission, callback) => callback(false),
    );
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({
        cancel:
          !details.url.startsWith("file:") &&
          !details.url.startsWith("devtools:"),
      }),
    );
    powerMonitor.on("suspend", invalidate);
    powerMonitor.on("resume", invalidate);
    renewTimer = setInterval(() => {
      if (server?.user) renew().catch(invalidate);
    }, 25000);
    await window.loadFile(rendererFile);
    window.show();
    if (process.argv.includes("--smoke")) setTimeout(() => app.quit(), 1500);
  })
  .catch(() => {
    process.stderr.write("MultiRPA desktop configuration failed.\n");
    app.exit(1);
  });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  clearInterval(renewTimer);
  if (runner) runner.close();
});
