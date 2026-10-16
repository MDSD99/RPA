"use strict";
// Stage a built artifact for authenticated portal downloads; no external publishing.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { releaseOrigin } = require("../src/contracts.cjs");
const root = path.resolve(__dirname, "../.."),
  desktop = path.join(root, "desktop"),
  directory = path.join(root, ".runtime/releases");
const platform = process.argv[2],
  source = process.argv[3];
if (!["linux", "windows"].includes(platform) || !source)
  throw Error(
    "Usage: node scripts/publish-release.cjs linux|windows <built-artifact>",
  );
const config = JSON.parse(
  fs.readFileSync(path.join(desktop, "deployment.json"), "utf8"),
);
const version = JSON.parse(
  fs.readFileSync(path.join(desktop, "package.json"), "utf8"),
).version;
const channel =
  process.env.MULTIRPA_DESKTOP_RELEASE === "1" ? "production" : "development";
if (channel === "production") releaseOrigin(config.server_url);
const extension = platform === "linux" ? ".tar.gz" : ".exe";
if (!source.endsWith(extension))
  throw Error("Artifact extension does not match target platform.");
const filename = `MultiRPA-${version}-${platform}-x64${extension}`;
fs.mkdirSync(directory, { recursive: true, mode: 0o750 });
const destination = path.join(directory, filename),
  temporary = destination + ".new";
fs.copyFileSync(path.resolve(source), temporary);
fs.renameSync(temporary, destination);
const digest = crypto.createHash("sha256");
const stream = fs.createReadStream(destination);
stream.on("data", (data) => digest.update(data));
stream.on("end", () => {
  const manifestPath = path.join(directory, "manifest.json");
  const prior = fs.existsSync(manifestPath)
    ? JSON.parse(fs.readFileSync(manifestPath, "utf8"))
    : {};
  const compatible =
    prior.channel === channel &&
    prior.server_url === config.server_url &&
    prior.server_public_key === config.server_public_key;
  const items = compatible
    ? (prior.items || []).filter((item) => item.platform !== platform)
    : [];
  items.push({
    platform,
    filename,
    version,
    sha256: digest.digest("hex"),
    size: fs.statSync(destination).size,
  });
  fs.writeFileSync(
    manifestPath + ".new",
    JSON.stringify(
      {
        schema: 1,
        channel,
        server_url: config.server_url,
        server_public_key: config.server_public_key,
        items,
      },
      null,
      2,
    ) + "\n",
    { mode: 0o640 },
  );
  fs.renameSync(manifestPath + ".new", manifestPath);
  console.log(`Staged ${filename} (${channel}) for approved portal accounts.`);
});
stream.on("error", () => {
  throw Error("Could not hash staged artifact.");
});
