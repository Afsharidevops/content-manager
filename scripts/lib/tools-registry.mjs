// Shared tool registry reader for the n8n connector.
//
// The content stack keeps one registry file (content/config/tools.json) that
// lists every remote tool source - MCP servers, OpenAPI services, plain HTTP
// endpoints - with the services allowed to call it. The Content Bot reads it
// with content_pipeline.tools, the Smart Router serves it on GET /v1/tools,
// and this reader gives the n8n bootstrap the same view. Plain JSON keeps the
// file readable from every runtime without extra dependencies.

import { readFileSync } from "node:fs";

export const REGISTRY_SCHEMA_VERSION = 1;

const KINDS = new Set(["mcp", "openapi", "http"]);
const PLACEHOLDER = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

export class ToolsRegistryError extends Error {
  constructor(message) {
    super(message);
    this.name = "ToolsRegistryError";
    this.code = "TOOLS_REGISTRY_INVALID";
  }
}

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function resolvePlaceholders(value, env = process.env) {
  return String(value ?? "").replace(PLACEHOLDER, (_match, name) => text(env?.[name]));
}

export function parseToolsRegistry(document, env = process.env) {
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new ToolsRegistryError("the tools registry must be a JSON object");
  }
  if (document.schema_version !== REGISTRY_SCHEMA_VERSION) {
    throw new ToolsRegistryError(
      `schema_version must be ${REGISTRY_SCHEMA_VERSION}, got ${JSON.stringify(document.schema_version)}`,
    );
  }
  if (!Array.isArray(document.tools)) {
    throw new ToolsRegistryError("the tools registry needs a tools list");
  }
  const seen = new Set();
  const tools = [];
  for (const entry of document.tools) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new ToolsRegistryError("every tool entry must be an object");
    }
    const id = text(entry.id);
    if (!id) throw new ToolsRegistryError("every tool needs an id");
    if (seen.has(id)) throw new ToolsRegistryError(`duplicate tool id ${id}`);
    seen.add(id);
    const kind = text(entry.kind).toLowerCase();
    if (!KINDS.has(kind)) {
      throw new ToolsRegistryError(`tool ${id}: kind must be mcp, openapi, or http`);
    }
    const auth = entry.auth && typeof entry.auth === "object" ? entry.auth : {};
    const authType = text(auth.type) || "none";
    const authEnv = text(auth.env);
    if (authType !== "none" && !authEnv) {
      throw new ToolsRegistryError(`tool ${id}: auth type ${authType} needs an env name`);
    }
    const endpoint = resolvePlaceholders(entry.url || entry.base_url || "", env);
    const specUrl = resolvePlaceholders(entry.spec_url || "", env);
    if (kind === "mcp" && !endpoint) {
      throw new ToolsRegistryError(`tool ${id}: an MCP tool needs url`);
    }
    if (kind === "openapi" && !endpoint && !specUrl) {
      throw new ToolsRegistryError(`tool ${id}: an OpenAPI tool needs base_url or spec_url`);
    }
    if (kind === "http" && !endpoint) {
      throw new ToolsRegistryError(`tool ${id}: an HTTP tool needs base_url`);
    }
    tools.push({
      id,
      title: text(entry.title) || id,
      kind,
      endpoint,
      specUrl,
      transport: text(entry.transport),
      authType,
      authEnv,
      capabilities: Array.isArray(entry.capabilities)
        ? entry.capabilities.map((item) => text(item)).filter(Boolean)
        : [],
      consumers: Array.isArray(entry.consumers)
        ? entry.consumers.map((item) => text(item)).filter(Boolean)
        : [],
      notes: text(entry.notes),
    });
  }
  return tools;
}

export function readToolsRegistry(path, env = process.env) {
  const location = text(path);
  if (!location) return { path: "", tools: [] };
  let raw;
  try {
    raw = readFileSync(location, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return { path: location, tools: [] };
    throw new ToolsRegistryError(`${location} could not be read: ${error.message}`);
  }
  let document;
  try {
    document = JSON.parse(raw);
  } catch (error) {
    throw new ToolsRegistryError(`${location} is not valid JSON: ${error.message}`);
  }
  return { path: location, tools: parseToolsRegistry(document, env) };
}

export function toolsForConsumer(tools, consumer) {
  const name = text(consumer).toLowerCase();
  return tools.filter((tool) => tool.consumers.includes(name));
}

export function missingCredentials(tools, env = process.env) {
  return tools
    .filter((tool) => tool.authType !== "none" && tool.authEnv && !text(env?.[tool.authEnv]))
    .map((tool) => ({ id: tool.id, env: tool.authEnv }));
}

export function summarizeRegistry(path, consumer, env = process.env) {
  // The registry is optional: a missing file is normal, an invalid one is a
  // warning the bootstrap reports instead of failing the whole run.
  try {
    const registry = readToolsRegistry(path, env);
    const tools = toolsForConsumer(registry.tools, consumer);
    return {
      path: registry.path,
      count: tools.length,
      missingCredentials: missingCredentials(tools, env),
      tools: tools.map((tool) => ({
        id: tool.id,
        kind: tool.kind,
        endpoint: tool.endpoint || tool.specUrl,
        capabilities: tool.capabilities,
      })),
      warning: "",
    };
  } catch (error) {
    if (error?.name === "ToolsRegistryError") {
      return { path: text(path), count: 0, missingCredentials: [], tools: [], warning: error.message };
    }
    throw error;
  }
}
