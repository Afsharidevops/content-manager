import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  REGISTRY_SCHEMA_VERSION,
  ToolsRegistryError,
  missingCredentials,
  parseToolsRegistry,
  readToolsRegistry,
  resolvePlaceholders,
  summarizeRegistry,
  toolsForConsumer,
} from "../scripts/lib/tools-registry.mjs";

function document(tools) {
  return { schema_version: REGISTRY_SCHEMA_VERSION, tools };
}

function entry(overrides = {}) {
  return {
    id: "media-studio",
    title: "Media Studio",
    kind: "openapi",
    base_url: "http://media-studio:8850",
    spec_url: "http://media-studio:8850/openapi.json",
    auth: { type: "bearer", env: "MEDIA_STUDIO_API_TOKEN" },
    consumers: ["bot", "router", "n8n"],
    capabilities: ["media.image"],
    ...overrides,
  };
}

async function registryFile(t, contents) {
  const directory = await mkdtemp(join(os.tmpdir(), "tools-registry-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, "tools.json");
  await writeFile(path, contents, "utf8");
  return path;
}

test("the shipped registry parses and filters by consumer", async () => {
  const registry = readToolsRegistry("content/config/tools.json");
  const ids = registry.tools.map((tool) => tool.id);
  assert.ok(ids.includes("media-studio"));
  assert.deepEqual(
    toolsForConsumer(registry.tools, "n8n").map((tool) => tool.id).sort(),
    ["media-studio", "smart-router"],
  );
});

test("placeholders resolve from the environment", () => {
  assert.equal(resolvePlaceholders("${HOST}/v1", { HOST: "http://x" }), "http://x/v1");
  assert.equal(resolvePlaceholders("${MISSING}/v1", {}), "/v1");
});

test("invalid documents are rejected", () => {
  assert.throws(() => parseToolsRegistry({ schema_version: 99, tools: [] }), ToolsRegistryError);
  assert.throws(() => parseToolsRegistry(document([entry({ kind: "grpc" })])), ToolsRegistryError);
  assert.throws(() => parseToolsRegistry(document([entry({ id: "" })])), ToolsRegistryError);
  assert.throws(
    () => parseToolsRegistry(document([entry({ auth: { type: "bearer" } })])),
    ToolsRegistryError,
  );
  assert.throws(
    () => parseToolsRegistry(document([entry(), entry()])),
    ToolsRegistryError,
  );
  assert.throws(
    () => parseToolsRegistry(document([entry({ kind: "mcp", url: "", base_url: "" })])),
    ToolsRegistryError,
  );
});

test("a missing registry file is not an error", async (t) => {
  assert.deepEqual(readToolsRegistry(""), { path: "", tools: [] });
  const existing = await registryFile(t, "{}");
  const absent = readToolsRegistry(join(existing, "..", "absent.json"));
  assert.deepEqual(absent.tools, []);
  assert.deepEqual(summarizeRegistry("", "n8n").tools, []);
});

test("credentials are reported when the environment is unset", () => {
  const tools = parseToolsRegistry(document([entry()]));
  assert.deepEqual(missingCredentials(tools, {}), [
    { id: "media-studio", env: "MEDIA_STUDIO_API_TOKEN" },
  ]);
  assert.deepEqual(missingCredentials(tools, { MEDIA_STUDIO_API_TOKEN: "token" }), []);
});

test("summarizeRegistry reports entries, warnings, and missing credentials", async (t) => {
  const path = await registryFile(t, JSON.stringify(document([entry()])));
  const summary = summarizeRegistry(path, "n8n", {});
  assert.equal(summary.count, 1);
  assert.equal(summary.warning, "");
  assert.deepEqual(summary.missingCredentials, [
    { id: "media-studio", env: "MEDIA_STUDIO_API_TOKEN" },
  ]);
  assert.equal(summary.tools[0].id, "media-studio");

  const broken = await registryFile(t, "{not json");
  const invalid = summarizeRegistry(broken, "n8n", {});
  assert.equal(invalid.count, 0);
  assert.match(invalid.warning, /not valid JSON/);
});
