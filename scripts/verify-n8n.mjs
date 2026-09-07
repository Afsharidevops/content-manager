#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import {
  MANAGED_NAMES,
  buildHostedChatWorkflow,
  hostedChatWebhookId,
  workflowFingerprint,
} from "./lib/n8n-workflows.mjs";

const MCP_PROTOCOL_VERSION = "2025-03-26";
const REQUEST_TIMEOUT_MS = 15_000;
const SECRET_ENV_KEYS = [
  "N8N_API_KEY",
  "N8N_TRIGGER_MCP_TOKEN",
  "N8N_INSTANCE_MCP_TOKEN",
  "N8N_MCP_TOKEN",
];

export class VerifyError extends Error {
  constructor(message, { code = "VERIFY_FAILED", check } = {}) {
    super(message);
    this.name = "VerifyError";
    this.code = code;
    this.check = check;
  }
}

function requireValue(value, name) {
  if (!value) throw new VerifyError(`${name} is required`, { code: "CONFIG_ERROR", check: "configuration" });
  return value;
}

function httpUrl(value, name) {
  requireValue(value, name);
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new VerifyError(`${name} must be a valid HTTP(S) URL`, {
      code: "CONFIG_ERROR",
      check: "configuration",
    });
  }
  if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.hash) {
    throw new VerifyError(`${name} must be an HTTP(S) URL without credentials or a fragment`, {
      code: "CONFIG_ERROR",
      check: "configuration",
    });
  }
  return url;
}

function redactText(value, secrets) {
  let text = String(value ?? "");
  for (const secret of secrets) {
    if (secret) text = text.split(secret).join("[REDACTED]");
  }
  return text;
}

function serviceBaseUrl(apiUrl) {
  const base = new URL(apiUrl);
  base.pathname = base.pathname.replace(/\/api\/v1\/?$/, "/");
  base.search = "";
  return base;
}

function endpoint(base, path) {
  const url = new URL(base);
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
  url.search = "";
  url.hash = "";
  return url;
}

async function fetchChecked(fetchImpl, url, init, check) {
  let response;
  try {
    response = await fetchImpl(url, {
      ...init,
      redirect: "manual",
      signal: init?.signal || AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new VerifyError(`${check} request failed`, { code: "HTTP_ERROR", check });
  }
  return response;
}

async function requireOk(fetchImpl, url, init, check) {
  const response = await fetchChecked(fetchImpl, url, init, check);
  if (!response.ok) {
    throw new VerifyError(`${check} returned HTTP ${response.status}`, {
      code: "HTTP_ERROR",
      check,
    });
  }
  return response;
}

async function readManagedState(stateFile) {
  requireValue(stateFile, "N8N_STATE_FILE");
  let state;
  try {
    state = JSON.parse(await readFile(resolve(stateFile), "utf8"));
  } catch {
    throw new VerifyError("N8N_STATE_FILE is unreadable or invalid", {
      code: "STATE_ERROR",
      check: "state",
    });
  }

  if (state?.version !== 1 || !state.workflows || typeof state.workflows !== "object") {
    throw new VerifyError("n8n state has an unsupported or incomplete format", {
      code: "STATE_ERROR",
      check: "state",
    });
  }

  const expected = { chat: MANAGED_NAMES.chatWorkflow };
  if (state.workflows.mcp) expected.mcp = MANAGED_NAMES.mcpWorkflow;
  const workflows = {};
  for (const [key, expectedName] of Object.entries(expected)) {
    const item = state.workflows[key];
    if (!item || typeof item.id !== "string" || !item.id || item.name !== expectedName) {
      throw new VerifyError(`managed ${key} workflow state is missing or invalid`, {
        code: "STATE_ERROR",
        check: "state",
      });
    }
    if (typeof item.fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(item.fingerprint)) {
      throw new VerifyError(`managed ${key} workflow fingerprint is missing or invalid`, {
        code: "STATE_ERROR",
        check: "state",
      });
    }
    workflows[key] = { id: item.id, name: expectedName, fingerprint: item.fingerprint };
  }
  if (workflows.mcp?.id === workflows.chat.id) {
    throw new VerifyError("managed workflow IDs must be distinct", {
      code: "STATE_ERROR",
      check: "state",
    });
  }
  return workflows;
}

function isPublished(workflow) {
  return workflow?.active === true || Boolean(workflow?.activeVersion?.versionId || workflow?.activeVersionId);
}

async function verifyManagedWorkflows({ fetchImpl, apiUrl, apiKey, workflows, mcpMode }) {
  if (!apiKey) return "skipped (N8N_API_KEY not set)";
  const origin = apiUrl.origin;
  for (const item of Object.values(workflows)) {
    const url = endpoint(apiUrl, `workflows/${encodeURIComponent(item.id)}`);
    if (url.origin !== origin) {
      throw new VerifyError("refusing to send N8N_API_KEY to another origin", {
        code: "ORIGIN_VIOLATION",
        check: "workflows",
      });
    }
    const response = await requireOk(
      fetchImpl,
      url,
      { headers: { Accept: "application/json", "X-N8N-API-KEY": apiKey } },
      "workflow API",
    );
    let workflow;
    try {
      workflow = await response.json();
    } catch {
      throw new VerifyError("workflow API returned invalid JSON", {
        code: "PROTOCOL_ERROR",
        check: "workflows",
      });
    }
    if (workflow?.id !== item.id || workflow?.name !== item.name) {
      throw new VerifyError("managed workflow identity does not match state", {
        code: "VERIFY_FAILED",
        check: "workflows",
      });
    }
    if (workflowFingerprint(workflow) !== item.fingerprint) {
      throw new VerifyError("managed workflow definition does not match its persisted fingerprint", {
        code: "VERIFY_FAILED",
        check: "workflows",
      });
    }
    const shouldPublish = item !== workflows.mcp || mcpMode === "trigger";
    if (isPublished(workflow) !== shouldPublish) {
      throw new VerifyError(
        `managed ${item === workflows.mcp ? "mcp" : "chat"} workflow has the wrong publication state`,
        { code: "VERIFY_FAILED", check: "workflows" },
      );
    }
  }
  return "ok";
}

function parseSse(text) {
  const payloads = [];
  for (const block of text.replace(/\r\n/g, "\n").split(/\n\n+/)) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data || data === "[DONE]") continue;
    try {
      payloads.push(JSON.parse(data));
    } catch {
      throw new VerifyError("MCP returned invalid SSE JSON", {
        code: "PROTOCOL_ERROR",
        check: "mcp",
      });
    }
  }
  return payloads;
}

async function parseMcpResponse(response, requestId, { allowEmpty = false } = {}) {
  const text = await response.text();
  if (!text.trim()) {
    if (allowEmpty) return null;
    throw new VerifyError("MCP returned an empty response", {
      code: "PROTOCOL_ERROR",
      check: "mcp",
    });
  }

  const contentType = response.headers.get("content-type")?.toLowerCase() || "";
  let messages;
  try {
    if (contentType.includes("text/event-stream")) messages = parseSse(text);
    else if (contentType.includes("application/json")) messages = [JSON.parse(text)];
    else {
      throw new VerifyError("MCP returned an unsupported content type", {
        code: "PROTOCOL_ERROR",
        check: "mcp",
      });
    }
  } catch (error) {
    if (error instanceof VerifyError) throw error;
    throw new VerifyError("MCP returned invalid JSON", {
      code: "PROTOCOL_ERROR",
      check: "mcp",
    });
  }

  const message = messages.find((item) => item?.id === requestId);
  if (!message || message.jsonrpc !== "2.0") {
    throw new VerifyError("MCP response did not contain the matching JSON-RPC result", {
      code: "PROTOCOL_ERROR",
      check: "mcp",
    });
  }
  if (message.error) {
    throw new VerifyError("MCP returned a JSON-RPC error", {
      code: "MCP_ERROR",
      check: "mcp",
    });
  }
  if (!Object.hasOwn(message, "result")) {
    throw new VerifyError("MCP response is missing a result", {
      code: "PROTOCOL_ERROR",
      check: "mcp",
    });
  }
  return message.result;
}

function calculatorSucceeded(result) {
  if (!result || result.isError === true) return false;
  const text = Array.isArray(result.content)
    ? result.content.map((item) => (typeof item?.text === "string" ? item.text : "")).join(" ")
    : "";
  if (!text) return false;
  return /(^|\D)5(?:\.0+)?(\D|$)/.test(text);
}

function instanceSearchResult(result) {
  if (!result || result.isError === true) return null;
  let payload = result.structuredContent;
  if (!payload && Array.isArray(result.content)) {
    const text = result.content.find((item) => item?.type === "text" && typeof item.text === "string")?.text;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        return null;
      }
    }
  }
  if (!payload || !Array.isArray(payload.data) || !Number.isInteger(payload.count) || payload.count < 0) {
    return null;
  }
  if (payload.data.length > 1 || payload.count < payload.data.length) return null;
  for (const workflow of payload.data) {
    if (!workflow || typeof workflow.id !== "string" || !workflow.id ||
        (workflow.name !== null && typeof workflow.name !== "string")) return null;
  }
  return payload;
}

function requireMcpMode(value) {
  const mode = value || "trigger";
  if (!["instance", "trigger", "off"].includes(mode)) {
    throw new VerifyError("N8N_MCP_MODE must be instance, trigger, or off", {
      code: "CONFIG_ERROR",
      check: "configuration",
    });
  }
  return mode;
}

export async function verifyMcp({
  fetchImpl = globalThis.fetch,
  mcpUrl,
  mcpToken,
  mode = "trigger",
}) {
  const selectedMode = requireMcpMode(mode);
  if (selectedMode === "off") return { status: "skipped (N8N_MCP_MODE=off)", session: "not applicable" };
  const urlName = selectedMode === "instance" ? "N8N_INSTANCE_MCP_URL" : "N8N_TRIGGER_MCP_URL";
  const tokenName = selectedMode === "instance" ? "N8N_INSTANCE_MCP_TOKEN" : "N8N_TRIGGER_MCP_TOKEN";
  const url = httpUrl(mcpUrl, urlName);
  requireValue(mcpToken, tokenName);
  const accept = "application/json, text/event-stream";
  const commonHeaders = { Accept: accept, "Content-Type": "application/json" };
  const initialize = {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "hermes-n8n-verifier", version: "1" },
    },
  };

  const unauthenticated = await fetchChecked(
    fetchImpl,
    url,
    { method: "POST", headers: commonHeaders, body: JSON.stringify(initialize) },
    "MCP unauthenticated probe",
  );
  await unauthenticated.body?.cancel().catch(() => {});
  if (![401, 403].includes(unauthenticated.status)) {
    throw new VerifyError("MCP accepted a request without authentication", {
      code: "AUTH_ERROR",
      check: "mcp-auth",
    });
  }

  let sessionId;
  const authenticatedHeaders = () => ({
    ...commonHeaders,
    Authorization: `Bearer ${mcpToken}`,
    ...(sessionId ? { "Mcp-Session-Id": sessionId } : {}),
  });

  async function post(body, requestId, { allowEmpty = false } = {}) {
    const response = await requireOk(
      fetchImpl,
      url,
      { method: "POST", headers: authenticatedHeaders(), body: JSON.stringify(body) },
      "MCP",
    );
    const suppliedSessionId = response.headers.get("mcp-session-id");
    if (suppliedSessionId) sessionId = suppliedSessionId;
    return parseMcpResponse(response, requestId, { allowEmpty });
  }

  let verificationSucceeded = false;
  try {
    const initialized = await post(initialize, initialize.id);
    if (typeof initialized?.protocolVersion !== "string") {
      throw new VerifyError("MCP initialize result is invalid", {
        code: "PROTOCOL_ERROR",
        check: "mcp",
      });
    }

    await post(
      { jsonrpc: "2.0", method: "notifications/initialized", params: {} },
      undefined,
      { allowEmpty: true },
    );

    const tools = await post({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, 2);
    if (!Array.isArray(tools?.tools)) {
      throw new VerifyError("MCP tools/list result is invalid", {
        code: "PROTOCOL_ERROR",
        check: "mcp",
      });
    }
    const toolNames = new Set(tools.tools.map((tool) => tool?.name).filter((name) => typeof name === "string"));
    if (selectedMode === "instance") {
      // Require the stable Instance MCP workflow core. Newer n8n releases
      // expose additional version-gated tools that are useful but not required
      // to authenticate Hermes to the Instance MCP endpoint.
      for (const requiredName of [
        "search_workflows",
        "get_workflow_details",
        "execute_workflow",
      ]) {
        if (!toolNames.has(requiredName)) {
          throw new VerifyError(`Instance MCP ${requiredName} tool is missing`, {
            code: "VERIFY_FAILED",
            check: "mcp",
          });
        }
      }
      const search = await post(
        {
          jsonrpc: "2.0",
          id: 3,
          method: "tools/call",
          params: {
            name: "search_workflows",
            arguments: { limit: 1, query: MANAGED_NAMES.chatWorkflow, sortBy: "updatedAt:desc" },
          },
        },
        3,
      );
      if (!instanceSearchResult(search)) {
        throw new VerifyError("Instance MCP search_workflows returned an invalid result", {
          code: "VERIFY_FAILED",
          check: "mcp",
        });
      }
    } else {
      if (!toolNames.has("Calculator")) {
        throw new VerifyError("MCP Calculator tool is missing", {
          code: "VERIFY_FAILED",
          check: "mcp",
        });
      }
      const calculation = await post(
        {
          jsonrpc: "2.0",
          id: 3,
          method: "tools/call",
          params: { name: "Calculator", arguments: { input: "2+3" } },
        },
        3,
      );
      if (!calculatorSucceeded(calculation)) {
        throw new VerifyError("MCP Calculator did not successfully evaluate the readiness input", {
          code: "VERIFY_FAILED",
          check: "mcp",
        });
      }
    }
    verificationSucceeded = true;
    return { status: "ok", session: sessionId ? "established" : "not supplied" };
  } finally {
    if (sessionId) {
      let closeResponse;
      try {
        closeResponse = await fetchChecked(
          fetchImpl,
          url,
          { method: "DELETE", headers: authenticatedHeaders() },
          "MCP session close",
        );
        await closeResponse.body?.cancel().catch(() => {});
      } catch (error) {
        if (verificationSucceeded) throw error;
      }
      if (verificationSucceeded && closeResponse && !closeResponse.ok) {
        throw new VerifyError(`MCP session close returned HTTP ${closeResponse.status}`, {
          code: "HTTP_ERROR",
          check: "mcp",
        });
      }
    }
  }
}

export async function verifyN8n({
  apiUrl,
  apiKey,
  mcpMode = "trigger",
  mcpUrl,
  mcpToken,
  stateFile,
  routerHealthUrl = "http://smart-router:8080/ready",
  smartRouterUrl,
  fetchImpl = globalThis.fetch,
} = {}) {
  const secrets = [apiKey, mcpToken].filter(Boolean);
  try {
    if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl must be a function");
    const selectedMode = requireMcpMode(mcpMode);
    const parsedApiUrl = httpUrl(apiUrl, "N8N_API_URL");
    const selectedHealthUrl = smartRouterUrl
      ? endpoint(httpUrl(smartRouterUrl, "SMART_ROUTER_URL"), "ready")
      : httpUrl(routerHealthUrl, "N8N_ROUTER_HEALTH_URL");
    const workflows = await readManagedState(stateFile);
    const base = serviceBaseUrl(parsedApiUrl);
    const webhookId = hostedChatWebhookId(buildHostedChatWorkflow({ id: "verification", name: "verification" }));
    if (!webhookId) {
      throw new VerifyError("managed hosted chat webhook ID is missing", {
        code: "VERIFY_FAILED",
        check: "hosted-chat",
      });
    }

    await requireOk(fetchImpl, endpoint(base, "healthz"), { headers: { Accept: "application/json" } }, "n8n healthz");
    await requireOk(
      fetchImpl,
      selectedHealthUrl,
      { headers: { Accept: "application/json" } },
      "n8n model router health",
    );
    // GET on the chat webhook serves only the static chat shell, which n8n
    // returns to anonymous callers by design. Authentication is enforced on the
    // POST that actually submits a message, so probe that instead.
    const hostedChat = await fetchChecked(
      fetchImpl,
      endpoint(base, `webhook/${encodeURIComponent(webhookId)}/chat`),
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ action: "sendMessage", chatInput: "verification", sessionId: "verification" }),
      },
      "hosted chat authentication",
    );
    await hostedChat.body?.cancel().catch(() => {});
    if (![401, 403].includes(hostedChat.status) && !(hostedChat.status >= 300 && hostedChat.status < 400)) {
      throw new VerifyError("hosted chat did not require n8n user authentication", {
        code: "AUTH_ERROR",
        check: "hosted-chat",
      });
    }

    if (selectedMode !== "trigger" && workflows.mcp && !apiKey) {
      throw new VerifyError(
        "N8N_API_KEY is required to verify that the retained MCP Server Trigger is unpublished",
        { code: "CONFIG_ERROR", check: "workflows" },
      );
    }
    const workflowStatus = await verifyManagedWorkflows({
      fetchImpl,
      apiUrl: parsedApiUrl,
      apiKey,
      workflows,
      mcpMode: selectedMode,
    });
    const mcp = await verifyMcp({ fetchImpl, mcpUrl, mcpToken, mode: selectedMode });

    return {
      status: "ok",
      checks: {
        state: "ok",
        healthz: "ok",
        modelRouter: "ok",
        hostedChat: "ok",
        workflows: workflowStatus,
        mcp: mcp.status,
        mcpSession: mcp.session,
      },
    };
  } catch (error) {
    const message = redactText(error?.message || "verification failed", secrets);
    throw new VerifyError(message, {
      code: error?.code || "VERIFY_FAILED",
      check: error?.check || "verification",
    });
  }
}

export function configFromEnv(env = process.env) {
  const mcpMode = env.N8N_MCP_MODE || (env.N8N_MCP_TOKEN ? "trigger" : "off");
  return {
    apiUrl: env.N8N_API_URL,
    apiKey: env.N8N_API_KEY,
    mcpMode,
    mcpUrl:
      mcpMode === "instance"
        ? env.N8N_INSTANCE_MCP_URL
        : mcpMode === "trigger"
          ? env.N8N_TRIGGER_MCP_URL || env.N8N_MCP_URL
          : undefined,
    mcpToken:
      mcpMode === "instance"
        ? env.N8N_INSTANCE_MCP_TOKEN
        : mcpMode === "trigger"
          ? env.N8N_TRIGGER_MCP_TOKEN || env.N8N_MCP_TOKEN
          : undefined,
    stateFile: env.N8N_STATE_FILE,
    routerHealthUrl: env.N8N_ROUTER_HEALTH_URL || "http://smart-router:8080/ready",
    ...(env.SMART_ROUTER_URL ? { smartRouterUrl: env.SMART_ROUTER_URL } : {}),
  };
}

async function main() {
  try {
    const result = await verifyN8n(configFromEnv());
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    const secrets = SECRET_ENV_KEYS.map((key) => process.env[key]).filter(Boolean);
    const output = {
      status: "error",
      code: error.code || "VERIFY_FAILED",
      check: error.check || "verification",
      message: redactText(error.message, secrets),
    };
    process.stderr.write(`${JSON.stringify(output)}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
