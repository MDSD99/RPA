"use strict";
const fs = require("node:fs");
const path = require("node:path");
const fields = {
  http: ["authorization"],
  smtp: ["host", "port", "sender", "security", "username", "password"],
  browser: ["username", "password"],
};
class LocalVault {
  constructor(filename, safeStorage) {
    this.filename = filename;
    this.storage = safeStorage;
  }
  read() {
    if (!fs.existsSync(this.filename)) return Object.create(null);
    return Object.assign(
      Object.create(null),
      JSON.parse(this.storage.decryptString(fs.readFileSync(this.filename))),
    );
  }
  snapshot() {
    return Object.fromEntries(
      Object.entries(this.read()).filter(
        ([_reference, value]) => !value.deleted_at,
      ),
    );
  }
  write(vault) {
    const plain = JSON.stringify(vault);
    if (Buffer.byteLength(plain) > 120000)
      throw Error("DESKTOP_VAULT_TOO_LARGE");
    fs.mkdirSync(path.dirname(this.filename), { recursive: true, mode: 0o700 });
    const temporary = this.filename + ".new";
    fs.writeFileSync(temporary, this.storage.encryptString(plain), {
      mode: 0o600,
    });
    fs.renameSync(temporary, this.filename);
  }
  request(method, route, body) {
    const vault = this.read();
    if (method === "GET" && route === "/api/secrets")
      return {
        items: Object.entries(vault)
          .filter(([_reference, value]) => !value.deleted_at)
          .map(([reference, value]) => ({
            reference,
            kind: value.kind,
            version: value.version,
            updated_at: value.updated_at,
          })),
      };
    const match = /^\/api\/secrets\/([a-z][a-z0-9_-]{0,79})$/.exec(route);
    if (
      !match ||
      !["PUT", "DELETE"].includes(method) ||
      !body ||
      typeof body !== "object" ||
      Array.isArray(body)
    )
      throw Error("DESKTOP_SECRET_INPUT_INVALID");
    const reference = match[1],
      prior = vault[reference],
      active = prior && !prior.deleted_at;
    if (method === "DELETE" && !active) throw Error("DESKTOP_SECRET_NOT_FOUND");
    if (
      active &&
      (typeof body.expected_version !== "number" ||
        !Number.isInteger(body.expected_version) ||
        body.expected_version !== prior.version)
    )
      throw Error("DESKTOP_SECRET_VERSION_CONFLICT");
    if (!active && body.expected_version !== undefined)
      throw Error("DESKTOP_SECRET_VERSION_CONFLICT");
    if (method === "DELETE") {
      if (!active) throw Error("DESKTOP_SECRET_NOT_FOUND");
      if (Object.keys(body).some((key) => key !== "expected_version"))
        throw Error("DESKTOP_SECRET_INPUT_INVALID");
      vault[reference] = {
        kind: prior.kind,
        version: prior.version,
        updated_at: new Date().toISOString(),
        deleted_at: new Date().toISOString(),
      };
      this.write(vault);
      return { deleted: true };
    }
    if (
      Object.keys(body).some(
        (key) => !["kind", "values", "expected_version"].includes(key),
      ) ||
      !Object.hasOwn(fields, body.kind) ||
      !body.values ||
      typeof body.values !== "object" ||
      Array.isArray(body.values)
    )
      throw Error("DESKTOP_SECRET_INPUT_INVALID");
    if (active && body.kind !== prior.kind)
      throw Error("DESKTOP_SECRET_VERSION_CONFLICT");
    if (
      !active &&
      Object.values(vault).filter((value) => !value.deleted_at).length >= 100
    )
      throw Error("DESKTOP_SECRET_LIMIT_REACHED");
    const names = Object.keys(body.values),
      expected = fields[body.kind];
    if (
      names.length !== expected.length ||
      names.some(
        (key) =>
          !expected.includes(key) ||
          typeof body.values[key] !== "string" ||
          body.values[key].length > 4096 ||
          /\p{C}/u.test(body.values[key]),
      )
    )
      throw Error("DESKTOP_SECRET_INPUT_INVALID");
    if (
      body.kind === "http" &&
      (!body.values.authorization ||
        /[^\x00-\x7f]/.test(body.values.authorization))
    )
      throw Error("DESKTOP_SECRET_INPUT_INVALID");
    if (
      body.kind === "browser" &&
      (!body.values.username || !body.values.password)
    )
      throw Error("DESKTOP_SECRET_INPUT_INVALID");
    if (
      body.kind === "smtp" &&
      (!(
        (body.values.security === "ssl" && body.values.port === "465") ||
        (body.values.security === "starttls" && body.values.port === "587")
      ) ||
        !body.values.host ||
        /\s/u.test(body.values.host) ||
        !/^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(body.values.sender) ||
        Boolean(body.values.username) !== Boolean(body.values.password))
    )
      throw Error("DESKTOP_SECRET_INPUT_INVALID");
    const stored = {
      kind: body.kind,
      values: { ...body.values },
      version: (prior?.version || 0) + 1,
      updated_at: new Date().toISOString(),
    };
    vault[reference] = stored;
    this.write(vault);
    return {
      secret: {
        reference,
        kind: stored.kind,
        version: stored.version,
        updated_at: stored.updated_at,
      },
    };
  }
}
module.exports = { LocalVault };
