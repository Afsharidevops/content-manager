// Restores the Flow dashboard for accounts Google marks as coming from an
// unsupported country.
//
// The verdict never reaches the tab as a redirect: the web app asks for two
// configuration RPCs while it boots and routes itself away when they answer
// "not allowed". Both answers are ordinary batchexecute responses:
//
//   VideoFxService.GetFlowAppConfig   rpcid cPZSdc
//     field 31 -> country supported  (absent or false routes to /unsupported-country)
//     field 32 -> age allowed        (absent or false routes the tab to /age-restricted)
//   AiSandbox.CheckToolAvailability   rpcid KV2T2d
//     field 1  -> tool status        (7 age-restricted, 4/5/6/8 unavailable)
//
// The answers arrive as length-prefixed JSON frames, and inside a frame the
// protocol-buffer JSON array stores field N at index N - 1, so the country
// flag lives at index 30 of the config array and the tool status at index 0 of
// its own array. Each length token covers the frame plus the newlines around
// it, counted in UTF-16 code units, and the closing "e" frame repeats the byte
// size of the whole answer, so both move with any frame this script rewrites.
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
  var FRAME_DEPTH_LIMIT = 3;

  function jsonIndexOf(fieldNumber) {
    return fieldNumber - 1;
  }

  // Breadcrumb for support: in the page console, window.__locallabFlowUnlock
  // tells whether this script ran, how many batchexecute answers it saw, and
  // which of the two region rpcs were among them.
  var STATE = { version: '0.4.0', batches: 0, seen: [], patched: false };
  try {
    window.__locallabFlowUnlock = STATE;
  } catch (error) {
    /* the page keeps its own globals when it blocks assignments */
  }

  function markConfigPatched() {
    try {
      document.documentElement.setAttribute(PATCHED_ATTRIBUTE, 'patched');
    } catch (error) {
      /* the attribute is only a hint for freeze.js */
    }
    STATE.patched = true;
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
      var index = jsonIndexOf(field);
      if (array[index] !== true) {
        array[index] = true;
        changed = true;
      }
    });
    return changed;
  }

  function patchToolJson(array) {
    var index = jsonIndexOf(TOOL_STATUS_FIELD);
    var status = Number(array[index]);
    if (TOOL_BLOCKED_STATUSES.indexOf(status) === -1) return false;
    array[index] = TOOL_READY_STATUS;
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
    if (STATE.seen.indexOf(rpcId) === -1) STATE.seen.push(rpcId);
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
    var walk = function (node, depth) {
      if (!Array.isArray(node) || depth > FRAME_DEPTH_LIMIT) return;
      if (node[0] === 'wrb.fr') {
        if (patchEntry(node)) changed = true;
        return;
      }
      node.forEach(function (child) {
        walk(child, depth + 1);
      });
    };
    walk(outer, 0);
    return changed;
  }

  // --- batchexecute framing ------------------------------------------------
  //
  // )]}'
  //
  // 3768
  // [["wrb.fr","cPZSdc","...",null,null,null,"generic"]]
  // 25
  // [["e",4,null,null,145]]
  //
  // Every frame is preceded by the length of the JSON that follows it, so that
  // token is rewritten whenever a frame changes.

  function jsonValueEnd(text, start) {
    var depth = 0;
    var inString = false;
    var escaped = false;
    for (var index = start; index < text.length; index += 1) {
      var character = text.charAt(index);
      if (inString) {
        if (escaped) escaped = false;
        else if (character === '\\') escaped = true;
        else if (character === '"') inString = false;
        continue;
      }
      if (character === '"') inString = true;
      else if (character === '[' || character === '{') depth += 1;
      else if (character === ']' || character === '}') {
        depth -= 1;
        if (depth === 0) return index + 1;
      }
    }
    return -1;
  }

  function utf8Length(text) {
    if (typeof TextEncoder === 'function') return new TextEncoder().encode(text).length;
    return text.length;
  }

  // A length token counts the frame plus the newline on each side of it, in
  // UTF-16 code units, so a rewritten frame moves its token by the same delta.
  function frameLength(declared, original, patched) {
    return declared + (patched.length - original.length);
  }

  // The closing "e" frame carries the byte size of the whole answer. Only a
  // frame whose value already matches the measured size is treated as that
  // marker, so fixtures and future shape changes are left alone.
  function endFrameValue(frameText, bodyBytes) {
    var data;
    try {
      data = JSON.parse(frameText);
    } catch (error) {
      return null;
    }
    if (!Array.isArray(data)) return null;
    for (var index = 0; index < data.length; index += 1) {
      var entry = data[index];
      if (!Array.isArray(entry) || entry[0] !== 'e' || !entry.length) continue;
      return entry[entry.length - 1] === bodyBytes;
    }
    return null;
  }

  function withEndValue(frameText, value) {
    var data;
    try {
      data = JSON.parse(frameText);
    } catch (error) {
      return frameText;
    }
    if (!Array.isArray(data)) return frameText;
    for (var index = 0; index < data.length; index += 1) {
      var entry = data[index];
      if (Array.isArray(entry) && entry[0] === 'e' && entry.length) {
        entry[entry.length - 1] = value;
        return JSON.stringify(data);
      }
    }
    return frameText;
  }

  function patchFrame(text) {
    var data;
    try {
      data = JSON.parse(text);
    } catch (error) {
      return null;
    }
    return patchBatchArray(data) ? JSON.stringify(data) : null;
  }

  function patchBatchText(text) {
    STATE.batches += 1;
    var first = text.indexOf('[');
    if (first === -1) return null;
    var frames = [];
    var position = first;
    while (position < text.length) {
      while (
        position < text.length &&
        text.charAt(position) !== '[' &&
        text.charAt(position) !== '{'
      ) {
        position += 1;
      }
      if (position >= text.length) break;
      var end = jsonValueEnd(text, position);
      if (end === -1) return null;
      var tokenEnd = position;
      var cursor = tokenEnd;
      while (cursor > 0 && ' \t\r\n'.indexOf(text.charAt(cursor - 1)) !== -1) cursor -= 1;
      var digitsEnd = cursor;
      var tokenStart = cursor;
      while (
        tokenStart > 0 &&
        text.charAt(tokenStart - 1) >= '0' &&
        text.charAt(tokenStart - 1) <= '9'
      ) {
        tokenStart -= 1;
      }
      frames.push({
        start: position,
        end: end,
        text: text.slice(position, end),
        tokenStart: tokenStart,
        digitsEnd: digitsEnd
      });
      position = end;
    }
    if (!frames.length) return null;

    var bodyBytes = utf8Length(text);
    var endFrameIndex = -1;
    for (var scan = 0; scan < frames.length; scan += 1) {
      if (endFrameValue(frames[scan].text, bodyBytes) === true) {
        endFrameIndex = scan;
        break;
      }
    }

    var patchedFrames = frames.map(function (frame) {
      return patchFrame(frame.text);
    });
    if (patchedFrames.every(function (value) { return value === null; })) return null;

    function assemble(endTotal) {
      var out = text.slice(0, frames[0].tokenStart);
      var copied = frames[0].tokenStart;
      frames.forEach(function (frame, index) {
        var patched = patchedFrames[index];
        if (patched === null && index === endFrameIndex && endTotal !== null) {
          patched = withEndValue(frame.text, endTotal);
        }
        out += text.slice(copied, frame.tokenStart);
        if (patched === null) {
          out += text.slice(frame.tokenStart, frame.end);
        } else {
          var declared = parseInt(text.slice(frame.tokenStart, frame.digitsEnd), 10);
          out += String(frameLength(declared, frame.text, patched));
          out += text.slice(frame.digitsEnd, frame.start);
          out += patched;
        }
        copied = frame.end;
      });
      out += text.slice(copied);
      return out;
    }

    var body = assemble(null);
    if (endFrameIndex === -1) return body;

    // The end value is the size of the whole answer, so it depends on the
    // digits of its own replacement; a couple of rounds settle it.
    var total = utf8Length(body);
    for (var attempt = 0; attempt < 4; attempt += 1) {
      body = assemble(total);
      var measured = utf8Length(body);
      if (measured === total) return body;
      total = measured;
    }
    return body;
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
