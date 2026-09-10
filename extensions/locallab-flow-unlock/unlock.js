// Restores the Flow dashboard for accounts Google marks as coming from an
// unsupported country.
//
// The verdict never reaches the tab as a redirect: the web app asks for two
// configuration RPCs while it boots and routes itself away when they answer
// "not allowed". Both answers are ordinary batchexecute responses:
//
//   VideoFxService.GetFlowAppConfig   rpcid cPZSdc
//     field 31 -> country supported  (false routes the tab to /unsupported-country)
//     field 32 -> age allowed        (false routes the tab to /age-restricted)
//   AiSandbox.CheckToolAvailability   rpcid KV2T2d
//     field 1  -> tool status        (7 age-restricted, 4/5/6/8 unavailable)
//
// This script runs in the page before the app boots and rewrites only those
// fields, so the dashboard renders for the account in the tab while every
// other byte of the answer is passed through untouched. It also marks the
// document so freeze.js can stand down once the answer is known to be patched.
(function () {
  'use strict';

  var CONFIG_RPC = 'cPZSdc';
  var TOOL_RPC = 'KV2T2d';
  var CONFIG_ALLOW_FIELDS = [31, 32];
  var TOOL_STATUS_FIELD = 1;
  var TOOL_BLOCKED_STATUSES = [4, 5, 6, 8];
  var TOOL_READY_STATUS = 1;
  var PATCHED_ATTRIBUTE = 'data-locallab-flow-config';

  function markConfigPatched() {
    try {
      document.documentElement.setAttribute(PATCHED_ATTRIBUTE, 'patched');
    } catch (error) {
      /* the attribute is only a hint for freeze.js */
    }
  }

  function isBatchUrl(url) {
    return typeof url === 'string' && url.indexOf('/data/batchexecute') !== -1;
  }

  // --- protobuf helpers (only varint fields are rewritten) -----------------

  function readVarint(bytes, start) {
    var value = 0;
    var shift = 0;
    var index = start;
    while (index < bytes.length) {
      var byte = bytes[index];
      index += 1;
      value += (byte & 0x7f) * Math.pow(2, shift);
      if ((byte & 0x80) === 0) return { value: value, next: index };
      shift += 7;
      if (shift > 56) return null;
    }
    return null;
  }

  function writeVarint(value) {
    var out = [];
    var rest = value;
    do {
      var byte = rest % 128;
      rest = Math.floor(rest / 128);
      if (rest > 0) byte += 128;
      out.push(byte);
    } while (rest > 0);
    return out;
  }

  function concatChunks(chunks) {
    var size = 0;
    var position;
    for (position = 0; position < chunks.length; position += 1) size += chunks[position].length;
    var out = new Uint8Array(size);
    var offset = 0;
    for (position = 0; position < chunks.length; position += 1) {
      out.set(chunks[position], offset);
      offset += chunks[position].length;
    }
    return out;
  }

  // Rewrites one varint field, keeping every other field as raw bytes. When
  // appendValue is a number the field is appended if it is absent, which is
  // what a proto3 boolean needs to move away from its false default.
  function withVarintField(bytes, fieldNumber, decide, appendValue) {
    var chunks = [];
    var index = 0;
    var changed = false;
    var seen = false;
    while (index < bytes.length) {
      var start = index;
      var tag = readVarint(bytes, index);
      if (!tag) return null;
      index = tag.next;
      var field = Math.floor(tag.value / 8);
      var wire = tag.value % 8;
      if (wire === 0) {
        var item = readVarint(bytes, index);
        if (!item) return null;
        index = item.next;
        if (field === fieldNumber) {
          seen = true;
          var replacement = decide(item.value);
          if (replacement !== null) {
            chunks.push(bytes.subarray(start, tag.next));
            chunks.push(Uint8Array.from(writeVarint(replacement)));
            changed = true;
            continue;
          }
        }
      } else if (wire === 2) {
        var length = readVarint(bytes, index);
        if (!length) return null;
        index = length.next + length.value;
      } else if (wire === 1) {
        index += 8;
      } else if (wire === 5) {
        index += 4;
      } else {
        return null;
      }
      if (index > bytes.length) return null;
      chunks.push(bytes.subarray(start, index));
    }
    if (!seen && typeof appendValue === 'number') {
      chunks.push(Uint8Array.from(writeVarint(fieldNumber * 8)));
      chunks.push(Uint8Array.from(writeVarint(appendValue)));
      changed = true;
    }
    return changed ? concatChunks(chunks) : null;
  }

  function decodeBase64(text) {
    var normalized = text.replace(/-/g, '+').replace(/_/g, '/');
    try {
      var binary = atob(normalized);
      var bytes = new Uint8Array(binary.length);
      for (var index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes;
    } catch (error) {
      return null;
    }
  }

  function encodeBase64(bytes) {
    var binary = '';
    for (var index = 0; index < bytes.length; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return btoa(binary);
  }

  // --- payload rewriting ---------------------------------------------------

  function patchConfigJson(array) {
    var changed = false;
    CONFIG_ALLOW_FIELDS.forEach(function (field) {
      if (array[field] !== true) {
        array[field] = true;
        changed = true;
      }
    });
    return changed;
  }

  function patchToolJson(array) {
    var status = Number(array[TOOL_STATUS_FIELD]);
    if (TOOL_BLOCKED_STATUSES.indexOf(status) === -1) return false;
    array[TOOL_STATUS_FIELD] = TOOL_READY_STATUS;
    return true;
  }

  function patchConfigProto(bytes) {
    var out = bytes;
    var changed = false;
    CONFIG_ALLOW_FIELDS.forEach(function (field) {
      var next = withVarintField(
        out,
        field,
        function (value) {
          return value === 1 ? null : 1;
        },
        1
      );
      if (next) {
        out = next;
        changed = true;
      }
    });
    return changed ? out : null;
  }

  function patchToolProto(bytes) {
    return withVarintField(
      bytes,
      TOOL_STATUS_FIELD,
      function (value) {
        return TOOL_BLOCKED_STATUSES.indexOf(value) === -1 ? null : TOOL_READY_STATUS;
      },
      null
    );
  }

  function patchPayload(rpcId, payload) {
    if (typeof payload !== 'string' || payload === '') return null;
    var trimmed = payload.trim();
    if (trimmed.charAt(0) === '[') {
      var array;
      try {
        array = JSON.parse(trimmed);
      } catch (error) {
        return null;
      }
      if (!Array.isArray(array)) return null;
      var jsonChanged = rpcId === CONFIG_RPC ? patchConfigJson(array) : patchToolJson(array);
      return jsonChanged ? JSON.stringify(array) : null;
    }
    var bytes = decodeBase64(trimmed);
    if (!bytes) return null;
    var patched = rpcId === CONFIG_RPC ? patchConfigProto(bytes) : patchToolProto(bytes);
    return patched ? encodeBase64(patched) : null;
  }

  function patchEntry(entry) {
    if (!Array.isArray(entry) || entry[0] !== 'wrb.fr') return false;
    var rpcId = entry[1];
    if (rpcId !== CONFIG_RPC && rpcId !== TOOL_RPC) return false;
    if (Array.isArray(entry[2])) {
      // The answer arrived already decoded; edit it in place.
      var inPlace = rpcId === CONFIG_RPC ? patchConfigJson(entry[2]) : patchToolJson(entry[2]);
      if (!inPlace) return false;
      if (rpcId === CONFIG_RPC) markConfigPatched();
      return true;
    }
    var patched = patchPayload(rpcId, entry[2]);
    if (patched === null) return false;
    entry[2] = patched;
    if (rpcId === CONFIG_RPC) markConfigPatched();
    return true;
  }

  function patchBatchArray(outer) {
    if (!Array.isArray(outer)) return false;
    var changed = false;
    outer.forEach(function (group) {
      if (!Array.isArray(group)) return;
      group.forEach(function (entry) {
        if (patchEntry(entry)) changed = true;
      });
    });
    return changed;
  }

  function patchBatchText(text) {
    var start = text.indexOf('[');
    if (start === -1) return null;
    var outer;
    try {
      outer = JSON.parse(text.slice(start));
    } catch (error) {
      return null;
    }
    if (!patchBatchArray(outer)) return null;
    return text.slice(0, start) + JSON.stringify(outer);
  }

  // --- transport hooks -----------------------------------------------------

  // The getters are installed when the request is sent, not when it finishes:
  // the app registers its own load listener first, so patching on load would
  // be too late for the listener that reads the answer.
  var responseTextGetter = Object.getOwnPropertyDescriptor(
    XMLHttpRequest.prototype,
    'responseText'
  );
  var responseGetter = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, 'response');

  function patchTextAnswer(raw) {
    if (typeof raw !== 'string' || raw.indexOf('wrb.fr') === -1) return null;
    return patchBatchText(raw);
  }

  function installPatchedGetters(xhr) {
    var cachedText = null;
    var cachedPatch = null;

    if (responseTextGetter && responseTextGetter.get) {
      Object.defineProperty(xhr, 'responseText', {
        configurable: true,
        get: function () {
          var raw = responseTextGetter.get.call(this);
          if (raw !== cachedText) {
            cachedText = raw;
            cachedPatch = patchTextAnswer(raw);
          }
          return cachedPatch === null || cachedPatch === undefined ? raw : cachedPatch;
        }
      });
    }

    if (responseGetter && responseGetter.get) {
      Object.defineProperty(xhr, 'response', {
        configurable: true,
        get: function () {
          var raw = responseGetter.get.call(this);
          if (typeof raw === 'string') {
            var patched = patchTextAnswer(raw);
            return patched === null ? raw : patched;
          }
          if (Array.isArray(raw)) {
            patchBatchArray(raw);
          }
          return raw;
        }
      });
    }
  }

  var openRequest = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__locallabFlowUrl = url;
    return openRequest.apply(this, arguments);
  };

  var sendRequest = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    if (isBatchUrl(this.__locallabFlowUrl)) {
      installPatchedGetters(this);
    }
    return sendRequest.apply(this, arguments);
  };

  if (typeof window.fetch === 'function') {
    var originalFetch = window.fetch;
    window.fetch = function (input) {
      var url = '';
      try {
        url = typeof input === 'string' ? input : (input && input.url) || '';
      } catch (error) {
        url = '';
      }
      var pending = originalFetch.apply(this, arguments);
      if (!isBatchUrl(url)) return pending;
      return pending.then(function (response) {
        return response
          .clone()
          .text()
          .then(function (text) {
            var patched = patchBatchText(text);
            if (!patched) return response;
            return new Response(patched, {
              status: response.status,
              statusText: response.statusText,
              headers: response.headers
            });
          })
          .catch(function () {
            return response;
          });
      });
    };
  }
})();
