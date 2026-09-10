import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPT = join(ROOT, "extensions", "locallab-flow-unlock", "unlock.js");
const BATCH_URL = "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=cPZSdc&rt=c";

// Flow answers batchexecute with length-prefixed JSON frames, and the array
// inside a frame stores protocol-buffer field N at index N - 1. The fixtures
// below keep that shape so the parser is exercised the way the page uses it.
const CONFIG_FIELDS = 36;
const CONFIG_COUNTRY_INDEX = 30;
const CONFIG_AGE_INDEX = 31;

function configPayload() {
  const values = new Array(CONFIG_FIELDS).fill(null);
  values[0] = "2026-09-03-v1-angular-2447aa1d-5f7d-4565-adf4-b1fa6efffd49";
  values[1] = [];
  values[2] = true;
  values[17] = [];
  values[18] = [];
  values[30] = null;
  values[31] = true;
  values[33] = true;
  return values;
}

function frame(value) {
  const json = JSON.stringify(value);
  return `${json.length}\n${json}\n`;
}

function batch(entries) {
  return ")]}'\n\n" + frame(entries) + frame([["e", 4, null, null, 145]]);
}

function configEntry(payload) {
  return [
    "wrb.fr",
    "cPZSdc",
    typeof payload === "string" ? payload : JSON.stringify(payload),
    null,
    null,
    null,
    "generic",
  ];
}

function framesOf(body) {
  const frames = [];
  const payload = body.slice(body.indexOf(")]}'") + 4);
  const pattern = /(\d+)\n/g;
  let match;
  while ((match = pattern.exec(payload)) !== null) {
    const length = Number(match[1]);
    const start = pattern.lastIndex;
    const json = payload.slice(start, start + length);
    assert.equal(JSON.stringify(JSON.parse(json)).length, json.length, "frame length token matches");
    frames.push(JSON.parse(json));
    pattern.lastIndex = start + length;
  }
  return frames;
}

function entryPayload(entry) {
  return JSON.parse(entry[2]);
}

async function loadUnlock(body) {
  const source = await readFile(SCRIPT, "utf8");
  const sandbox = {
    console,
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    btoa: (value) => Buffer.from(value, "binary").toString("base64"),
    TextEncoder,
    Response: class {
      constructor(payload) {
        this.body = payload;
      }
    },
    XMLHttpRequest: class {
      addEventListener() {}
      open() {}
      send() {
        this.ready = true;
      }
      get responseText() {
        if (!this.ready) throw new Error("InvalidStateError");
        return body;
      }
      get response() {
        return this.responseText;
      }
    },
    document: {
      documentElement: {
        attributes: {},
        setAttribute(name, value) {
          this.attributes[name] = value;
        },
      },
    },
  };
  sandbox.window = sandbox;
  sandbox.fetch = async () => ({
    status: 200,
    statusText: "OK",
    headers: {},
    body,
    clone: () => ({ text: async () => body }),
  });
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return sandbox;
}

// The live service writes each token as the frame length plus the two
// newlines around it, and the closing frame repeats the byte size of the whole
// answer, so a rewritten body has to move both. These helpers build fixtures
// the same way.
function realisticFrame(value) {
  const json = JSON.stringify(value);
  return `${json.length + 2}\n${json}\n`;
}

function realisticBatch(entries) {
  const render = (total) => {
    const end = [["e", 4, null, null, total]];
    return ")]}'\n\n" + realisticFrame(entries) + realisticFrame(end);
  };
  let total = 0;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const body = render(total);
    const measured = Buffer.byteLength(body, "utf8");
    if (measured === total) return body;
    total = measured;
  }
  throw new Error("fixture did not settle");
}

function scanFrames(body) {
  const frames = [];
  let pos = body.indexOf("[");
  while (pos !== -1 && pos < body.length) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    let end = -1;
    for (let i = pos; i < body.length; i += 1) {
      const ch = body[i];
      if (inString) {
        if (escaped) escaped = false;
        else if (ch === "\\") escaped = true;
        else if (ch === '"') inString = false;
      } else if (ch === '"') inString = true;
      else if (ch === "[" || ch === "{") depth += 1;
      else if (ch === "]" || ch === "}") {
        depth -= 1;
        if (depth === 0) {
          end = i + 1;
          break;
        }
      }
    }
    assert.notEqual(end, -1, "frame closes");
    let tokenStart = pos;
    while (tokenStart > 0 && " \t\r\n".includes(body[tokenStart - 1])) tokenStart -= 1;
    const digitsEnd = tokenStart;
    while (tokenStart > 0 && body[tokenStart - 1] >= "0" && body[tokenStart - 1] <= "9") {
      tokenStart -= 1;
    }
    frames.push({ declared: Number(body.slice(tokenStart, digitsEnd)), text: body.slice(pos, end) });
    pos = body.indexOf("[", end);
  }
  return frames;
}

async function patch(body) {
  const sandbox = await loadUnlock(body);
  const response = await sandbox.fetch(BATCH_URL);
  return { body: response.body, sandbox };
}

test("sets the country flag of a framed JSON app config", async () => {
  const entry = configEntry(configPayload());
  const { body: patched } = await patch(batch([entry]));
  const frames = framesOf(patched);
  const payload = entryPayload(frames[0][0]);
  assert.equal(payload[CONFIG_COUNTRY_INDEX], true);
  assert.equal(payload[CONFIG_AGE_INDEX], true);
  assert.equal(frames[0][0][1], "cPZSdc");
  assert.deepEqual(frames[1], [["e", 4, null, null, 145]], "later frames stay intact");
});

test("keeps the answer untouched when the flags are already set", async () => {
  const values = configPayload();
  values[CONFIG_COUNTRY_INDEX] = true;
  const body = batch([configEntry(values)]);
  const { body: patched, sandbox } = await patch(body);
  assert.equal(patched, body);
  assert.equal(sandbox.__locallabFlowUnlock.patched, false);
});

test("rewrites the country flag of a base64 protobuf app config", async () => {
  const bytes = Uint8Array.from([...varint(31 * 8), 0, ...varint(32 * 8), 1, ...varint(9 * 8), 4]);
  const source = batch([configEntry(Buffer.from(bytes).toString("base64"))]);
  const { body: patched } = await patch(source);
  const payload = framesOf(patched)[0][0][2];
  assert.deepEqual([...Buffer.from(payload, "base64")], [0xf8, 0x01, 0x01, 0x80, 0x02, 0x01, 0x48, 0x04]);
});

test("clears a blocked tool status and keeps an allowed one", async () => {
  const blocked = batch([["wrb.fr", "KV2T2d", "[4]", null, null, null, "generic"]]);
  const cleared = await patch(blocked);
  assert.deepEqual(entryPayload(framesOf(cleared.body)[0][0]), [1]);

  const allowed = batch([["wrb.fr", "KV2T2d", "[1]", null, null, null, "generic"]]);
  const untouched = await patch(allowed);
  assert.equal(untouched.body, allowed);
});

test("marks the document and the breadcrumb once the config is patched", async () => {
  const { sandbox } = await patch(batch([configEntry(configPayload())]));
  assert.equal(sandbox.document.documentElement.attributes["data-locallab-flow-config"], "patched");
  assert.equal(sandbox.__locallabFlowUnlock.patched, true);
  assert.deepEqual([...sandbox.__locallabFlowUnlock.seen], ["cPZSdc"]);
  assert.equal(sandbox.__locallabFlowUnlock.batches, 1);
});

test("exposes the breadcrumb with the manifest version", async () => {
  const manifest = JSON.parse(
    await readFile(join(ROOT, "extensions", "locallab-flow-unlock", "manifest.json"), "utf8")
  );
  const { sandbox } = await patch(batch([configEntry(configPayload())]));
  assert.equal(sandbox.__locallabFlowUnlock.version, manifest.version);
});

test("leaves unrelated responses untouched", async () => {
  const body = batch([["wrb.fr", "RVZT2", "[null,null,1]", null, null, null, "generic"]]);
  const { body: patched, sandbox } = await patch(body);
  assert.equal(patched, body);
  assert.equal(sandbox.document.documentElement.attributes["data-locallab-flow-config"], undefined);
});

test("patches the answer through the XMLHttpRequest getters", async () => {
  const sandbox = await loadUnlock(batch([configEntry(configPayload())]));
  const request = new sandbox.XMLHttpRequest();
  request.open("POST", BATCH_URL);
  request.send();
  const payload = entryPayload(framesOf(request.responseText)[0][0]);
  assert.equal(payload[CONFIG_COUNTRY_INDEX], true);
});

test("keeps length tokens and the end total in step with the rewritten body", async () => {
  const body = realisticBatch([configEntry(configPayload())]);
  const { body: patched } = await patch(body);
  assert.notEqual(patched, body, "the fixture is patched");

  const frames = scanFrames(patched);
  assert.equal(frames.length, 2);
  for (const frame of frames) {
    assert.equal(
      frame.declared,
      frame.text.length + 2,
      "the token still covers the frame and the newlines around it"
    );
  }
  const end = JSON.parse(frames[1].text)[0];
  assert.equal(end[0], "e");
  assert.equal(end[end.length - 1], Buffer.byteLength(patched, "utf8"));
  const payload = entryPayload(JSON.parse(frames[0].text)[0]);
  assert.equal(payload[CONFIG_COUNTRY_INDEX], true);
  assert.equal(payload[CONFIG_AGE_INDEX], true);
});

function varint(value) {
  const out = [];
  let rest = value;
  do {
    let byte = rest % 128;
    rest = Math.floor(rest / 128);
    if (rest > 0) byte += 128;
    out.push(byte);
  } while (rest > 0);
  return out;
}
