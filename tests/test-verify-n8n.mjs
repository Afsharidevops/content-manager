import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  MANAGED_NAMES,
  buildHostedChatWorkflow,
  buildMcpWorkflow,
  workflowFingerprint,
} from "../scripts/lib/n8n-workflows.mjs";
import { VerifyError, verifyN8n } from "../scripts/verify-n8n.mjs";

const API_KEY = "owner-api-key-secret";
const MCP_TOKEN = "mcp-token-secret";
const WEBHOOK_ID = "06eea7e2-805f-5fab-87ef-a64072a28d86";
const SESSION_ID = "fixture-session-id";

async function stateFile(t, state = {}) {
  const directory = await mkdtemp(join(os.tmpdir(), "verify-n8n-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const path = join(directory, "state.json");
  const mcp = buildMcpWorkflow({ id: "credential-mcp", name: "MCP credential" });
  const chat = buildHostedChatWorkflow({ id: "credential-router", name: "Router credential" });
  await writeFile(
    path,
    JSON.stringify({
      version: 1,
      workflows: {
        mcp: {
          id: "workflow-mcp",
          name: MANAGED_NAMES.mcpWorkflow,
          fingerprint: workflowFingerprint(mcp),
        },
        chat: {
          id: "workflow-chat",
          name: MANAGED_NAMES.chatWorkflow,
          fingerprint: workflowFingerprint(chat),
        },
      },
      ...state,
    }),
  );
  return path;
}

async function bodyOf(request) {
  let text = "";
  for await (const chunk of request) text += chunk;
  return text ? JSON.parse(text) : undefined;
}

function send(response, status, value, type = "application/json", headers = {}) {
  response.writeHead(status, { "Content-Type": type, ...headers });
  response.end(type === "application/json" ? JSON.stringify(value) : value);
}

function rpcResult(id, result) {
  return { jsonrpc: "2.0", id, result };
}

async function fixture(t, options = {}) {
  const calls = [];
  const transport = options.transport || "json";
  let toolsCallCount = 0;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://fixture.invalid");
    const body = request.method === "POST" ? await bodyOf(request) : undefined;
    calls.push({ method: request.method, path: url.pathname, headers: request.headers, body });

    if (options.httpFailure === url.pathname) return send(response, 503, { error: "failure" });
    if (request.method === "GET" && url.pathname === "/healthz") return send(response, 200, { status: "ok" });
    if (request.method === "GET" && ["/ready", "/api/health"].includes(url.pathname)) {
      return send(response, 200, { status: "ready" });
    }
    if (url.pathname === `/webhook/${WEBHOOK_ID}/chat`) {
      // n8n serves the static chat shell to anonymous GETs by design and only
      // enforces authentication on the message POST, so the verifier must probe
      // POST. A GET probe here would let an unauthenticated instance pass.
      if (request.method === "GET") {
        return send(response, 200, "<!doctype html><title>Chat</title>", "text/html");
      }
      return send(
        response,
        options.acceptUnauthenticatedChat ? 200 : 401,
        options.acceptUnauthenticatedChat ? { output: "hello" } : "User not authenticated!",
        options.acceptUnauthenticatedChat ? "application/json" : "text/plain",
      );
    }
    if (request.method === "GET" && url.pathname.startsWith("/api/v1/workflows/")) {
      if (request.headers["x-n8n-api-key"] !== API_KEY) return send(response, 401, { error: "bad key" });
      const id = decodeURIComponent(url.pathname.split("/").at(-1));
      const isMcp = id === "workflow-mcp";
      const workflow = isMcp
        ? buildMcpWorkflow({ id: "credential-mcp", name: "MCP credential" })
        : buildHostedChatWorkflow({ id: "credential-router", name: "Router credential" });
      return send(response, 200, {
        ...workflow,
        id,
        name: options.wrongWorkflowName && id === "workflow-chat" ? "wrong" : workflow.name,
        ...(options.workflowDrift && id === "workflow-chat" ? { settings: { executionOrder: "v0" } } : {}),
        active:
          (options.unpublished && id === "workflow-chat") || (options.unpublishedMcp && id === "workflow-mcp")
            ? false
            : true,
        activeVersion:
          (options.unpublished && id === "workflow-chat") || (options.unpublishedMcp && id === "workflow-mcp")
            ? null
            : { versionId: `version-${id}` },
      });
    }
    if (request.method === "DELETE" && ["/mcp/hermes", "/mcp-server/http"].includes(url.pathname)) {
      assert.equal(request.headers.authorization, `Bearer ${MCP_TOKEN}`);
      assert.equal(request.headers["mcp-session-id"], SESSION_ID);
      response.writeHead(options.closeFailure ? 500 : 200);
      return response.end();
    }
    if (request.method === "POST" && ["/mcp/hermes", "/mcp-server/http"].includes(url.pathname)) {
      const isInstance = url.pathname === "/mcp-server/http";
      if (request.headers.authorization !== `Bearer ${MCP_TOKEN}`) {
        return send(response, options.acceptUnauthenticated ? 200 : 401, { error: "unauthorized" });
      }
      if (!options.noSession && body?.method !== "initialize") {
        assert.equal(request.headers["mcp-session-id"], SESSION_ID);
      }
      if (body?.method === "notifications/initialized") {
        response.writeHead(202);
        return response.end();
      }

      let payload;
      if (body?.method === "initialize") {
        payload = rpcResult(body.id, {
          protocolVersion: "2025-03-26",
          capabilities: { tools: {} },
          serverInfo: { name: "fixture", version: "1" },
        });
      } else if (body?.method === "tools/list") {
        toolsCallCount += 1;
        if (options.protocolFailure === "tools-list") payload = { jsonrpc: "2.0", id: 999, result: {} };
        else if (options.rpcFailure === "tools-list") payload = { jsonrpc: "2.0", id: body.id, error: { code: -1, message: `${MCP_TOKEN} failure` } };
        else if (isInstance) {
          const names = [
            "search_workflows",
            "get_workflow_details",
            "execute_workflow",
            "publish_workflow",
            "unpublish_workflow",
            "list_credentials",
            "search_executions",
          ].filter((name) => name !== options.missingInstanceTool);
          payload = rpcResult(body.id, { tools: names.map((name) => ({ name, inputSchema: { type: "object" } })) });
        } else {
          payload = rpcResult(body.id, { tools: options.missingCalculator ? [] : [{ name: "Calculator", inputSchema: { type: "object" } }] });
        }
      } else if (body?.method === "tools/call") {
        if (isInstance) {
          assert.deepEqual(body.params, {
            name: "search_workflows",
            arguments: { limit: 1, query: MANAGED_NAMES.chatWorkflow, sortBy: "updatedAt:desc" },
          });
          const search = options.invalidInstanceSearch
            ? { data: "invalid", count: 1 }
            : { data: [{ id: "workflow-chat", name: MANAGED_NAMES.chatWorkflow }], count: 1 };
          payload = rpcResult(body.id, {
            structuredContent: search,
            content: [{ type: "text", text: JSON.stringify(search) }],
          });
        } else {
          assert.deepEqual(body.params, { name: "Calculator", arguments: { input: "2+3" } });
          payload = rpcResult(
            body.id,
            options.toolFailure
              ? { isError: true, content: [{ type: "text", text: `${MCP_TOKEN} failed` }] }
              : options.emptyCalculator
                ? { content: [] }
                : { content: [{ type: "text", text: "5" }] },
          );
        }
      } else {
        return send(response, 404, { error: "unhandled" });
      }

      const headers = body?.method === "initialize" && !options.noSession ? { "Mcp-Session-Id": SESSION_ID } : {};
      if (transport === "sse") {
        return send(response, 200, `event: message\ndata: ${JSON.stringify(payload)}\n\n`, "text/event-stream", headers);
      }
      return send(response, 200, payload, "application/json", headers);
    }
    send(response, 404, { error: `unhandled ${request.method} ${url.pathname}` });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const { port } = server.address();
  return { baseUrl: `http://127.0.0.1:${port}`, calls, get toolsCallCount() { return toolsCallCount; } };
}

function input(server, path, options = {}) {
  return {
    apiUrl: `${server.baseUrl}/api/v1`,
    apiKey: API_KEY,
    mcpUrl: `${server.baseUrl}/mcp/hermes`,
    mcpToken: MCP_TOKEN,
    stateFile: path,
    smartRouterUrl: server.baseUrl,
    ...options,
  };
}

async function runCli(env) {
  const script = fileURLToPath(new URL("../scripts/verify-n8n.mjs", import.meta.url));
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], { env: { ...process.env, ...env } });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

test("verifies health, workflows, hosted chat, and JSON MCP with session reuse", async (t) => {
  const server = await fixture(t);
  const path = await stateFile(t);
  const result = await verifyN8n(input(server, path));

  assert.equal(result.status, "ok");
  assert.equal(result.checks.workflows, "ok");
  assert.equal(result.checks.mcpSession, "established");
  assert.equal(server.toolsCallCount, 1);
  const unauthenticated = server.calls.find((call) => call.path === "/mcp/hermes" && !call.headers.authorization);
  assert.ok(unauthenticated);
  assert.equal(server.calls.find((call) => call.path.endsWith("/chat")).method, "POST");
  assert.ok(server.calls.some((call) => call.method === "DELETE" && call.path === "/mcp/hermes"));
  assert.equal(server.calls.filter((call) => call.path.startsWith("/api/v1/workflows/")).length, 2);
});

test("supports SSE MCP responses", async (t) => {
  const server = await fixture(t, { transport: "sse" });
  const result = await verifyN8n(input(server, await stateFile(t)));
  assert.equal(result.checks.mcp, "ok");
});

test("supports a direct 9router health endpoint", async (t) => {
  const server = await fixture(t);
  const result = await verifyN8n(
    input(server, await stateFile(t), {
      smartRouterUrl: undefined,
      routerHealthUrl: `${server.baseUrl}/api/health`,
    }),
  );
  assert.equal(result.checks.modelRouter, "ok");
  assert.ok(server.calls.some((call) => call.path === "/api/health"));
  assert.equal(server.calls.some((call) => call.path === "/ready"), false);
});

test("verifies Instance MCP with only a bounded read-only workflow search", async (t) => {
  const server = await fixture(t, { unpublishedMcp: true });
  const result = await verifyN8n(
    input(server, await stateFile(t), {
      mcpMode: "instance",
      mcpUrl: `${server.baseUrl}/mcp-server/http`,
    }),
  );

  assert.equal(result.checks.mcp, "ok");
  const toolCalls = server.calls.filter((call) => call.body?.method === "tools/call");
  assert.equal(toolCalls.length, 1);
  assert.equal(toolCalls[0].body.params.name, "search_workflows");
  assert.deepEqual(toolCalls[0].body.params.arguments, {
    limit: 1,
    query: MANAGED_NAMES.chatWorkflow,
    sortBy: "updatedAt:desc",
  });
});

test("supports Instance MCP without a server session", async (t) => {
  const server = await fixture(t, { unpublishedMcp: true, noSession: true });
  const result = await verifyN8n(
    input(server, await stateFile(t), {
      mcpMode: "instance",
      mcpUrl: `${server.baseUrl}/mcp-server/http`,
    }),
  );
  assert.equal(result.checks.mcpSession, "not supplied");
  assert.equal(server.calls.filter((call) => call.method === "DELETE").length, 0);
});

test("accepts Instance MCP when newer version-gated tools are absent", async (t) => {
  for (const missingInstanceTool of ["publish_workflow", "unpublish_workflow", "list_credentials", "search_executions"]) {
    await t.test(missingInstanceTool, async (t) => {
      const server = await fixture(t, { unpublishedMcp: true, missingInstanceTool });
      const result = await verifyN8n(
        input(server, await stateFile(t), {
          mcpMode: "instance",
          mcpUrl: `${server.baseUrl}/mcp-server/http`,
        }),
      );
      assert.equal(result.checks.mcp, "ok");
    });
  }
});

test("fails when required Instance MCP core tools or search results are invalid", async (t) => {
  for (const options of [
    { unpublishedMcp: true, missingInstanceTool: "get_workflow_details" },
    { unpublishedMcp: true, invalidInstanceSearch: true },
  ]) {
    await t.test(Object.keys(options).at(-1), async (t) => {
      const server = await fixture(t, options);
      await assert.rejects(
        verifyN8n(
          input(server, await stateFile(t), {
            mcpMode: "instance",
            mcpUrl: `${server.baseUrl}/mcp-server/http`,
          }),
        ),
        (error) => error.code === "VERIFY_FAILED" && error.check === "mcp",
      );
    });
  }
});

test("off mode skips MCP and requires retained trigger state to be unpublished", async (t) => {
  const server = await fixture(t, { unpublishedMcp: true });
  const result = await verifyN8n(input(server, await stateFile(t), { mcpMode: "off", mcpUrl: undefined, mcpToken: undefined }));
  assert.equal(result.checks.mcp, "skipped (N8N_MCP_MODE=off)");
  assert.equal(server.calls.filter((call) => call.path.startsWith("/mcp")).length, 0);

  const published = await fixture(t);
  await assert.rejects(
    verifyN8n(input(published, await stateFile(t), { mcpMode: "off", mcpUrl: undefined, mcpToken: undefined })),
    (error) => error.code === "VERIFY_FAILED" && error.check === "workflows",
  );
});

test("Instance and off modes require the API key to prove retained trigger unpublication", async (t) => {
  const server = await fixture(t, { unpublishedMcp: true });
  await assert.rejects(
    verifyN8n(
      input(server, await stateFile(t), {
        apiKey: undefined,
        mcpMode: "instance",
        mcpUrl: `${server.baseUrl}/mcp-server/http`,
      }),
    ),
    (error) => error.code === "CONFIG_ERROR" && error.check === "workflows",
  );
});

test("allows the public workflow API check to be skipped", async (t) => {
  const server = await fixture(t);
  const result = await verifyN8n(input(server, await stateFile(t), { apiKey: undefined }));
  assert.equal(result.checks.workflows, "skipped (N8N_API_KEY not set)");
  assert.equal(server.calls.filter((call) => call.path.startsWith("/api/v1/workflows/")).length, 0);
});

test("requires unauthenticated MCP requests to be rejected", async (t) => {
  const server = await fixture(t, { acceptUnauthenticated: true });
  await assert.rejects(verifyN8n(input(server, await stateFile(t))), (error) => {
    assert.equal(error.code, "AUTH_ERROR");
    assert.equal(error.check, "mcp-auth");
    return true;
  });
});

test("requires hosted chat to reject an unauthenticated browser", async (t) => {
  const server = await fixture(t, { acceptUnauthenticatedChat: true });
  await assert.rejects(verifyN8n(input(server, await stateFile(t))), (error) => {
    assert.equal(error.code, "AUTH_ERROR");
    assert.equal(error.check, "hosted-chat");
    return true;
  });
});

test("fails when Calculator is missing", async (t) => {
  const server = await fixture(t, { missingCalculator: true });
  await assert.rejects(verifyN8n(input(server, await stateFile(t))), /Calculator tool is missing/);
});

test("fails when Calculator returns no textual result", async (t) => {
  const server = await fixture(t, { emptyCalculator: true });
  await assert.rejects(verifyN8n(input(server, await stateFile(t))), (error) => {
    assert.equal(error.code, "VERIFY_FAILED");
    assert.equal(error.check, "mcp");
    return true;
  });
});

test("fails when an established MCP session cannot be closed", async (t) => {
  const server = await fixture(t, { closeFailure: true });
  await assert.rejects(verifyN8n(input(server, await stateFile(t))), (error) => {
    assert.equal(error.code, "HTTP_ERROR");
    assert.equal(error.check, "mcp");
    return true;
  });
});

test("fails closed on managed workflow identity, definition, or publication drift", async (t) => {
  for (const options of [
    { wrongWorkflowName: true },
    { workflowDrift: true },
    { unpublished: true },
  ]) {
    await t.test(Object.keys(options)[0], async (t) => {
      const server = await fixture(t, options);
      await assert.rejects(verifyN8n(input(server, await stateFile(t))), (error) => {
        assert.equal(error.code, "VERIFY_FAILED");
        assert.equal(error.check, "workflows");
        return true;
      });
    });
  }
});

test("reports HTTP, protocol, JSON-RPC, and tool-call failures safely", async (t) => {
  const cases = [
    [{ httpFailure: "/healthz" }, "HTTP_ERROR"],
    [{ protocolFailure: "tools-list" }, "PROTOCOL_ERROR"],
    [{ rpcFailure: "tools-list" }, "MCP_ERROR"],
    [{ toolFailure: true }, "VERIFY_FAILED"],
  ];
  for (const [options, code] of cases) {
    await t.test(code, async (t) => {
      const server = await fixture(t, options);
      await assert.rejects(verifyN8n(input(server, await stateFile(t))), (error) => {
        assert.ok(error instanceof VerifyError);
        assert.equal(error.code, code);
        assert.doesNotMatch(error.message, new RegExp(`${API_KEY}|${MCP_TOKEN}`));
        return true;
      });
    });
  }
});

test("validates managed workflow IDs and expected names from state", async (t) => {
  const server = await fixture(t);
  const invalidStates = [
    { workflows: { mcp: { id: "", name: MANAGED_NAMES.mcpWorkflow }, chat: { id: "workflow-chat", name: MANAGED_NAMES.chatWorkflow } } },
    { workflows: { mcp: { id: "same", name: MANAGED_NAMES.mcpWorkflow }, chat: { id: "same", name: MANAGED_NAMES.chatWorkflow } } },
    { workflows: { mcp: { id: "workflow-mcp", name: "wrong" }, chat: { id: "workflow-chat", name: MANAGED_NAMES.chatWorkflow } } },
  ];
  for (const state of invalidStates) {
    await assert.rejects(verifyN8n(input(server, await stateFile(t, state))), (error) => error.code === "STATE_ERROR");
  }
});

test("CLI stdout, stderr, and failure messages never reveal secrets", async (t) => {
  const server = await fixture(t, { rpcFailure: "tools-list" });
  const path = await stateFile(t);
  const result = await runCli({
    N8N_API_URL: `${server.baseUrl}/api/v1`,
    N8N_API_KEY: API_KEY,
    N8N_MCP_URL: `${server.baseUrl}/mcp/hermes`,
    N8N_MCP_TOKEN: MCP_TOKEN,
    N8N_STATE_FILE: path,
    SMART_ROUTER_URL: server.baseUrl,
  });

  assert.equal(result.code, 1);
  assert.equal(result.stdout, "");
  assert.match(result.stderr, /"status":"error"/);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, new RegExp(`${API_KEY}|${MCP_TOKEN}`));
});
