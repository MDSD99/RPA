"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  localRequest,
  serverOrigin,
  releaseOrigin,
} = require("../src/contracts.cjs");
test("IPC allows local flow/history operations, never central auth, shell, generic jobs, or URLs", () => {
  assert.equal(
    localRequest({ path: "/api/flows?limit=20", method: "GET" }).method,
    "GET",
  );
  for (const path of [
    "/api/auth/me",
    "/api/license/activate",
    "/api/jobs",
    "file:///etc/passwd",
    "https://attacker.invalid/api/flows",
    "/api/demo-jobs",
    "/api/../api/auth/login",
  ])
    assert.throws(() => localRequest({ path, method: "POST", body: {} }));
  assert.throws(() =>
    localRequest({
      path: "/api/flows",
      method: "POST",
      body: { graph: "x".repeat(250000) },
    }),
  );
});
test("desktop retry boundary rejects automatic replay", () => {
  const path = "/api/flows/aaaa0000-0000-4000-8000-000000000000/runs";
  assert.throws(
    () => localRequest({ path, method: "POST", body: { max_attempts: 3 } }),
    /RETRY/,
  );
  assert.equal(
    localRequest({ path, method: "POST", body: { max_attempts: 1 } }).path,
    path,
  );
});
test("flow transfer permits only the exact import POST and UUID export GET", () => {
  const exported = "/api/flows/aaaa0000-0000-4000-8000-000000000000/export";
  assert.equal(localRequest({ path: exported, method: "GET" }).path, exported);
  assert.equal(
    localRequest({
      path: "/api/flows/import",
      method: "POST",
      body: { document: {} },
    }).path,
    "/api/flows/import",
  );
  for (const [path, method] of [
    [exported, "POST"],
    [exported, "PUT"],
    [exported + "/more", "GET"],
    [exported + "?path=/etc/passwd", "GET"],
    ["/api/flows/import", "GET"],
    ["/api/flows/import", "PUT"],
    ["/api/flows/import?run=true", "POST"],
    ["/api/flows/other/../import", "POST"],
    ["/api/flows/not-a-uuid/export", "GET"],
    ["/api/jobs/aaaa0000-0000-4000-8000-000000000000/export", "GET"],
  ])
    assert.throws(() => localRequest({ path, method }));
});
test("server origins require HTTPS except loopback development", () => {
  assert.equal(
    serverOrigin("https://app.multirpa.example"),
    "https://app.multirpa.example",
  );
  assert.equal(serverOrigin("http://127.0.0.1:8770"), "http://127.0.0.1:8770");
  for (const url of [
    "http://example.com",
    "https://user:pass@example.com",
    "https://example.com/secret",
    "file:///etc/passwd",
  ])
    assert.throws(() => serverOrigin(url));
});
test("production releases reject loopback, IP, and reserved development origins", () => {
  assert.equal(
    releaseOrigin("https://app.multirpa.com"),
    "https://app.multirpa.com",
  );
  for (const url of [
    "http://127.0.0.1:8770",
    "https://127.0.0.1:8770",
    "https://[::1]:8770",
    "https://localhost",
    "https://app.localhost",
    "https://app.multirpa.example",
    "https://portal.test",
    "https://invalid.invalid",
  ])
    assert.throws(() => releaseOrigin(url), /PRODUCTION_ORIGIN/);
});
