"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { LocalVault } = require("../src/local-vault.cjs");
test("local vault persists ciphertext and exposes only versioned metadata", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "multirpa-vault-")),
    filename = path.join(directory, "vault.bin"),
    key = crypto.randomBytes(32);
  const storage = {};
  storage.encryptString = (plain) => {
    const iv = crypto.randomBytes(12),
      cipher = crypto.createCipheriv("aes-256-gcm", key, iv),
      data = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), data]);
  };
  storage.decryptString = (data) => {
    const cipher = crypto.createDecipheriv(
      "aes-256-gcm",
      key,
      data.subarray(0, 12),
    );
    cipher.setAuthTag(data.subarray(12, 28));
    return Buffer.concat([
      cipher.update(data.subarray(28)),
      cipher.final(),
    ]).toString("utf8");
  };
  try {
    const vault = new LocalVault(filename, storage);
    const result = vault.request("PUT", "/api/secrets/report_api", {
      kind: "http",
      values: { authorization: "Bearer sensitive-local-test" },
    });
    assert.equal(result.secret.version, 1);
    assert(!JSON.stringify(result).includes("sensitive-local-test"));
    assert(
      !fs.readFileSync(filename).includes(Buffer.from("sensitive-local-test")),
    );
    assert.deepEqual(
      Object.keys(vault.request("GET", "/api/secrets").items[0]).sort(),
      ["kind", "reference", "updated_at", "version"],
    );
    assert.throws(() => vault.request("GET", "/api/secrets/report_api"));
    assert.throws(
      () =>
        vault.request("PUT", "/api/secrets/report_api", {
          kind: "http",
          values: { authorization: "changed" },
          expected_version: 9,
        }),
      /VERSION/,
    );
    vault.request("PUT", "/api/secrets/report_api", {
      kind: "http",
      values: { authorization: "changed" },
      expected_version: 1,
    });
    assert.equal(vault.read().report_api.values.authorization, "changed");
    vault.request("DELETE", "/api/secrets/report_api", { expected_version: 2 });
    assert.deepEqual(vault.request("GET", "/api/secrets").items, []);
    assert.deepEqual(vault.snapshot(), {});
    assert.equal(vault.read().report_api.version, 2);
    assert.equal(vault.read().report_api.values, undefined);
    assert.throws(
      () =>
        vault.request("DELETE", "/api/secrets/report_api", {
          expected_version: 2,
        }),
      /NOT_FOUND/,
    );
    assert.throws(
      () =>
        vault.request("PUT", "/api/secrets/report_api", {
          kind: "http",
          values: { authorization: "Bearer recreated" },
          expected_version: 2,
        }),
      /VERSION/,
    );
    const recreated = vault.request("PUT", "/api/secrets/report_api", {
      kind: "browser",
      values: { username: "local-user", password: "local-password" },
    });
    assert.equal(recreated.secret.version, 3);
    assert.equal(vault.snapshot().report_api.kind, "browser");
    assert.throws(
      () =>
        vault.request("PUT", "/api/secrets/report_api", {
          kind: "http",
          values: { authorization: "Bearer wrong kind" },
          expected_version: 3,
        }),
      /VERSION/,
    );
    assert.throws(
      () =>
        vault.request("PUT", "/api/secrets/unicode_header", {
          kind: "http",
          values: { authorization: "Bearer ö" },
        }),
      /INPUT/,
    );
    assert.throws(
      () =>
        vault.request("PUT", "/api/secrets/empty_browser", {
          kind: "browser",
          values: { username: "", password: "" },
        }),
      /INPUT/,
    );
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
