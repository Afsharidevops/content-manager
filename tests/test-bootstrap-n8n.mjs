import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  CREDENTIAL_TYPES,
  MANAGED_NAMES,
  MCP_PATH,
  NINEROUTER_BASE_URL,
  SMART_ROUTER_BASE_URL,
  buildHostedChatWorkflow,
  buildMcpWorkflow,
  workflowFingerprint,
} from "../scripts/lib/n8n-workflows.mjs";
import { createN8nClient, reconcileN8n, redact } from "../scripts/bootstrap-n8n.mjs";

const API_KEY = "owner-api-key-secret";
const MCP_TOKEN = "mcp-token-secret";
const ROUTER_KEY = "router-key-secret";

async function fixture(t, options = {}) {
  const credentials = new Map();
  const workflows = new Map();
  const calls = [];
  let credentialSequence = 0;
  let workflowSequence = 0;
  let failure = options.failure;

  function json(response, status, value) {
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(JSON.stringify(value));
  }

  // Real n8n assigns a random webhookId to every webhook-bearing trigger node
  // that was submitted without one, so a template omitting it can never match
  // its own round-trip fingerprint. Reproduce that here.
  const WEBHOOK_NODE_TYPES = new Set([
    "@n8n/n8n-nodes-langchain.mcpTrigger",
    "@n8n/n8n-nodes-langchain.chatTrigger",
  ]);

  function workflowResponse(body, id) {
    const nodes = (body?.nodes || []).map((node, index) =>
      WEBHOOK_NODE_TYPES.has(node.type) && !node.webhookId
        ? { ...node, webhookId: `generated-webhook-${id}-${index}` }
        : node,
    );
    return structuredClone({
      ...body,
      nodes,
      id,
      active: false,
      versionId: `version-${id}-1`,
      activeVersion: null,
    });
  }

  const server = http.createServer(async (request, response) => {
    const bodyText = await new Promise((resolve) => {
      let value = "";
      request.setEncoding("utf8");
      request.on("data", (part) => (value += part));
      request.on("end", () => resolve(value));
    });
    const body = bodyText ? JSON.parse(bodyText) : undefined;
    const url = new URL(request.url, "http://fixture.invalid");
    calls.push({ method: request.method, path: url.pathname, search: url.search, headers: request.headers, body });

    if (request.headers["x-n8n-api-key"] !== API_KEY) return json(response, 401, { message: "bad key" });
    let deferredFailure = null;
    if (failure && failure.method === request.method && failure.path === url.pathname) {
      const selected = failure;
      if (failure.once !== false) failure = null;
      if (selected.afterMutation) deferredFailure = selected;
      else return json(response, selected.status || 500, { message: selected.message || "fixture failure" });
    }
    const reply = (status, value) =>
      deferredFailure
        ? json(response, deferredFailure.status || 500, {
            message: deferredFailure.message || "fixture failure after mutation",
          })
        : json(response, status, value);

    const path = url.pathname.replace(/^\/api\/v1\/?/, "");
    if (request.method === "GET" && path === "credentials/schema/httpBearerAuth") {
      if (options.badSchema === "httpBearerAuth") return json(response, 200, { type: "object", properties: {} });
      return json(response, 200, { type: "object", properties: { token: { type: "string" } }, required: ["token"] });
    }
    if (request.method === "GET" && path === "credentials/schema/openAiApi") {
      if (options.badSchema === "openAiApi") return json(response, 200, { type: "object", properties: { apiKey: {} } });
      return json(response, 200, {
        type: "object",
        properties: { apiKey: { type: "string" }, url: { type: "string" } },
        required: ["apiKey"],
      });
    }
    if (request.method === "GET" && path === "credentials") {
      return json(response, 200, { data: [...credentials.values()].map(({ data, ...item }) => item), nextCursor: null });
    }
    if (request.method === "POST" && path === "credentials") {
      const id = `credential-${++credentialSequence}`;
      const item = {
        id,
        name: body.name,
        type: body.type,
        data: structuredClone(body.data),
        updatedAt: `credential-time-${id}-1`,
      };
      credentials.set(id, item);
      return json(response, 200, { id, name: item.name, type: item.type, updatedAt: item.updatedAt });
    }

    let match = path.match(/^credentials\/([^/]+)$/);
    if (match) {
      const id = decodeURIComponent(match[1]);
      const item = credentials.get(id);
      if (!item) return json(response, 404, { message: "not found" });
      if (request.method === "GET") {
        const { data, ...publicItem } = item;
        return json(response, 200, publicItem);
      }
      if (request.method === "PATCH") {
        const revision = Number(item.updatedAt?.match(/-(\d+)$/)?.[1] || 1) + 1;
        const updated = {
          id,
          name: body.name ?? item.name,
          type: body.type ?? item.type,
          data: body.data ?? item.data,
          updatedAt: `credential-time-${id}-${revision}`,
        };
        credentials.set(id, updated);
        return reply(200, {
          id,
          name: updated.name,
          type: updated.type,
          updatedAt: updated.updatedAt,
        });
      }
      if (request.method === "DELETE") {
        credentials.delete(id);
        return json(response, 200, { id, name: item.name, type: item.type });
      }
    }

    if (request.method === "GET" && path === "workflows") {
      const name = url.searchParams.get("name");
      const data = [...workflows.values()].filter((item) => !name || item.name.includes(name));
      return json(response, 200, { data: structuredClone(data), nextCursor: null });
    }
    if (request.method === "POST" && path === "workflows") {
      const id = `workflow-${++workflowSequence}`;
      const item = workflowResponse(body, id);
      workflows.set(id, item);
      return json(response, 200, structuredClone(item));
    }

    match = path.match(/^workflows\/([^/]+)\/publish$/);
    if (match && request.method === "POST") {
      const id = decodeURIComponent(match[1]);
      const item = workflows.get(id);
      if (!item) return json(response, 404, { message: "not found" });
      item.active = true;
      item.activeVersion = { versionId: body?.versionId || item.versionId };
      return reply(200, structuredClone(item));
    }
    match = path.match(/^workflows\/([^/]+)\/unpublish$/);
    if (match && request.method === "POST") {
      const id = decodeURIComponent(match[1]);
      const item = workflows.get(id);
      if (!item) return json(response, 404, { message: "not found" });
      item.active = false;
      item.activeVersion = null;
      return json(response, 200, structuredClone(item));
    }
    match = path.match(/^workflows\/([^/]+)$/);
    if (match) {
      const id = decodeURIComponent(match[1]);
      const item = workflows.get(id);
      if (!item) return json(response, 404, { message: "not found" });
      if (request.method === "GET") return json(response, 200, structuredClone(item));
      if (request.method === "PUT") {
        const revision = Number(item.versionId.match(/-(\d+)$/)?.[1] || 1) + 1;
        const updated = {
          ...workflowResponse(body, id),
          versionId: `version-${id}-${revision}`,
          active: item.active,
          activeVersion: item.active ? { versionId: `version-${id}-${revision}` } : null,
        };
        workflows.set(id, updated);
        return reply(200, structuredClone(updated));
      }
      if (request.method === "DELETE") {
        workflows.delete(id);
        return json(response, 200, structuredClone(item));
      }
    }
    json(response, 404, { message: `unhandled ${request.method} ${path}` });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const address = server.address();
  return {
    apiUrl: `http://127.0.0.1:${address.port}/api/v1`,
    credentials,
    workflows,
    calls,
    setFailure(value) {
      failure = value;
    },
    seedCredential(item) {
      credentials.set(item.id, structuredClone(item));
    },
    seedWorkflow(item) {
      workflows.set(item.id, structuredClone(item));
    },
  };
}

async function tempState(t) {
  const directory = await mkdtemp(join(os.tmpdir(), "bootstrap-n8n-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return join(directory, "state.json");
}

function input(server, stateFile) {
  return {
    apiUrl: server.apiUrl,
    apiKey: API_KEY,
    mcpMode: "trigger",
    mcpToken: MCP_TOKEN,
    routerApiKey: ROUTER_KEY,
    stateFile,
  };
}

function countCalls(server, method, suffix) {
  return server.calls.filter((call) => call.method === method && call.path.endsWith(suffix)).length;
}

test("workflow builders emit the approved exact node contracts", () => {
  const mcp = buildMcpWorkflow({ id: "bearer-id", name: "Bearer" });
  assert.equal(mcp.name, MANAGED_NAMES.mcpWorkflow);
  assert.deepEqual(mcp.nodes.map(({ type, typeVersion }) => ({ type, typeVersion })), [
    { type: "@n8n/n8n-nodes-langchain.mcpTrigger", typeVersion: 2 },
    { type: "@n8n/n8n-nodes-langchain.toolCalculator", typeVersion: 1 },
  ]);
  assert.deepEqual(mcp.nodes[0].parameters, { authentication: "bearerAuth", path: MCP_PATH });
  assert.deepEqual(mcp.nodes[0].credentials.httpBearerAuth, { id: "bearer-id", name: "Bearer" });
  // Every webhook-bearing trigger must pin its webhookId, or n8n generates one
  // and the workflow can never match its own persisted fingerprint.
  assert.match(mcp.nodes[0].webhookId, /^[0-9a-f-]{36}$/);
  assert.match(buildHostedChatWorkflow({ id: "x", name: "y" }).nodes[0].webhookId, /^[0-9a-f-]{36}$/);
  assert.deepEqual(mcp.connections.Calculator.ai_tool[0][0], {
    node: "MCP Server Trigger",
    type: "ai_tool",
    index: 0,
  });

  const chat = buildHostedChatWorkflow({ id: "openai-id", name: "OpenAI" });
  assert.deepEqual(chat.nodes.map(({ type, typeVersion }) => ({ type, typeVersion })), [
    { type: "@n8n/n8n-nodes-langchain.chatTrigger", typeVersion: 1.4 },
    { type: "@n8n/n8n-nodes-langchain.agent", typeVersion: 3.1 },
    { type: "@n8n/n8n-nodes-langchain.lmChatOpenAi", typeVersion: 1.3 },
  ]);
  assert.deepEqual(chat.nodes[0].parameters, {
    public: true,
    mode: "hostedChat",
    authentication: "n8nUserAuth",
    options: { responseMode: "streaming" },
  });
  assert.deepEqual(chat.nodes[2].parameters.model, { mode: "id", value: "auto" });
  assert.deepEqual(
    buildHostedChatWorkflow({ id: "openai-id", name: "OpenAI" }, "ai").nodes[2].parameters.model,
    { mode: "id", value: "ai" },
  );
  assert.deepEqual(chat.nodes[2].credentials.openAiApi, { id: "openai-id", name: "OpenAI" });
  assert.equal(chat.connections["When chat message received"].main[0][0].node, "AI Agent");
  assert.equal(chat.connections["OpenAI Chat Model"].ai_languageModel[0][0].type, "ai_languageModel");
});

test("clean run discovers schemas, creates credentials/workflows, publishes, verifies, and writes atomic state", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const result = await reconcileN8n(input(server, stateFile));

  assert.equal(result.status, "ok");
  assert.deepEqual(Object.values(result.credentials).map((item) => item.status), ["created", "created"]);
  assert.deepEqual(Object.values(result.workflows).map((item) => item.status), ["created", "created"]);
  assert.match(result.urls.mcp, /\/mcp\/hermes$/);
  assert.match(result.urls.hostedChat, /\/webhook\/.+\/chat$/);
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);
  assert.equal(countCalls(server, "POST", "/publish"), 2);
  for (const call of server.calls) assert.equal(call.headers["x-n8n-api-key"], API_KEY);

  const mcpCredential = [...server.credentials.values()].find((item) => item.type === CREDENTIAL_TYPES.mcp);
  const routerCredential = [...server.credentials.values()].find((item) => item.type === CREDENTIAL_TYPES.router);
  assert.deepEqual(mcpCredential.data, { token: MCP_TOKEN });
  assert.deepEqual(routerCredential.data, { apiKey: ROUTER_KEY, url: SMART_ROUTER_BASE_URL });

  const stateText = await readFile(stateFile, "utf8");
  assert.doesNotMatch(stateText, new RegExp([API_KEY, MCP_TOKEN, ROUTER_KEY].join("|")));
  const state = JSON.parse(stateText);
  assert.equal(state.version, 1);
  assert.equal(state.credentials.mcp.id, mcpCredential.id);
  assert.equal(state.workflows.chat.id, result.workflows.chat.id);
});

test("direct 9router mode provisions its URL and ai model", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const result = await reconcileN8n({
    ...input(server, stateFile),
    routerBaseUrl: NINEROUTER_BASE_URL,
    routerModel: "ai",
  });

  const routerCredential = [...server.credentials.values()].find(
    (item) => item.type === CREDENTIAL_TYPES.router,
  );
  const chat = [...server.workflows.values()].find((item) => item.name === MANAGED_NAMES.chatWorkflow);
  assert.deepEqual(routerCredential.data, { apiKey: ROUTER_KEY, url: NINEROUTER_BASE_URL });
  assert.deepEqual(chat.nodes[2].parameters.model, { mode: "id", value: "ai" });
  const state = JSON.parse(await readFile(stateFile, "utf8"));
  assert.equal(state.routerBaseUrl, NINEROUTER_BASE_URL);
  assert.equal(state.routerModel, "ai");
  assert.equal(result.workflows.chat.published, true);
});

test("router transition updates the same credential and workflow with proven prior URL", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const routerId = first.credentials.router.id;
  const chatId = first.workflows.chat.id;

  const changed = await reconcileN8n({
    ...input(server, stateFile),
    routerBaseUrl: NINEROUTER_BASE_URL,
    routerModel: "ai",
    previousRouterBaseUrl: SMART_ROUTER_BASE_URL,
  });

  assert.equal(changed.credentials.router.id, routerId);
  assert.equal(changed.credentials.router.status, "updated");
  assert.equal(changed.workflows.chat.id, chatId);
  assert.equal(changed.workflows.chat.status, "updated");
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);
});

test("second run is a no-op and creates no duplicates", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  await reconcileN8n(input(server, stateFile));
  const before = server.calls.length;
  const result = await reconcileN8n(input(server, stateFile));
  const rerunCalls = server.calls.slice(before);

  assert.deepEqual(Object.values(result.credentials).map((item) => item.status), ["unchanged", "unchanged"]);
  assert.deepEqual(Object.values(result.workflows).map((item) => item.status), ["unchanged", "unchanged"]);
  assert.equal(rerunCalls.filter((call) => ["POST", "PUT", "PATCH", "DELETE"].includes(call.method)).length, 0);
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);
});

test("instance mode creates only hosted chat and reports the Instance endpoint", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const result = await reconcileN8n({ ...input(server, stateFile), mcpMode: "instance", mcpToken: undefined });

  assert.equal(result.mcpMode, "instance");
  assert.match(result.urls.mcp, /\/mcp-server\/http$/);
  assert.deepEqual(Object.keys(result.credentials), ["router"]);
  assert.deepEqual(Object.keys(result.workflows), ["chat"]);
  assert.equal(server.credentials.size, 1);
  assert.equal(server.workflows.size, 1);
  assert.equal(countCalls(server, "GET", "/credentials/schema/httpBearerAuth"), 0);
  assert.equal(countCalls(server, "POST", "/publish"), 1);
  const state = JSON.parse(await readFile(stateFile, "utf8"));
  assert.equal(state.mcpMode, "instance");
  assert.equal(state.credentials.mcp, undefined);
  assert.equal(state.workflows.mcp, undefined);
});

test("switching trigger to instance unpublishes and retains managed objects", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const trigger = await reconcileN8n(input(server, stateFile));
  const credentialId = trigger.credentials.mcp.id;
  const workflowId = trigger.workflows.mcp.id;

  const instance = await reconcileN8n({
    ...input(server, stateFile),
    mcpMode: "instance",
    mcpToken: undefined,
  });
  assert.equal(instance.credentials.mcp.id, credentialId);
  assert.equal(instance.credentials.mcp.status, "retained");
  assert.equal(instance.workflows.mcp.id, workflowId);
  assert.equal(instance.workflows.mcp.published, false);
  assert.equal(server.workflows.get(workflowId).active, false);
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);

  const restored = await reconcileN8n(input(server, stateFile));
  assert.equal(restored.credentials.mcp.id, credentialId);
  assert.equal(restored.workflows.mcp.id, workflowId);
  assert.equal(restored.workflows.mcp.published, true);
  assert.equal(server.workflows.get(workflowId).active, true);
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);
});

test("off mode unpublishes retained trigger objects and has no MCP URL", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const trigger = await reconcileN8n(input(server, stateFile));
  const result = await reconcileN8n({
    ...input(server, stateFile),
    mcpMode: "off",
    mcpToken: undefined,
  });
  assert.equal(result.urls.mcp, null);
  assert.equal(result.workflows.mcp.id, trigger.workflows.mcp.id);
  assert.equal(result.workflows.mcp.published, false);
  assert.equal(server.workflows.get(trigger.workflows.mcp.id).active, false);
});

test("retained trigger drift fails closed during an instance switch", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const trigger = await reconcileN8n(input(server, stateFile));
  server.workflows.get(trigger.workflows.mcp.id).nodes[1].position = [999, 999];
  await assert.rejects(
    reconcileN8n({ ...input(server, stateFile), mcpMode: "instance", mcpToken: undefined }),
    (error) => {
      assert.equal(error.code, "CONFLICT");
      assert.equal(error.stage, "mcp-workflow");
      return true;
    },
  );
  assert.equal(server.workflows.get(trigger.workflows.mcp.id).active, true);
});

test("changed secret updates only its persisted credential ID", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const result = await reconcileN8n({
    ...input(server, stateFile),
    mcpToken: "rotated-token",
    previousMcpToken: MCP_TOKEN,
  });

  assert.equal(result.credentials.mcp.id, first.credentials.mcp.id);
  assert.equal(result.credentials.mcp.status, "updated");
  assert.equal(result.credentials.router.status, "unchanged");
  assert.equal(server.credentials.size, 2);
  const patches = server.calls.filter((call) => call.method === "PATCH");
  assert.equal(patches.length, 1);
  assert.equal(patches[0].path, `/api/v1/credentials/${first.credentials.mcp.id}`);
});

test("secret rotation fails closed without a fingerprint-proven previous secret", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  await reconcileN8n(input(server, stateFile));

  await assert.rejects(
    reconcileN8n({ ...input(server, stateFile), mcpToken: "rotated-token" }),
    (error) => {
      assert.equal(error.code, "CONFLICT");
      assert.match(error.message, /cannot be proven/);
      return true;
    },
  );
  assert.equal(server.calls.filter((call) => call.method === "PATCH").length, 0);
});

test("later failure rolls credential rotation back and persists rollback timestamp", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  server.setFailure({
    method: "GET",
    path: `/api/v1/workflows/${first.workflows.mcp.id}`,
    status: 503,
    message: "later stage failed",
  });

  await assert.rejects(
    reconcileN8n({
      ...input(server, stateFile),
      mcpToken: "rotated-token",
      previousMcpToken: MCP_TOKEN,
    }),
    (error) => {
      assert.equal(error.stage, "mcp-workflow");
      return true;
    },
  );
  const credential = server.credentials.get(first.credentials.mcp.id);
  assert.deepEqual(credential.data, { token: MCP_TOKEN });
  const state = JSON.parse(await readFile(stateFile, "utf8"));
  assert.equal(state.credentials.mcp.updatedAt, credential.updatedAt);
  const retry = await reconcileN8n(input(server, stateFile));
  assert.equal(retry.credentials.mcp.status, "unchanged");
});

test("credential update response failure is compensated because journal precedes mutation", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  server.setFailure({
    method: "PATCH",
    path: `/api/v1/credentials/${first.credentials.mcp.id}`,
    status: 500,
    message: "lost update response",
    afterMutation: true,
  });

  await assert.rejects(
    reconcileN8n({
      ...input(server, stateFile),
      mcpToken: "rotated-token",
      previousMcpToken: MCP_TOKEN,
    }),
    (error) => {
      assert.equal(error.stage, "mcp-credential");
      return true;
    },
  );
  assert.deepEqual(server.credentials.get(first.credentials.mcp.id).data, { token: MCP_TOKEN });
  const state = JSON.parse(await readFile(stateFile, "utf8"));
  assert.equal(
    state.credentials.mcp.updatedAt,
    server.credentials.get(first.credentials.mcp.id).updatedAt,
  );
});

test("credential timestamp drift fails closed before secret rotation", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const result = await reconcileN8n(input(server, stateFile));
  const state = JSON.parse(await readFile(stateFile, "utf8"));
  assert.equal(
    state.credentials.mcp.updatedAt,
    server.credentials.get(result.credentials.mcp.id).updatedAt,
  );
  server.credentials.get(result.credentials.mcp.id).updatedAt = "manually-edited";

  await assert.rejects(
    reconcileN8n({ ...input(server, stateFile), mcpToken: "requested-rotation" }),
    (error) => {
      assert.equal(error.code, "CONFLICT");
      assert.equal(error.stage, "mcp-credential");
      assert.match(error.message, /manual drift/);
      return true;
    },
  );
  assert.equal(server.calls.filter((call) => call.method === "PATCH").length, 0);
});

test("first-run name collision fails closed without updating unrelated objects", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  server.seedCredential({
    id: "unrelated",
    name: MANAGED_NAMES.mcpCredential,
    type: CREDENTIAL_TYPES.mcp,
    data: { token: "someone-elses-token" },
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.code, "CONFLICT");
    assert.equal(error.stage, "mcp-credential");
    return true;
  });
  assert.equal(countCalls(server, "PATCH", "/unrelated"), 0);
  assert.equal(server.credentials.get("unrelated").data.token, "someone-elses-token");
});

test("manual workflow drift fails closed", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const result = await reconcileN8n(input(server, stateFile));
  const workflow = server.workflows.get(result.workflows.mcp.id);
  workflow.nodes[1].position = [999, 999];

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.code, "CONFLICT");
    assert.equal(error.stage, "mcp-workflow");
    assert.match(error.message, /manual drift/);
    return true;
  });
  assert.equal(workflow.nodes[1].position[0], 999);
});

test("deleted managed workflow is recreated only when its managed name is free", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  server.workflows.delete(first.workflows.mcp.id);

  const result = await reconcileN8n(input(server, stateFile));
  assert.equal(result.workflows.mcp.status, "created");
  assert.notEqual(result.workflows.mcp.id, first.workflows.mcp.id);
  assert.equal(server.workflows.size, 2);
});

test("deleted managed object with an occupied name fails closed", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  server.workflows.delete(first.workflows.mcp.id);
  const desired = buildMcpWorkflow({
    id: first.credentials.mcp.id,
    name: MANAGED_NAMES.mcpCredential,
  });
  server.seedWorkflow({
    ...desired,
    id: "replacement-by-user",
    active: false,
    versionId: "user-version",
    activeVersion: null,
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.code, "CONFLICT");
    assert.match(error.message, /missing and its name is occupied/);
    return true;
  });
  assert.equal(server.workflows.get("replacement-by-user").active, false);
});

test("first-run partial failure removes only objects created in that run and leaves no state", async (t) => {
  const server = await fixture(t, {
    failure: { method: "POST", path: "/api/v1/workflows", status: 500, message: "publish secret? no" },
  });
  const stateFile = await tempState(t);

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.stage, "mcp-workflow");
    return true;
  });
  assert.equal(server.credentials.size, 0);
  assert.equal(server.workflows.size, 0);
  await assert.rejects(readFile(stateFile, "utf8"), { code: "ENOENT" });
  assert.equal(server.calls.filter((call) => call.method === "DELETE" && call.path.includes("credentials/")).length, 2);
});

test("later failure restores an updated workflow and removes recreated objects", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const priorWorkflow = structuredClone(server.workflows.get(first.workflows.mcp.id));
  server.credentials.delete(first.credentials.mcp.id);
  server.setFailure({
    method: "GET",
    path: `/api/v1/workflows/${first.workflows.chat.id}`,
    status: 503,
    message: "fail after mcp workflow update",
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.stage, "chat-workflow");
    return true;
  });
  assert.equal(server.credentials.size, 1, "recreated MCP credential was removed");
  const restored = server.workflows.get(first.workflows.mcp.id);
  assert.equal(workflowFingerprint(restored), workflowFingerprint(priorWorkflow));
  assert.equal(restored.active, true);
  assert.ok(restored.activeVersion);
});

test("workflow update response failure restores prior definition and removes recreation", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const priorWorkflow = structuredClone(server.workflows.get(first.workflows.mcp.id));
  server.credentials.delete(first.credentials.mcp.id);
  server.setFailure({
    method: "PUT",
    path: `/api/v1/workflows/${first.workflows.mcp.id}`,
    status: 500,
    message: "lost workflow update response",
    afterMutation: true,
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.stage, "mcp-workflow");
    return true;
  });
  assert.equal(server.credentials.size, 1);
  const restored = server.workflows.get(first.workflows.mcp.id);
  assert.equal(workflowFingerprint(restored), workflowFingerprint(priorWorkflow));
  assert.equal(restored.active, true);
  assert.ok(restored.activeVersion);
});

test("publication response failure is compensated because journal precedes mutation", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const mcp = server.workflows.get(first.workflows.mcp.id);
  mcp.active = false;
  mcp.activeVersion = null;
  server.setFailure({
    method: "POST",
    path: `/api/v1/workflows/${first.workflows.mcp.id}/publish`,
    status: 500,
    message: "lost publication response",
    afterMutation: true,
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.stage, "mcp-publish");
    return true;
  });
  assert.equal(server.workflows.get(first.workflows.mcp.id).active, false);
  assert.equal(server.workflows.get(first.workflows.mcp.id).activeVersion, null);
});

test("later failure reverses a publication mutation", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const mcp = server.workflows.get(first.workflows.mcp.id);
  mcp.active = false;
  mcp.activeVersion = null;
  server.setFailure({
    method: "GET",
    path: `/api/v1/workflows/${first.workflows.chat.id}`,
    status: 503,
    message: "fail after publication",
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.stage, "chat-workflow");
    return true;
  });
  assert.equal(server.workflows.get(first.workflows.mcp.id).active, false);
  assert.equal(server.workflows.get(first.workflows.mcp.id).activeVersion, null);
  assert.ok(
    server.calls.some(
      (call) =>
        call.method === "POST" &&
        call.path === `/api/v1/workflows/${first.workflows.mcp.id}/unpublish`,
    ),
  );
});

test("later reconciliation failure retains prior objects and state", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  const priorState = await readFile(stateFile, "utf8");
  server.setFailure({
    method: "GET",
    path: `/api/v1/workflows/${first.workflows.chat.id}`,
    status: 503,
    message: "temporary failure",
  });

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.stage, "chat-workflow");
    return true;
  });
  assert.equal(await readFile(stateFile, "utf8"), priorState);
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);
  assert.equal(server.calls.filter((call) => call.method === "DELETE").length, 0);
});

test("state write failure rolls back live mutations and persists rollback timestamp", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  const first = await reconcileN8n(input(server, stateFile));
  let writes = 0;

  await assert.rejects(
    reconcileN8n({
      ...input(server, stateFile),
      mcpToken: "rotated-token",
      previousMcpToken: MCP_TOKEN,
      stateWriter: async (path, state) => {
        writes += 1;
        if (writes === 1) throw new Error("injected state write failure");
        const temporary = `${path}.test-tmp`;
        await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 });
        await import("node:fs/promises").then(({ rename }) => rename(temporary, path));
      },
    }),
    (error) => {
      assert.equal(error.stage, "state");
      assert.equal(error.code, "RECONCILE_FAILED");
      return true;
    },
  );
  const credential = server.credentials.get(first.credentials.mcp.id);
  assert.deepEqual(credential.data, { token: MCP_TOKEN });
  assert.equal(server.credentials.size, 2);
  assert.equal(server.workflows.size, 2);
  const state = JSON.parse(await readFile(stateFile, "utf8"));
  assert.equal(state.credentials.mcp.updatedAt, credential.updatedAt);
  assert.equal(writes, 1, "rollback timestamp uses the internal atomic writer");
});

test("an empty or corrupt state file fails closed before API access", async (t) => {
  const server = await fixture(t);
  const stateFile = await tempState(t);
  await writeFile(stateFile, "");

  await assert.rejects(reconcileN8n(input(server, stateFile)), (error) => {
    assert.equal(error.code, "STATE_ERROR");
    return true;
  });
  assert.equal(server.calls.length, 0);
});

test("schema and API errors report the failed stage without leaking secrets", async (t) => {
  const badSchemaServer = await fixture(t, { badSchema: "openAiApi" });
  const stateFile = await tempState(t);
  await assert.rejects(reconcileN8n(input(badSchemaServer, stateFile)), (error) => {
    assert.equal(error.code, "SCHEMA_CONFLICT");
    assert.equal(error.stage, "credential-schemas");
    return true;
  });

  const failingServer = await fixture(t, {
    failure: {
      method: "GET",
      path: "/api/v1/workflows",
      status: 500,
      message: `failure ${API_KEY} ${MCP_TOKEN} ${ROUTER_KEY}`,
    },
  });
  const otherState = await tempState(t);
  await assert.rejects(reconcileN8n(input(failingServer, otherState)), (error) => {
    assert.equal(error.stage, "connectivity");
    assert.doesNotMatch(error.message, new RegExp([API_KEY, MCP_TOKEN, ROUTER_KEY].join("|")));
    assert.match(error.message, /\[REDACTED\]/);
    return true;
  });
});

test("API key is sent only to the configured origin", async () => {
  const seen = [];
  const client = createN8nClient({
    apiUrl: "https://n8n.example.test/api/v1",
    apiKey: API_KEY,
    fetchImpl: async (url, init) => {
      seen.push({ url: String(url), init });
      return new Response(JSON.stringify({ data: [], nextCursor: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  await client.listCredentials();
  assert.equal(new URL(seen[0].url).origin, "https://n8n.example.test");
  assert.equal(seen[0].init.headers["X-N8N-API-KEY"], API_KEY);
});

test("redact removes secret-valued fields and embedded secret strings", () => {
  assert.deepEqual(
    redact({ apiKey: API_KEY, nested: { token: MCP_TOKEN, message: `bad ${ROUTER_KEY}` } }, [ROUTER_KEY]),
    { apiKey: "[REDACTED]", nested: { token: "[REDACTED]", message: "bad [REDACTED]" } },
  );
});

test("workflow fingerprint ignores API response metadata but detects managed definition drift", () => {
  const workflow = buildHostedChatWorkflow({ id: "id", name: "name" });
  const response = { ...structuredClone(workflow), id: "workflow", active: true, versionId: "version" };
  assert.equal(workflowFingerprint(workflow), workflowFingerprint(response));
  response.nodes[1].parameters.text = "changed";
  assert.notEqual(workflowFingerprint(workflow), workflowFingerprint(response));
});
