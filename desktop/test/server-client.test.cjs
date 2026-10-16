"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { ServerClient } = require("../src/server-client.cjs");
test("main retains customer cookie/CSRF privately and proves a nonce with its device key", async () => {
  const signing = crypto.generateKeyPairSync("ed25519"),
    device = crypto.generateKeyPairSync("ed25519");
  const raw = (key) =>
    key
      .export({ type: "spki", format: "der" })
      .subarray(-32)
      .toString("base64url");
  const tenant = "aaaa0000-0000-4000-8000-000000000000",
    flow = "bbbb0000-0000-4000-8000-000000000000";
  const calls = [],
    original = global.fetch;
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    const body = options.body ? JSON.parse(options.body) : null;
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Origin, "https://app.example");
    if (url.endsWith("/api/auth/login"))
      return new Response(
        JSON.stringify({
          user: { tenant_id: tenant, email: "client@example.test" },
          csrf_token: "opaque-csrf",
        }),
        {
          headers: {
            "Set-Cookie":
              "__Host-multirpa_customer=opaque-cookie; Path=/; Secure; HttpOnly; SameSite=Strict",
          },
        },
      );
    assert.equal(
      options.headers.Cookie,
      "__Host-multirpa_customer=opaque-cookie",
    );
    assert.equal(options.headers["X-CSRF-Token"], "opaque-csrf");
    if (url.endsWith("/challenge")) {
      assert.equal(body.public_key, raw(device.publicKey));
      return new Response(
        JSON.stringify({
          challenge_id: "opaque-challenge",
          message: "nonce exact bytes",
          expires_at: "later",
        }),
      );
    }
    if (url.endsWith("/renew")) {
      assert(
        crypto.verify(
          null,
          Buffer.from("nonce exact bytes"),
          device.publicKey,
          Buffer.from(body.signature, "base64url"),
        ),
      );
      const payload = JSON.stringify({
        tenant_id: tenant,
        key_thumbprint: crypto
          .createHash("sha256")
          .update(Buffer.from(raw(device.publicKey), "base64url"))
          .digest("hex"),
      });
      return new Response(
        JSON.stringify({
          payload,
          signature: crypto
            .sign(null, Buffer.from(payload), signing.privateKey)
            .toString("base64url"),
          public_key: raw(signing.publicKey),
        }),
      );
    }
    if (url.endsWith("/api/trial/flows")) {
      assert.deepEqual(body, { flow_id: flow });
      return new Response("{}");
    }
    throw Error("unexpected route");
  };
  try {
    const client = new ServerClient(
      {
        server_url: "https://app.example",
        server_public_key: raw(signing.publicKey),
      },
      device.privateKey,
      raw(device.publicKey),
    );
    await client.login("client@example.test", "never retained");
    await client.request("/api/trial/flows", { flow_id: flow });
    const lease = await client.lease("renew");
    assert(lease.elapsed_ms >= 0);
    assert.equal(typeof lease.envelope.payload, "string");
    assert.equal(calls.length, 4);
    await assert.rejects(
      client.request("/api/flows", { graph: "private flow content" }),
    );
  } finally {
    global.fetch = original;
  }
});
