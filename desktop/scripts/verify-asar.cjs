"use strict";
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../.."),
  pkg = process.argv[2],
  asar = require("@electron/asar");
let count = 0;
const compare = (source, target) => {
  assert(fs.readFileSync(source).equals(asar.extractFile(pkg, target)), target);
  count++;
};
for (const file of fs.readdirSync(path.join(root, "app/web/static")))
  compare(path.join(root, "app/web/static", file), "renderer/static/" + file);
for (const file of fs.readdirSync(path.join(root, "desktop/src")))
  compare(path.join(root, "desktop/src", file), "src/" + file);
for (const file of ["desktop-bootstrap.js", "desktop.css"])
  compare(path.join(root, "desktop/src", file), "renderer/static/" + file);
compare(path.join(root, "desktop/renderer/index.html"), "renderer/index.html");
compare(path.join(root, "desktop/deployment.json"), "deployment.json");
const source = JSON.parse(
    fs.readFileSync(path.join(root, "desktop/package.json")),
  ),
  built = JSON.parse(asar.extractFile(pkg, "package.json"));
for (const key of ["version", "productName", "main"])
  assert.equal(source[key], built[key]);
console.log(
  `ASAR matches ${count} current shared UI, desktop source, template, and pin files.`,
);
