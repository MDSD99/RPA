"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { releaseOrigin } = require("../src/contracts.cjs");
module.exports = async (context) => {
  const platform = context.electronPlatformName;
  const deployment = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../deployment.json"), "utf8"),
  );
  if (process.env.MULTIRPA_DESKTOP_RELEASE === "1")
    releaseOrigin(deployment.server_url);
  const marker = JSON.parse(
    fs.readFileSync(
      path.join(__dirname, "../runner-dist/platform.json"),
      "utf8",
    ),
  );
  if (marker.platform !== platform)
    throw Error(
      "Build the Python runner on the target platform before packaging.",
    );
  const binary =
    platform === "win32" ? "multirpa-runner.exe" : "multirpa-runner";
  if (
    !fs.existsSync(
      path.join(__dirname, "../runner-dist/multirpa-runner", binary),
    )
  )
    throw Error("Native Python runner missing.");
};
