"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { serverOrigin } = require("../src/contracts.cjs");
const root = path.resolve(__dirname, "../.."),
  desktop = path.join(root, "desktop"),
  renderer = path.join(desktop, "renderer");
fs.mkdirSync(path.join(renderer, "static"), { recursive: true });
for (const filename of fs.readdirSync(path.join(root, "app/web/static")))
  fs.copyFileSync(
    path.join(root, "app/web/static", filename),
    path.join(renderer, "static", filename),
  );
for (const filename of ["desktop-bootstrap.js", "desktop.css"])
  fs.copyFileSync(
    path.join(desktop, "src", filename),
    path.join(renderer, "static", filename),
  );
fs.copyFileSync(
  path.join(desktop, "assets/icon.png"),
  path.join(renderer, "static/desktop-icon.png"),
);
let html = fs
  .readFileSync(path.join(root, "app/web/templates/index.html"), "utf8")
  .replaceAll("__PRODUCT_NAME__", "MultiRPA")
  .replace('<html lang="tr"', '<html lang="tr" data-workspace="desktop"')
  .replaceAll('"/static/', '"./static/');
html = html.replace(
  '<script src="./static/app.js" defer></script>',
  '<script src="./static/desktop-bootstrap.js" defer></script>\n  <script src="./static/app.js" defer></script>',
);
html = html.replace(
  "</head>",
  "<link rel=\"stylesheet\" href=\"./static/desktop.css\">\n  <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'\">\n</head>",
);
fs.writeFileSync(path.join(renderer, "index.html"), html);
const configFile = path.join(desktop, "deployment.json");
const prior = fs.existsSync(configFile)
  ? JSON.parse(fs.readFileSync(configFile, "utf8"))
  : {};
const config = {
  server_url:
    process.env.MULTIRPA_DESKTOP_SERVER_URL ||
    prior.server_url ||
    "http://127.0.0.1:8770",
  server_public_key:
    process.env.MULTIRPA_DESKTOP_SERVER_PUBLIC_KEY ||
    prior.server_public_key ||
    "",
  browser_allowed_hosts:
    process.env.MULTIRPA_DESKTOP_BROWSER_HOSTS?.split(",").filter(Boolean) ||
    prior.browser_allowed_hosts ||
    [],
  http_allowed_hosts:
    process.env.MULTIRPA_DESKTOP_HTTP_HOSTS?.split(",").filter(Boolean) ||
    prior.http_allowed_hosts ||
    [],
};
config.mail_allowed_hosts =
  process.env.MULTIRPA_DESKTOP_MAIL_HOSTS?.split(",").filter(Boolean) ||
  prior.mail_allowed_hosts ||
  [];
serverOrigin(config.server_url);
for (const hosts of [
  config.browser_allowed_hosts,
  config.http_allowed_hosts,
  config.mail_allowed_hosts,
])
  if (
    !Array.isArray(hosts) ||
    hosts.length > 12 ||
    hosts.some(
      (host) =>
        typeof host !== "string" || !/^([a-z0-9-]+\.)+[a-z]{2,63}$/.test(host),
    )
  )
    throw Error("Invalid reviewed adapter destination hosts.");
if (!/^[A-Za-z0-9_-]{43}$/.test(config.server_public_key))
  throw Error(
    "Set MULTIRPA_DESKTOP_SERVER_PUBLIC_KEY to the server public signing key before preparing the desktop.",
  );
fs.writeFileSync(configFile, JSON.stringify(config, null, 2) + "\n");
console.log(
  "Shared MultiRPA editor copied; deployment origin and public signing key pinned.",
);
