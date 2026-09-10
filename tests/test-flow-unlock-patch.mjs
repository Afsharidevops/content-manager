import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPT = join(ROOT, "extensions", "locallab-flow-unlock", "unlock.js");
const BATCH_URL = "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=cPZSdc&rt=c";

// The extension runs inside the page, so the tests load it into a sandbox with
// the globals Chrome provides and drive it through its own fetch hook.
async function loadUnlock(body) {
  const source = await readFile(SCRIPT, "utf8");
  const sandbox = {
    console,
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    btoa: (value) => Buffer.from(value, "binary").toString("base64"),
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

function batch(entries) {
  return ")]}'\n\n" + JSON.stringify([entries]);
}

function entryPayload(body, rpcId) {
  const outer = JSON.parse(body.slice(body.indexOf("[")));
  const entry = outer[0].find((item) => item[1] === rpcId);
  return entry[2];
}

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

async function patch(body) {
  const sandbox = await loadUnlock(body);
  const response = await sandbox.fetch(BATCH_URL);
  return { body: response.body, sandbox };
}

test("rewrites the country and age flags of the JSON app config", async () => {
  const config = new Array(33).fill(null);
  config[31] = false;
  config[32] = false;
  const body = batch([["wrb.fr", "cPZSdc", JSON.stringify(config), null, null, null, "generic"]]);
  const { body: patched } = await patch(body);
  const configAfter = JSON.parse(entryPayload(patched, "cPZSdc"));
  assert.equal(configAfter[31], true);
  assert.equal(configAfter[32], true);
});

test("rewrites the country flag of a base64 protobuf app config", async () => {
  const bytes = Uint8Array.from([...varint(31 * 8), 0, ...varint(32 * 8), 1, ...varint(9 * 8), 4]);
  const body = batch([
    ["wrb.fr", "cPZSdc", Buffer.from(bytes).toString("base64"), null, null, null, "generic"],
  ]);
  const { body: patched } = await patch(body);
  const decoded = [...Buffer.from(entryPayload(patched, "cPZSdc"), "base64")];
  assert.deepEqual(decoded, [0xf8, 0x01, 0x01, 0x80, 0x02, 0x01, 0x48, 0x04]);
});

test("appends the country flag when the server omits it", async () => {
  const body = batch([
    ["wrb.fr", "cPZSdc", Buffer.from(Uint8Array.from([...varint(9 * 8), 4])).toString("base64"), null, null, null, "generic"],
  ]);
  const { body: patched } = await patch(body);
  const decoded = [...Buffer.from(entryPayload(patched, "cPZSdc"), "base64")];
  assert.deepEqual(decoded, [0x48, 0x04, 0xf8, 0x01, 0x01, 0x80, 0x02, 0x01]);
});

test("clears a blocked tool status and keeps an allowed one", async () => {
  const blocked = batch([["wrb.fr", "KV2T2d", "[null,8]", null, null, null, "generic"]]);
  const cleared = await patch(blocked);
  assert.equal(JSON.parse(entryPayload(cleared.body, "KV2T2d"))[1], 1);

  const allowed = batch([["wrb.fr", "KV2T2d", "[null,1]", null, null, null, "generic"]]);
  const untouched = await patch(allowed);
  assert.equal(untouched.body, allowed);
});

test("marks the document once the config answer is patched", async () => {
  const config = new Array(33).fill(null);
  config[31] = false;
  const body = batch([["wrb.fr", "cPZSdc", JSON.stringify(config), null, null, null, "generic"]]);
  const { sandbox } = await patch(body);
  assert.equal(sandbox.document.documentElement.attributes["data-locallab-flow-config"], "patched");
});

test("leaves unrelated responses untouched", async () => {
  const body = batch([["wrb.fr", "RVZT2", "[null,null,1]", null, null, null, "generic"]]);
  const { body: patched, sandbox } = await patch(body);
  assert.equal(patched, body);
  assert.equal(sandbox.document.documentElement.attributes["data-locallab-flow-config"], undefined);
});

test("patches the answer through the XMLHttpRequest getters", async () => {
  const config = new Array(33).fill(null);
  config[31] = false;
  const body = batch([["wrb.fr", "cPZSdc", JSON.stringify(config), null, null, null, "generic"]]);
  const sandbox = await loadUnlock(body);
  const request = new sandbox.XMLHttpRequest();
  request.open("POST", BATCH_URL);
  request.send();
  const configAfter = JSON.parse(entryPayload(request.responseText, "cPZSdc"));
  assert.equal(configAfter[31], true);
});
