"use strict";
const { spawnSync } = require("node:child_process");
const result = spawnSync(
  process.execPath,
  [
    require.resolve("electron-builder/cli"),
    "--linux",
    "tar.gz",
    "--publish",
    "never",
  ],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      ELECTRON_BUILDER_COMPRESSION_LEVEL:
        process.env.ELECTRON_BUILDER_COMPRESSION_LEVEL || "3",
    },
  },
);
process.exit(result.status ?? 1);
