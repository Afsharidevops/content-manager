#!/usr/bin/env node
import { chmod, mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  CREDENTIAL_TYPES,
  MANAGED_NAMES,
  buildCredentialSpecs,
  buildHostedChatWorkflow,
  buildMcpWorkflow,
  fingerprint,
  hostedChatWebhookId,
  workflowComparable,
  workflowFingerprint,
} from "./lib/n8n-workflows.mjs";

const STATE_VERSION = 1;
const SECRET_KEYS = new Set(["apikey", "token", "authorization"]);

export class ReconcileError extends Error {
  constructor(message, { code = "RECONCILE_FAILED", stage, status, cause } = {}) {
    super(message, { cause });
    this.name = "ReconcileError";
    this.code = code;
    this.stage = stage;
    this.status = status;
  }
}

function normalizeApiUrl(value) {
  if (!value) throw new ReconcileError("N8N_API_URL is required", { code: "CONFIG_ERROR" });
  const url = new URL(value);
  if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new ReconcileError("N8N_API_URL must be an HTTP(S) URL without credentials, query, or fragment", {
      code: "CONFIG_ERROR",
    });
  }
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/`;
  return url;
}

function requireValue(value, name) {
  if (!value) throw new ReconcileError(`${name} is required`, { code: "CONFIG_ERROR" });
  return value;
}

function requireHttpUrl(value, name) {
  requireValue(value, name);
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new ReconcileError(`${name} must be a valid HTTP(S) URL`, { code: "CONFIG_ERROR" });
  }
  if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new ReconcileError(
      `${name} must be an HTTP(S) URL without credentials, query, or fragment`,
      { code: "CONFIG_ERROR" },
    );
  }
  return url;
}

function sanitizedText(value, secrets) {
  let result = String(value ?? "");
  for (const secret of secrets) {
    if (secret) result = result.split(secret).join("[REDACTED]");
  }
  return result;
}

export function redact(value, secrets = []) {
  if (typeof value === "string") return sanitizedText(value, secrets);
  if (Array.isArray(value)) return value.map((item) => redact(item, secrets));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        SECRET_KEYS.has(key.toLowerCase()) ? "[REDACTED]" : redact(item, secrets),
      ]),
    );
  }
  return value;
}

export function createN8nClient({ apiUrl, apiKey, fetchImpl = globalThis.fetch }) {
  const baseUrl = normalizeApiUrl(apiUrl);
  requireValue(apiKey, "N8N_API_KEY");
  if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl must be a function");
  const origin = baseUrl.origin;

  async function request(method, path, body, { allowNotFound = false } = {}) {
    const url = new URL(path.replace(/^\/+/, ""), baseUrl);
    if (url.origin !== origin) {
      throw new ReconcileError("Refusing to send the n8n API key to another origin", {
        code: "ORIGIN_VIOLATION",
      });
    }
    const headers = { Accept: "application/json", "X-N8N-API-KEY": apiKey };
    const init = { method, headers, redirect: "manual" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetchImpl(url, init);
    } catch (cause) {
      throw new ReconcileError(`n8n API request failed for ${method} ${url.pathname}`, {
        code: "HTTP_ERROR",
        cause,
      });
    }
    if (allowNotFound && response.status === 404) return null;
    const text = await response.text();
    let payload;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }
    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" ? payload.message || payload.error || "" : payload || "";
      throw new ReconcileError(
        `n8n API ${method} ${url.pathname} returned ${response.status}${detail ? `: ${detail}` : ""}`,
        { code: "API_ERROR", status: response.status },
      );
    }
    return payload;
  }

  async function collectPages(path) {
    const data = [];
    let cursor;
    do {
      const separator = path.includes("?") ? "&" : "?";
      const page = await request(
        "GET",
        cursor ? `${path}${separator}cursor=${encodeURIComponent(cursor)}` : path,
      );
      data.push(...(Array.isArray(page?.data) ? page.data : []));
      cursor = page?.nextCursor || null;
    } while (cursor);
    return { data, nextCursor: null };
  }

  return {
    listWorkflows: (name) =>
      collectPages(`workflows?limit=100&name=${encodeURIComponent(name)}&excludePinnedData=true`),
    getWorkflow: (id) => request("GET", `workflows/${encodeURIComponent(id)}?excludePinnedData=true`, undefined, { allowNotFound: true }),
    createWorkflow: (workflow) => request("POST", "workflows", workflow),
    updateWorkflow: (id, workflow) => request("PUT", `workflows/${encodeURIComponent(id)}`, workflow),
    deleteWorkflow: (id) => request("DELETE", `workflows/${encodeURIComponent(id)}`),
    publishWorkflow: (id, versionId) =>
      request("POST", `workflows/${encodeURIComponent(id)}/publish`, versionId ? { versionId } : {}),
    unpublishWorkflow: (id) => request("POST", `workflows/${encodeURIComponent(id)}/unpublish`, {}),
    listCredentials: () => collectPages("credentials?limit=250"),
    getCredential: (id) => request("GET", `credentials/${encodeURIComponent(id)}`, undefined, { allowNotFound: true }),
    createCredential: (credential) => request("POST", "credentials", credential),
    updateCredential: (id, credential) => request("PATCH", `credentials/${encodeURIComponent(id)}`, credential),
    deleteCredential: (id) => request("DELETE", `credentials/${encodeURIComponent(id)}`),
    getCredentialSchema: (type) => request("GET", `credentials/schema/${encodeURIComponent(type)}`),
  };
}

function initialState() {
  return { version: STATE_VERSION, credentials: {}, workflows: {} };
}

async function readState(stateFile) {
  if (!stateFile) throw new ReconcileError("N8N_STATE_FILE is required", { code: "CONFIG_ERROR" });
  try {
    const parsed = JSON.parse(await readFile(stateFile, "utf8"));
    if (parsed?.version !== STATE_VERSION || !parsed.credentials || !parsed.workflows) {
      throw new Error("unsupported or incomplete state");
    }
    return { state: parsed, firstRun: false };
  } catch (error) {
    if (error.code === "ENOENT") return { state: initialState(), firstRun: true };
    throw new ReconcileError(`Cannot read n8n state: ${error.message}`, { code: "STATE_ERROR" });
  }
}

async function writeStateAtomic(stateFile, state) {
  const directory = dirname(stateFile);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temporary = `${stateFile}.tmp-${process.pid}-${Date.now()}`;
  try {
    await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    await rename(temporary, stateFile);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw new ReconcileError(`Cannot save n8n state: ${error.message}`, { code: "STATE_ERROR" });
  }
}

function listData(response) {
  return Array.isArray(response?.data) ? response.data : [];
}

function assertCredentialSchema(schema, type) {
  const expected = type === CREDENTIAL_TYPES.mcp ? ["token"] : ["apiKey", "url"];
  const properties = schema?.properties || {};
  for (const field of expected) {
    if (!Object.hasOwn(properties, field)) {
      throw new ReconcileError(`Credential schema ${type} is missing required field ${field}`, {
        code: "SCHEMA_CONFLICT",
      });
    }
  }
}

function conflict(message) {
  return new ReconcileError(`${message}; use an explicit replacement command`, { code: "CONFLICT" });
}

async function reconcileCredential({ client, spec, rollbackSpec, prior, allCredentials, journal }) {
  const secretFingerprint = fingerprint(spec);
  if (prior?.id) {
    const existing = await client.getCredential(prior.id);
    if (existing) {
      if (existing.name !== spec.name || existing.type !== spec.type) {
        throw conflict(`Managed credential ${prior.id} drifted from its expected identity`);
      }
      if (allCredentials.some((item) => item.name === spec.name && item.id !== existing.id)) {
        throw conflict(`Credential name ${spec.name} is used by more than the managed object`);
      }
      if (prior.updatedAt && existing.updatedAt && prior.updatedAt !== existing.updatedAt) {
        throw conflict(`Managed credential ${prior.id} has manual drift`);
      }
      if (prior.fingerprint === secretFingerprint) {
        return {
          reference: { id: existing.id, name: existing.name },
          status: "unchanged",
          fingerprint: secretFingerprint,
          updatedAt: existing.updatedAt,
        };
      }
      if (!rollbackSpec || fingerprint(rollbackSpec) !== prior.fingerprint) {
        throw conflict(`Prior secret for managed credential ${prior.id} cannot be proven`);
      }
      journal.push({ kind: "credential-update", id: existing.id, spec: rollbackSpec });
      const updated = await client.updateCredential(existing.id, spec);
      return {
        reference: { id: updated.id, name: updated.name },
        status: "updated",
        fingerprint: secretFingerprint,
        updatedAt: updated.updatedAt,
      };
    }
    const collisions = allCredentials.filter((item) => item.name === spec.name);
    if (collisions.length) throw conflict(`Managed credential ${prior.id} is missing and its name is occupied`);
  } else {
    const collisions = allCredentials.filter((item) => item.name === spec.name);
    if (collisions.length) throw conflict(`Credential name ${spec.name} is already in use`);
  }
  const result = await client.createCredential(spec);
  journal.push({ kind: "created", objectKind: "credential", id: result.id });
  return {
    reference: { id: result.id, name: result.name },
    status: "created",
    fingerprint: secretFingerprint,
    updatedAt: result.updatedAt,
  };
}

async function reconcileWorkflow({ client, desired, prior, journal }) {
  const desiredFingerprint = workflowFingerprint(desired);
  if (prior?.id) {
    const existing = await client.getWorkflow(prior.id);
    if (existing) {
      if (existing.name !== desired.name) throw conflict(`Managed workflow ${prior.id} changed name`);
      if (prior.fingerprint !== workflowFingerprint(existing)) {
        throw conflict(`Managed workflow ${prior.id} has manual drift`);
      }
      if (prior.fingerprint !== desiredFingerprint) {
        journal.push({
          kind: "workflow-update",
          id: existing.id,
          workflow: workflowComparable(existing),
          published: existing.active === true || Boolean(activeVersionId(existing)),
        });
        const updated = await client.updateWorkflow(existing.id, desired);
        return { workflow: updated, status: "updated", fingerprint: desiredFingerprint };
      }
      return { workflow: existing, status: "unchanged", fingerprint: desiredFingerprint };
    }
  }

  const collisions = listData(await client.listWorkflows(desired.name)).filter(
    (workflow) => workflow.name === desired.name,
  );
  if (collisions.length) {
    throw conflict(
      prior?.id
        ? `Managed workflow ${prior.id} is missing and its name is occupied`
        : `Workflow name ${desired.name} is already in use`,
    );
  }
  const result = await client.createWorkflow(desired);
  journal.push({ kind: "created", objectKind: "workflow", id: result.id });
  return { workflow: result, status: "created", fingerprint: desiredFingerprint };
}

function activeVersionId(workflow) {
  return workflow?.activeVersion?.versionId || workflow?.activeVersionId || null;
}

async function setAndVerifyPublication(client, result, desired, journal, shouldPublish) {
  const workflow = result.workflow;
  const desiredFingerprint = workflowFingerprint(desired);
  let status = result.status;
  const isPublished = workflow.active === true || Boolean(activeVersionId(workflow));
  const currentVersionPublished =
    workflow.active === true && activeVersionId(workflow) === workflow.versionId;
  if ((shouldPublish && !currentVersionPublished) || (!shouldPublish && isPublished)) {
    const previousVersionId = activeVersionId(workflow);
    journal.push({ kind: "publication", id: workflow.id, previousVersionId });
    if (shouldPublish) await client.publishWorkflow(workflow.id, workflow.versionId);
    else await client.unpublishWorkflow(workflow.id);
    status = status === "unchanged" ? (shouldPublish ? "published" : "unpublished") : status;
  }
  const verified = await client.getWorkflow(workflow.id);
  const action = shouldPublish ? "publication" : "unpublication";
  if (!verified) throw new ReconcileError(`Workflow ${workflow.id} disappeared after ${action}`);
  if (verified.name !== desired.name || workflowFingerprint(verified) !== desiredFingerprint) {
    throw new ReconcileError(`Workflow ${workflow.id} did not verify after ${action}`, {
      code: "VERIFY_FAILED",
    });
  }
  const verifiedPublished = verified.active === true || Boolean(activeVersionId(verified));
  if (shouldPublish !== verifiedPublished) {
    throw new ReconcileError(
      `Workflow ${workflow.id} is ${shouldPublish ? "not published" : "still published"}`,
      { code: "VERIFY_FAILED" },
    );
  }
  return { workflow: verified, status, fingerprint: desiredFingerprint };
}

async function rollbackMutations(client, journal, rollbackState) {
  const failures = [];
  let stateNeedsWrite = false;
  for (const item of [...journal].reverse()) {
    try {
      if (item.kind === "created") {
        if (item.objectKind === "workflow") await client.deleteWorkflow(item.id);
        else await client.deleteCredential(item.id);
      } else if (item.kind === "credential-update") {
        const restored = await client.updateCredential(item.id, item.spec);
        for (const stateCredential of Object.values(rollbackState.credentials)) {
          if (stateCredential.id === item.id && restored.updatedAt) {
            stateCredential.updatedAt = restored.updatedAt;
            stateNeedsWrite = true;
          }
        }
      } else if (item.kind === "workflow-update") {
        const restored = await client.updateWorkflow(item.id, item.workflow);
        if (item.published) await client.publishWorkflow(item.id, restored.versionId);
        else await client.unpublishWorkflow(item.id);
      } else if (item.kind === "publication") {
        if (item.previousVersionId) await client.publishWorkflow(item.id, item.previousVersionId);
        else await client.unpublishWorkflow(item.id);
      }
    } catch {
      failures.push(`${item.kind} ${item.id}`);
    }
  }
  return { failures, stateNeedsWrite };
}

function requireMcpMode(value) {
  const mode = value || "trigger";
  if (!["instance", "trigger", "off"].includes(mode)) {
    throw new ReconcileError("N8N_MCP_MODE must be instance, trigger, or off", {
      code: "CONFIG_ERROR",
    });
  }
  return mode;
}

function publicUrls(apiUrl, mcpMode, chatWorkflow) {
  const base = new URL(apiUrl);
  base.pathname = base.pathname.replace(/\/api\/v1\/?$/, "/");
  const webhookId = hostedChatWebhookId(workflowComparable(chatWorkflow));
  const mcpPath =
    mcpMode === "trigger"
      ? new URL("mcp/hermes", base).toString()
      : mcpMode === "instance"
        ? new URL("mcp-server/http", base).toString()
        : null;
  return {
    mcp: mcpPath,
    hostedChat: webhookId ? new URL(`webhook/${webhookId}/chat`, base).toString() : null,
  };
}

export async function reconcileN8n({
  apiUrl,
  apiKey,
  mcpMode = "trigger",
  mcpToken,
  routerApiKey,
  routerBaseUrl = "http://smart-router:8080/v1",
  routerModel = "auto",
  previousMcpToken = mcpToken,
  previousRouterApiKey = routerApiKey,
  previousRouterBaseUrl = routerBaseUrl,
  stateFile,
  fetchImpl = globalThis.fetch,
  stateWriter = writeStateAtomic,
} = {}) {
  const mode = requireMcpMode(mcpMode);
  const secrets = [
    apiKey,
    mcpToken,
    routerApiKey,
    previousMcpToken,
    previousRouterApiKey,
  ].filter(Boolean);
  requireValue(apiKey, "N8N_API_KEY");
  if (mode === "trigger") requireValue(mcpToken, "N8N_TRIGGER_MCP_TOKEN");
  requireValue(routerApiKey, "NINEROUTER_API_KEY");
  const selectedRouterUrl = requireHttpUrl(routerBaseUrl, "N8N_ROUTER_BASE_URL").toString().replace(/\/$/, "");
  const rollbackRouterUrl = requireHttpUrl(
    previousRouterBaseUrl,
    "N8N_PREVIOUS_ROUTER_BASE_URL",
  ).toString().replace(/\/$/, "");
  requireValue(routerModel, "N8N_CHAT_MODEL");
  const resolvedStateFile = resolve(requireValue(stateFile, "N8N_STATE_FILE"));
  const client = createN8nClient({ apiUrl, apiKey, fetchImpl });
  const { state: priorState, firstRun } = await readState(resolvedStateFile);
  const nextState = structuredClone(priorState);
  const rollbackState = structuredClone(priorState);
  const journal = [];
  let stage = "connectivity";

  try {
    await client.listWorkflows("__hermes_connectivity_probe__");
    stage = "credential-schemas";
    const schemaRequests = [
      client.getCredentialSchema(CREDENTIAL_TYPES.router),
      client.listCredentials(),
    ];
    if (mode === "trigger") schemaRequests.unshift(client.getCredentialSchema(CREDENTIAL_TYPES.mcp));
    const schemaResults = await Promise.all(schemaRequests);
    const mcpSchema = mode === "trigger" ? schemaResults[0] : null;
    const routerSchema = mode === "trigger" ? schemaResults[1] : schemaResults[0];
    const credentialList = mode === "trigger" ? schemaResults[2] : schemaResults[1];
    if (mcpSchema) assertCredentialSchema(mcpSchema, CREDENTIAL_TYPES.mcp);
    assertCredentialSchema(routerSchema, CREDENTIAL_TYPES.router);
    const routerSpec = {
      name: MANAGED_NAMES.routerCredential,
      type: CREDENTIAL_TYPES.router,
      data: { apiKey: routerApiKey, url: selectedRouterUrl },
    };
    const rollbackRouterSpec = {
      ...routerSpec,
      data: { apiKey: previousRouterApiKey, url: rollbackRouterUrl },
    };
    const allCredentials = listData(credentialList);

    let mcpCredential = null;
    let mcpWorkflow = null;
    if (mode === "trigger") {
      const credentials = buildCredentialSpecs({
        mcpToken,
        routerApiKey,
        routerBaseUrl: selectedRouterUrl,
      });
      const rollbackCredentials = buildCredentialSpecs({
        mcpToken: previousMcpToken,
        routerApiKey: previousRouterApiKey,
        routerBaseUrl: rollbackRouterUrl,
      });
      stage = "mcp-credential";
      mcpCredential = await reconcileCredential({
        client,
        spec: credentials.mcp,
        rollbackSpec: rollbackCredentials.mcp,
        prior: priorState.credentials.mcp,
        allCredentials,
        journal,
      });
      nextState.credentials.mcp = {
        id: mcpCredential.reference.id,
        name: mcpCredential.reference.name,
        type: credentials.mcp.type,
        fingerprint: mcpCredential.fingerprint,
        ...(mcpCredential.updatedAt ? { updatedAt: mcpCredential.updatedAt } : {}),
      };
    } else if (priorState.credentials.mcp?.id) {
      stage = "mcp-credential";
      const retained = await client.getCredential(priorState.credentials.mcp.id);
      if (!retained) throw conflict(`Managed credential ${priorState.credentials.mcp.id} is missing`);
      if (retained.name !== MANAGED_NAMES.mcpCredential || retained.type !== CREDENTIAL_TYPES.mcp) {
        throw conflict(`Managed credential ${retained.id} drifted from its expected identity`);
      }
      mcpCredential = {
        reference: { id: retained.id, name: retained.name },
        status: "retained",
      };
    }

    stage = "router-credential";
    const routerCredential = await reconcileCredential({
      client,
      spec: routerSpec,
      rollbackSpec: rollbackRouterSpec,
      prior: priorState.credentials.router,
      allCredentials,
      journal,
    });
    nextState.credentials.router = {
      id: routerCredential.reference.id,
      name: routerCredential.reference.name,
      type: routerSpec.type,
      fingerprint: routerCredential.fingerprint,
      ...(routerCredential.updatedAt ? { updatedAt: routerCredential.updatedAt } : {}),
    };

    if (mode === "trigger") {
      const mcpDesired = buildMcpWorkflow(mcpCredential.reference);
      stage = "mcp-workflow";
      mcpWorkflow = await reconcileWorkflow({
        client,
        desired: mcpDesired,
        prior: priorState.workflows.mcp,
        journal,
      });
      stage = "mcp-publish";
      mcpWorkflow = await setAndVerifyPublication(client, mcpWorkflow, mcpDesired, journal, true);
      nextState.workflows.mcp = {
        id: mcpWorkflow.workflow.id,
        name: mcpDesired.name,
        fingerprint: mcpWorkflow.fingerprint,
      };
    } else if (priorState.workflows.mcp?.id) {
      if (!mcpCredential?.reference) {
        throw conflict("Managed MCP workflow state exists without its retained credential");
      }
      const mcpDesired = buildMcpWorkflow(mcpCredential.reference);
      stage = "mcp-workflow";
      mcpWorkflow = await reconcileWorkflow({
        client,
        desired: mcpDesired,
        prior: priorState.workflows.mcp,
        journal,
      });
      stage = "mcp-unpublish";
      mcpWorkflow = await setAndVerifyPublication(client, mcpWorkflow, mcpDesired, journal, false);
    }

    const chatDesired = buildHostedChatWorkflow(routerCredential.reference, routerModel);
    stage = "chat-workflow";
    let chatWorkflow = await reconcileWorkflow({
      client,
      desired: chatDesired,
      prior: priorState.workflows.chat,
      journal,
    });
    stage = "chat-publish";
    chatWorkflow = await setAndVerifyPublication(client, chatWorkflow, chatDesired, journal, true);
    nextState.workflows.chat = {
      id: chatWorkflow.workflow.id,
      name: chatDesired.name,
      fingerprint: chatWorkflow.fingerprint,
    };

    stage = "state";
    nextState.version = STATE_VERSION;
    nextState.mcpMode = mode;
    nextState.routerBaseUrl = selectedRouterUrl;
    nextState.routerModel = routerModel;
    nextState.updatedAt = new Date().toISOString();
    await stateWriter(resolvedStateFile, nextState);

    return {
      status: "ok",
      mcpMode: mode,
      credentials: {
        ...(mcpCredential ? { mcp: { id: mcpCredential.reference.id, status: mcpCredential.status } } : {}),
        router: { id: routerCredential.reference.id, status: routerCredential.status },
      },
      workflows: {
        ...(mcpWorkflow
          ? {
              mcp: {
                id: mcpWorkflow.workflow.id,
                status: mcpWorkflow.status,
                published: mode === "trigger",
              },
            }
          : {}),
        chat: { id: chatWorkflow.workflow.id, status: chatWorkflow.status, published: true },
      },
      urls: publicUrls(apiUrl, mode, chatWorkflow.workflow),
      stateFile: resolvedStateFile,
    };
  } catch (error) {
    const rollback = await rollbackMutations(client, journal, rollbackState);
    let rollbackStateFailure = null;
    if (!firstRun && rollback.stateNeedsWrite) {
      try {
        rollbackState.updatedAt = new Date().toISOString();
        await writeStateAtomic(resolvedStateFile, rollbackState);
      } catch (stateError) {
        rollbackStateFailure = stateError;
      }
    }
    const safeMessage = sanitizedText(error?.message || String(error), secrets);
    const rollbackFailures = [...rollback.failures];
    if (rollbackStateFailure) rollbackFailures.push("rollback state");
    const suffix = rollbackFailures.length
      ? `; rollback failed for ${rollbackFailures.join(", ")}`
      : "";
    throw new ReconcileError(`${safeMessage}${suffix}`, {
      code: rollbackFailures.length ? "ROLLBACK_FAILED" : error?.code || "RECONCILE_FAILED",
      stage,
      status: error?.status,
      cause: error,
    });
  }
}

export function configFromEnv(env = process.env) {
  return {
    apiUrl: env.N8N_API_URL,
    apiKey: env.N8N_API_KEY,
    mcpMode: env.N8N_MCP_MODE || "trigger",
    mcpToken: env.N8N_TRIGGER_MCP_TOKEN || env.N8N_MCP_TOKEN,
    routerApiKey: env.NINEROUTER_API_KEY,
    previousMcpToken:
      env.N8N_PREVIOUS_TRIGGER_MCP_TOKEN || env.N8N_PREVIOUS_MCP_TOKEN ||
      env.N8N_TRIGGER_MCP_TOKEN || env.N8N_MCP_TOKEN,
    previousRouterApiKey: env.N8N_PREVIOUS_NINEROUTER_API_KEY || env.NINEROUTER_API_KEY,
    routerBaseUrl: env.N8N_ROUTER_BASE_URL || "http://smart-router:8080/v1",
    routerModel: env.N8N_CHAT_MODEL || "auto",
    previousRouterBaseUrl:
      env.N8N_PREVIOUS_ROUTER_BASE_URL || env.N8N_ROUTER_BASE_URL || "http://smart-router:8080/v1",
    stateFile: env.N8N_STATE_FILE,
  };
}

async function main() {
  try {
    const result = await reconcileN8n(configFromEnv());
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    const safe = redact(
      { status: "error", code: error.code, stage: error.stage, message: error.message },
      [
        process.env.N8N_API_KEY,
        process.env.N8N_TRIGGER_MCP_TOKEN,
        process.env.N8N_MCP_TOKEN,
        process.env.NINEROUTER_API_KEY,
        process.env.N8N_PREVIOUS_TRIGGER_MCP_TOKEN,
        process.env.N8N_PREVIOUS_MCP_TOKEN,
        process.env.N8N_PREVIOUS_NINEROUTER_API_KEY,
      ],
    );
    process.stderr.write(`${JSON.stringify(safe)}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
