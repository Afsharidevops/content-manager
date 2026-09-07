import crypto from "node:crypto";
import { createRequire } from "node:module";

const require = createRequire("/app/package.json");
const Database = require("better-sqlite3");
const database = new Database("/app/data/db/data.sqlite");
database.pragma("busy_timeout = 30000");

const provisionHermes = process.env.PROVISION_HERMES === "true";
const provisionOpenWebUI = process.env.PROVISION_OPENWEBUI === "true";
const provisionSmartRouter = process.env.PROVISION_SMART_ROUTER === "true";
const provisionN8n = process.env.PROVISION_N8N === "true";
const hermesModelName = process.env.HERMES_MODEL_NAME || "ai";

function provisionKey(keyName) {
  let row = database
    .prepare("SELECT key FROM apiKeys WHERE name = ? AND isActive = 1 ORDER BY createdAt ASC LIMIT 1")
    .get(keyName);
  let status = "reused";
  if (!row?.key) {
    const now = new Date().toISOString();
    // 9router accepts its legacy sk-{random} key format and validates the exact
    // value against this local database. No management/provider credential is used.
    const apiKey = `sk-${crypto.randomBytes(24).toString("hex")}`;
    database
      .prepare("INSERT INTO apiKeys(id, key, name, machineId, isActive, createdAt) VALUES(?, ?, ?, ?, 1, ?)")
      .run(crypto.randomUUID(), apiKey, keyName, null, now);
    row = { key: apiKey };
    status = "created";
  }
  return { key: row.key, status };
}

function upsertCombo(name, models, { createOnly = false } = {}) {
  const now = new Date().toISOString();
  const existing = database.prepare("SELECT id FROM combos WHERE name = ? LIMIT 1").get(name);
  if (existing) {
    if (createOnly) return "preserved";
    database
      .prepare("UPDATE combos SET kind = ?, models = ?, updatedAt = ? WHERE id = ?")
      .run("llm", JSON.stringify(models), now, existing.id);
    return "updated";
  }
  database
    .prepare("INSERT INTO combos(id, name, kind, models, createdAt, updatedAt) VALUES(?, ?, ?, ?, ?, ?)")
    .run(crypto.randomUUID(), name, "llm", JSON.stringify(models), now, now);
  return "created";
}

const openWebUIKey = provisionOpenWebUI
  ? provisionKey("Open WebUI (hermes-linux-stack)")
  : null;
const hermesKey = provisionHermes
  ? provisionKey("Hermes Agent (hermes-linux-stack)")
  : null;
const n8nKey = provisionN8n
  ? provisionKey("n8n (hermes-linux-stack)")
  : null;

let openCodeComboStatus = "not-requested";
let aiComboStatus = "not-requested";
let smartRouterComboStatus = "not-requested";
let freeModelCount = 0;
if (provisionOpenWebUI || (provisionHermes && (hermesModelName === "ai" || provisionSmartRouter))) {
  try {
    const response = await fetch("https://opencode.ai/zen/v1/models", {
      headers: { "x-opencode-client": "desktop" },
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) throw new Error(`OpenCode model catalog returned ${response.status}`);
    const catalog = await response.json();
    const freeModels = (catalog.data || [])
      .map((model) => model?.id)
      .filter((id) => typeof id === "string" && (id.endsWith("-free") || id === "big-pickle"))
      .map((id) => `oc/${id}`);
    freeModelCount = freeModels.length;
    if (freeModelCount === 0) throw new Error("OpenCode returned no currently free models");

    if (provisionOpenWebUI) openCodeComboStatus = upsertCombo("OpenCode-Free", freeModels);
    if (provisionHermes && (hermesModelName === "ai" || provisionSmartRouter)) {
      aiComboStatus = upsertCombo("ai", freeModels);
    }
    if (provisionSmartRouter) {
      // Tier combos are seeded once, then owned by the operator. Reruns must not
      // discard model lists customized in the 9router dashboard.
      const tierNames = [
        process.env.SMART_ROUTER_FAST_MODEL || "combo-fast",
        process.env.SMART_ROUTER_STANDARD_MODEL || "combo-standard",
        process.env.SMART_ROUTER_STRONG_MODEL || "combo-strong",
      ];
      const statuses = tierNames.map((name) =>
        upsertCombo(name, freeModels, { createOnly: true }),
      );
      smartRouterComboStatus = statuses.every((status) => status === "created")
        ? "created"
        : statuses.every((status) => status === "preserved")
          ? "preserved"
          : "partially-created";
    }
  } catch (error) {
    if (provisionOpenWebUI) openCodeComboStatus = "unavailable";
    if (provisionHermes && (hermesModelName === "ai" || provisionSmartRouter)) {
      aiComboStatus = "unavailable";
    }
    if (provisionSmartRouter) smartRouterComboStatus = "unavailable";
    process.stderr.write(`OpenCode free-model combo warning: ${error.message}\n`);
  }
}

database.close();

if (openWebUIKey) {
  process.stdout.write(`OPENWEBUI_API_KEY=${openWebUIKey.key}\n`);
  process.stdout.write(`OPENWEBUI_KEY_STATUS=${openWebUIKey.status}\n`);
}
if (hermesKey) {
  process.stdout.write(`HERMES_API_KEY=${hermesKey.key}\n`);
  process.stdout.write(`HERMES_KEY_STATUS=${hermesKey.status}\n`);
}
if (n8nKey) {
  process.stdout.write(`N8N_API_KEY=${n8nKey.key}\n`);
  process.stdout.write(`N8N_KEY_STATUS=${n8nKey.status}\n`);
}
process.stdout.write(`OPENCODE_COMBO_STATUS=${openCodeComboStatus}\n`);
process.stdout.write(`AI_COMBO_STATUS=${aiComboStatus}\n`);
process.stdout.write(`SMART_ROUTER_COMBO_STATUS=${smartRouterComboStatus}\n`);
process.stdout.write(`OPENCODE_FREE_MODEL_COUNT=${freeModelCount}\n`);
