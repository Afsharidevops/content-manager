/* Operator console for the content stack. No build step, no dependencies. */
"use strict";

const VIEWS = [
  { id: "overview", label: "Overview", title: "Stack status", hint: "Containers, disk, published ports and the content pipeline at a glance." },
  { id: "state", label: "Pipeline state", title: "Pipeline state", hint: "Draft counters, scheduled routines and the most recent drafts." },
  { id: "config", label: "Configuration", title: "Configuration files", hint: "Validated YAML/JSON editors with automatic backups before every save." },
  { id: "env", label: "Environment", title: "Environment (.env)", hint: "Secret values stay masked; edited keys apply after Apply changes." },
  { id: "storage", label: "Storage", title: "Object storage (S3)", hint: "Shared S3 block, RustFS state and the per-service storage matrix." },
  { id: "backups", label: "Backups", title: "Backups", hint: "Stack archives with their sections, sizes and creation times." },
  { id: "logs", label: "Logs", title: "Service logs", hint: "Tail docker compose logs without leaving the console." },
  { id: "actions", label: "Actions", title: "Stack actions", hint: "A fixed whitelist of compose and manage.sh actions. Nothing else runs." },
];

const root = document.getElementById("shell");
const loginBox = document.getElementById("login");
const page = document.getElementById("page");
const nav = document.getElementById("nav");
const toast = document.getElementById("toast");
const banner = document.getElementById("banner");

let session = null;
let currentView = "overview";
let toastTimer = null;
let autoRefreshTimer = null;

/* ------------------------------------------------------------------ utils */

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function notify(message, kind) {
  toast.textContent = message;
  toast.className = "toast" + (kind === "error" ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 4200);
}

function showBanner(message, kind) {
  banner.replaceChildren(message ? h("div", { class: "banner" + (kind === "error" ? " error" : ""), text: message }) : "");
}

async function api(path, { method = "GET", body = null } = {}) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== null) {
    options.headers["Content-Type"] = "application/json";
    options.headers["X-Panel-Csrf"] = "1";
    options.body = JSON.stringify(body);
  } else if (method !== "GET") {
    options.headers["X-Panel-Csrf"] = "1";
  }
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  if (response.status === 401) {
    showLogin();
    throw new Error("session expired");
  }
  if (!response.ok) {
    throw new Error((payload && payload.error) || `request failed (${response.status})`);
  }
  return payload || {};
}

function dot(state) {
  const value = String(state || "").toLowerCase();
  return h("span", { class: "dot " + value, title: value || "unknown" });
}

function card(title, ...children) {
  return h("section", { class: "card" }, title ? h("h3", { text: title }) : null, ...children);
}

function table(headers, rows) {
  return h(
    "div",
    { class: "tablewrap" },
    h(
      "table",
      null,
      h("thead", null, h("tr", null, ...headers.map((header) => h("th", { text: header })))),
      h(
        "tbody",
        null,
        rows.length
          ? rows.map((cells) => h("tr", null, ...cells.map((cell) => h("td", null, cell))))
          : h("tr", null, h("td", { colspan: headers.length, class: "muted", text: "Nothing to show yet." })),
      ),
    ),
  );
}

function metric(label, value, note) {
  return card(label, h("div", { class: "metric", text: String(value) }), note ? h("div", { class: "muted small", text: note }) : null);
}

/* ------------------------------------------------------------------ shell */

function showLogin() {
  session = null;
  loginBox.classList.remove("hidden");
  root.classList.add("hidden");
}

function showShell() {
  loginBox.classList.add("hidden");
  root.classList.remove("hidden");
}

function renderNav() {
  nav.replaceChildren(
    ...VIEWS.map((view) =>
      h("button", {
        class: view.id === currentView ? "active" : "",
        type: "button",
        text: view.label,
        onclick: () => selectView(view.id),
      }),
    ),
  );
}

function selectView(id) {
  const view = VIEWS.find((item) => item.id === id) || VIEWS[0];
  currentView = view.id;
  document.getElementById("eyebrow").textContent = view.label;
  document.getElementById("title").textContent = view.title;
  document.getElementById("hint").textContent = view.hint;
  renderNav();
  showBanner("");
  loadView();
}

async function loadView() {
  page.replaceChildren(h("div", { class: "muted", text: "Loading..." }));
  try {
    if (currentView === "overview") await renderOverview();
    else if (currentView === "state") await renderState();
    else if (currentView === "config") await renderConfig();
    else if (currentView === "env") await renderEnv();
    else if (currentView === "storage") await renderStorage();
    else if (currentView === "backups") await renderBackups();
    else if (currentView === "logs") await renderLogs();
    else if (currentView === "actions") await renderActions();
  } catch (error) {
    if (String(error.message) === "session expired") return;
    page.replaceChildren(h("div", { class: "banner error", text: String(error.message) }));
  }
}

/* ----------------------------------------------------------------- views */

async function renderOverview() {
  const status = await api("/api/status");
  const [links, media, instagram] = await Promise.all([
    api("/api/links").catch(() => ({ links: [] })),
    api("/api/media/jobs").catch(() => ({ ok: false, error: "", jobs: [] })),
    api("/api/instagram").catch(() => null),
  ]);

  if (status.error) showBanner(status.error, "error");
  else if ((status.exposure.warnings || []).length) showBanner(status.exposure.warnings.join(" "), "warn");

  const running = status.services.filter((service) => String(service.state).toLowerCase() === "running").length;
  const counters = status.state || {};

  page.replaceChildren(
    h(
      "div",
      { class: "grid cols-4" },
      metric("Containers", `${running}/${status.services.length}`, "running / defined"),
      metric("Profiles", (status.profiles || []).length ? status.profiles.join(", ") : "none", "COMPOSE_PROFILES"),
      metric("Disk free", status.disk.filesystem.free, `${status.disk.filesystem.percent}% used`),
      metric("Published today", counters.published_today || 0, `total ${counters.published_total || 0}`),
    ),
    h("div", { class: "grid cols-2", style: "margin-top:12px" },
      card("Containers",
        table(
          ["Service", "State", "Status", "Health", "Image", "Host ports", "Up for"],
          status.services.map((service) => [
            h("strong", { text: service.service || service.name }),
            h("span", null, dot(service.state), service.state),
            service.status,
            service.health,
            h("code", { text: service.image }),
            (service.ports || []).join(", "),
            service.running_for,
          ]),
        ),
      ),
      card("Image tags",
        h("div", { class: "row" },
          ...Object.entries(status.image_tags || {}).map(([key, value]) =>
            h("span", { class: "chip", text: `${key.replace("_IMAGE_TAG", "").toLowerCase()}: ${value}` }),
          ),
        ),
        h("h3", { text: "Endpoints", style: "margin-top:16px" }),
        h("div", { class: "grid" },
          ...(links.links || []).map((link) =>
            h("div", null,
              h("a", { href: link.url, target: "_blank", rel: "noreferrer", text: link.label }),
              h("div", { class: "muted small", text: link.note }),
            ),
          ),
        ),
        h("h3", { text: "Local data", style: "margin-top:16px" }),
        h("div", { class: "row" },
          ...(status.disk.data || []).map((item) => h("span", { class: "chip", text: `${item.name}: ${item.size}` })),
        ),
      ),
    ),
    h("div", { class: "grid cols-2", style: "margin-top:12px" },
      card("Instagram publishing",
        instagram
          ? h("div", { class: "grid" },
              h("div", { class: "row" },
                h("span", { class: "chip " + (instagram.configured ? "ok" : "warn"),
                  text: instagram.configured ? "configured" : "not configured" }),
                h("span", { class: "chip", text: `API ${instagram.api_version}` }),
                h("span", { class: "chip " + (instagram.token_set ? "ok" : "warn"),
                  text: instagram.token_set ? "token stored" : "no token" }),
              ),
              h("div", { class: "muted small" },
                instagram.expires_at
                  ? `Long-lived token refreshed ${instagram.refreshed_at || "?"} and expires ${instagram.expires_at}.`
                  : "No automatic refresh recorded yet; the bot refreshes the long-lived token before it expires."),
              instagram.last_error
                ? h("div", { class: "error small", text: `Last refresh error: ${instagram.last_error}` })
                : null,
              h("div", { class: "muted small" },
                `Media base URL: ${instagram.public_base_url || "not set (Instagram needs a public HTTPS URL)"}`),
              h("div", { class: "row actions" },
                h("button", {
                  class: "btn small",
                  type: "button",
                  text: "Refresh token now",
                  onclick: async (event) => {
                    event.currentTarget.disabled = true;
                    try {
                      await api("/api/instagram/refresh", { method: "POST" });
                      notify("Refresh queued; the bot confirms the result in Telegram.");
                    } catch (error) {
                      notify(String(error.message), "error");
                    } finally {
                      event.currentTarget.disabled = false;
                    }
                  },
                }),
              ),
            )
          : h("div", { class: "muted small", text: "Instagram status unavailable." }),
      ),
      card("Media jobs",
        media.ok
          ? table(
              ["Job", "Driver", "Status", "Created", "Artifacts", "Error"],
              (media.jobs || []).map((job) => [
                h("code", { text: job.id }),
                job.driver,
                job.status,
                job.created_at,
                (job.artifacts || []).join(", "),
                h("span", { class: "error", text: job.error }),
              ]),
            )
          : h("div", { class: "muted", text: media.error || "Media Studio is not reachable." }),
      ),
    ),
  );
}

const PUBLISHABLE_STATUSES = ["text", "text_only", "media_ready"];
const MEDIA_QUESTION_STATUSES = ["media_ask", "awaiting_media"];

function draftActionButton(draft, action, label, confirmFirst) {
  return h("button", {
    class: "btn small",
    type: "button",
    text: label,
    onclick: async (event) => {
      if (confirmFirst && !confirm(`${label}: draft "${draft.title || draft.id}"?`)) return;
      event.currentTarget.disabled = true;
      try {
        await api(`/api/drafts/${encodeURIComponent(draft.id)}/action`, { method: "POST", body: { action } });
        notify(`Queued: ${label}. The bot applies it within a few seconds.`);
        loadView();
      } catch (error) {
        notify(String(error.message), "error");
        event.currentTarget.disabled = false;
      }
    },
  });
}

function draftControls(draft) {
  const status = String(draft.status || "");
  const buttons = [];
  if (MEDIA_QUESTION_STATUSES.includes(status)) {
    buttons.push(draftActionButton(draft, "text-only", "Text only", false));
    if (status === "media_ask") buttons.push(draftActionButton(draft, "image", "AI image", false));
  }
  if (PUBLISHABLE_STATUSES.includes(status)) {
    buttons.push(draftActionButton(draft, "publish", "Publish (Telegram)", true));
    buttons.push(draftActionButton(draft, "publish_both", "Telegram + Instagram", true));
    buttons.push(draftActionButton(draft, "publish_ig", "Instagram", true));
  }
  buttons.push(draftActionButton(draft, "discard", "Discard", true));
  return h("div", { class: "row actions" }, ...buttons);
}

function statusChip(status) {
  const value = String(status || "unknown");
  const tone = value === "media_ready" || value === "text_only" || value === "text" ? "ok"
    : value === "media_failed" ? "warn"
    : value === "media_running" || value === "collecting" ? "warn"
    : "";
  return h("span", { class: "chip " + tone, text: value });
}

function stateSignature(state) {
  const drafts = (state.drafts || []).map((draft) => `${draft.id}:${draft.status}:${draft.media}`).join("|");
  const results = (state.results || []).map((row) => `${row.finished_at}:${row.draft_id}:${row.ok}`).join("|");
  return `${drafts}::${results}`;
}

async function renderState() {
  const state = await api("/api/drafts");
  const counters = state.counters || {};
  const runs = state.routine_last_run || {};
  const signature = stateSignature(state);

  page.replaceChildren(
    h(
      "div",
      { class: "grid cols-4" },
      metric("Drafts", counters.drafts_total || 0, state.exists ? "live queue" : "state file not created yet"),
      metric("Published today", counters.published_today || 0, counters.day || ""),
      metric("Published total", counters.published_total || 0),
      metric("Daily research", counters.daily_last_run || "not yet", "last scheduled run"),
    ),
    h("div", { class: "grid cols-2", style: "margin-top:12px" },
      card("Draft queue",
        table(
          ["Draft", "Status", "Title", "Media", "Created", "Console actions"],
          (state.drafts || []).map((draft) => [
            h("code", { text: draft.id }),
            statusChip(draft.status),
            h("div", null, h("span", { text: draft.title || "-" }),
              h("div", { class: "muted small", text: `${draft.kind} - ${draft.category || "no category"}` })),
            draft.media || "-",
            draft.created_at,
            draftControls(draft),
          ]),
        ),
        h("div", { class: "muted small", style: "margin-top:10px" },
          "Console actions are applied by the Content Bot within a few seconds and confirmed by a Telegram message."),
      ),
      card("Console action results",
        (state.results || []).length
          ? table(
              ["When", "Action", "Draft", "Result"],
              (state.results || []).map((row) => [
                row.finished_at || row.requested_at || "",
                row.action,
                h("code", { text: row.draft_id }),
                h("span", { class: row.ok ? "" : "error", text: row.message || (row.ok ? "ok" : "failed") }),
              ]),
            )
          : h("div", { class: "muted small", text: "No console actions yet." }),
      ),
    ),
    h("div", { style: "margin-top:12px" },
      card("Scheduled routines",
        table(
          ["Routine", "Platform", "Cadence", "Time", "Weekday", "Count", "Media", "Enabled", "Last run"],
          (state.routines || []).map((routine) => [
            h("code", { text: routine.id }),
            routine.platform,
            routine.cadence,
            routine.time,
            routine.weekday,
            routine.count,
            routine.media,
            h("span", { class: "chip " + (routine.enabled ? "ok" : "warn"), text: routine.enabled ? "enabled" : "disabled" }),
            runs[routine.id] || "never",
          ]),
        ),
      ),
    ),
  );
  clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    if (currentView !== "state") return;
    api("/api/drafts")
      .then((fresh) => {
        if (stateSignature(fresh) !== signature) loadView();
      })
      .catch(() => {});
  }, 10000);
}

async function renderConfig() {
  const listing = await api("/api/config");
  let selected = (listing.files || []).find((file) => file.exists) || (listing.files || [])[0] || null;
  let editor = null;
  let meta = h("div", { class: "muted small" });
  let backupBox = h("div", { class: "grid" });

  const fileButton = (file) =>
    h("button", {
      class: file === selected ? "active" : "",
      type: "button",
      text: file.title,
      onclick: () => {
        selected = file;
        paint();
      },
    });

  async function loadFile() {
    editor.value = "";
    meta.textContent = "Loading...";
    backupBox.replaceChildren(h("div", { class: "muted small", text: "Loading backups..." }));
    if (!selected) return;
    seedButton.classList.add("hidden");
    try {
      const data = await api(`/api/config/${encodeURIComponent(selected.name)}`);
      editor.value = data.text;
      meta.textContent = `${data.path} - modified ${data.modified_at}`;
    } catch (error) {
      meta.textContent = "";
      meta.append(h("span", { class: "error", text: String(error.message) }));
      if (selected.can_seed) seedButton.classList.remove("hidden");
    }
    const backups = await api(`/api/config/${encodeURIComponent(selected.name)}/backups`).catch(() => ({ backups: [] }));
    const rows = backups.backups || [];
    backupBox.replaceChildren(
      rows.length
        ? table(
            ["Backup", "Size", "Created", ""],
            rows.map((backup) => [
              h("code", { text: backup.name }),
              `${backup.bytes} B`,
              backup.modified_at,
              h("button", {
                class: "btn ghost",
                type: "button",
                text: "Restore",
                onclick: async () => {
                  if (!confirm(`Restore ${backup.name}? The current file is backed up first.`)) return;
                  await api(`/api/config/${encodeURIComponent(selected.name)}/restore`, {
                    method: "POST",
                    body: { backup: backup.name },
                  });
                  notify("Configuration restored.");
                  loadFile();
                },
              }),
            ]),
          )
        : h("div", { class: "muted small", text: "No backups yet; one is written before every save." }),
    );
  }

  const seedButton = h("button", {
    class: "btn",
    type: "button",
    text: "Create from shipped default",
    onclick: async () => {
      try {
        await api(`/api/config/${encodeURIComponent(selected.name)}/seed`, { method: "POST", body: {} });
        notify("Working copy created from the shipped default.");
        await renderConfig();
      } catch (error) {
        notify(String(error.message), "error");
      }
    },
  });

  function paint() {
    const list = h("div", { class: "filelist" }, ...(listing.files || []).map(fileButton));
    editor = h("textarea", { spellcheck: "false" });
    const view = h(
      "div",
      { class: "grid cols-2" },
      h("div", null, list, h("div", { class: "muted small", style: "margin-top:10px", text: selected ? selected.description : "" })),
      card(
        selected ? selected.title : "Configuration",
        h("div", { class: "spread" },
          meta,
          h("div", { class: "row" },
            h("button", { class: "btn", type: "button", text: "Reload", onclick: loadFile }),
            seedButton,
            h("button", {
              class: "btn primary",
              type: "button",
              text: "Validate & save",
              onclick: async (event) => {
                const button = event.currentTarget;
                button.disabled = true;
                try {
                  const result = await api(`/api/config/${encodeURIComponent(selected.name)}`, {
                    method: "PUT",
                    body: { text: editor.value },
                  });
                  notify(result.backup ? `Saved. Backup: ${result.backup}` : "Saved.");
                  await loadFile();
                } catch (error) {
                  notify(String(error.message), "error");
                } finally {
                  button.disabled = false;
                }
              },
            }),
          ),
        ),
        editor,
        h("h3", { text: "Backups", style: "margin-top:16px" }),
        backupBox,
      ),
    );
    page.replaceChildren(view);
    loadFile();
  }

  paint();
}

async function renderEnv() {
  const payload = await api("/api/env");
  const entries = payload.entries || [];
  const filter = h("input", { placeholder: "Filter keys...", type: "search" });
  const container = h("div");

  function row(entry) {
    const input = h("input", {
      type: entry.secret ? "password" : "text",
      placeholder: entry.secret ? (entry.set ? "stored - type to replace" : "not set") : "",
      autocomplete: "off",
    });
    if (!entry.secret && entry.value !== null && entry.value !== undefined) input.value = entry.value;
    return [
      h("td", null,
        h("code", { text: entry.key }),
        h("div", { class: "muted small", text: entry.comment || "" })),
      h("td", null, entry.secret ? h("span", { class: "chip", text: entry.set ? "secret stored" : "empty" }) : h("span", { class: "chip", text: entry.set ? "set" : "empty" })),
      h("td", null, input),
      h("td", null,
        h("button", {
          class: "btn",
          type: "button",
          text: "Save",
          onclick: async () => {
            try {
              const result = await api(`/api/env/${encodeURIComponent(entry.key)}`, {
                method: "PUT",
                body: { key: entry.key, value: input.value },
              });
              notify(result.restart_hint || "Saved.");
              renderEnv();
            } catch (error) {
              notify(String(error.message), "error");
            }
          },
        }),
      ),
    ];
  }

  function paint() {
    const needle = filter.value.trim().toLowerCase();
    const visible = entries.filter((entry) => !needle || entry.key.toLowerCase().includes(needle));
    container.replaceChildren(
      table(["Key", "", "Value", ""], visible.map(row)),
      h("div", { class: "muted small", style: "margin-top:10px" },
        "Editing .env does not restart containers. Run Apply changes from the Actions view afterwards."),
    );
  }

  filter.addEventListener("input", paint);
  page.replaceChildren(
    card("Environment",
      h("div", { class: "spread" }, filter,
        h("span", { class: "chip", text: `${entries.length} keys` })),
      container,
    ),
  );
  paint();
}

async function renderLogs() {
  const status = await api("/api/status");
  const services = (status.services || []).map((service) => service.service).filter(Boolean);
  const select = h("select", null, ...services.map((name) => h("option", { value: name, text: name })));
  const tail = h("select", null, ...[100, 200, 500, 1000].map((value) => h("option", { value, text: `${value} lines` })));
  tail.value = "200";
  const output = h("pre", { text: "Choose a service and press Load logs." });

  async function load() {
    output.textContent = "Loading...";
    try {
      const data = await api(`/api/logs/${encodeURIComponent(select.value)}?lines=${tail.value}`);
      output.textContent = (data.lines || []).join("\n") || "(no output)";
    } catch (error) {
      output.textContent = String(error.message);
    }
  }

  const auto = h("input", { type: "checkbox" });
  clearInterval(autoRefreshTimer);
  auto.addEventListener("change", () => {
    clearInterval(autoRefreshTimer);
    if (auto.checked) autoRefreshTimer = setInterval(load, 5000);
  });

  page.replaceChildren(
    card("Service logs",
      h("div", { class: "spread" },
        h("div", { class: "row" }, select, tail,
          h("button", { class: "btn primary", type: "button", text: "Load logs", onclick: load })),
        h("label", { class: "row muted small" }, auto, "auto-refresh every 5s"),
      ),
      output,
    ),
  );
  select.value = services.includes("content-bot") ? "content-bot" : services[0] || "";
  if (services.length) load();
}

async function renderActions() {
  const payload = await api("/api/actions");
  const output = h("pre", { class: "hidden" });

  async function run(action) {
    if (action.confirm && !confirm(`${action.label}: ${action.description}`)) return;
    output.classList.remove("hidden");
    output.textContent = `Running ${action.label}...`;
    try {
      const result = await api(`/api/actions/${encodeURIComponent(action.name)}`, { method: "POST", body: {} });
      output.textContent = [
        `${result.label}: ${result.ok ? "ok" : "failed"} (exit ${result.returncode}, ${result.duration_seconds}s)`,
        result.output || "",
      ].join("\n\n");
      notify(`${result.label}: ${result.ok ? "ok" : "failed"}`, result.ok ? "" : "error");
    } catch (error) {
      output.textContent = String(error.message);
      notify(String(error.message), "error");
    }
  }

  page.replaceChildren(
    payload.enabled
      ? card("Whitelisted actions",
          h("div", { class: "grid cols-2" },
            ...(payload.actions || []).map((action) =>
              card(action.label,
                h("div", { class: "muted small", text: action.description }),
                h("div", { class: "row", style: "margin-top:10px" },
                  h("button", {
                    class: "btn " + (action.confirm ? "danger" : "primary"),
                    type: "button",
                    text: action.confirm ? "Confirm & run" : "Run",
                    onclick: () => run(action),
                  }),
                ),
              ),
            ),
          ),
        )
      : h("div", { class: "banner", text: "Actions are disabled (PANEL_ACTIONS_ENABLED=false). Read-only views stay available." }),
    h("div", { style: "margin-top:12px" }, card("Output", output)),
  );
}

async function runActionInto(output, name, label, confirmFirst, params) {
  if (confirmFirst && !confirm(`${label}: continue?`)) return;
  output.classList.remove("hidden");
  output.textContent = `Running ${label}...`;
  try {
    const result = await api(`/api/actions/${encodeURIComponent(name)}`, { method: "POST", body: params || {} });
    output.textContent = [
      `${result.label}: ${result.ok ? "ok" : "failed"} (exit ${result.returncode}, ${result.duration_seconds}s)`,
      result.output || "",
    ].join("\n\n");
    notify(`${result.label}: ${result.ok ? "ok" : "failed"}`, result.ok ? "" : "error");
  } catch (error) {
    output.textContent = String(error.message);
    notify(String(error.message), "error");
  }
  return output;
}

async function renderStorage() {
  const storage = await api("/api/storage");
  const output = h("pre", { class: "hidden" });
  if ((storage.warnings || []).length) showBanner(storage.warnings.join(" "), "warn");

  const rustfs = storage.rustfs;
  const rustfsState = rustfs && rustfs.service
    ? `${rustfs.service.state || "unknown"}${rustfs.service.health ? ` (${rustfs.service.health})` : ""}`
    : "not running";

  page.replaceChildren(
    h("div", { class: "grid cols-4" },
      metric("Backend", storage.backend, "S3_STORAGE_BACKEND"),
      metric("Bucket", storage.bucket || "none", storage.region || "no region"),
      metric("Stack endpoint", storage.endpoint || "not set", "how containers reach the bucket"),
      metric("Public origin", storage.public_base_url || "-", "S3_PUBLIC_BASE_URL"),
    ),
    h("div", { class: "grid cols-2", style: "margin-top:12px" },
      card("Configuration",
        table(["Key", "Value"], [
          ["Host endpoint", storage.host_endpoint || "-"],
          ["Key prefix", storage.key_prefix || "-"],
          ["Path-style addressing", storage.force_path_style ? "true" : "false"],
          ["Console origin", storage.public_console_url || "-"],
          ["Open WebUI storage", storage.openwebui_storage_provider],
        ]),
        h("div", { class: "muted small", style: "margin-top:10px", text: `Guide: ${storage.guide}` }),
      ),
      card("Bundled RustFS",
        rustfs
          ? h("div", { class: "grid" },
              h("div", { class: "row" },
                h("span", { class: "chip " + (rustfs.service && String(rustfs.service.state).toLowerCase() === "running" ? "ok" : "warn"), text: rustfsState }),
                h("span", { class: "chip", text: `API ${rustfs.api_url}` }),
              ),
              h("div", { class: "muted small", text: `Console bind ${rustfs.console_bind}; keep it on loopback unless a proxy publishes it.` }),
              h("div", { class: "row" },
                h("a", { href: rustfs.console_url, target: "_blank", rel: "noreferrer", text: "Open the console" }),
              ),
            )
          : h("div", { class: "muted", text: "RustFS is not part of this configuration; the stack points at an external provider or no object storage." }),
        h("div", { class: "row actions", style: "margin-top:10px" },
          h("button", { class: "btn primary", type: "button", text: "Run status", onclick: () => runActionInto(output, "s3-status", "Object storage status") }),
          h("button", { class: "btn", type: "button", text: "Verify endpoint", onclick: () => runActionInto(output, "s3-verify", "Verify object storage") }),
        ),
      ),
    ),
    h("div", { style: "margin-top:12px" },
      card("Consumers",
        table(["Service", "Storage", "Notes"],
          (storage.consumers || []).map((row) => [
            h("strong", { text: row.service }),
            h("span", { class: "chip " + (row.mode === "s3" ? "ok" : ""), text: row.mode }),
            row.note,
          ])),
      ),
    ),
    h("div", { style: "margin-top:12px" }, card("Output", output)),
  );
}

/* Archives record UTC timestamps; trimming the fraction keeps the card narrow. */
function shortStamp(value) {
  const match = String(value || "").match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]} UTC` : String(value || "");
}

async function renderBackups() {
  const payload = await api("/api/backups");
  const output = h("pre", { class: "hidden" });
  if (payload.error) showBanner(payload.error, "warn");
  else if (!payload.exists) showBanner(`No backups yet: ${payload.directory} does not exist. Create one with the action below or ./manage.sh backup.`, "warn");
  const entries = payload.entries || [];
  const newest = entries[0];
  const sections = payload.sections || [];
  const chosen = new Set();

  page.replaceChildren(
    h("div", { class: "grid cols-3" },
      metric("Archives", entries.length, payload.directory || "backup directory"),
      metric("Newest", newest ? shortStamp(newest.created_at || newest.modified) : "none", newest ? (newest.full ? "full stack archive" : "partial archive") : "run a backup to create one"),
      metric("Contents", newest ? (newest.full ? "full stack" : (newest.sections.join(", ") || "partial")) : "-", newest ? newest.size : ""),
    ),
    h("div", { style: "margin-top:12px" },
      card("Archives",
        table(["Created", "Name", "Contents", "Size", "Version"],
          entries.map((row) => [
            shortStamp(row.created_at || row.modified),
            h("code", { text: row.name }),
            (row.full ? "full stack" : (row.sections.join(", ") || "partial")) + (row.encrypted ? " (encrypted)" : ""),
            row.size,
            row.stack_version,
          ])),
        h("div", { class: "row actions", style: "margin-top:10px" },
          h("button", { class: "btn danger", type: "button", text: "Create backup", onclick: runBackup }),
          h("button", { class: "btn ghost", type: "button", text: "Reload list", onclick: () => loadView() }),
        ),
        h("div", { class: "muted small", style: "margin-top:10px" },
          "The panel creates live backups without pausing containers (it runs inside the stack). " +
          "Run ./manage.sh backup on the host for a paused snapshot, and treat the backup directory as sensitive: archives contain .env secrets."),
      ),
    ),
    h("div", { style: "margin-top:12px" },
      card("Partial backup",
        h("div", { class: "muted small", text: sections.length
          ? "Archive only the selected parts of the stack (manage.sh backup --only). A restore of a partial archive merges; it does not replace the rest of the stack."
          : "The section list is unavailable: manage.sh backup-sections did not answer." }),
        sections.length
          ? h("div", { class: "row", style: "margin-top:10px" },
              sections.map((row) =>
                h("button", {
                  class: "chip selectable",
                  type: "button",
                  title: row.paths,
                  text: row.name,
                  onclick: (event) => {
                    if (chosen.has(row.name)) chosen.delete(row.name);
                    else chosen.add(row.name);
                    event.currentTarget.classList.toggle("ok", chosen.has(row.name));
                  },
                })))
          : null,
        sections.length
          ? h("div", { class: "row actions", style: "margin-top:10px" },
              h("button", { class: "btn", type: "button", text: "Back up selected sections", onclick: runSectionBackup }))
          : null),
    ),
    h("div", { style: "margin-top:12px" }, card("Output", output)),
  );

  async function runBackup() {
    await runActionInto(output, "backup", "Create stack backup", true);
    loadView();
  }

  async function runSectionBackup() {
    const list = sections.filter((row) => chosen.has(row.name)).map((row) => row.name);
    if (!list.length) {
      notify("Select at least one section.", "error");
      return;
    }
    await runActionInto(output, "backup-section", `Back up ${list.join(", ")}`, true, { sections: list.join(",") });
    loadView();
  }
}

/* ------------------------------------------------------------------ login */

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("login-error");
  error.classList.add("hidden");
  try {
    await api("/api/login", { method: "POST", body: { token: document.getElementById("token").value } });
    await boot();
  } catch (failure) {
    error.textContent = String(failure.message);
    error.classList.remove("hidden");
  }
});

document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: {} }).catch(() => {});
  showLogin();
});

document.getElementById("refresh").addEventListener("click", loadView);

async function boot() {
  try {
    session = await api("/api/session");
  } catch (error) {
    showLogin();
    return;
  }
  showShell();
  document.getElementById("session-note").textContent =
    `panel v${session.version} - actions ${session.actions_enabled ? "enabled" : "read-only"}`;
  selectView(currentView);
}

boot();
