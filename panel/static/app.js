/* Operator console for the content stack. No build step, no dependencies. */
"use strict";

const VIEWS = [
  { id: "overview", label: "Overview", title: "Stack status", hint: "Containers, disk, published ports and the content pipeline at a glance." },
  { id: "state", label: "Pipeline state", title: "Pipeline state", hint: "Draft counters, scheduled routines and the most recent drafts." },
  { id: "platforms", label: "Platforms", title: "Publishing platforms", hint: "Tokens, ids and API base URLs per channel. Secrets stay masked; every save is written to .env." },
  { id: "config", label: "Configuration", title: "Configuration files", hint: "Validated YAML/JSON editors with automatic backups before every save." },
  { id: "env", label: "Environment", title: "Environment (.env)", hint: "Secret values stay masked; edited keys apply after Apply changes." },
  { id: "storage", label: "Storage", title: "Object storage (S3)", hint: "Shared S3 block, RustFS state and the per-service storage matrix." },
  { id: "backups", label: "Backups", title: "Backups", hint: "Stack archives with their sections, sizes and creation times." },
  { id: "logs", label: "Logs", title: "Service logs", hint: "Tail docker compose logs without leaving the console." },
  { id: "video", label: "Video Studio", title: "Video studio", hint: "Plan a video with the Storyboard and Video Director agents, edit the timeline, then render it with the deterministic Media Studio driver." },
  { id: "hermes", label: "Hermes", title: "Hermes control center", hint: "Agents, routing profiles, models and measured routing telemetry from the Hermes control plane." },
  { id: "orchestration", label: "Orchestration", title: "Orchestration center", hint: "Multi-agent runs with their steps, approvals, reviewer results and failures." },
  { id: "knowledge", label: "Knowledge", title: "Knowledge center", hint: "Knowledge bases, document counts and live retrieval testing." },
  { id: "notebooklm", label: "NotebookLM", title: "NotebookLM video", hint: "Session management, credential sign-in and session import for the Google NotebookLM video provider." },
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
    else if (currentView === "platforms") await renderPlatforms();
    else if (currentView === "config") await renderConfig();
    else if (currentView === "env") await renderEnv();
    else if (currentView === "storage") await renderStorage();
    else if (currentView === "backups") await renderBackups();
    else if (currentView === "logs") await renderLogs();
    else if (currentView === "video") await renderVideoStudio();
    else if (currentView === "hermes") await renderHermes();
    else if (currentView === "orchestration") await renderOrchestration();
    else if (currentView === "knowledge") await renderKnowledge();
    else if (currentView === "actions") await renderActions();
    else if (currentView === "notebooklm") await renderNotebookLM();
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
              h("div", { class: "muted small", text: link.url }),
              h("div", { class: "muted small", text: link.note }),
            ),
          ),
        ),
        h("h3", { text: "Network exposure", style: "margin-top:16px" }),
        h("div", { class: "muted small", text: "Where each service is published, and whether the container is up. A service that is stopped exposes nothing." }),
        table(
          ["Service", "Key", "Bind", "State", "Host ports"],
          (status.exposure.rows || []).map((row) => [
            row.service,
            h("code", { text: row.key }),
            h("code", { text: row.bind }),
            row.loopback
              ? h("span", { class: "chip ok", text: "loopback only" })
              : row.stopped
                ? h("span", { class: "chip warn", text: row.state || "stopped" })
                : h("span", { class: "chip warn", text: row.state || "published" }),
            (row.ports || []).join(", ") || h("span", { class: "muted", text: "-" }),
          ]),
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
                h("span", { class: "chip " + (instagram.auto_publish ? "ok" : "warn"),
                  text: instagram.auto_publish ? "auto publish on" : "manual package" }),
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

function draftControls(draft, instagramAuto) {
  const status = String(draft.status || "");
  const buttons = [];
  if (MEDIA_QUESTION_STATUSES.includes(status)) {
    buttons.push(draftActionButton(draft, "text-only", "Text only", false));
    if (status === "media_ask") buttons.push(draftActionButton(draft, "image", "AI image", false));
  }
  if (PUBLISHABLE_STATUSES.includes(status)) {
    buttons.push(draftActionButton(draft, "publish", "Publish (Telegram)", true));
    if (instagramAuto) {
      buttons.push(draftActionButton(draft, "publish_both", "Telegram + Instagram", true));
      buttons.push(draftActionButton(draft, "publish_ig", "Instagram", true));
    }
    buttons.push(draftActionButton(draft, "post_package", "Post package", false));
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
  const [state, instagram] = await Promise.all([
    api("/api/drafts"),
    api("/api/instagram").catch(() => null),
  ]);
  const instagramAuto = Boolean(instagram && instagram.auto_publish);
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
            draftControls(draft, instagramAuto),
          ]),
        ),
        h("div", { class: "muted small", style: "margin-top:10px" },
          "Console actions are applied by the Content Bot within a few seconds and confirmed by a Telegram message."
          + (instagramAuto
              ? " Post package sends the media file and a copy-ready caption to your Telegram chat."
              : " Instagram automatic publishing is off (INSTAGRAM_AUTO_PUBLISH); Post package sends the media file and a copy-ready caption to your Telegram chat.")),
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

  const newKey = h("input", { placeholder: "NEW_KEY_NAME", type: "text", autocomplete: "off" });
  const newValue = h("input", { placeholder: "value", type: "text", autocomplete: "off" });
  const addKey = h("button", {
    class: "btn primary",
    type: "button",
    text: "Add key",
    onclick: async () => {
      const key = newKey.value.trim();
      if (!key) {
        notify("Enter a key name first, for example PANEL_PUBLIC_URL.", "error");
        return;
      }
      try {
        const result = await api(`/api/env/${encodeURIComponent(key)}`, {
          method: "PUT",
          body: { key, value: newValue.value },
        });
        notify(result.restart_hint || "Saved.");
        renderEnv();
      } catch (error) {
        notify(String(error.message), "error");
      }
    },
  });

  filter.addEventListener("input", paint);
  page.replaceChildren(
    card("Environment",
      h("div", { class: "spread" }, filter,
        h("span", { class: "chip", text: `${entries.length} keys` })),
      container,
      h("div", { class: "row", style: "margin-top:12px" }, newKey, newValue, addKey),
      h("div", { class: "muted small", style: "margin-top:6px" },
        "Add key appends a new line, for example PANEL_PUBLIC_URL=https://panel.example.com."),
    ),
  );
  paint();
}

/* -------------------------------------------------------------- platforms */

const PLATFORM_STATES = {
  ready: ["ok", "ready"],
  partial: ["warn", "incomplete"],
  empty: ["warn", "not configured"],
  package: ["", "package handover"],
  on: ["ok", "on"],
  off: ["warn", "off"],
};

function platformStateChip(state, mode) {
  const [kind, text] = PLATFORM_STATES[state] || ["", state || "unknown"];
  const chip = h("span", { class: "chip" + (kind ? " " + kind : ""), text });
  if (mode === "auto" && state === "ready") chip.title = "Publishes right away when picked on a draft";
  if (mode === "package") chip.title = "Hands over a copy-ready package instead of publishing";
  return chip;
}

function platformCard(platform, replaceCard) {
  const inputs = new Map();
  const dirty = new Set();
  const result = h("div", { class: "small muted" });

  const rows = (platform.fields || []).map((field) => {
    const input = h("input", {
      type: field.secret ? "password" : "text",
      autocomplete: "off",
      spellcheck: "false",
      placeholder: field.secret
        ? (field.set ? "stored - type to replace" : "not set")
        : field.placeholder || "",
      oninput: () => {
        dirty.add(field.key);
      },
    });
    if (!field.secret && field.value) input.value = field.value;
    inputs.set(field.key, input);
    return h(
      "label",
      { class: "pfield" },
      h(
        "div",
        { class: "pfield-head" },
        h("span", { class: "pfield-label", text: field.label }),
        field.required ? h("span", { class: "chip warn", text: "needed" }) : null,
        field.secret && field.set ? h("span", { class: "chip", text: "stored" }) : null,
      ),
      input,
      field.help ? h("div", { class: "muted small", text: field.help }) : null,
      h("code", { class: "pkey", text: field.key }),
    );
  });

  function collect(all) {
    const values = {};
    for (const [key, input] of inputs) {
      if (!all && !dirty.has(key)) continue;
      values[key] = input.value;
    }
    return values;
  }

  async function save() {
    const values = collect(false);
    if (!Object.keys(values).length) {
      notify(`${platform.label}: nothing changed.`);
      return;
    }
    try {
      const payload = await api(`/api/platforms/${encodeURIComponent(platform.key)}`, {
        method: "PUT",
        body: { values },
      });
      notify(`${platform.label}: saved ${payload.changed.length} value(s). ${payload.restart_hint || ""}`);
      replaceCard(payload.platform);
    } catch (error) {
      notify(String(error.message), "error");
    }
  }

  async function check() {
    result.className = "small muted";
    result.textContent = "Checking the credential...";
    try {
      const payload = await api(`/api/platforms/${encodeURIComponent(platform.key)}/test`, {
        method: "POST",
        body: { values: collect(true) },
      });
      result.className = "small " + (payload.ok ? "oktext" : "error");
      result.textContent = `${payload.ok ? "OK" : "Failed"}: ${payload.detail}`;
    } catch (error) {
      result.className = "small error";
      result.textContent = String(error.message);
    }
  }

  return h(
    "section",
    { class: "card platform" },
    h(
      "div",
      { class: "spread" },
      h("div", null,
        h("h3", { text: platform.label }),
        h("div", { class: "muted small", text: platform.summary })),
      h("div", { class: "row" }, platformStateChip(platform.state, platform.mode), h("span", { class: "chip", text: platform.mode })),
    ),
    rows.length ? h("div", { class: "pfields" }, ...rows) : null,
    h(
      "div",
      { class: "row actions" },
      rows.length ? h("button", { class: "btn primary small", type: "button", text: "Save", onclick: save }) : null,
      platform.test ? h("button", { class: "btn small", type: "button", text: "Test connection", onclick: check }) : null,
      platform.docs ? h("code", { class: "pkey", text: platform.docs }) : null,
    ),
    result,
  );
}

async function renderPlatforms() {
  const payload = await api("/api/platforms");
  const wrap = h("div", { class: "grid platform-grid" });
  const applyNote = h("div", { class: "muted small" });

  function paint(platforms) {
    wrap.replaceChildren(
      ...platforms.map((platform, index) =>
        platformCard(platform, (updated) => {
          const next = platforms.slice();
          next[index] = updated;
          paint(next);
        }),
      ),
    );
  }
  paint(payload.platforms || []);

  async function applyChanges(event) {
    const button = event.currentTarget;
    button.disabled = true;
    applyNote.textContent = "Applying: docker compose up -d ...";
    try {
      const result = await api("/api/actions/stack-up", { method: "POST", body: {} });
      applyNote.textContent = `${result.label}: ${result.ok ? "ok" : "failed"} (exit ${result.returncode}).`;
      notify(`${result.label}: ${result.ok ? "ok" : "failed"}`, result.ok ? "" : "error");
    } catch (error) {
      applyNote.textContent = String(error.message);
      notify(String(error.message), "error");
    } finally {
      button.disabled = false;
    }
  }

  page.replaceChildren(
    card(
      "Platforms",
      h("div", { class: "muted small" },
        "Secrets are never sent back to the browser: a stored token shows as masked. Saving writes .env, then Apply changes recreates the containers so content-bot reads the new values. Test connection runs from the panel and only reports whether the provider accepted the credential."),
      h("div", { class: "row actions" },
        h("button", { class: "btn primary", type: "button", text: "Apply changes", onclick: applyChanges }),
      ),
      applyNote,
    ),
    wrap,
  );
}

async function renderVideoStudio() {
  const data = await api("/api/video/studio");
  const jobs = data.jobs || [];
  const timelineBox = h("textarea", {
    id: "video-timeline",
    style: "min-height:260px;width:100%;font-family:ui-monospace,monospace;font-size:12px",
    placeholder: "The render timeline appears here after Plan; edit it before Render if needed.",
  });
  const storyboardBox = h("textarea", {
    id: "video-storyboard",
    style: "min-height:180px;width:100%;font-family:ui-monospace,monospace;font-size:12px",
    placeholder: "The storyboard appears here after Plan.",
  });
  const topicInput = h("input", { type: "text", placeholder: "Topic, e.g. Running Docker containers on MikroTik RouterOS" });
  const scriptInput = h("textarea", {
    style: "min-height:120px;width:100%",
    placeholder: "Optional script or research notes. Long text becomes the storyboard source.",
  });
  const aspectSelect = h("select", null,
    ...["9:16", "16:9", "1:1", "4:5"].map((value) => h("option", { value, text: value })));
  const languageInput = h("input", { type: "text", placeholder: "e.g. fa", style: "width:90px" });
  const durationInput = h("input", { type: "number", min: "5", max: "600", value: "45", style: "width:90px" });
  const brandInput = h("input", { type: "text", placeholder: "Brand label on the video" });
  const planStatus = h("div", { class: "muted small" });

  async function plan() {
    planStatus.textContent = "Planning with the Storyboard and Video Director agents...";
    try {
      const result = await api("/api/video/plan", {
        method: "POST",
        body: {
          topic: topicInput.value,
          script: scriptInput.value,
          aspect_ratio: aspectSelect.value,
          language: languageInput.value,
          duration: Number(durationInput.value) || 0,
          brand: brandInput.value,
        },
      });
      storyboardBox.value = JSON.stringify(result.storyboard || {}, null, 2);
      timelineBox.value = JSON.stringify(result.timeline || {}, null, 2);
      const scenes = (result.timeline && result.timeline.scenes) || [];
      planStatus.textContent = `Timeline ready: ${scenes.length} scene(s), ${aspectSelect.value}. Press Render to queue it.`;
      notify("Video plan ready");
    } catch (error) {
      planStatus.textContent = "";
      showBanner(String(error.message), "error");
    }
  }

  async function validate() {
    let timeline = null;
    try {
      timeline = JSON.parse(timelineBox.value || "null");
    } catch (error) {
      showBanner(`The timeline is not valid JSON: ${error.message}`, "error");
      return;
    }
    if (!timeline) {
      showBanner("Plan a video or paste a timeline first.", "error");
      return;
    }
    planStatus.textContent = "Validating against the renderer schema...";
    try {
      const result = await api("/api/video/timeline/validate", { method: "POST", body: { timeline } });
      if (result.ok === false) {
        const location = result.field ? ` (${result.field})` : "";
        showBanner(`Timeline rejected${location}: ${result.error}${result.hint ? ` - ${result.hint}` : ""}`, "error");
        planStatus.textContent = "Timeline needs fixing.";
        return;
      }
      if (result.timeline) timelineBox.value = JSON.stringify(result.timeline, null, 2);
      const totals = result.totals || {};
      planStatus.textContent = `Timeline valid: ${totals.scenes || 0} scene(s), ${totals.duration_seconds || 0}s, ${totals.resolution || ""} @ ${totals.fps || ""}fps.`;
      notify("Timeline is valid");
    } catch (error) {
      showBanner(String(error.message), "error");
    }
  }

  async function retryJob(id) {
    try {
      const result = await api(`/api/video/jobs/${encodeURIComponent(id)}/retry`, { method: "POST", body: {} });
      notify(`Retry queued as ${(result.job || {}).id || "?"}`);
      await loadView();
    } catch (error) {
      showBanner(String(error.message), "error");
    }
  }

  async function render() {
    let timeline = null;
    try {
      timeline = JSON.parse(timelineBox.value || "null");
    } catch (error) {
      showBanner(`The timeline is not valid JSON: ${error.message}`, "error");
      return;
    }
    if (!timeline) {
      showBanner("Plan a video or paste a timeline first.", "error");
      return;
    }
    planStatus.textContent = "Submitting the render job...";
    try {
      const result = await api("/api/video/render", {
        method: "POST",
        body: { timeline, brand: brandInput.value },
      });
      const id = (result.job || {}).id || "?";
      notify(`Render job ${id} queued`);
      planStatus.textContent = `Job ${id} queued. Watch its status below.`;
      await loadView();
    } catch (error) {
      showBanner(String(error.message), "error");
    }
  }

  async function showJob(id) {
    try {
      const result = await api(`/api/video/jobs/${encodeURIComponent(id)}`);
      const job = result.job || {};
      const log = job.log_tail || "(no log yet)";
      window.alert(`${id} - ${job.status}\n\n${log.slice(-3000)}`);
    } catch (error) {
      showBanner(String(error.message), "error");
    }
  }

  async function cancelJob(id) {
    if (!window.confirm(`Cancel queued job ${id}?`)) return;
    try {
      await api(`/api/video/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
      notify("Job cancelled");
      await loadView();
    } catch (error) {
      showBanner(String(error.message), "error");
    }
  }

  const jobRows = jobs.map((job) => {
    const artifactCells = (job.artifacts || []).map((artifact) => {
      const url = `/api/video/artifacts/${encodeURIComponent(job.id)}/${encodeURIComponent(artifact.name)}`;
      const link = h("a", { href: url, text: artifact.name });
      const preview = artifact.kind === "video"
        ? h("video", { controls: true, src: url, style: "max-width:180px;display:block;margin-top:4px" })
        : null;
      return h("div", null, link, preview);
    });
    const actions = h("div", { class: "row" },
      h("button", { class: "btn", type: "button", text: "Log", onclick: () => showJob(job.id) }),
      job.status === "queued"
        ? h("button", { class: "btn danger", type: "button", text: "Cancel", onclick: () => cancelJob(job.id) })
        : null,
      job.status === "error" || job.status === "cancelled"
        ? h("button", { class: "btn", type: "button", text: "Retry", onclick: () => retryJob(job.id) })
        : null,
    );
    return [
      h("code", { text: job.id }),
      job.driver,
      h("span", null, dot(job.status), " ", job.status),
      job.created_at || "",
      job.error ? h("span", { class: "error small", text: job.error }) : "",
      artifactCells.length ? h("div", null, ...artifactCells) : h("span", { class: "muted", text: "—" }),
      actions,
    ];
  });

  const timelineEnabled = data.timeline_driver;
  const driverSource = data.drivers_source === "worker" ? "the running worker" : "MEDIA_STUDIO_DRIVERS in .env";
  const warnings = [];
  if (!data.ok) warnings.push(`Media Studio is unreachable: ${data.error || "unknown error"}`);
  if (!timelineEnabled) {
    warnings.push(
      `The timeline-video driver is not enabled (read from ${driverSource}); ` +
      `add it to MEDIA_STUDIO_DRIVERS in .env and recreate the media-studio container.`,
    );
  }
  if (!data.router_ready) warnings.push("SMART_ROUTER_ADMIN_API_KEY is not set, so planning is unavailable.");
  if (warnings.length) showBanner(warnings.join(" "), "warn");

  page.replaceChildren(
    h("div", { class: "grid cols-4" },
      metric("Render jobs", jobs.length, `${jobs.filter((job) => job.status === "done").length} done`),
      metric("Timeline driver", timelineEnabled ? "enabled" : "missing", "Media Studio driver"),
      metric("Planning", data.router_ready ? "ready" : "unavailable", "Smart Router content agents"),
      metric("Drivers", (data.drivers || []).length, `${(data.drivers || []).join(", ") || "none"} - read from ${driverSource}`),
    ),
    card("Plan a video",
      h("div", { class: "grid cols-2" },
        h("label", null, "Topic", topicInput),
        h("label", null, "Brand label", brandInput),
      ),
      h("label", null, "Script or notes", scriptInput),
      h("div", { class: "row" },
        h("label", { class: "row" }, "Aspect", aspectSelect),
        h("label", { class: "row" }, "Language", languageInput),
        h("label", { class: "row" }, "Seconds", durationInput),
          h("button", { class: "btn primary", type: "button", text: "Plan", onclick: plan }),
        h("button", { class: "btn", type: "button", text: "Validate", onclick: validate }),
        h("button", { class: "btn", type: "button", text: "Render", onclick: render }),
      ),
      planStatus,
      h("details", { style: "margin-top:8px" },
        h("summary", { class: "muted small", text: "Storyboard JSON" }),
        storyboardBox,
      ),
      h("details", { open: true, style: "margin-top:8px" },
        h("summary", { class: "muted small", text: "Timeline JSON (sent to the renderer)" }),
        timelineBox,
      ),
    ),
    card("Render queue",
      h("div", { class: "muted small", text: "Newest first. Downloads stream through the panel, so the worker port stays private." }),
      table(
        ["Job", "Driver", "Status", "Created", "Error", "Artifacts", "Actions"],
        jobRows,
      ),
    ),
  );
}

async function renderHermes() {
  const data = await api("/api/hermes/overview");
  const info = data.info || {};
  const summary = data.summary || {};
  const agents = data.agents || [];
  const contentAgents = (data.content_agents || {}).data || [];
  const models = data.models || [];

  const warnings = [];
  if (data.info_error) warnings.push(`Router info: ${data.info_error}`);
  if (data.summary_error) warnings.push(`Routing telemetry: ${data.summary_error}`);
  if (data.agents_error) warnings.push(`Agent registry: ${data.agents_error}`);
  if (data.content_error) warnings.push(`Content agents: ${data.content_error}`);
  if (data.models_error) warnings.push(`Model list: ${data.models_error}`);

  const consoleLink = h("a", { href: data.operations_url, target: "_blank", rel: "noreferrer", text: "Operations Center" });
  const dashboardLink = h("a", { href: data.dashboard_url, target: "_blank", rel: "noreferrer", text: "Flight Deck" });

  const agentRows = agents.map((agent) => [
    h("code", { text: String(agent.id || "") }),
    agent.name || "",
    agent.role || agent.kind || "",
    agent.model || "",
    h("span", null, dot(agent.active === false ? "exited" : "running"), " ", agent.active === false ? "disabled" : "active"),
    String((agent.skills || []).length),
  ]);

  const contentRows = contentAgents.map((agent) => [
    agent.name || "",
    agent.tier || "",
    agent.profile || "",
    agent.agent_id ? h("code", { text: String(agent.agent_id) }) : h("span", { class: "muted", text: "not seeded" }),
    agent.description || "",
  ]);

  const profileRows = Object.entries(summary.profiles || {}).map(([name, count]) => [name, String(count)]);
  const tierRows = Object.entries(summary.tiers || {}).map(([name, count]) => [name, String(count)]);
  const modelRows = (summary.models || []).map((row) => [
    row.model || "",
    String(row.requests || 0),
    `$${Number(row.cost_usd || 0).toFixed(4)}`,
  ]);

  if (warnings.length) showBanner(warnings.join(" "), "warn");

  page.replaceChildren(
    h("div", { class: "grid cols-4" },
      metric("Router version", info.version || "unknown", `mode ${info.mode || "?"} - policy ${info.policy || "?"}`),
      metric("Routed requests", summary.requests ?? "-", `last ${summary.window_hours || 24}h`),
      metric("Measured cost", `$${Number(summary.cost_usd || 0).toFixed(4)}`, `avg latency ${summary.avg_latency_ms || 0} ms`),
      metric("Error rate", `${summary.error_rate || 0}%`, `${summary.policy_denials || 0} policy denial(s)`),
    ),
    card("Control plane",
      h("div", { class: "row" },
        h("span", null, dot(info.control_plane ? "running" : "exited"), " ", info.control_plane ? "control plane enabled" : "control plane disabled"),
        h("span", { class: "muted small", text: `store ${info.sticky_backend || "?"} - ha ${info.ha_mode || "off"} - redis ${info.redis_enabled ? "on" : "off"}` }),
      ),
      h("div", { class: "muted small", style: "margin-top:8px" },
        "Agents, prompts, routing profiles, keys and budgets are managed in the router console. ",
        consoleLink, " - telemetry and traces: ", dashboardLink,
      ),
      h("div", { class: "muted small", style: "margin-top:6px" },
        `Registered users ${summary.users ?? "-"} - active keys ${summary.api_keys ?? "-"} - knowledge bases ${summary.knowledge_bases ?? "-"} - agents ${summary.agents ?? "-"}`,
      ),
    ),
    card("Content production agents",
      h("div", { class: "muted small", text: "Seeded by the Smart Router for the video pipeline: Storyboard, Video Director, Media Planning and NotebookLM Recovery." }),
      table(["Agent", "Tier", "Profile", "Agent id", "Purpose"], contentRows),
    ),
    card("Agent registry", table(["Id", "Name", "Role", "Model", "State", "Skills"], agentRows)),
    card("Routing profiles", table(["Profile", "Routed requests"], profileRows)),
    card("Tier selection", table(["Tier", "Routed requests"], tierRows)),
    card("Top models", table(["Model", "Requests", "Measured cost"], modelRows)),
  );
}

function runCells(run) {
  const awaiting = run.awaiting_title ? `step ${run.awaiting_step}: ${run.awaiting_title}` : "";
  return [
    h("code", { text: String(run.id || "") }),
    run.goal || run.task || "",
    h("span", null, dot(run.status === "failed" ? "unhealthy" : run.status), " ", run.status || ""),
    run.approval_mode || "",
    `${run.steps_done || 0}/${run.steps_total || 0}`,
    run.review_status || "",
    awaiting,
  ];
}

async function renderOrchestration() {
  const data = await api("/api/orchestration/runs?limit=50");
  const runs = data.runs || [];
  if (data.error) showBanner(`The router could not list runs: ${data.error}`, "error");

  const awaiting = runs.filter((run) => run.awaiting_step !== null && run.awaiting_step !== undefined);
  const failed = runs.filter((run) => run.status === "failed");

  const detail = h("pre", { text: "Select a run to see its steps and approvals." });

  async function showRun(runId) {
    detail.textContent = `Loading run ${runId}...`;
    try {
      const result = await api(`/api/orchestration/runs/${encodeURIComponent(runId)}`);
      const run = result.run || {};
      const lines = [
        `#${run.id} ${run.status || ""}  approval=${run.approval_mode || "?"}  actor=${run.actor || "?"}`,
        `goal: ${run.goal || run.task || ""}`,
        run.error ? `error: ${run.error}` : "",
        "",
        ...(run.steps || []).map((step) => {
          const index = step.index !== undefined ? step.index : step.idx;
          const output = step.output || step.result || {};
          const preview = typeof output === "object" ? JSON.stringify(output) : String(output || "");
          return [
            `[${index}] ${step.status || ""} ${step.title || ""} agent=${step.agent_id || "-"}`,
            step.error ? `    error: ${step.error}` : "",
            preview ? `    ${preview.slice(0, 600)}` : "",
          ].filter(Boolean).join("\n");
        }),
      ].filter(Boolean);
      detail.textContent = lines.join("\n") || "(empty run)";
    } catch (error) {
      detail.textContent = String(error.message);
    }
  }

  const rows = runs.map((run) => runCells(run).concat([
    h("button", { class: "btn", type: "button", text: "Details", onclick: () => showRun(run.id) }),
  ]));

  page.replaceChildren(
    h("div", { class: "grid cols-4" },
      metric("Recent runs", runs.length, "newest first"),
      metric("Awaiting approval", awaiting.length, awaiting.length ? `oldest #${awaiting[awaiting.length - 1].id}` : "nothing waiting"),
      metric("Failed", failed.length, failed.length ? `newest #${failed[0].id}` : "no failures"),
      metric("Reviewer feedback", runs.filter((run) => run.review_status).length, "runs with a review verdict"),
    ),
    card("Runs",
      h("div", { class: "muted small", text: "Multi-agent runs planned and executed through the Hermes orchestrator." }),
      table(["Run", "Goal", "Status", "Approval", "Steps", "Review", "Waiting on", "Actions"], rows),
    ),
    card("Run detail", detail),
  );
}

async function renderKnowledge() {
  const data = await api("/api/knowledge");
  const bases = data.bases || [];
  if (data.error) showBanner(`The router could not list knowledge bases: ${data.error}`, "error");

  const query = h("input", { type: "text", placeholder: "Ask the knowledge base a question", style: "flex:1;min-width:200px" });
  const limit = h("input", { type: "number", min: "1", max: "20", value: "5", style: "width:70px" });
  const picked = new Set(bases.map((base) => String(base.id)));
  const boxes = bases.map((base) =>
    h("label", { class: "row muted small" },
      h("input", {
        type: "checkbox",
        checked: picked.has(String(base.id)),
        onchange: (event) => {
          if (event.target.checked) picked.add(String(base.id));
          else picked.delete(String(base.id));
        },
      }),
      ` ${base.name || base.id} (${base.chunks || 0} chunks)`,
    ),
  );
  const status = h("div", { class: "muted small" });
  const output = h("pre", { text: "Run a query to see what retrieval returns." });

  async function search() {
    status.textContent = "Searching...";
    try {
      const result = await api("/api/knowledge/search", {
        method: "POST",
        body: { query: query.value, kb_ids: [...picked], limit: Number(limit.value) || 5 },
      });
      const results = result.results || [];
      status.textContent = `${results.length} result(s).`;
      output.textContent = results.length
        ? results.map((row, index) => {
            const score = row.score !== undefined ? Number(row.score).toFixed(4) : "n/a";
            const source = row.source || row.kb_id || "";
            const text = String(row.content || row.text || "").slice(0, 900);
            return `[${index + 1}] score=${score} source=${source}\n${text}`;
          }).join("\n\n")
        : "(no matches)";
    } catch (error) {
      status.textContent = "";
      output.textContent = String(error.message);
    }
  }

  const ingestBase = h("select", null, ...bases.map((base) => h("option", { value: String(base.id), text: base.name || String(base.id) })));
  const ingestTitle = h("input", { type: "text", placeholder: "Document title", style: "flex:1;min-width:160px" });
  const ingestText = h("textarea", { style: "min-height:120px;width:100%", placeholder: "Paste the document, brand note or research text to index." });
  const ingestStatus = h("div", { class: "muted small" });

  async function ingest() {
    ingestStatus.textContent = "Indexing...";
    try {
      const result = await api("/api/knowledge/documents", {
        method: "POST",
        body: { kb_id: ingestBase.value, title: ingestTitle.value, content: ingestText.value },
      });
      ingestStatus.textContent = `Indexed ${result.chunks || 0} chunk(s).`;
      ingestText.value = "";
      notify("Document indexed");
      await loadView();
    } catch (error) {
      ingestStatus.textContent = "";
      showBanner(String(error.message), "error");
    }
  }

  const rows = bases.map((base) => [
    h("code", { text: String(base.id) }),
    base.name || "",
    String(base.chunks || 0),
    base.owner || "",
    base.description || "",
  ]);

  page.replaceChildren(
    h("div", { class: "grid cols-4" },
      metric("Knowledge bases", bases.length, "router knowledge store"),
      metric("Indexed chunks", bases.reduce((total, base) => total + Number(base.chunks || 0), 0), "embeddings across all bases"),
      metric("Retrieval", "on demand", "query testing below"),
      metric("Ingestion", "router API", "documents are added in the router console"),
    ),
    card("Knowledge bases", table(["Id", "Name", "Chunks", "Owner", "Description"], rows)),
    card("Index a document",
      h("div", { class: "muted small", text: "Text is chunked and embedded by the router, then becomes searchable by the agents and the retrieval test below." }),
      h("div", { class: "row" }, ingestBase, ingestTitle, h("button", { class: "btn primary", type: "button", text: "Index", onclick: ingest })),
      ingestText,
      ingestStatus,
    ),
    card("Retrieval test",
      h("div", { class: "row" }, query, limit, h("button", { class: "btn primary", type: "button", text: "Search", onclick: search })),
      h("div", { class: "row", style: "flex-wrap:wrap" }, boxes.length ? boxes : h("span", { class: "muted small", text: "No knowledge bases yet." })),
      status,
      output,
    ),
  );
}

async function renderLogs() {
  const status = await api("/api/status");
  const services = (status.services || []).map((service) => service.service).filter(Boolean);
  const select = h("select", null, ...services.map((name) => h("option", { value: name, text: name })));
  const tail = h("select", null, ...[100, 200, 500, 1000, 2000, 5000].map((value) => h("option", { value, text: `${value} lines` })));
  tail.value = "500";
  const output = h("pre", { text: "Choose a service and press Load logs." });
  const filterInput = h("input", {
    type: "text",
    placeholder: "Filter logs...",
    style: "flex:1;min-width:120px",
  });
  let _allLines = [];

  function applyFilter() {
    const q = filterInput.value.toLowerCase().trim();
    if (!q) {
      output.textContent = _allLines.join("\n") || "(no output)";
      return;
    }
    const matched = _allLines.filter(line => line.toLowerCase().includes(q));
    output.textContent = matched.join("\n") || '(no matches for "' + q + '")';
  }

  async function load() {
    output.textContent = "Loading...";
    try {
      const data = await api(`/api/logs/${encodeURIComponent(select.value)}?lines=${tail.value}`);
      _allLines = data.lines || [];
      applyFilter();
      // Auto-scroll to bottom (tail -f behavior)
      setTimeout(function() { if (output) output.scrollTop = output.scrollHeight; }, 50);
    } catch (error) {
      output.textContent = String(error.message);
      _allLines = [];
    }
  }

  filterInput.addEventListener("input", applyFilter);

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
      h("div", { class: "row", style: "margin:6px 0" },
        filterInput,
        h("span", { id: "log-count", class: "muted small" }),
      ),
      output,
    ),
  );
  select.value = services.includes("content-bot") ? "content-bot" : services[0] || "";
  if (services.length) load();
}

let __nlmConfigCache = null;

async function loadNlmConfig() {
  try {
    const data = await api("/api/notebooklm/profiles");
    __nlmConfigCache = data;
    return data;
  } catch (err) {
    return { error: String(err) };
  }
}

async function saveNlmConfig() {
  const btn = document.getElementById("nlm-config-save-btn");
  const status = document.getElementById("nlm-config-status");
  btn.disabled = true;
  status.textContent = "Saving…";
  try {
    // Read profile fields from the form
    const profiles = {};
    const container = document.getElementById("nlm-config-form");
    const profileEls = container.querySelectorAll("[data-profile-name]");
    profileEls.forEach(function(el) {
      const name = el.getAttribute("data-profile-name");
      const fields = {};
      el.querySelectorAll("[data-field]").forEach(function(f) {
        fields[f.getAttribute("data-field")] = f.value;
      });
      if (name && fields.language) profiles[name] = fields;
    });
    // Read duration targets
    const targets = {};
    const targetRows = container.querySelectorAll("[data-dur-key]");
    targetRows.forEach(function(row) {
      const key = row.getAttribute("data-dur-key");
      const val = row.querySelector("input")?.value || "";
      if (key && val) targets[key] = val;
    });
    // Read trim seconds
    const trimInput = document.getElementById("nlm-trim-seconds");
    const trimVal = trimInput ? parseInt(trimInput.value, 10) || 0 : 0;
    if (trimVal > 0) targets["_trim"] = "__trim__" + trimVal;

    const payload = {};
    if (Object.keys(profiles).length) payload.profiles = profiles;
    if (Object.keys(targets).length) payload.duration_targets = targets;
    // Remove the _trim marker before sending
    if (payload.duration_targets && payload.duration_targets["_trim"]) {
      delete payload.duration_targets["_trim"];
    }

    const result = await api("/api/notebooklm/profiles", { method: "POST", body: payload });
    if (result.ok !== false) {
      status.textContent = "Saved! Config reloaded.";
      __nlmConfigCache = result;
      renderNlmConfigForm(result);
    } else {
      status.textContent = "Error: " + (result.error || "unknown");
    }
  } catch (err) {
    status.textContent = "Error: " + String(err.message);
  }
  btn.disabled = false;
}

function renderNlmConfigForm(data) {
  const container = document.getElementById("nlm-config-form");
  const saveBtn = document.getElementById("nlm-config-save-btn");
  if (!data || data.error) {
    container.innerHTML = "<span class='warn'>Could not load config: " + (data?.error || "unknown") + "</span>";
    return;
  }
  const profiles = data.profiles || {};
  const targets = data.duration_targets || {};

  let html = "";

  // ----- Duration presets -----
  html += "<h4 style='margin:8px 0 4px'>Duration presets</h4>";
  html += "<table style='width:100%'><tbody>";
  Object.keys(targets).forEach(function(key) {
    const val = targets[key];
    html += "<tr data-dur-key='" + key + "'><td style='padding:2px 6px 2px 0;white-space:nowrap'>" + key + "</td>"
         + "<td><input type='text' value='" + htmlEscape(val) + "' style='width:100%' placeholder='e.g. approximately 3 minutes'></td></tr>";
  });
  html += "</tbody></table>";

  // ----- Trim seconds -----
  let trimVal = 0;
  try { trimVal = parseInt(window.__nlmTrimSec || "0", 10) || 0; } catch(e) {}
  html += "<h4 style='margin:8px 0 4px'>Trim (remove from end)</h4>";
  html += "<input id='nlm-trim-seconds' type='number' min='0' max='60' value='" + trimVal + "' style='width:80px'> seconds";

  // ----- Video profiles -----
  html += "<h4 style='margin:12px 0 4px'>Video profiles</h4>";
  Object.keys(profiles).forEach(function(name) {
    const p = profiles[name] || {};
    html += "<details style='margin:4px 0' data-profile-name='" + name + "'>"
         + "<summary style='cursor:pointer'><b>" + htmlEscape(name) + "</b></summary>"
         + "<div style='padding:4px 0 4px 12px'>";
    const fields = ["language", "duration", "voice_gender", "style", "tone", "audience"];
    fields.forEach(function(f) {
      const val = p[f] || "";
      html += "<label style='display:block;margin:3px 0'><span style='width:110px;display:inline-block;font-size:12px'>" + f + "</span>"
           + "<input type='text' data-field='" + f + "' value='" + htmlEscape(String(val)) + "' style='width:calc(100% - 120px)'></label>";
    });
    html += "</div></details>";
  });

  container.innerHTML = html;
  saveBtn.classList.remove("hidden");

  // Load trim from worker config (try to detect from duration_targets with a special method)
  // For now use the env var value or default 0
  const existingTrim = data.trim_last_seconds || 0;
  const trimInput = document.getElementById("nlm-trim-seconds");
  if (trimInput) trimInput.value = existingTrim;
}

function htmlEscape(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

let __nlmTrimSec = 0;

async function renderNotebookLM() {
  const [status, actions, config] = await Promise.all([
    api("/api/notebooklm").catch(() => ({ enabled: false })),
    api("/api/actions").catch(() => ({ enabled: false, actions: [] })),
    loadNlmConfig().catch(() => ({ error: "unreachable" })),
  ]);
  const output = h("pre", { class: "hidden", style: "max-height:400px;overflow:auto" });

  let loginSpinner = null;
  async function doLogin() {
    loginSpinner = h("span", { class: "muted", text: " Running login… (may take up to 5 minutes)" });
    document.getElementById("login-status").replaceChildren(loginSpinner);
    try {
      const result = await api("/api/notebooklm/login", { method: "POST" });
      output.classList.remove("hidden");
      output.textContent = [
        `Login: ${result.ok ? "ok" : "failed"} (exit ${result.returncode}, ${result.duration || "?"})`,
        result.output || "",
        result.error || "",
      ].join("\n\n");
      notify(`Login ${result.ok ? "succeeded" : "failed"}`, result.ok ? "" : "error");
    } catch (error) {
      output.classList.remove("hidden");
      output.textContent = String(error.message);
      notify(String(error.message), "error");
    } finally {
      if (loginSpinner) loginSpinner.remove();
    }
  }

  function renderSessionPaste() {
    return h("div", null,
      h("p", { class: "muted small" },
        "Paste the content of <code>notebooklm-session.json</code> from the export script, or the full cookies+localStorage JSON."),
      h("textarea", {
        id: "session-paste",
        style: "width:100%;min-height:120px",
        placeholder: '{\n  "cookies": [...],\n  "origins": [...]\n}',
      }),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", {
          class: "btn primary",
          type: "button",
          text: "Import and apply",
          onclick: async () => {
            const textarea = document.getElementById("session-paste");
            try {
              const data = JSON.parse(textarea.value);
              output.classList.remove("hidden");
              output.textContent = "Importing session...";
              const result = await api("/api/notebooklm/import-session", { method: "POST", body: { session: data } });
              output.textContent = [
                `Import: ${result.ok ? "ok" : "failed"} (exit ${result.returncode}, ${result.duration || "?"})`,
                result.output || "",
                result.error || "",
              ].join("\n\n");
              notify(result.ok ? "Session imported" : "Import failed", result.ok ? "" : "error");
            } catch (err) {
              output.textContent = "Invalid JSON: " + String(err.message);
            }
          },
        }),
      ),
    );
  }

  page.replaceChildren(
    h("div", { class: "grid cols-4" },
      h("section", { class: "card" },
        h("h3", { text: "Status" }),
        h("table", { style: "width:100%;margin-bottom:8px" },
          h("tbody", null,
            h("tr", null,
              h("td", { class: "muted small", style: "white-space:nowrap;padding-right:12px" }, "Status"),
              h("td", null,
                h("span", { class: "chip " + (status.enabled ? "ok" : "warn"), text: status.enabled ? "enabled" : "disabled" }),
              ),
            ),
            h("tr", null,
              h("td", { class: "muted small", style: "white-space:nowrap;padding-right:12px" }, "Worker"),
              h("td", null,
                h("span", { class: "chip " + (status.worker_running ? "ok" : "warn"), text: status.worker_running ? "running" : "stopped" }),
              ),
            ),
            h("tr", null,
              h("td", { class: "muted small", style: "white-space:nowrap;padding-right:12px" }, "Google"),
              h("td", null,
                h("span", { class: "chip " + (status.signed_in ? "ok" : (status.signed_in === false ? "warn" : "muted")), text: status.signed_in ? "signed in" : (status.signed_in === false ? "not signed in" : "unknown") }),
                h("span", { class: "muted small", style: "margin-left:8px" }, "mode: " + status.session_mode),
              ),
            ),
            status.google_creds_set ? h("tr", null,
              h("td", { class: "muted small", style: "white-space:nowrap;padding-right:12px" }, "Saved creds"),
              h("td", null,
                h("span", { class: "chip ok", text: status.google_creds_email }),
              ),
            ) : null,
          ),
        ),
        (!status.enabled)
          ? h("div", { class: "muted small", style: "margin-top:10px" }, "NotebookLM profile is not enabled. Run ", h("code", { text: "notebooklm-enable" }), " in manage.sh or toggle the compose profile.")
          : null,
      ),
      h("section", { class: "card" },
        h("h3", { text: "Interactive login (recommended)" }),
        h("p", { class: "muted small" },
          "Start a visible browser session inside the worker. You sign in to Google interactively through a VNC window."),
        h("div", { id: "vnc-status", class: "muted small", style: "margin-top:8px" }),
        h("div", { class: "row", style: "margin-top:10px" },
          h("button", {
            id: "vnc-start-btn",
            class: "btn primary",
            type: "button",
            text: "Start interactive login",
            onclick: async () => {
              const btn = document.getElementById("vnc-start-btn");
              const status = document.getElementById("vnc-status");
              const stopBtn = document.getElementById("vnc-stop-btn");
              btn.disabled = true;
              status.textContent = "Starting login session...";
              try {
                const result = await api("/api/notebooklm/start-vnc-login", { method: "POST" });
                if (result.ok) {
                  status.innerHTML = `<a href="${result.vnc_url}" target="_blank" rel="noreferrer">${result.vnc_url}</a><br>${result.note}`;
                  window.open(result.vnc_url, "_blank");
                  btn.classList.add("hidden");
                  stopBtn.classList.remove("hidden");
                } else {
                  status.textContent = "Error: " + (result.error || "unknown");
                  btn.disabled = false;
                }
              } catch (err) { status.textContent = "Error: " + err.message; btn.disabled = false; }
            },
          }),
          h("button", {
            id: "vnc-stop-btn",
            class: "btn danger hidden",
            type: "button",
            text: "Done - I signed in",
            onclick: async () => {
              const btn = document.getElementById("vnc-stop-btn");
              const status = document.getElementById("vnc-status");
              const startBtn = document.getElementById("vnc-start-btn");
              btn.disabled = true;
              status.textContent = "Stopping login session and restarting worker...";
              try {
                const result = await api("/api/notebooklm/stop-vnc-login", { method: "POST" });
                status.textContent = result.ok ? "Session saved. Worker restarted." : "Error: " + (result.error || "");
                if (result.ok) notify("Login session saved. Worker restarted.");
                startBtn.classList.remove("hidden");
                btn.classList.add("hidden");
                startBtn.disabled = false;
              } catch (err) { status.textContent = "Error: " + err.message; btn.disabled = false; }
            },
          }),
        ),
      ),
      h("section", { class: "card" },
        h("h3", { text: "Auto login (credentials)" }),
        h("p", { class: "muted small" },
          "Google email/password automation. May fail if phone verification or CAPTCHA is required."),
        h("label", { text: "Email" }),
        h("input", { id: "login-email", type: "email", placeholder: "user@gmail.com", style: "width:100%" }),
        h("label", { text: "Password", style: "margin-top:8px" }),
        h("input", { id: "login-pass", type: "password", placeholder: "App password or regular password", style: "width:100%" }),
        h("label", { text: "TOTP secret (optional)", style: "margin-top:8px" }),
        h("input", { id: "login-totp", type: "password", placeholder: "JBSWY3DPEHPK3PXP", style: "width:100%" }),
        h("div", { id: "login-status", style: "margin-top:8px" }),
        h("div", { class: "row", style: "margin-top:8px" },
          h("button", { class: "btn primary", type: "button", text: "Save & sign in", onclick: async () => {
            const email = document.getElementById("login-email").value;
            const pass = document.getElementById("login-pass").value;
            const totp = document.getElementById("login-totp").value;
            if (!email || !pass) { notify("Email and password required", "error"); return; }
            try {
              const saveResult = await api("/api/notebooklm/creds", { method: "POST", body: { email, password: pass, totp } });
              if (!saveResult.ok) {
                notify("Failed to save credentials: email=" + saveResult.email_set + " password=" + saveResult.password_set, "error");
                return;
              }
              notify("Credentials saved");
              doLogin();
            } catch (err) { notify(String(err.message), "error"); }
          }}),
        ),
      ),
      h("section", { class: "card" },
        h("h3", { text: "Import session" }),
        h("p", { class: "muted small" },
          "If auto login fails, export the session from your browser on any OS:",
          h("pre", { style: "font-size:11px" },
            "pip install playwright\nplaywright install chromium\npython notebooklm-worker/scripts/export_session.py\n\nThen copy the JSON content and paste it below."
          ),
        ),
        renderSessionPaste(),
      ),
      h("section", { class: "card" },
        h("h3", { text: "Video Config" }),
        h("p", { class: "muted small" }, "Edit video profiles, duration presets, and trim settings. Changes are saved to the worker and take effect for new jobs."),
        h("div", { id: "nlm-config-status", class: "muted small", style: "margin-top:4px" }),
        h("div", { id: "nlm-config-form", style: "margin-top:8px" }, "Loading config…"),
        h("button", {
          id: "nlm-config-save-btn",
          class: "btn primary hidden",
          type: "button",
          text: "Save config",
          onclick: async () => { await saveNlmConfig(); },
        }),
      ),
    ),
    h("div", { style: "margin-top:12px" }, card("Output", output)),
  );
  if (config) renderNlmConfigForm(config);
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
