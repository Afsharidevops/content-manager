import { createHash } from "node:crypto";

export const MANAGED_NAMES = Object.freeze({
  mcpCredential: "Hermes MCP Bearer (managed)",
  routerCredential: "Hermes Smart Router OpenAI (managed)",
  mcpWorkflow: "Hermes MCP Tools (managed)",
  chatWorkflow: "Hermes Hosted Chat (managed)",
});

export const CREDENTIAL_TYPES = Object.freeze({
  mcp: "httpBearerAuth",
  router: "openAiApi",
});

export const MCP_PATH = "hermes";
export const SMART_ROUTER_BASE_URL = "http://smart-router:8080/v1";
export const NINEROUTER_BASE_URL = "http://nine-router:20128/v1";

const MCP_TRIGGER_NAME = "MCP Server Trigger";
const CALCULATOR_NAME = "Calculator";
const CHAT_TRIGGER_NAME = "When chat message received";
const AGENT_NAME = "AI Agent";
const MODEL_NAME = "OpenAI Chat Model";

function credentialReference(credential) {
  if (!credential?.id || !credential?.name) {
    throw new TypeError("A credential reference with id and name is required");
  }
  return { id: credential.id, name: credential.name };
}

export function buildMcpWorkflow(mcpCredential) {
  return {
    name: MANAGED_NAMES.mcpWorkflow,
    nodes: [
      {
        parameters: {
          authentication: "bearerAuth",
          path: MCP_PATH,
        },
        type: "@n8n/n8n-nodes-langchain.mcpTrigger",
        typeVersion: 2,
        position: [420, 300],
        id: "80918e1d-6a47-5cad-a96b-b77c0d30c475",
        // n8n assigns a random webhookId when one is absent, which would make
        // every round-trip fingerprint differ. The production URL still comes
        // from the fixed `path` above, so pinning this only keeps state stable.
        webhookId: "8a2c9f61-4d0e-5b73-9c15-6e3a7d84b0f2",
        name: MCP_TRIGGER_NAME,
        credentials: {
          httpBearerAuth: credentialReference(mcpCredential),
        },
      },
      {
        parameters: {},
        type: "@n8n/n8n-nodes-langchain.toolCalculator",
        typeVersion: 1,
        position: [180, 500],
        id: "c4e7bcf3-8b36-525a-9427-b80a04a807cf",
        name: CALCULATOR_NAME,
      },
    ],
    connections: {
      [CALCULATOR_NAME]: {
        ai_tool: [[{ node: MCP_TRIGGER_NAME, type: "ai_tool", index: 0 }]],
      },
    },
    settings: { executionOrder: "v1" },
  };
}

export function buildHostedChatWorkflow(routerCredential, model = "auto") {
  return {
    name: MANAGED_NAMES.chatWorkflow,
    nodes: [
      {
        parameters: {
          public: true,
          mode: "hostedChat",
          authentication: "n8nUserAuth",
          options: { responseMode: "streaming" },
        },
        type: "@n8n/n8n-nodes-langchain.chatTrigger",
        typeVersion: 1.4,
        position: [160, 300],
        id: "1f4d3546-b64b-5d83-a3eb-2296aeab089d",
        webhookId: "06eea7e2-805f-5fab-87ef-a64072a28d86",
        name: CHAT_TRIGGER_NAME,
      },
      {
        parameters: {
          promptType: "define",
          text: "={{ $json.chatInput }}",
          options: {},
        },
        type: "@n8n/n8n-nodes-langchain.agent",
        typeVersion: 3.1,
        position: [420, 300],
        id: "599201b8-d1f7-514a-86c2-ddb97875e6eb",
        name: AGENT_NAME,
      },
      {
        parameters: {
          model: { mode: "id", value: model },
          options: {},
        },
        type: "@n8n/n8n-nodes-langchain.lmChatOpenAi",
        typeVersion: 1.3,
        position: [420, 520],
        id: "3d679b0f-689a-5103-a547-12e27938150a",
        name: MODEL_NAME,
        credentials: {
          openAiApi: credentialReference(routerCredential),
        },
      },
    ],
    connections: {
      [CHAT_TRIGGER_NAME]: {
        main: [[{ node: AGENT_NAME, type: "main", index: 0 }]],
      },
      [MODEL_NAME]: {
        ai_languageModel: [[{ node: AGENT_NAME, type: "ai_languageModel", index: 0 }]],
      },
    },
    settings: { executionOrder: "v1" },
  };
}

export function buildCredentialSpecs({
  mcpToken,
  routerApiKey,
  routerBaseUrl = SMART_ROUTER_BASE_URL,
}) {
  if (!mcpToken || !routerApiKey) throw new TypeError("Both credential secrets are required");
  return {
    mcp: {
      name: MANAGED_NAMES.mcpCredential,
      type: CREDENTIAL_TYPES.mcp,
      data: { token: mcpToken },
    },
    router: {
      name: MANAGED_NAMES.routerCredential,
      type: CREDENTIAL_TYPES.router,
      data: { apiKey: routerApiKey, url: routerBaseUrl },
    },
  };
}

function sortValue(value) {
  if (Array.isArray(value)) return value.map(sortValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortValue(value[key])]),
    );
  }
  return value;
}

export function fingerprint(value) {
  return createHash("sha256").update(JSON.stringify(sortValue(value))).digest("hex");
}

export function workflowComparable(workflow) {
  return {
    name: workflow?.name,
    nodes: workflow?.nodes,
    connections: workflow?.connections,
    settings: workflow?.settings,
  };
}

export function workflowFingerprint(workflow) {
  return fingerprint(workflowComparable(workflow));
}

export function hostedChatWebhookId(workflow) {
  return workflow.nodes.find((node) => node.name === CHAT_TRIGGER_NAME)?.webhookId;
}
