"use strict";
const fs = require("node:fs/promises");
const path = require("node:path");
const { exportFlowId } = require("./contracts.cjs");
const MAX_DOCUMENT_BYTES = 128 * 1024;
async function writePrivateDocument(filename, serialized) {
  try {
    if (!(await fs.lstat(filename)).isFile())
      throw Error("DESKTOP_FILE_INVALID");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const file = await fs.open(
    filename,
    fs.constants.O_WRONLY |
      fs.constants.O_CREAT |
      (fs.constants.O_NOFOLLOW || 0) |
      (fs.constants.O_NONBLOCK || 0),
    0o600,
  );
  try {
    if (!(await file.stat()).isFile()) throw Error("DESKTOP_FILE_INVALID");
    await file.chmod(0o600);
    await file.truncate(0);
    await file.writeFile(serialized, "utf8");
  } finally {
    await file.close();
  }
}
function exactKeys(value, names) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === names.length &&
    names.every((key) => Object.hasOwn(value, key))
  );
}
// Only the trusted runner can supply the document. The renderer supplies one
// UUID; the native dialog supplies the destination. No general file IPC exists.
async function saveFlowExport(
  flowId,
  { request, chooseFile, writeFile = writePrivateDocument },
) {
  const id = exportFlowId(flowId);
  const exported = await request({
    operation: "request",
    method: "GET",
    path: `/api/flows/${id}/export`,
  });
  const document = exported?.document;
  if (
    !exactKeys(document, ["format", "schema_version", "flow"]) ||
    document.format !== "multirpa-flow" ||
    (document.schema_version !== 1 && document.schema_version !== 2) ||
    !exactKeys(document.flow, ["name", "description", "graph"]) ||
    typeof document.flow.name !== "string" ||
    typeof document.flow.description !== "string" ||
    !exactKeys(document.flow.graph, ["nodes", "edges"]) ||
    !Array.isArray(document.flow.graph.nodes) ||
    !Array.isArray(document.flow.graph.edges) ||
    typeof exported.filename !== "string" ||
    !new RegExp(`^MultiRPA-flow-${id}-v[1-9][0-9]*\\.multirpa\\.json$`).test(
      exported.filename,
    )
  )
    throw Error("FLOW_DOCUMENT_INVALID");
  const serialized = JSON.stringify(document);
  if (Buffer.byteLength(serialized, "utf8") > MAX_DOCUMENT_BYTES)
    throw Error("FLOW_DOCUMENT_TOO_LARGE");
  const chosen = await chooseFile({
    title: "Akışı dışa aktar",
    buttonLabel: "Dışa aktar",
    defaultPath: exported.filename,
    filters: [{ name: "MultiRPA akışı", extensions: ["multirpa.json"] }],
    properties: ["createDirectory", "showOverwriteConfirmation"],
  });
  if (chosen.canceled || !chosen.filePath) return { cancelled: true };
  await writeFile(chosen.filePath, serialized);
  return { cancelled: false, filename: path.basename(chosen.filePath) };
}
module.exports = { saveFlowExport };
