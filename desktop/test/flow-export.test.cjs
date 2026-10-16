"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { saveFlowExport } = require("../src/flow-export.cjs");
const flowId = "aaaa0000-0000-4000-8000-000000000000";
function exported(schemaVersion = 1) {
  return {
    document: {
      format: "multirpa-flow",
      schema_version: schemaVersion,
      flow: {
        name: "Yerel akış",
        description: "",
        graph: {
          nodes: ["start", "end"].map((type, index) => ({
            id: type,
            type,
            ...(schemaVersion === 2 ? { type_version: 1 } : {}),
            label: type,
            position: { x: index * 180, y: 40 },
            config: type === "end" ? { output: {} } : {},
          })),
          edges: [{ source: "start", target: "end", port: "next" }],
        },
      },
    },
    filename: `MultiRPA-flow-${flowId}-v2.multirpa.json`,
  };
}
for (const schemaVersion of [1, 2])
  test(`native export preserves schema ${schemaVersion} in a private file chosen by the dialog`, async (t) => {
    const directory = await fs.mkdtemp(
      path.join(os.tmpdir(), "multirpa-export-"),
    );
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const destination = path.join(directory, "chosen.multirpa.json");
    const response = exported(schemaVersion);
    const result = await saveFlowExport(flowId.toUpperCase(), {
      request: async (message) => {
        assert.deepEqual(message, {
          operation: "request",
          method: "GET",
          path: `/api/flows/${flowId}/export`,
        });
        return response;
      },
      chooseFile: async (options) => {
        assert.equal(options.defaultPath, response.filename);
        assert.ok(options.properties.includes("showOverwriteConfirmation"));
        return { canceled: false, filePath: destination };
      },
    });
    assert.deepEqual(result, {
      cancelled: false,
      filename: "chosen.multirpa.json",
    });
    assert.deepEqual(
      JSON.parse(await fs.readFile(destination, "utf8")),
      response.document,
    );
    if (process.platform !== "win32")
      assert.equal((await fs.stat(destination)).mode & 0o777, 0o600);
  });
test("canceling the native dialog creates no file and reports cancellation", async () => {
  assert.deepEqual(
    await saveFlowExport(flowId, {
      request: async () => exported(),
      chooseFile: async () => ({ canceled: true }),
      writeFile: async () => assert.fail("cancelled export must not write"),
    }),
    { cancelled: true },
  );
});
test("renderer cannot supply a path, payload, object or malformed identity", async () => {
  for (const value of [
    null,
    {},
    { flowId, path: "/etc/passwd", document: exported().document },
    "/etc/passwd",
    flowId + "/export",
    flowId + "?path=x",
  ])
    await assert.rejects(
      saveFlowExport(value, {
        request: async () =>
          assert.fail("invalid identity must not reach runner"),
        chooseFile: async () =>
          assert.fail("invalid identity must not open dialog"),
      }),
      /DESKTOP_FLOW_ID_INVALID/,
    );
});
test("untrusted output metadata, unknown fields and oversized documents fail before dialog", async () => {
  for (const change of [
    (value) => {
      value.filename = "../../private.json";
    },
    (value) => {
      value.filename = `MultiRPA-flow-bbbb0000-0000-4000-8000-000000000000-v1.multirpa.json`;
    },
    (value) => {
      value.document.flow.id = flowId;
    },
    (value) => {
      value.document.schema_version = 3;
    },
    (value) => {
      value.document.flow.description = "x".repeat(128 * 1024);
    },
  ]) {
    const response = exported();
    change(response);
    await assert.rejects(
      saveFlowExport(flowId, {
        request: async () => response,
        chooseFile: async () =>
          assert.fail("invalid output must not open dialog"),
      }),
      /FLOW_DOCUMENT_(?:INVALID|TOO_LARGE)/,
    );
  }
});
test("native export rejects unknown or coerced schema versions before choosing or writing a file", async () => {
  for (const schemaVersion of [
    undefined,
    null,
    false,
    true,
    "1",
    "2",
    0,
    -1,
    1.5,
    3,
    NaN,
    Infinity,
    [],
    {},
    new Number(1),
  ])
    await assert.rejects(
      saveFlowExport(flowId, {
        request: async () => {
          const response = exported();
          response.document.schema_version = schemaVersion;
          return response;
        },
        chooseFile: async () =>
          assert.fail("invalid version must not open dialog"),
        writeFile: async () => assert.fail("invalid version must not write"),
      }),
      /FLOW_DOCUMENT_INVALID/,
    );
});
test(
  "native export refuses a symlink destination and preserves its target",
  { skip: process.platform === "win32" },
  async (t) => {
    const directory = await fs.mkdtemp(
      path.join(os.tmpdir(), "multirpa-export-"),
    );
    t.after(() => fs.rm(directory, { recursive: true, force: true }));
    const target = path.join(directory, "private.txt");
    const link = path.join(directory, "chosen.multirpa.json");
    await fs.writeFile(target, "keep existing data");
    await fs.symlink(target, link);
    await assert.rejects(
      saveFlowExport(flowId, {
        request: async () => exported(),
        chooseFile: async () => ({ canceled: false, filePath: link }),
      }),
      /DESKTOP_FILE_INVALID/,
    );
    assert.equal(await fs.readFile(target, "utf8"), "keep existing data");
  },
);
