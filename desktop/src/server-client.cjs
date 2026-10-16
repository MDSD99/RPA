"use strict";
const crypto = require("node:crypto");
const { performance } = require("node:perf_hooks");
const { serverOrigin } = require("./contracts.cjs");
const keyObject = (raw) =>
  crypto.createPublicKey({
    key: Buffer.concat([
      Buffer.from("302a300506032b6570032100", "hex"),
      Buffer.from(raw, "base64url"),
    ]),
    format: "der",
    type: "spki",
  });
class ServerClient {
  constructor(config, privateKey, publicKey) {
    this.origin = serverOrigin(config.server_url);
    this.serverKey = config.server_public_key;
    if (!/^[A-Za-z0-9_-]{43}$/.test(this.serverKey))
      throw Error("DESKTOP_SERVER_KEY_REQUIRED");
    this.privateKey = privateKey;
    this.publicKey = publicKey;
    this.cookie = "";
    this.csrf = "";
    this.user = null;
  }
  async request(path, body, method = body === undefined ? "GET" : "POST") {
    // Every caller is in main; paths never come from arbitrary renderer requests.
    if (
      ![
        "/api/auth/me",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/trial",
        "/api/trial/start",
        "/api/trial/flows",
        "/api/license/challenge",
        "/api/license/activate",
        "/api/license/renew",
      ].includes(path)
    )
      throw Error("DESKTOP_SERVER_ROUTE_INVALID");
    const headers = {
      Accept: "application/json",
      Origin: this.origin,
      "X-RPA-Request": "1",
    };
    if (this.cookie) headers.Cookie = this.cookie;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.csrf) headers["X-CSRF-Token"] = this.csrf;
    let response;
    try {
      response = await fetch(this.origin + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        redirect: "error",
        signal: AbortSignal.timeout(10000),
      });
    } catch {
      throw Error("DESKTOP_SERVER_UNAVAILABLE");
    }
    if (Number(response.headers.get("content-length") || 0) > 65536)
      throw Error("DESKTOP_SERVER_RESPONSE_INVALID");
    const reader = response.body.getReader(),
      chunks = [];
    let total = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > 65536) {
        await reader.cancel();
        throw Error("DESKTOP_SERVER_RESPONSE_INVALID");
      }
      chunks.push(Buffer.from(value));
    }
    const source = Buffer.concat(chunks).toString("utf8");
    let data;
    try {
      data = JSON.parse(source);
    } catch {
      throw Error("DESKTOP_SERVER_RESPONSE_INVALID");
    }
    if (!response.ok) {
      if (response.status === 401) {
        this.cookie = "";
        this.csrf = "";
        this.user = null;
      }
      throw Error(data.error?.code || "DESKTOP_SERVER_DENIED");
    }
    for (const cookie of response.headers.getSetCookie()) {
      const pair = cookie.split(";", 1)[0];
      if (
        /^(?:__Host-)?multirpa_customer=/.test(pair) ||
        /^(?:__Host-)?multirpa_customer_session=/.test(pair)
      )
        this.cookie = pair;
      // Cookie name is deployment-dependent; accept only the returned customer cookie.
      else if (/^(?:__Host-)?multirpa-session=/.test(pair)) this.cookie = pair;
    }
    if (data.csrf_token) this.csrf = data.csrf_token;
    if (data.user) this.user = data.user;
    return data;
  }
  async login(email, password) {
    await this.request("/api/auth/login", { email, password });
    return this.user;
  }
  async lease(purpose) {
    const started = performance.now();
    const challenge = await this.request("/api/license/challenge", {
      public_key: this.publicKey,
      purpose,
    });
    const signature = crypto
      .sign(null, Buffer.from(challenge.message, "utf8"), this.privateKey)
      .toString("base64url");
    const envelope = await this.request("/api/license/" + purpose, {
      challenge_id: challenge.challenge_id,
      signature,
    });
    if (
      typeof envelope.payload !== "string" ||
      !crypto.verify(
        null,
        Buffer.from(envelope.payload, "utf8"),
        keyObject(this.serverKey),
        Buffer.from(envelope.signature, "base64url"),
      )
    )
      throw Error("DESKTOP_LICENSE_INVALID");
    const permit = JSON.parse(envelope.payload);
    if (
      permit.tenant_id !== this.user?.tenant_id ||
      permit.key_thumbprint !==
        crypto
          .createHash("sha256")
          .update(Buffer.from(this.publicKey, "base64url"))
          .digest("hex")
    )
      throw Error("DESKTOP_LICENSE_INVALID");
    return { envelope, elapsed_ms: performance.now() - started };
  }
}
module.exports = { ServerClient, keyObject };
