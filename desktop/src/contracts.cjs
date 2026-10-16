"use strict";
const UUID = "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}";
const { isIP } = require("node:net");
const item = new RegExp(
  `^/api/(flows|jobs|transactions)/${UUID}(?:/(runs|transactions|cancel))?$`,
  "i",
);
const spec = new RegExp(`^/api/flows/runs/by-job/${UUID}$`, "i");
const flowExport = new RegExp(`^/api/flows/${UUID}/export$`, "i");
function exportFlowId(value) {
  if (typeof value !== "string" || !new RegExp(`^${UUID}$`, "i").test(value))
    throw Error("DESKTOP_FLOW_ID_INVALID");
  return value.toLowerCase();
}
function localRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("DESKTOP_MESSAGE_INVALID");
  const { path, method = "GET", body } = value;
  if (
    typeof path !== "string" ||
    path.length > 4096 ||
    !["GET", "POST", "PUT"].includes(method)
  )
    throw Error("DESKTOP_ROUTE_INVALID");
  const url = new URL(path, "https://local.invalid");
  if (
    url.origin !== "https://local.invalid" ||
    !path.startsWith("/api/") ||
    url.hash
  )
    throw Error("DESKTOP_ROUTE_INVALID");
  const route = url.pathname;
  if (
    (route === "/api/flows/import" || flowExport.test(route)) &&
    path !== route
  )
    throw Error("DESKTOP_ROUTE_INVALID");
  const read =
    [
      "/api/overview",
      "/api/workflows",
      "/api/workers",
      "/api/flows",
      "/api/jobs",
      "/api/transactions",
    ].includes(route) ||
    item.test(route) ||
    spec.test(route) ||
    flowExport.test(route);
  const write =
    (method === "POST" &&
      (route === "/api/flows" ||
        route === "/api/flows/import" ||
        new RegExp(`^/api/flows/${UUID}/runs$`, "i").test(route) ||
        new RegExp(`^/api/jobs/${UUID}/cancel$`, "i").test(route))) ||
    (method === "PUT" && new RegExp(`^/api/flows/${UUID}$`, "i").test(route));
  if (!(method === "GET" ? read : write))
    throw Error("DESKTOP_OPERATION_UNSUPPORTED");
  if (
    body !== undefined &&
    (!body || typeof body !== "object" || Array.isArray(body))
  )
    throw Error("DESKTOP_MESSAGE_INVALID");
  if (Buffer.byteLength(JSON.stringify(value), "utf8") > 240000)
    throw Error("DESKTOP_MESSAGE_TOO_LARGE");
  if (route.endsWith("/runs") && method === "POST" && body?.max_attempts !== 1)
    throw Error("DESKTOP_RETRY_UNSUPPORTED");
  return { path, method, body };
}
function serverOrigin(value) {
  const url = new URL(value);
  const local = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if (
    (url.protocol !== "https:" && !(url.protocol === "http:" && local)) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw Error("DESKTOP_SERVER_INVALID");
  return url.origin;
}
function releaseOrigin(value) {
  const origin = serverOrigin(value),
    url = new URL(origin);
  if (
    url.protocol !== "https:" ||
    isIP(url.hostname.replace(/^\[|\]$/g, "")) ||
    !url.hostname.includes(".") ||
    /\.(?:localhost|test|invalid|example)$/.test(url.hostname)
  )
    throw Error("DESKTOP_PRODUCTION_ORIGIN_REQUIRED");
  return origin;
}
module.exports = { localRequest, serverOrigin, releaseOrigin, exportFlowId };
