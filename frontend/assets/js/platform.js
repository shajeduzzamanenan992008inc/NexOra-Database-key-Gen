import { api, ApiProblem, setCsrfToken } from "./api.js";
import {
  $,
  $$,
  announce,
  clearNode,
  closeDialog,
  confirmAction,
  downloadText,
  el,
  formatDate,
  formatRelative,
  openDialog,
  setButtonBusy,
  statusBadge,
  toast,
} from "./ui.js";
import { dismissBootScreen, initMotion, isLiteMode, setLiteMode } from "./motion.js";
import { omegaAsset, omegaPicture } from "./omega-assets.js";
import { secureSignOut } from "./shutdown.js";

initMotion();

const VIEWS = {
  overview: ["Overview", "Unified platform pulse, key inventory, and live access telemetry.", "COMMAND / LIVE PULSE"],
  analytics: ["Analytics", "Generation volume, API traffic, failure rates, and key-type distribution.", "COMMAND / TELEMETRY"],
  projects: ["Projects", "Projects own key types, expiration policy, keys, and access scopes.", "KEY ENGINE / PROJECTS"],
  keys: ["Keys", "One key store for reader, license, API, service, device, and custom credentials.", "KEY ENGINE / LIFECYCLE"],
  generator: ["Generate Keys", "Issue one or many keys. Plaintext is revealed once and never stored.", "KEY ENGINE / CSPRNG"],
  "key-types": ["Key Types", "Built-in types plus project-owned declarative custom types.", "KEY ENGINE / TYPE CATALOGUE"],
  builder: ["Type Builder", "Create a declarative key type. No user code is executed.", "KEY ENGINE / CUSTOM TYPE"],
  installations: ["Installations", "Reader App installations, device bindings, and revocation.", "ACCESS / INSTALLATIONS"],
  devices: ["Devices", "Device bindings created by type-isolated authentication.", "ACCESS / DEVICES"],
  "api-usage": ["API Usage", "Requests, failures, latency, and endpoint activity for universal access.", "ACCESS / ANALYTICS"],
  webhooks: ["Webhooks", "Signed event delivery with retry and permanent-failure handling.", "ACCESS / WEBHOOKS"],
  notifications: ["Notifications", "Expiry warnings, security signals, and delivery failures.", "GOVERNANCE / INBOX"],
  search: ["Global Search", "Search projects, key hints, fingerprints, types, devices, and audit events.", "COMMAND / SEARCH"],
  audit: ["Audit Trail", "Server-side administrative and security history.", "GOVERNANCE / AUDIT"],
  "import-export": ["Import / Export", "Bulk key metadata export and validated one-time key import.", "GOVERNANCE / DATA"],
  "imported-keys": ["Imported Keys", "Imported metadata inventory and independent replacement-key generation.", "KEY ENGINE / RECOVERY"],
  automation: ["Automation", "Expiry sweeps, retention cleanup, notifications, and webhook retries.", "GOVERNANCE / SCHEDULER"],
  scheduler: ["Scheduler", "Worker health, lock state, job durations, queue depth, and recent failures.", "GOVERNANCE / WORKER"],
  roles: ["Roles", "Platform permission matrix. Authorization remains server-side.", "GOVERNANCE / RBAC"],
  admins: ["Administrators", "Platform role assignment and session revocation.", "GOVERNANCE / ADMINS"],
  security: ["Security Center", "Authentication, session, API, and key-storage posture.", "SYSTEM / SECURITY"],
  health: ["Health", "Database, API, and security readiness.", "SYSTEM / HEALTH"],
  settings: ["Preferences", "Notification routing, reduced mode, and session context.", "SYSTEM / PREFERENCES"],
  docs: ["API Docs", "The versioned endpoint surface and a minimal access example.", "SYSTEM / OPENAPI"],
};

const state = {
  user: null,
  view: "overview",
  overview: null,
  projects: [],
  keyTypes: [],
  keys: [],
  keysPage: 1,
  keySelection: new Set(),
  keyRows: [],
  projectFilter: "",
  refreshing: false,
  redirecting: false,
  health: null,
};

const numberFormat = new Intl.NumberFormat();

function redirectToLogin() {
  if (state.redirecting) return;
  state.redirecting = true;
  window.location.replace("/login");
}

function fail(error, fallback = "The request could not be completed.") {
  if (error instanceof ApiProblem && error.status === 401) {
    redirectToLogin();
    return;
  }
  const detail = error instanceof ApiProblem && error.details
    ? Object.entries(error.details.fields || error.details)
        .map(([field, message]) => `${field}: ${message}`)
        .join(" · ")
    : "";
  toast(error && error.message ? error.message : fallback, { type: "error", detail });
}

function showError(message) {
  const node = $("#global-error");
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
}

function clearError() {
  const node = $("#global-error");
  if (node) {
    node.hidden = true;
    node.textContent = "";
  }
}

function node(tag, className, text) {
  return el(tag, { class: className || undefined, text: text === undefined ? undefined : text });
}

function button(label, onclick, className = "btn btn-ghost btn-sm", extra = {}) {
  return el("button", { class: className, type: "button", text: label, onclick, ...extra });
}

function link(label, href, className = "btn btn-ghost btn-sm") {
  return el("a", { class: className, href, text: label });
}

function pageHeader(title, subtitle, actions = []) {
  return el("div", { class: "section-title" }, [
    el("div", {}, [node("h2", "", title), node("p", "", subtitle)]),
    actions.length ? el("div", { class: "card-actions" }, actions) : null,
  ]);
}

function card(title, children, action) {
  return el("section", { class: "platform-card glass" }, [
    title
      ? el("div", { class: "card-head" }, [
          node("h2", "", title),
          action || null,
        ])
      : null,
    ...(Array.isArray(children) ? children : [children]),
  ]);
}

function stat(label, value, tone = "") {
  return el("article", { class: `stat-mini ${tone}`.trim() }, [
    node("span", "", label),
    node("strong", "", numberFormat.format(Number(value) || 0)),
  ]);
}

function emptyState(title, subtitle, icon = "◇") {
  return el("div", { class: "empty" }, [
    node("span", "empty-icon", icon),
    node("strong", "", title),
    node("span", "", subtitle),
  ]);
}

function table(headers, rows, emptyTitle = "No records") {
  if (!rows.length) {
    return emptyState(emptyTitle, "There is nothing to display for this filter yet.", "◌");
  }
  return el("div", { class: "platform-table-wrap" }, [
    el("table", { class: "platform-table" }, [
      el("thead", {}, [el("tr", {}, headers.map((h) => el("th", { text: h })))]),
      el("tbody", {}, rows),
    ]),
  ]);
}

function cell(content, className = "") {
  return el("td", { class: className || undefined }, content instanceof Node ? [content] : [String(content ?? "—")]);
}

function pill(text, tone = "") {
  return el("span", { class: `pill ${tone}`.trim(), text });
}

function statusTone(status) {
  if (["active", "success", "connected", "ok"].includes(status)) return "good";
  if (["generated", "info"].includes(status)) return "info";
  if (["expired", "failed", "warning", "degraded"].includes(status)) return "warn";
  if (["revoked", "deleted", "dead", "error", "unavailable"].includes(status)) return "bad";
  return "";
}

function field(labelText, input, hint = "") {
  return el("label", { class: "field" }, [
    node("span", "", labelText),
    input,
    hint ? node("small", "", hint) : null,
  ]);
}

function input(attributes = {}) {
  return el("input", { type: "text", ...attributes });
}

function select(options, attributes = {}) {
  const { value, ...rest } = attributes;
  const node = el("select", rest);
  options.forEach(([optionValue, label]) => node.append(el("option", { value: optionValue, text: label })));
  if (value !== undefined && value !== null) node.value = value;
  return node;
}

const EXPIRY_PRESETS = [
  ["never", "Never expires"],
  ["1_day", "1 day"],
  ["7_days", "7 days"],
  ["30_days", "30 days"],
  ["90_days", "90 days"],
  ["1_year", "1 year"],
  ["custom", "Custom date & time"],
];

function allowedExpiryOptions(type) {
  const policy = type?.expiration_policy || {};
  const allowed = Array.isArray(policy.presets) && policy.presets.length
    ? new Set(policy.presets)
    : null;
  return EXPIRY_PRESETS.filter(([value]) => !allowed || allowed.has(value));
}

const KEY_TYPE_COPY = {
  reader: {
    labelPlaceholder: "e.g. Reader rollout — warehouse A",
    permissionsPlaceholder: "read, device_access",
    scopesPlaceholder: "e.g. devices.connect",
    metadataPlaceholder: 'Optional JSON, e.g. {"site":"warehouse-a"}',
    notesPlaceholder: "Optional internal note (never a secret)",
    description: "Reader App activation key. Device binding and one-time issuance follow the type policy.",
  },
  license: {
    labelPlaceholder: "e.g. Annual license batch",
    permissionsPlaceholder: "read, feature_access",
    scopesPlaceholder: "e.g. billing.plan",
    metadataPlaceholder: 'Optional JSON, e.g. {"tier":"pro"}',
    notesPlaceholder: "Optional license note (never a secret)",
    description: "License credential for entitlement checks. Feature permissions and scopes are bounded by the type.",
  },
  api: {
    labelPlaceholder: "e.g. Billing service — production",
    permissionsPlaceholder: "read, write, api_access",
    scopesPlaceholder: "e.g. reports.read, invoices.write",
    metadataPlaceholder: 'Optional JSON, e.g. {"client":"billing-service"}',
    notesPlaceholder: "Optional API client note (never a secret)",
    description: "API client credential. Narrow the permission and scope ceiling for each integration.",
  },
  database: {
    labelPlaceholder: "e.g. Primary database — read replica",
    permissionsPlaceholder: "read, write, database_access",
    scopesPlaceholder: "e.g. tables.orders.read",
    metadataPlaceholder: 'Optional JSON, e.g. {"database":"orders"}',
    notesPlaceholder: "Optional database access note (never a secret)",
    description: "Database access credential. The type policy controls whether writes and device binding are allowed.",
  },
  service: {
    labelPlaceholder: "e.g. Sync worker — region EU",
    permissionsPlaceholder: "read, service_access",
    scopesPlaceholder: "e.g. sync.run",
    metadataPlaceholder: 'Optional JSON, e.g. {"service":"sync-worker"}',
    notesPlaceholder: "Optional service note (never a secret)",
    description: "Service-to-service credential. Use a narrow scope set for each deployment.",
  },
  device: {
    labelPlaceholder: "e.g. Kiosk device — lobby",
    permissionsPlaceholder: "read, device_access",
    scopesPlaceholder: "e.g. device.heartbeat",
    metadataPlaceholder: 'Optional JSON, e.g. {"asset":"kiosk-04"}',
    notesPlaceholder: "Optional device note (never a secret)",
    description: "Device-bound credential. The generated key inherits the type’s binding policy.",
  },
  custom: {
    labelPlaceholder: "e.g. Integration batch — partner A",
    permissionsPlaceholder: "optional, comma-separated permissions",
    scopesPlaceholder: "optional, comma-separated scopes",
    metadataPlaceholder: "Optional metadata JSON object",
    notesPlaceholder: "Optional internal note (never a secret)",
    description: "Custom key type. Permissions, scopes and allowed expirations are defined by this type.",
  },
};

function typeCopy(type) {
  const id = String(type?.id || "").toLowerCase();
  const name = String(type?.name || "").toLowerCase();
  if (KEY_TYPE_COPY[id]) return KEY_TYPE_COPY[id];
  if (id.includes("database") || name.includes("database")) return KEY_TYPE_COPY.database;
  if (id.includes("license") || name.includes("license")) return KEY_TYPE_COPY.license;
  if (id.includes("api") || name.includes("api")) return KEY_TYPE_COPY.api;
  if (id.includes("reader") || name.includes("reader")) return KEY_TYPE_COPY.reader;
  if (id.includes("device") || name.includes("device")) return KEY_TYPE_COPY.device;
  if (id.includes("service") || name.includes("service")) return KEY_TYPE_COPY.service;
  return KEY_TYPE_COPY.custom;
}

function localDateTimeValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function expiryLabel(preset, value) {
  if (preset === "keep") return "Current expiration is unchanged.";
  if (preset === "never") return "Never expires";
  if (preset === "custom") {
    const raw = String(value || "").trim();
    if (!raw) return "Choose a future date and time.";
    const date = new Date(raw);
    return Number.isNaN(date.getTime()) ? "Invalid date and time." : `Expires ${date.toLocaleString()}`;
  }
  return EXPIRY_PRESETS.find(([key]) => key === preset)?.[1] || "Server-computed expiration";
}

function expiryValidation(preset, value) {
  if (preset !== "custom") return null;
  const raw = String(value || "").trim();
  if (!raw) return "Choose a custom expiration date and time.";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "Enter a valid custom expiration date and time.";
  if (date.getTime() <= Date.now()) return "The custom expiration must be in the future.";
  return null;
}

function expiryStatus(project) {
  if (!project || !project.expires_at) return "Never expires";
  const expired = project.effective_status === "expired" || new Date(project.expires_at).getTime() <= Date.now();
  return `${expired ? "Expired" : "Expires"} ${formatDate(project.expires_at)}`;
}

function renderExpiryPreview(previewNode, preset, value) {
  if (!previewNode) return;
  previewNode.classList.toggle("is-custom", preset === "custom");
  previewNode.classList.toggle("is-never", preset === "never");
  clearNode(previewNode);
  previewNode.append(
    el("span", { class: "muted", text: "Expiration:" }),
    el("strong", { text: expiryLabel(preset, value) }),
    el("span", { class: "muted", text: "Server-enforced · UTC storage" })
  );
}

function pulseControl(node, className = "is-picked", duration = 420) {
  if (!node) return;
  node.classList.remove(className);
  void node.offsetWidth;
  node.classList.add(className);
  window.setTimeout(() => node.classList.remove(className), duration);
}

function bindExpiryControls(selectNode, fieldNode, inputNode, previewNode) {
  const sync = () => {
    const custom = selectNode.value === "custom";
    if (fieldNode) fieldNode.hidden = !custom;
    renderExpiryPreview(previewNode, selectNode.value, inputNode ? inputNode.value : "");
  };
  selectNode.addEventListener("change", () => {
    pulseControl(selectNode);
    sync();
  });
  if (inputNode) {
    inputNode.addEventListener("input", sync);
    inputNode.addEventListener("change", sync);
  }
  sync();
  return sync;
}

function formStep(text) {
  return el("div", { class: "span-2 form-step" }, [node("span", "", text)]);
}

function dynamicField(labelText, control, hintNode) {
  return el("label", { class: "field" }, [
    node("span", "", labelText),
    control,
    hintNode || null,
  ]);
}

function readJson(text, fieldName) {
  const value = String(text || "").trim();
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("must be an object");
    }
    return parsed;
  } catch {
    throw new Error(`${fieldName} must be valid JSON object text.`);
  }
}

function downloadKeys(items, name = "nexora-universal-keys.txt") {
  const text = items.map((item) => `${item.key_hint || ""}\t${item.key}`).join("\n");
  downloadText(name, text, "text/plain;charset=utf-8");
}

function closeSecretDialog() {
  const dialog = $("#reveal-dialog");
  if (!dialog) return;
  const body = $("#reveal-body");
  if (body) clearNode(body);
  closeDialog(dialog);
}

function showSecretReveal(title, items, metadata = "") {
  const body = $("#reveal-body");
  if (!body) return;
  clearNode(body);
  $("#reveal-title").textContent = title;
  body.append(
    el("p", { class: "secret-warning", text: "This plaintext is shown once. Only an HMAC digest is stored. Copy or download it before closing." }),
    ...items.map((item) => el("div", {}, [
      el("code", { class: "reveal-key", text: item.key }),
      el("p", { class: "mono", text: `${item.key_hint || ""} · ${item.fingerprint || ""}` }),
    ])),
    metadata ? el("p", { class: "muted", text: metadata }) : null,
    el("div", { class: "card-actions" }, [
      button("Copy all", async () => {
        try {
          await navigator.clipboard.writeText(items.map((item) => item.key).join("\n"));
          toast("One-time keys copied.", { type: "success" });
        } catch {
          toast("Clipboard is unavailable. Select the key text manually.", { type: "error" });
        }
      }, "btn btn-primary btn-sm"),
      button("Download .txt", () => downloadKeys(items), "btn btn-ghost btn-sm"),
    ])
  );
  openDialog($("#reveal-dialog"));
  announce("One-time key material revealed. Store it now.");
}

function showPanel(title, content) {
  const body = $("#reveal-body");
  if (!body) return;
  clearNode(body);
  $("#reveal-title").textContent = title;
  body.append(content);
  openDialog($("#reveal-dialog"));
}

function setMeta(view) {
  const [title, subtitle, eyebrow] = VIEWS[view] || VIEWS.overview;
  $("#page-title").textContent = title;
  $("#page-subtitle").textContent = subtitle;
  $("#page-eyebrow").textContent = `OMEGA NEX-ORA / ${eyebrow}`;
  document.title = `${title} · OMEGA NEX-ORA Platform`;
}

function applyIdentity() {
  const user = state.user;
  if (!user) return;
  $("#user-name").textContent = user.full_name || user.email || "Administrator";
  $("#user-role").textContent = `${(user.role || "admin").toUpperCase()} · SERVER-SIDE AUTH`;
  const initials = (user.full_name || user.email || "NX")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
  $("#user-initials").textContent = initials || "NX";
}

async function loadHealth() {
  try {
    const health = await api.health();
    state.health = health;
    $("#platform-health-text").textContent = health.database === "connected" ? "DB CONNECTED" : "DB DEGRADED";
    $("#platform-health-chip").classList.toggle("chip-danger", health.database !== "connected");
  } catch {
    $("#platform-health-text").textContent = "HEALTH UNKNOWN";
  }
}

function tableEmpty(title = "No records") {
  return emptyState(title, "Nothing matches this view yet.", "◌");
}

/* ------------------------------------------------------------------ views */

async function renderOverview(root) {
  const data = await api.universalOverview();
  state.overview = data;
  const projects = data.projects || {};
  const keys = data.keys || {};
  const devices = data.devices || {};
  const usage = data.api_usage_24h || {};
  root.append(
    el("section", { class: "platform-hero glass" }, [
      el("div", {}, [
        node("p", "eyebrow", "UNIFIED KEY PLATFORM / ONLINE"),
        node("h2", "", "One control plane. Every credential class."),
        el("p", { text: "Projects, key types, permissions, device policy, lifecycle, webhooks, audit, and automation share one server-enforced access model. Plaintext never leaves the creation response." }),
        el("div", { class: "card-actions" }, [
          button("Generate keys", () => showView("generator"), "btn btn-primary btn-sm"),
          button("Review keys", () => showView("keys"), "btn btn-ghost btn-sm"),
        ]),
      ]),
      el("div", { class: "platform-hero-art" }, [omegaPicture("key-verified", { alt: "" })]),
    ]),
    el("div", { class: "stat-strip" }, [
      stat("Projects", projects.total, "is-good"),
      stat("Active keys", keys.active, "is-good"),
      stat("Expiring in 7 days", keys.expiring_7d, keys.expiring_7d ? "is-warn" : ""),
      stat("API requests / 24h", usage.requests),
      stat("Failed / 24h", usage.failed, usage.failed ? "is-bad" : ""),
      stat("Avg latency", usage.avg_latency_ms, ""),
      stat("Active devices", devices.active),
      stat("Unread alerts", data.unread_notifications, data.unread_notifications ? "is-warn" : ""),
    ]),
    el("div", { class: "platform-grid" }, [
      card("Key lifecycle", [
        el("dl", { class: "record-grid" }, [
          el("div", { class: "record-item" }, [node("dt", "", "Generated / active"), el("dd", {}, [statusBadge("active"), ` ${numberFormat.format(keys.active || 0)}`])]),
          el("div", { class: "record-item" }, [node("dt", "", "Deactivated"), el("dd", { text: numberFormat.format(keys.deactivated || 0) })]),
          el("div", { class: "record-item" }, [node("dt", "", "Expired"), el("dd", { text: numberFormat.format(keys.expired || 0) })]),
          el("div", { class: "record-item" }, [node("dt", "", "Revoked (audit retained)"), el("dd", { text: numberFormat.format(keys.revoked || 0) })]),
        ]),
        el("p", { class: "muted", text: "Revocation destroys the live key row and active device bindings. Audit and key-event history remain." }),
      ]),
      card("Key type distribution", data.key_type_distribution && data.key_type_distribution.length
        ? el("div", { class: "timeline" }, data.key_type_distribution.slice(0, 8).map((item) =>
            el("div", { class: "timeline-row" }, [
              node("span", "mono", item.key_type_id),
              el("progress", { max: String(Math.max(...data.key_type_distribution.map((x) => Number(x.count) || 1))), value: String(item.count) }),
              node("span", "mono", numberFormat.format(item.count)),
            ])
          ))
        : emptyState("No keys yet", "Generate a key to populate the type distribution.", "⌘")),
    ]),
    el("div", { class: "platform-grid" }, [
      card("Recent key events", data.recent_events && data.recent_events.length
        ? el("div", { class: "stack" }, data.recent_events.map((event) =>
            el("div", { class: "result-row" }, [
              node("strong", "", event.event),
              el("span", { class: "mono", text: `${event.fingerprint || "—"} · ${event.actor_email || "system"}` }),
              node("span", "muted", formatRelative(event.created_at)),
            ])
          ))
        : emptyState("No key events", "Lifecycle actions will appear here.", "≡")),
      card("Server health", [
        el("dl", { class: "record-grid" }, [
          el("div", { class: "record-item" }, [node("dt", "", "Database"), el("dd", {}, [pill(data.health.database, data.health.database === "connected" ? "good" : "bad")])]),
          el("div", { class: "record-item" }, [node("dt", "", "API version"), el("dd", { text: data.health.api_version || "v1" })]),
          el("div", { class: "record-item" }, [node("dt", "", "Server time"), el("dd", { text: formatDate(data.health.server_time) })]),
        ]),
        el("p", { class: "muted", text: "Health reflects a live database probe. This page never displays cached placeholder metrics." }),
      ]),
    ])
  );
}

async function renderAnalytics(root) {
  const [data, usage] = await Promise.all([
    api.universalOverview(),
    api.apiUsage({ page: 1, page_size: 12 }),
  ]);
  state.overview = data;
  const generation = data.generation_timeline || [];
  const maxGeneration = Math.max(1, ...generation.map((item) => Number(item.generated) || 0));
  root.append(
    pageHeader("Platform analytics", "Live generation and access telemetry. No synthetic values."),
    el("div", { class: "stat-strip" }, [
      stat("Total keys", data.keys.total),
      stat("Expiring 30d", data.keys.expiring_30d, data.keys.expiring_30d ? "is-warn" : ""),
      stat("Requests 24h", usage.summary_24h.requests),
      stat("Failures 24h", usage.summary_24h.failed, usage.summary_24h.failed ? "is-bad" : ""),
    ]),
    el("div", { class: "platform-grid" }, [
      card("14-day generation volume", generation.length
        ? el("div", { class: "timeline" }, generation.map((item) =>
            el("div", { class: "timeline-row" }, [
              node("span", "mono", item.day),
              el("progress", { max: String(maxGeneration), value: String(item.generated) }),
              node("span", "mono", numberFormat.format(item.generated)),
            ])
          ))
        : emptyState("No generation activity", "Issue keys to begin the timeline.", "⚡")),
      card("Key status mix", el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Active"), el("dd", { text: numberFormat.format(data.keys.active || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Deactivated"), el("dd", { text: numberFormat.format(data.keys.deactivated || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Expired"), el("dd", { text: numberFormat.format(data.keys.expired || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Revoked"), el("dd", { text: numberFormat.format(data.keys.revoked || 0) })]),
      ])),
    ]),
    card("Recent API calls", table(
      ["Time", "Project", "Key", "Endpoint", "Status", "Latency"],
      (usage.items || []).map((item) => el("tr", {}, [
        cell(formatDate(item.created_at)),
        cell(item.project_name || "—"),
        cell(el("span", { class: "mono", text: item.key_hint || "—" })),
        cell(el("span", { class: "mono", text: item.endpoint })),
        cell(pill(String(item.status_code), statusTone(item.status_code >= 400 ? "failed" : "success"))),
        cell(`${item.latency_ms} ms`),
      ])),
      "No API traffic yet"
    ))
  );
}

function newProjectForm() {
  const name = input({ placeholder: "e.g. Warehouse Operations", maxlength: "200", "aria-label": "Project name" });
  const preset = select(EXPIRY_PRESETS, { value: "never", "aria-label": "Project expiration preset" });
  const custom = input({ type: "datetime-local", "aria-label": "Custom project expiration" });
  const customField = field("Custom project expiration (local time)", custom, "Converted to UTC. Must be in the future.");
  customField.classList.add("span-2");
  const preview = el("div", { class: "expiration-preview span-2", "aria-live": "polite" });
  bindExpiryControls(preset, customField, custom, preview);
  const submit = button("Create project", async () => {
    if (!name.value.trim()) return toast("Enter a project name.", { type: "error" });
    const error = expiryValidation(preset.value, custom.value);
    if (error) return toast(error, { type: "error" });
    setButtonBusy(submit, true, "Creating…");
    try {
      const body = { expiration: preset.value };
      if (preset.value === "custom") body.expires_at = new Date(custom.value).toISOString();
      await api.createProject(name.value.trim(), body);
      toast("Project created.", { type: "success" });
      closeSecretDialog();
      showView("projects", { reload: true });
    } catch (error) {
      fail(error, "Could not create the project.");
    } finally {
      setButtonBusy(submit, false);
    }
  }, "btn btn-primary btn-sm");
  const form = el("form", { class: "form-grid" }, [
    field("Project name", name, "1–200 characters. Names are normalized for lookup."),
    field("Project expiration", preset),
    customField,
    preview,
    el("p", { class: "muted span-2", text: "Project expiry blocks project authentication. It does not rewrite individual key expiry timestamps; each key keeps its own schedule." }),
    el("div", { class: "span-2 card-actions" }, [submit]),
  ]);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit.click();
  });
  return form;
}

async function renderProjects(root) {
  const data = await api.universalProjects({ page_size: 100 });
  state.projects = data.items || [];
  const search = input({ placeholder: "Search projects", "aria-label": "Search projects" });
  const tbody = el("tbody");
  const draw = (items) => {
    clearNode(tbody);
    if (!items.length) {
      tbody.append(el("tr", {}, [el("td", { colspan: "8" }, [tableEmpty("No projects match")])]));
      return;
    }
    items.forEach((project) => {
      tbody.append(el("tr", {}, [
        cell(el("div", {}, [
          el("strong", { text: project.project_name }),
          el("div", { class: "mono", text: project.id }),
        ])),
        cell(project.project_type || "database"),
        cell(statusBadge(project.effective_status || project.status)),
        cell(`${project.active_keys || 0} active / ${project.total_keys || 0} total`),
        cell(String(project.active_devices || 0)),
        cell(el("div", { class: "expiry-cell" }, [
          node("span", "", project.expires_at ? formatDate(project.expires_at) : "Never"),
          project.effective_status === "expired" ? pill("Expired", "warn") : null,
        ])),
        cell(formatDate(project.created_at, { time: false })),
        cell(el("div", { class: "row-actions" }, [
          button("Details", () => showProjectDetails(project)),
          button("Keys", () => manageProjectKeys(project)),
          button("Edit", () => editProject(project)),
          button("Clone", () => cloneProject(project)),
          button("Export", () => exportProject(project)),
          project.effective_status !== "archived"
            ? button("Archive", () => archiveProject(project), "btn btn-danger btn-sm")
            : null,
          button("Delete", () => deleteProject(project), "btn btn-danger btn-sm"),
        ]), "align-right"),
      ]));
    });
  };
  const applyFilter = () => {
    const term = search.value.trim().toLowerCase();
    draw(term
      ? state.projects.filter((project) => `${project.project_name} ${project.project_type || ""} ${project.id}`.toLowerCase().includes(term))
      : state.projects);
  };
  search.addEventListener("input", applyFilter);
  const actions = [
    button("New project", () => showPanel("Create a project", newProjectForm()), "btn btn-primary btn-sm"),
    button("Export all (ZIP)", exportAllProjects, "btn btn-ghost btn-sm"),
  ];
  if (state.user && state.user.role === "owner") {
    actions.push(button("Delete all projects", deleteAllProjects, "btn btn-danger btn-sm"));
  }
  root.append(
    pageHeader("Projects", "Each project is an isolation boundary for keys, permissions, scope, device policy, and expiry.", actions),
    el("div", { class: "page-toolbar" }, [
      el("div", { class: "filter-row" }, [search, button("Search", applyFilter, "btn btn-primary btn-sm")]),
    ]),
    el("div", { class: "platform-table-wrap" }, [
      el("table", { class: "platform-table" }, [
        el("thead", {}, [el("tr", {}, ["Project", "Type", "Status", "Keys", "Devices", "Expires", "Created", "Actions"].map((header) => el("th", { text: header })))]),
        tbody,
      ]),
    ])
  );
  draw(state.projects);
}

function manageProjectKeys(project) {
  state.projectFilter = project.id;
  showView("keys");
}

async function showProjectDetails(project) {
  try {
    const [stats, timeline, keyData] = await Promise.all([
      api.universalProjectStats(project.id),
      api.universalProjectTimeline(project.id),
      api.universalKeys({ project_id: project.id, page_size: 20 }),
    ]);
    const keys = keyData.items || [];
    const events = timeline.events || timeline.items || [];
    showPanel(`Project · ${project.project_name}`, el("div", { class: "stack" }, [
      el("div", { class: "expiry-current" }, [
        statusBadge(project.effective_status || project.status),
        node("span", "", expiryStatus(project)),
      ]),
      el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Project ID"), el("dd", { class: "mono", text: project.id })]),
        el("div", { class: "record-item" }, [node("dt", "", "Type"), el("dd", { text: project.project_type || "database" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Created"), el("dd", { text: formatDate(project.created_at) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Description"), el("dd", { text: project.description || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Auto-delete"), el("dd", { text: project.auto_delete_at ? formatDate(project.auto_delete_at) : "Disabled" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Keys"), el("dd", { text: `${stats.keys?.active || 0} active / ${stats.keys?.total || 0} total` })]),
        el("div", { class: "record-item" }, [node("dt", "", "Devices"), el("dd", { text: String(stats.devices?.active || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "API calls / 7d"), el("dd", { text: numberFormat.format(stats.api_usage_7d || 0) })]),
      ]),
      el("div", { class: "card-actions" }, [
        button("Edit project", () => editProject(project)),
        button("Manage keys", () => manageProjectKeys(project), "btn btn-primary btn-sm"),
      ]),
      node("h3", "", "Keys"),
      table(
        ["Hint", "Type", "Status", "Expires", ""],
        keys.map((key) => el("tr", {}, [
          cell(el("span", { class: "mono", text: key.key_hint })),
          cell(pill(key.key_type_id, "info")),
          cell(statusBadge(key.status)),
          cell(key.expires_at ? formatDate(key.expires_at) : "Never"),
          cell(button("Details", () => showKeyDetails(key.id)), "align-right"),
        ])),
        "No keys in this project"
      ),
      node("h3", "", "Recent activity"),
      events.length
        ? el("div", { class: "stack" }, events.slice(0, 12).map((event) =>
            el("div", { class: "result-row" }, [
              node("strong", "", event.event || event.action || "event"),
              node("span", "muted", event.created_at ? formatDate(event.created_at) : ""),
            ])
          ))
        : node("p", "muted", "No project activity recorded."),
    ]));
  } catch (error) { fail(error, "Project details unavailable."); }
}

function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function exportProject(project) {
  try {
    const preview = await api.universalProjectExportPreview(project.id);
    const box = el("div", { class: "stack" }, [
      el("p", { class: "muted", text: "Exports contain project configuration, key metadata, devices and webhook configuration. Key material and hashes are never recoverable from a bundle." }),
      el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Project"), el("dd", { text: preview.project.project_name })]),
        el("div", { class: "record-item" }, [node("dt", "", "Keys"), el("dd", { text: String(preview.counts.keys) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Key types"), el("dd", { text: String(preview.counts.key_types) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Devices"), el("dd", { text: String(preview.counts.devices) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Webhooks"), el("dd", { text: String(preview.counts.webhooks) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Secrets exposed"), el("dd", { text: "No" })]),
      ]),
      el("div", { class: "card-actions" }, [
        link("Download JSON", api.universalProjectExportUrl(project.id, "json"), "btn btn-primary btn-sm"),
        link("Download ZIP backup", api.universalProjectExportUrl(project.id, "zip"), "btn btn-ghost btn-sm"),
      ]),
      ...(preview.warnings || []).map((warning) => el("p", { class: "muted", text: `• ${warning}` })),
    ]);
    showPanel(`Export '${project.project_name}'`, box);
  } catch (error) { fail(error, "Export preview failed."); }
}

async function exportAllProjects() {
  if (!state.projects.length) return toast("There are no projects to export.", { type: "info" });
  try {
    const blob = await api.exportUniversalProjectsBlob({ all: true, format: "zip" });
    downloadBlob("nexora-projects-bundle.zip", blob);
    toast("Project archive downloaded.", { type: "success" });
  } catch (error) { fail(error, "Bulk export failed."); }
}

function deleteProject(project) {
  const name = input({ placeholder: project.project_name, maxlength: "200", autocomplete: "off" });
  const password = input({ type: "password", maxlength: "128", autocomplete: "current-password" });
  const submit = button("Permanently delete project", async () => {
    if (!name.value.trim()) return toast("Type the exact project name.", { type: "error" });
    if (!password.value) return toast("Enter your current password.", { type: "error" });
    setButtonBusy(submit, true, "Deleting…");
    try {
      const result = await api.deleteUniversalProject(project.id, name.value.trim(), password.value);
      toast(`Deleted '${result.project.project_name}'. ${result.counts.keys} key(s) removed.`, { type: "success" });
      closeSecretDialog();
      showView("projects", { reload: true });
    } catch (error) { fail(error, "Permanent deletion failed."); }
    finally { setButtonBusy(submit, false); }
  }, "btn btn-danger btn-sm");
  showPanel(`Delete '${project.project_name}' permanently`, el("div", { class: "stack" }, [
    el("p", { class: "muted", text: "This removes the project and every owned key, device, webhook, delivery, imported record and usage row in one transaction. Audit history and administrator accounts are preserved. The archive action is separate and reversible." }),
    field("Type the exact project name", name),
    field("Current password (re-authentication)", password),
    el("div", { class: "card-actions" }, [submit]),
  ]));
}

async function deleteAllProjects() {
  let preview;
  try {
    preview = await api.universalProjectDeletePreview();
  } catch (error) {
    fail(error, "The deletion preview could not be loaded.");
    return;
  }
  const counts = preview.counts || {};
  const phrase = input({ placeholder: "DELETE ALL PROJECTS", maxlength: "64", autocomplete: "off" });
  const password = input({ type: "password", maxlength: "128", autocomplete: "current-password" });
  const submit = button("Delete every project", async () => {
    if (phrase.value.trim() !== "DELETE ALL PROJECTS") return toast("Type DELETE ALL PROJECTS exactly.", { type: "error" });
    if (!password.value) return toast("Enter your current password.", { type: "error" });
    const confirmed = await confirmAction({
      title: "Delete all projects?",
      message: "This permanently deletes every project and its keys, devices, webhooks, deliveries and imported metadata. Administrator accounts, settings and audit history are preserved. This cannot be undone.",
      confirmLabel: "Delete all projects",
      tone: "danger",
    });
    if (!confirmed.confirmed) return;
    setButtonBusy(submit, true, "Deleting all…");
    try {
      const result = await api.deleteAllUniversalProjects("DELETE ALL PROJECTS", password.value);
      toast(`Deleted ${result.deleted_count} project(s) and ${result.counts.keys} key(s).`, { type: "success" });
      closeSecretDialog();
      showView("projects", { reload: true });
    } catch (error) { fail(error, "Delete-all failed."); }
    finally { setButtonBusy(submit, false); }
  }, "btn btn-danger btn-sm");
  showPanel("Delete all projects", el("div", { class: "stack" }, [
    el("p", { class: "muted", text: "Owner-only. Requires the exact phrase and a fresh password check. This is a permanent database deletion, not an archive." }),
    el("dl", { class: "record-grid" }, [
      el("div", { class: "record-item" }, [node("dt", "", "Projects"), el("dd", { text: numberFormat.format(counts.projects || 0) })]),
      el("div", { class: "record-item" }, [node("dt", "", "Keys"), el("dd", { text: numberFormat.format(counts.keys || 0) })]),
      el("div", { class: "record-item" }, [node("dt", "", "Devices"), el("dd", { text: numberFormat.format(counts.devices || 0) })]),
      el("div", { class: "record-item" }, [node("dt", "", "Webhooks"), el("dd", { text: numberFormat.format(counts.webhooks || 0) })]),
      el("div", { class: "record-item" }, [node("dt", "", "Key types"), el("dd", { text: numberFormat.format(counts.key_types || 0) })]),
      el("div", { class: "record-item" }, [node("dt", "", "Delivery records"), el("dd", { text: numberFormat.format(counts.deliveries || 0) })]),
    ]),
    field("Confirmation phrase", phrase),
    field("Current password", password),
    el("div", { class: "card-actions" }, [submit]),
  ]));
}

function editProject(project) {
  const name = input({ value: project.project_name, maxlength: "200" });
  const description = el("textarea", { rows: "3", text: project.description || "" });
  const type = select([
    ["database", "Database"], ["api", "API"], ["service", "Service"],
    ["webhook", "Webhook"], ["ai", "AI service"], ["other", "Other"],
  ], { value: project.project_type || "database" });
  const storedStatus = project.status === "inactive"
    ? "inactive"
    : (project.status === "expired" ? "expired" : "active");
  const status = select([["active", "Active"], ["inactive", "Inactive"]], { value: storedStatus === "inactive" ? "inactive" : "active" });
  const autoDeleteInitial = localDateTimeValue(project.auto_delete_at);
  const autoDelete = input({
    type: "datetime-local",
    value: autoDeleteInitial,
    "aria-label": "Scheduled project deletion",
  });
  const hasExpiry = Boolean(project.expires_at);
  const preset = select([
    ...(hasExpiry ? [["keep", "Keep current expiration"]] : []),
    ...EXPIRY_PRESETS,
  ], { value: hasExpiry ? "keep" : "never", "aria-label": "Project expiration preset" });
  const custom = input({
    type: "datetime-local",
    value: localDateTimeValue(project.expires_at),
    "aria-label": "Custom project expiration",
  });
  const customField = field("Custom project expiration (local time)", custom, "Converted to UTC. Must be in the future.");
  customField.classList.add("span-2");
  const preview = el("div", { class: "expiration-preview span-2", "aria-live": "polite" });
  bindExpiryControls(preset, customField, custom, preview);
  const submit = button("Save changes", async () => {
    if (!name.value.trim()) return toast("Enter a project name.", { type: "error" });
    const error = preset.value === "keep" ? null : expiryValidation(preset.value, custom.value);
    if (error) return toast(error, { type: "error" });
    setButtonBusy(submit, true, "Saving…");
    try {
      const body = {
        project_name: name.value.trim(),
        description: description.value,
        project_type: type.value,
      };
      // A stored 'expired' status is derived; leave it untouched unless the
      // administrator explicitly edits it or extends the expiry below.
      if (storedStatus !== "expired" || status.value !== "active") {
        body.status = status.value;
      }
      if (preset.value !== "keep") {
        body.expiration_preset = preset.value;
        body.expires_at = preset.value === "custom" ? new Date(custom.value).toISOString() : null;
      }
      await api.updateUniversalProject(project.id, body);
      if (autoDelete.value !== autoDeleteInitial) {
        await api.setProjectAutoDelete(
          project.id,
          autoDelete.value ? new Date(autoDelete.value).toISOString() : null,
        );
      }
      toast("Project updated.", { type: "success" });
      closeSecretDialog();
      showView("projects", { reload: true });
    } catch (error) { fail(error, "Project update failed."); }
    finally { setButtonBusy(submit, false); }
  }, "btn btn-primary btn-sm");
  const form = el("form", { class: "form-grid" }, [
    field("Project name", name),
    field("Project type", type),
    field("Description", description),
    field("Status", status),
    field("Project expiration", preset, "Choose ‘Keep current expiration’ to edit other fields without changing this date."),
    customField,
    preview,
    field("Scheduled auto-delete (local time)", autoDelete, "Optional. Empty disables scheduled deletion. Deletion only runs when the server scheduler executes."),
    el("div", { class: "span-2 expiry-current" }, [
      node("span", "muted", "Current expiration"),
      statusBadge(project.effective_status || project.status),
      node("span", "", expiryStatus(project)),
    ]),
    el("p", { class: "muted span-2", text: "Project expiry blocks project authentication. Existing key expiry timestamps are preserved." }),
    el("div", { class: "span-2 card-actions" }, [submit]),
  ]);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit.click();
  });
  showPanel("Edit project", form);
}

async function archiveProject(project) {
  const confirmed = await confirmAction({
    title: `Archive '${project.project_name}'?`,
    message: "Archiving blocks all project keys from authenticating and deactivates project devices. Key records and audit history remain.",
    confirmLabel: "Archive project",
    tone: "danger",
  });
  if (!confirmed.confirmed) return;
  try {
    await api.archiveUniversalProject(project.id);
    toast("Project archived.", { type: "success" });
    showView("projects", { reload: true });
  } catch (error) { fail(error, "Archive failed."); }
}

function cloneProject(project) {
  const name = input({ placeholder: "New project name", maxlength: "200" });
  showPanel("Clone project configuration", el("div", { class: "stack" }, [
    el("p", { class: "muted", text: "Configuration and key types are copied. Key plaintext is never cloned and no secrets are duplicated." }),
    field("New project name", name),
    button("Clone project", async () => {
      if (!name.value.trim()) return toast("Enter a name.", { type: "error" });
      try {
        const result = await api.cloneUniversalProject(project.id, name.value.trim());
        toast(`Cloned as '${result.project.project_name}'. Keys cloned: 0.`, { type: "success" });
        closeSecretDialog();
        showView("projects", { reload: true });
      } catch (error) { fail(error, "Clone failed."); }
    }, "btn btn-primary btn-sm"),
  ]));
}

async function renderGenerator(root) {
  const [projectsData, typesData] = await Promise.all([
    api.universalProjects({ page_size: 100 }),
    api.keyTypes({ include_inactive: "false" }),
  ]);
  state.projects = projectsData.items || [];
  state.keyTypes = typesData.key_types || [];
  const projectSelect = select([["", "Choose a project"]].concat(state.projects.map((p) => [p.id, p.project_name])), { required: "required" });
  const typeSelect = select(state.keyTypes.map((t) => [t.id, `${t.name} (${t.id})`]));
  const typeSummary = el("div", { class: "key-type-summary span-2", "aria-live": "polite" });
  const projectState = el("p", { class: "span-2 muted", "aria-live": "polite" });
  const label = input({ placeholder: "Optional batch label", maxlength: "100" });
  const labelHint = node("small", "muted", "Optional; shown in inventory. Never put a secret here.");
  const quantity = input({ type: "number", min: "1", max: "500", value: "1" });
  const expiration = select(EXPIRY_PRESETS, { value: "never", "aria-label": "Expiration preset" });
  const expires = input({ type: "datetime-local", "aria-label": "Custom key expiration" });
  const customExpiryField = field("Custom expiration (local time)", expires, "Converted to UTC. Must be in the future.");
  customExpiryField.classList.add("span-2");
  const expiryPreview = el("div", { class: "expiration-preview span-2", "aria-live": "polite" });
  const syncExpiry = bindExpiryControls(expiration, customExpiryField, expires, expiryPreview);
  const permissions = input();
  const permissionsHint = node("small", "muted", "");
  const scopes = input();
  const scopesHint = node("small", "muted", "");
  const tags = input({ placeholder: "optional tags, comma-separated" });
  const notes = el("textarea", { rows: "2", placeholder: "Optional internal notes (never secret)" });
  const metadata = el("textarea", { rows: "2", placeholder: "Optional metadata JSON object" });
  const metadataHint = node("small", "muted", "");
  const maxUsage = input({ type: "number", min: "1", placeholder: "Unlimited" });
  const deviceNote = el("p", { class: "span-2 muted", text: "" });

  const syncProjectState = () => {
    const project = state.projects.find((item) => item.id === projectSelect.value);
    projectState.textContent = project
      ? `Project status: ${project.effective_status || project.status} · ${expiryStatus(project)}`
      : "Choose the project that will own this key.";
  };
  projectSelect.addEventListener("change", () => {
    pulseControl(projectSelect);
    syncProjectState();
  });
  syncProjectState();

  const applyType = (animate = true) => {
    const selected = state.keyTypes.find((item) => item.id === typeSelect.value);
    const copy = typeCopy(selected);
    label.placeholder = copy.labelPlaceholder;
    permissions.placeholder = copy.permissionsPlaceholder;
    scopes.placeholder = copy.scopesPlaceholder;
    metadata.placeholder = copy.metadataPlaceholder;
    notes.placeholder = copy.notesPlaceholder;
    permissionsHint.textContent = selected
      ? `Allowed ceiling for ${selected.name}: ${(selected.permissions || []).join(", ") || "none"}. Broader requests are rejected server-side.`
      : "Select a type to see its permission ceiling.";
    scopesHint.textContent = selected
      ? `Allowed scopes: ${(selected.scopes || []).join(", ") || "none declared"}.`
      : "Select a type to see its scope ceiling.";
    const schemaFields = Object.keys(selected?.metadata_schema?.fields || {});
    metadataHint.textContent = schemaFields.length
      ? `Known metadata fields: ${schemaFields.slice(0, 6).join(", ")}. Must be a JSON object.`
      : "Optional JSON object; the type schema is enforced by the server.";
    deviceNote.textContent = selected
      ? `Device policy: ${selected.device_policy?.binds ? `binding required, ${selected.device_policy.max_devices || 1} device(s) per key` : "unbound"} · Rate limit: ${selected.usage_limits?.rate_per_minute ? `${selected.usage_limits.rate_per_minute}/min` : "type default"}.`
      : "";

    clearNode(typeSummary);
    if (!selected) {
      typeSummary.append(node("p", "muted", "Select a key type to load its placeholders, permissions and expiration policy."));
    } else {
      typeSummary.append(
        el("div", { class: "type-summary-head" }, [
          el("strong", { text: selected.name }),
          pill(selected.id, "info"),
          node("span", "muted", selected.is_builtin ? "Built-in type" : "Project type"),
        ]),
        node("p", "muted", selected.description || copy.description),
        el("div", { class: "type-summary-chips" }, [
          node("span", "type-chip", `Format ${selected.key_prefix || "NX"}-••••`),
          node("span", "type-chip", selected.device_policy?.binds ? `Device-bound · max ${selected.device_policy.max_devices || 1}` : "Unbound"),
          node("span", "type-chip", selected.usage_limits?.rate_per_minute ? `Rate ${selected.usage_limits.rate_per_minute}/min` : "Rate policy default"),
          node("span", "type-chip", `Permissions ${(selected.permissions || []).length}`),
        ])
      );
    }

    const policy = selected?.expiration_policy || {};
    const options = allowedExpiryOptions(selected);
    const previous = expiration.value;
    clearNode(expiration);
    options.forEach(([value, text]) => expiration.append(el("option", { value, text })));
    const preferred = options.some(([value]) => value === previous)
      ? previous
      : (policy.default && options.some(([value]) => value === policy.default) ? policy.default : options[0]?.[0] || "never");
    expiration.value = preferred;
    syncExpiry();
    if (animate) {
      pulseControl(typeSelect);
      pulseControl(typeSummary);
    }
  };
  typeSelect.addEventListener("change", () => applyType(true));
  applyType(false);

  const submit = button("Generate secure keys", async () => {
    if (!projectSelect.value) return toast("Choose a project.", { type: "error" });
    if (!typeSelect.value) return toast("Choose a key type.", { type: "error" });
    const error = expiryValidation(expiration.value, expires.value);
    if (error) return toast(error, { type: "error" });
    setButtonBusy(submit, true, "Generating…");
    const split = (value) => String(value || "").split(",").map((x) => x.trim()).filter(Boolean);
    try {
      const body = {
        project_id: projectSelect.value,
        key_type_id: typeSelect.value,
        quantity: Number(quantity.value) || 1,
        label: label.value.trim(),
        notes: notes.value,
        expiration_preset: expiration.value,
        expires_at: expiration.value === "custom" ? new Date(expires.value).toISOString() : null,
        tags: split(tags.value),
        permissions: permissions.value.trim() ? split(permissions.value) : undefined,
        scopes: scopes.value.trim() ? split(scopes.value) : undefined,
        metadata: readJson(metadata.value, "Metadata"),
        max_usage: maxUsage.value ? Number(maxUsage.value) : null,
      };
      const result = await api.generateUniversalKeys(body);
      showSecretReveal(
        `${result.keys.length} key${result.keys.length === 1 ? "" : "s"} generated`,
        result.keys,
        `Type: ${result.key_type.name} · Batch ${result.batch_id}`
      );
      form.reset();
      applyType(false);
      syncProjectState();
    } catch (error) {
      fail(error, "Key generation failed.");
    } finally {
      setButtonBusy(submit, false);
    }
  }, "btn btn-primary btn-glow");

  const form = el("form", { class: "form-grid" }, [
    formStep("1 · Project"),
    field("Project", projectSelect),
    projectState,
    formStep("2 · Key type"),
    field("Key type", typeSelect),
    typeSummary,
    formStep("3 · Type configuration"),
    dynamicField("Batch label", label, labelHint),
    field("Quantity", quantity, "Maximum is server-configured (default 500)."),
    deviceNote,
    formStep("4 · Expiration"),
    field("Expiration preset", expiration),
    customExpiryField,
    expiryPreview,
    formStep("5 · Permissions and features"),
    dynamicField("Permission subset", permissions, permissionsHint),
    dynamicField("Scope subset", scopes, scopesHint),
    field("Tags", tags),
    field("Max usage", maxUsage),
    dynamicField("Metadata JSON", metadata, metadataHint),
    field("Notes", notes),
    formStep("6 · Review and generate"),
    el("div", { class: "span-2 card-actions" }, [submit]),
  ]);
  form.addEventListener("submit", (event) => event.preventDefault());
  root.append(
    pageHeader("Generate universal keys", "One flow: project, type, type configuration, expiry, then a one-time key reveal."),
    card("Key creation", form),
    el("div", { class: "asset-banner" }, [
      el("img", { src: omegaAsset("key-generating"), alt: "", width: "58", height: "58" }),
      el("div", {}, [
        el("strong", { text: "Server-side entropy" }),
        el("span", { text: "Keys use the operating system CSPRNG. Only an HMAC-SHA256 digest is stored; no recovery path exists." }),
      ]),
    ])
  );
}

async function loadKeyFilters() {
  const [projects, types] = await Promise.all([
    api.universalProjects({ page_size: 100 }),
    api.keyTypes({ include_inactive: "true" }),
  ]);
  state.projects = projects.items || [];
  state.keyTypes = types.key_types || [];
}

async function renderKeys(root) {
  await loadKeyFilters();
  state.keySelection = new Set();
  const projectFilter = select(
    [["", "All projects"]].concat(state.projects.map((p) => [p.id, p.project_name])),
    { value: state.projectFilter || "" }
  );
  const typeFilter = select([["", "All types"]].concat(state.keyTypes.map((t) => [t.id, t.name])));
  const statusFilter = select([
    ["", "All statuses"], ["generated", "Generated"], ["active", "Active"],
    ["used", "Used"], ["deactivated", "Deactivated"], ["expired", "Expired"],
  ]);
  const search = input({ placeholder: "Search hint, fingerprint, label, tags" });
  const tbody = el("tbody");
  const selectAll = el("input", { type: "checkbox", "aria-label": "Select all visible keys" });
  const bulkCount = node("span", "muted", "0 selected");
  bulkCount.id = "key-bulk-count";
  const bulkDeactivate = button("Deactivate selected", bulkDeactivateKeys, "btn btn-ghost btn-sm");
  bulkDeactivate.id = "key-bulk-deactivate";
  bulkDeactivate.disabled = true;
  const bulkRevoke = button("Revoke selected", bulkRevokeKeys, "btn btn-danger btn-sm");
  bulkRevoke.id = "key-bulk-revoke";
  bulkRevoke.disabled = true;
  const filters = { projectFilter, typeFilter, statusFilter, search };
  const reload = () => loadKeysTable(tbody, filters).catch((error) => fail(error, "Keys unavailable."));
  projectFilter.addEventListener("change", () => {
    state.projectFilter = projectFilter.value;
    reload();
  });
  typeFilter.addEventListener("change", reload);
  statusFilter.addEventListener("change", reload);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); reload(); }
  });
  selectAll.addEventListener("change", () => {
    for (const key of state.keyRows || []) {
      if (selectAll.checked) state.keySelection.add(key.id);
      else state.keySelection.delete(key.id);
    }
    tbody.querySelectorAll('input[type="checkbox"]').forEach((box) => { box.checked = selectAll.checked; });
    updateKeyBulk();
  });
  const exportLink = link("Export CSV", "#", "btn btn-ghost btn-sm");
  exportLink.addEventListener("click", (event) => {
    event.preventDefault();
    window.location.assign(api.universalKeysExportUrl({
      project_id: projectFilter.value,
      key_type_id: typeFilter.value,
      status: statusFilter.value,
      format: "csv",
    }));
  });
  root.append(
    pageHeader("Key inventory", "One store for every key type. Filtering, bulk lifecycle actions, permissions, and expiry are server-authorized.", [
      button("Generate keys", () => showView("generator"), "btn btn-primary btn-sm"),
      exportLink,
    ]),
    el("div", { class: "page-toolbar" }, [
      el("div", { class: "filter-row" }, [projectFilter, typeFilter, statusFilter, search]),
      el("div", { class: "card-actions" }, [
        button("Apply", reload, "btn btn-primary btn-sm"),
      ]),
    ]),
    el("div", { class: "toolbar" }, [
      el("label", { class: "field-inline" }, [selectAll, node("span", "", "Select all visible")]),
      bulkCount,
      bulkDeactivate,
      bulkRevoke,
    ]),
    el("div", { class: "platform-table-wrap" }, [
      el("table", { class: "platform-table" }, [
        el("thead", {}, [el("tr", {}, ["", "Key hint", "Project", "Type", "Status", "Usage", "Expires", "Created", "Actions"].map((header) => el("th", { text: header })))]),
        tbody,
      ]),
    ])
  );
  await loadKeysTable(tbody, filters);
}

function updateKeyBulk() {
  const count = state.keySelection ? state.keySelection.size : 0;
  const label = $("#key-bulk-count");
  if (label) label.textContent = `${count} selected`;
  ["#key-bulk-deactivate", "#key-bulk-revoke"].forEach((selector) => {
    const node = $(selector);
    if (node) node.disabled = count === 0;
  });
}

async function bulkDeactivateKeys() {
  const keys = (state.keyRows || []).filter((key) => state.keySelection.has(key.id));
  if (!keys.length) return toast("Select at least one key.", { type: "error" });
  const confirmed = await confirmAction({
    title: `Deactivate ${keys.length} key(s)?`,
    message: "Every selected key stops authenticating and its active devices are deactivated. Revoked keys are skipped by the server.",
    confirmLabel: "Deactivate selected",
    tone: "warn",
  });
  if (!confirmed.confirmed) return;
  let applied = 0;
  let failed = 0;
  for (const key of keys) {
    try {
      await api.deactivateUniversalKey(key.id, "Bulk deactivation from unified platform");
      applied += 1;
    } catch {
      failed += 1;
    }
  }
  toast(`${applied} key(s) deactivated${failed ? `; ${failed} failed` : ""}.`, { type: failed ? "warn" : "success" });
  showView("keys", { reload: true });
}

async function bulkRevokeKeys() {
  const keys = (state.keyRows || []).filter((key) => state.keySelection.has(key.id));
  if (!keys.length) return toast("Select at least one key.", { type: "error" });
  const confirmed = await confirmAction({
    title: `Permanently revoke ${keys.length} key(s)?`,
    message: "The selected keys and their device bindings are destroyed. Audit and key-event history remain. This cannot be undone.",
    confirmLabel: "Revoke selected",
    tone: "danger",
    requireText: "REVOKE",
    textLabel: "Type REVOKE to confirm",
  });
  if (!confirmed.confirmed) return;
  let applied = 0;
  let failed = 0;
  for (const key of keys) {
    try {
      await api.revokeUniversalKey(key.id, "Bulk revocation from unified platform");
      applied += 1;
    } catch {
      failed += 1;
    }
  }
  toast(`${applied} key(s) revoked${failed ? `; ${failed} failed` : ""}.`, { type: failed ? "warn" : "success" });
  showView("keys", { reload: true });
}

async function loadKeysTable(tbody, filters) {
  clearNode(tbody);
  tbody.append(el("tr", {}, [el("td", { colspan: "9" }, [emptyState("Loading keys…", "Reading the authenticated inventory.", "⌘")])]));
  try {
    const data = await api.universalKeys({
      page: state.keysPage,
      page_size: 50,
      project_id: filters.projectFilter.value,
      key_type_id: filters.typeFilter.value,
      status: filters.statusFilter.value,
      search: filters.search.value.trim(),
    });
    state.keys = data.items || [];
    state.keyRows = state.keys;
    clearNode(tbody);
    if (!state.keys.length) {
      tbody.append(el("tr", {}, [el("td", { colspan: "9" }, [tableEmpty("No keys match")])]));
      updateKeyBulk();
      return;
    }
    state.keys.forEach((key) => {
      const box = el("input", { type: "checkbox", "aria-label": `Select ${key.key_hint}` });
      box.checked = state.keySelection.has(key.id);
      box.addEventListener("change", () => {
        if (box.checked) state.keySelection.add(key.id);
        else state.keySelection.delete(key.id);
        updateKeyBulk();
      });
      tbody.append(el("tr", {}, [
        cell(box),
        cell(el("div", {}, [
          el("strong", { class: "mono", text: key.key_hint }),
          el("div", { class: "mono truncate", text: key.fingerprint || "—" }),
        ])),
        cell(key.project_name),
        cell(pill(key.key_type_id, "info")),
        cell(statusBadge(key.status)),
        cell(`${key.usage_count || 0}${key.max_usage ? ` / ${key.max_usage}` : ""}`),
        cell(key.expires_at ? formatDate(key.expires_at) : "Never"),
        cell(formatDate(key.created_at, { time: false })),
        cell(el("div", { class: "row-actions" }, [
          button("Details", () => showKeyDetails(key.id)),
          button("Edit", () => editKey(key)),
          button("Expiration", () => editKeyExpiration(key)),
          key.status === "active" || key.status === "generated"
            ? button("Deactivate", () => deactivateKey(key))
            : null,
          key.status === "deactivated" || key.status === "expired"
            ? button("Activate", () => activateKey(key))
            : null,
          button("Regenerate", () => regenerateKey(key)),
          button("Revoke", () => revokeKey(key), "btn btn-danger btn-sm"),
        ]), "align-right"),
      ]));
    });
    updateKeyBulk();
  } catch (error) {
    clearNode(tbody);
    tbody.append(el("tr", {}, [el("td", { colspan: "9" }, [emptyState("Keys unavailable", error.message || "Request failed.")])]));
    throw error;
  }
}

async function showKeyDetails(id) {
  try {
    if (!state.keyTypes.length) await loadKeyFilters();
    const data = await api.universalKey(id);
    const key = data.key;
    showPanel(`Key ${key.key_hint}`, el("div", { class: "stack" }, [
      el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Project"), el("dd", { text: key.project_name })]),
        el("div", { class: "record-item" }, [node("dt", "", "Type"), el("dd", {}, [pill(key.key_type_id, "info")])]),
        el("div", { class: "record-item" }, [node("dt", "", "Label"), el("dd", { text: key.label || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Status"), el("dd", {}, [statusBadge(key.status)])]),
        el("div", { class: "record-item" }, [node("dt", "", "Fingerprint"), el("dd", { class: "mono", text: key.fingerprint || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Permissions"), el("dd", { text: (key.permissions || []).join(", ") || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Scopes"), el("dd", { text: (key.scopes || []).join(", ") || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Tags"), el("dd", { text: (key.tags || []).join(", ") || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Notes"), el("dd", { text: key.notes || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Usage"), el("dd", { text: `${key.usage_count || 0}${key.max_usage ? ` / ${key.max_usage}` : ""}` })]),
        el("div", { class: "record-item" }, [node("dt", "", "Expires"), el("dd", { text: key.expires_at ? formatDate(key.expires_at) : "Never" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Expiration policy"), el("dd", {}, [pill(key.expiration_preset || (key.expires_at ? "custom" : "never"), "info")])]),
      ]),
      el("div", { class: "card-actions" }, [
        button("Edit key", () => editKey(key), "btn btn-primary btn-sm"),
        button("Edit expiration", () => editKeyExpiration(key), "btn btn-ghost btn-sm"),
      ]),
      node("h3", "", "Recent key events"),
      data.events && data.events.length
        ? el("div", { class: "stack" }, data.events.map((event) =>
            el("div", { class: "result-row" }, [
              node("strong", "", event.event),
              node("span", "mono", event.actor_email || "system"),
              node("span", "muted", formatRelative(event.created_at)),
            ])
          ))
        : node("p", "muted", "No events recorded."),
      data.devices && data.devices.length ? node("p", "muted", `${data.devices.length} device binding(s) recorded.`) : null,
    ]));
  } catch (error) { fail(error, "Key details unavailable."); }
}

function editKeyExpiration(key) {
  const hasExpiry = Boolean(key.expires_at);
  const type = state.keyTypes.find((item) => item.id === key.key_type_id);
  const policy = type?.expiration_policy || {};
  const options = allowedExpiryOptions(type);
  const initial = hasExpiry
    ? "keep"
    : (options.some(([value]) => value === "never") ? "never" : (options[0]?.[0] || "never"));
  const preset = select([
    ...(hasExpiry ? [["keep", "Keep current expiration"]] : []),
    ...options,
  ], { value: initial, "aria-label": "Key expiration preset" });
  const custom = input({
    type: "datetime-local",
    value: localDateTimeValue(key.expires_at),
    "aria-label": "Custom key expiration",
  });
  const customField = field("Custom expiration (local time)", custom, "Converted to UTC. Must be in the future.");
  customField.classList.add("span-2");
  const preview = el("div", { class: "expiration-preview span-2", "aria-live": "polite" });
  bindExpiryControls(preset, customField, custom, preview);
  const submit = button("Save expiration", async () => {
    if (preset.value === "keep") {
      closeSecretDialog();
      return toast("Expiration unchanged.", { type: "info" });
    }
    const error = expiryValidation(preset.value, custom.value);
    if (error) return toast(error, { type: "error" });
    setButtonBusy(submit, true, "Saving…");
    try {
      await api.updateUniversalKey(key.id, {
        expiration_preset: preset.value,
        expires_at: preset.value === "custom" ? new Date(custom.value).toISOString() : null,
      });
      toast("Key expiration updated.", { type: "success" });
      closeSecretDialog();
      showView("keys", { reload: true });
    } catch (error) { fail(error, "Key expiration update failed."); }
    finally { setButtonBusy(submit, false); }
  }, "btn btn-primary btn-sm");
  showPanel(`Edit expiration · ${key.key_hint}`, el("div", { class: "form-grid" }, [
    el("div", { class: "span-2 expiry-current" }, [
      node("span", "muted", "Current status"),
      statusBadge(key.status),
      node("span", "", key.expires_at ? `Expires ${formatDate(key.expires_at)}` : "Never expires"),
    ]),
    field("Expiration preset", preset, type ? `Allowed by ${type.name}: ${(policy.presets || ["never"]).join(", ")}.` : ""),
    customField,
    preview,
    el("p", { class: "muted span-2", text: "Changing key expiry never changes the project expiry. Expired keys remain rejected by authentication." }),
    el("div", { class: "span-2 card-actions" }, [submit]),
  ]));
}

async function editKey(key) {
  if (!state.keyTypes.length) await loadKeyFilters();
  const type = state.keyTypes.find((item) => item.id === key.key_type_id);
  const options = allowedExpiryOptions(type);
  const label = input({ value: key.label || "", maxlength: "100" });
  const permissions = input({ value: (key.permissions || []).join(", ") });
  const scopes = input({ value: (key.scopes || []).join(", ") });
  const tags = input({ value: (key.tags || []).join(", ") });
  const notes = el("textarea", { rows: "2" });
  notes.value = key.notes || "";
  const maxUsage = input({ type: "number", min: "1", value: key.max_usage ? String(key.max_usage) : "" });
  const preset = select([
    ...(key.expires_at ? [["keep", "Keep current expiration"]] : []),
    ...options,
  ], {
    value: key.expires_at
      ? "keep"
      : (options.some(([value]) => value === "never") ? "never" : (options[0]?.[0] || "never")),
    "aria-label": "Key expiration preset",
  });
  const custom = input({ type: "datetime-local", value: localDateTimeValue(key.expires_at), "aria-label": "Custom key expiration" });
  const customField = field("Custom expiration (local time)", custom, "Converted to UTC. Must be in the future.");
  customField.classList.add("span-2");
  const preview = el("div", { class: "expiration-preview span-2", "aria-live": "polite" });
  bindExpiryControls(preset, customField, custom, preview);
  const submit = button("Save key", async () => {
    const error = preset.value === "keep" ? null : expiryValidation(preset.value, custom.value);
    if (error) return toast(error, { type: "error" });
    setButtonBusy(submit, true, "Saving…");
    const split = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
    try {
      const body = {
        label: label.value.trim(),
        permissions: split(permissions.value),
        scopes: split(scopes.value),
        tags: split(tags.value),
        notes: notes.value,
        max_usage: maxUsage.value ? Number(maxUsage.value) : null,
      };
      if (preset.value !== "keep") {
        body.expiration_preset = preset.value;
        body.expires_at = preset.value === "custom" ? new Date(custom.value).toISOString() : null;
      }
      await api.updateUniversalKey(key.id, body);
      toast("Key updated.", { type: "success" });
      closeSecretDialog();
      showView("keys", { reload: true });
    } catch (error) { fail(error, "Key update failed."); }
    finally { setButtonBusy(submit, false); }
  }, "btn btn-primary btn-sm");
  showPanel(`Edit key · ${key.key_hint}`, el("div", { class: "form-grid" }, [
    el("div", { class: "span-2 expiry-current" }, [
      node("span", "muted", "Current status"),
      statusBadge(key.status),
      node("span", "", key.expires_at ? `Expires ${formatDate(key.expires_at)}` : "Never expires"),
    ]),
    field("Label", label),
    field("Max usage", maxUsage, "Empty means unlimited."),
    field("Permissions", permissions, type ? `Allowed for ${type.name}: ${(type.permissions || []).join(", ") || "none"}.` : "Comma-separated permission ids."),
    field("Scopes", scopes, type ? `Allowed scopes: ${(type.scopes || []).join(", ") || "none"}.` : "Comma-separated scopes."),
    field("Tags", tags, "Comma-separated."),
    field("Notes", notes),
    field("Expiration preset", preset, type ? `Allowed by ${type.name}: ${((type.expiration_policy || {}).presets || options.map(([value]) => value)).join(", ")}.` : ""),
    customField,
    preview,
    el("p", { class: "muted span-2", text: "Save changes without losing unrelated key settings. Expiration options follow the key type policy; the project expiry is not changed." }),
    el("div", { class: "span-2 card-actions" }, [submit]),
  ]));
}

async function activateKey(key) {
  try { await api.activateUniversalKey(key.id); toast("Key activated.", { type: "success" }); showView("keys", { reload: true }); }
  catch (error) { fail(error, "Activation failed."); }
}

async function deactivateKey(key) {
  const confirmed = await confirmAction({ title: "Deactivate key?", message: `Key ${key.key_hint} will stop authenticating and its active devices will be deactivated.`, confirmLabel: "Deactivate", tone: "warn" });
  if (!confirmed.confirmed) return;
  try { await api.deactivateUniversalKey(key.id, "Deactivated from platform console"); toast("Key deactivated.", { type: "success" }); showView("keys", { reload: true }); }
  catch (error) { fail(error, "Deactivation failed."); }
}

async function revokeKey(key) {
  const confirmed = await confirmAction({ title: "Permanently revoke key?", message: "The live key row and all device bindings are destroyed. Audit and key-event history remain. This cannot be undone.", confirmLabel: "Revoke permanently", tone: "danger", requireText: "REVOKE", textLabel: "Type REVOKE to confirm" });
  if (!confirmed.confirmed) return;
  try { await api.revokeUniversalKey(key.id, "Permanent revocation from platform console"); toast("Key permanently revoked.", { type: "success" }); showView("keys", { reload: true }); }
  catch (error) { fail(error, "Revocation failed."); }
}

async function regenerateKey(key) {
  const confirmed = await confirmAction({ title: "Regenerate key?", message: "A new secret will replace the current digest. Existing device bindings are revoked. The old plaintext stops working immediately.", confirmLabel: "Regenerate", tone: "warn" });
  if (!confirmed.confirmed) return;
  try {
    const result = await api.regenerateUniversalKey(key.id);
    showSecretReveal("Replacement key generated", [result.key], "The previous secret is now invalid.");
    showView("keys", { reload: true });
  } catch (error) { fail(error, "Regeneration failed."); }
}

async function renderKeyTypes(root) {
  const data = await api.keyTypes({ include_inactive: "true" });
  state.keyTypes = data.key_types || [];
  root.append(
    pageHeader("Key type catalogue", "Built-in types cover common credential classes; project types are declarative and isolated.", [
      button("Open type builder", () => showView("builder"), "btn btn-primary btn-sm"),
    ]),
    table(
      ["Type", "Scope", "Permissions", "Format", "Device policy", "Active", "Keys", "Actions"],
      state.keyTypes.map((type) => el("tr", {}, [
        cell(el("div", {}, [el("strong", { text: type.name }), el("div", { class: "mono", text: type.id })])),
        cell(type.project_id ? type.project_name : "Global"),
        cell((type.permissions || []).join(", ")),
        cell(el("span", { class: "mono", text: `${type.key_prefix || ""}-${"*".repeat(4)} · ${type.key_format?.groups || 4}×${type.key_format?.group_length || 4}` })),
        cell(type.device_policy?.binds ? `Bound · max ${type.device_policy.max_devices || 0}` : "Unbound"),
        cell(statusBadge(type.is_active ? "active" : "inactive")),
        cell(String(type.key_count || 0)),
        cell(type.is_builtin
          ? pill("Built-in", "info")
          : button(type.is_active ? "Disable" : "Enable", () => toggleKeyType(type), "btn btn-ghost btn-sm"), "align-right"),
      ])),
      "No key types"
    )
  );
}

async function toggleKeyType(type) {
  try {
    await api.updateKeyType(type.id, { is_active: !type.is_active });
    toast(`Key type ${type.is_active ? "disabled" : "enabled"}.`, { type: "success" });
    showView("key-types", { reload: true });
  } catch (error) { fail(error, "Key type update failed."); }
}

async function renderBuilder(root) {
  const projects = await api.universalProjects({ page_size: 100 });
  const typeData = await api.keyTypes({ include_inactive: "true" });
  const permissions = typeData.permissions || [];
  const project = select([["", "Global (all projects)"]].concat((projects.items || []).map((p) => [p.id, p.project_name])));
  const id = input({ placeholder: "custom_reader", maxlength: "50", pattern: "[a-z][a-z0-9_]{2,49}" });
  const name = input({ placeholder: "My custom key", maxlength: "80" });
  const description = input({ placeholder: "What this type is for" });
  const prefix = input({ value: "CUS", maxlength: "12" });
  const groups = input({ type: "number", min: "1", max: "8", value: "4" });
  const length = input({ type: "number", min: "3", max: "10", value: "4" });
  const separator = select([["-", "Hyphen"], ["_", "Underscore"], ["", "None"]]);
  const scopes = input({ placeholder: "reports.read, devices.connect" });
  const deviceBinds = el("input", { type: "checkbox" });
  const maxDevices = input({ type: "number", min: "0", max: "1000", value: "0" });
  const rate = input({ type: "number", min: "1", max: "100000", value: "60" });
  const metadata = el("textarea", { rows: "2", placeholder: '{"fields":{"tenant":{"type":"string","required":true}}}' });
  const checks = el("div", { class: "check-grid" }, permissions.map((permission) =>
    el("label", { class: "check" }, [
      el("input", { type: "checkbox", value: permission.id, checked: permission.id === "read" ? "checked" : null }),
      el("span", { text: permission.name }),
    ])
  ));
  const form = el("form", { class: "form-grid" }, [
    field("Type ID", id, "3-50 lowercase letters, digits, underscores."),
    field("Display name", name),
    field("Project scope", project, "Global types can be used by any project."),
    field("Key prefix", prefix),
    field("Groups", groups),
    field("Group length", length),
    field("Separator", separator),
    field("Description", description),
    field("Allowed scopes", scopes, "Comma-separated. Empty allows no scopes."),
    field("Metadata schema JSON", metadata),
    el("div", { class: "span-2" }, [node("span", "muted", "Permissions (a key may only narrow these)"), checks]),
    el("div", { class: "form-grid span-2" }, [
      field("Bind devices", el("label", { class: "check" }, [deviceBinds, el("span", { text: "Require a device identifier" })])),
      field("Max devices per project", maxDevices),
      field("Rate limit / minute", rate),
    ]),
    el("div", { class: "span-2 card-actions" }, [
      el("button", { class: "btn btn-primary", type: "submit", text: "Create key type" }),
    ]),
  ]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    setButtonBusy(submit, true, "Creating…");
    try {
      const selectedPermissions = $$("input[type=checkbox]:checked", checks).map((node) => node.value);
      const result = await api.createKeyType({
        id: id.value.trim(),
        name: name.value.trim(),
        description: description.value.trim(),
        project_id: project.value || null,
        key_prefix: prefix.value.trim().toUpperCase(),
        key_format: {
          groups: Number(groups.value),
          group_length: Number(length.value),
          separator: separator.value,
        },
        permissions: selectedPermissions,
        scopes: scopes.value.split(",").map((x) => x.trim()).filter(Boolean),
        device_policy: {
          binds: deviceBinds.checked,
          required: deviceBinds.checked,
          max_devices: Number(maxDevices.value) || 0,
        },
        usage_limits: { rate_per_minute: Number(rate.value) || 60, max_usage: null },
        metadata_schema: readJson(metadata.value, "Metadata schema"),
      });
      toast(`Key type '${result.key_type.name}' created.`, { type: "success" });
      form.reset();
      showView("key-types", { reload: true });
    } catch (error) { fail(error, "Key type creation failed."); }
    finally { setButtonBusy(submit, false); }
  });
  root.append(
    pageHeader("Custom key type builder", "Declarative policy only: format, permissions, scopes, device limits, rate limits, and metadata rules. No code is executed."),
    card("Type definition", form)
  );
}

async function renderDevices(root) {
  const data = await api.universalDevices({ page_size: 100 });
  const devices = data.items || [];
  root.append(
    pageHeader("Device registry", "Bindings are created only after a key with device policy authenticates. A device identifier alone is never authentication."),
    table(
      ["Device", "Project", "Key", "Platform", "Status", "Last seen", "Created", "Actions"],
      devices.map((device) => el("tr", {}, [
        cell(el("div", {}, [el("strong", { text: device.device_name || device.device_id }), el("div", { class: "mono", text: device.device_id })])),
        cell(device.project_name),
        cell(el("span", { class: "mono", text: device.key_hint || "—" })),
        cell(device.platform || device.device_model || "—"),
        cell(statusBadge(device.status)),
        cell(formatRelative(device.last_seen_at)),
        cell(formatDate(device.created_at, { time: false })),
        cell(el("div", { class: "row-actions" }, [
          device.status !== "revoked" ? button("Revoke", () => revokeDevice(device), "btn btn-danger btn-sm") : null,
          button("Reset", () => resetDevice(device)),
        ]), "align-right"),
      ])),
      "No device bindings"
    )
  );
}

async function revokeDevice(device) {
  const confirmed = await confirmAction({ title: "Revoke device?", message: "This binding will be permanently blocked. Use Reset to allow the device to re-register.", confirmLabel: "Revoke device", tone: "danger" });
  if (!confirmed.confirmed) return;
  try { await api.revokeUniversalDevice(device.id, "Revoked from platform console"); toast("Device revoked.", { type: "success" }); showView("devices", { reload: true }); }
  catch (error) { fail(error, "Device revocation failed."); }
}

async function resetDevice(device) {
  const confirmed = await confirmAction({ title: "Reset device binding?", message: "The binding row is deleted so the device can register again on a future authentication.", confirmLabel: "Reset binding", tone: "warn" });
  if (!confirmed.confirmed) return;
  try { await api.resetUniversalDevice(device.id); toast("Device binding reset.", { type: "success" }); showView("devices", { reload: true }); }
  catch (error) { fail(error, "Device reset failed."); }
}

async function renderApiUsage(root) {
  const data = await api.apiUsage({ page: 1, page_size: 50 });
  const summary = data.summary_24h || {};
  root.append(
    pageHeader("API usage", "Every access attempt is recorded with a hashed IP, endpoint, status, and latency. Key material is never recorded."),
    el("div", { class: "stat-strip" }, [
      stat("Requests 24h", summary.requests),
      stat("Failed 24h", summary.failed, summary.failed ? "is-bad" : ""),
      stat("Average latency", summary.avg_latency_ms, ""),
      stat("Tracked calls", data.pagination?.total || 0),
    ]),
    table(
      ["Time", "Project", "Key", "Type", "Endpoint", "Method", "Status", "Latency"],
      (data.items || []).map((item) => el("tr", {}, [
        cell(formatDate(item.created_at)),
        cell(item.project_name || "Unattributed"),
        cell(el("span", { class: "mono", text: item.key_hint || "—" })),
        cell(item.key_type_id || "—"),
        cell(el("span", { class: "mono", text: item.endpoint })),
        cell(item.method),
        cell(pill(String(item.status_code), statusCodeTone(item.status_code))),
        cell(`${item.latency_ms} ms`),
      ])),
      "No API calls recorded"
    )
  );
}

function statusCodeTone(code) {
  if (code >= 200 && code < 300) return "good";
  if (code >= 400 && code < 500) return "warn";
  return "bad";
}

async function renderWebhooks(root) {
  const [data, projectData] = await Promise.all([
    api.webhooks(),
    api.universalProjects({ page_size: 100 }),
  ]);
  state.projects = projectData.items || [];
  const hooks = data.webhooks || [];
  root.append(
    pageHeader("Webhook delivery", "Events are queued without secrets and signed with a derived per-webhook secret. Retries are handled by automation.", [
      button("Create webhook", () => createWebhookForm(data.events || []), "btn btn-primary btn-sm"),
    ])
  );
  root.append(table(
    ["Webhook", "Project", "URL", "Events", "Status", "Deliveries", "Actions"],
    hooks.map((hook) => el("tr", {}, [
      cell(el("div", {}, [el("strong", { text: hook.name }), el("div", { class: "mono", text: hook.id })])),
      cell(hook.project_name),
      cell(el("span", { class: "mono truncate", text: hook.url })),
      cell((hook.events || []).length ? `${hook.events.length} events` : "—"),
      cell(statusBadge(hook.is_active ? "active" : "inactive")),
      cell(`${hook.delivered_count || 0} delivered · ${hook.failed_count || 0} failed`),
      cell(el("div", { class: "row-actions" }, [
        button("Test", () => testWebhook(hook)),
        button("Deliveries", () => showDeliveries(hook)),
        button("Rotate secret", () => rotateWebhook(hook)),
        button("Delete", () => deleteWebhook(hook), "btn btn-danger btn-sm"),
      ]), "align-right"),
    ])),
    "No webhooks configured"
  ));
}

function createWebhookForm(events) {
  const projects = state.projects || [];
  const project = select([["", "Choose a project"]].concat(projects.map((p) => [p.id, p.project_name])));
  const name = input({ placeholder: "Order events", maxlength: "100" });
  const url = input({ placeholder: "https://example.com/hooks/nexora", maxlength: "2048" });
  const checks = el("div", { class: "check-grid" }, events.slice(0, 12).map((event) =>
    el("label", { class: "check" }, [
      el("input", { type: "checkbox", value: event, checked: event === "key.generated" ? "checked" : null }),
      el("span", { text: event }),
    ])
  ));
  showPanel("Create webhook", el("div", { class: "stack" }, [
    field("Project", project),
    field("Name", name),
    field("HTTPS endpoint", url, "Private-network targets are rejected in production."),
    el("div", {}, [node("span", "muted", "Events"), checks]),
    button("Create and reveal secret", async () => {
      const selected = Array.from(checks.querySelectorAll("input:checked")).map((node) => node.value);
      try {
        const result = await api.createWebhook({
          project_id: project.value,
          name: name.value.trim(),
          url: url.value.trim(),
          events: selected,
        });
        showSecretReveal("Webhook signing secret", [{ key: result.secret, key_hint: "whsec_…", fingerprint: result.webhook.id }], result.secret_notice);
        showView("webhooks", { reload: true });
      } catch (error) { fail(error, "Webhook creation failed."); }
    }, "btn btn-primary btn-sm"),
  ]));
}

async function testWebhook(hook) {
  try {
    const result = await api.testWebhook(hook.id);
    toast(result.message || "Test delivery queued.", { type: "success" });
  } catch (error) { fail(error, "Webhook test failed."); }
}

async function showDeliveries(hook) {
  try {
    const data = await api.webhookDeliveries(hook.id);
    showPanel(`Deliveries · ${hook.name}`, table(
      ["Event", "Status", "Attempts", "Response", "Created", "Delivered"],
      (data.deliveries || []).map((delivery) => el("tr", {}, [
        cell(delivery.event_type),
        cell(statusBadge(delivery.status)),
        cell(String(delivery.attempts)),
        cell(delivery.response_code || delivery.error || "—"),
        cell(formatDate(delivery.created_at)),
        cell(delivery.delivered_at ? formatDate(delivery.delivered_at) : "—"),
      ])),
      "No deliveries"
    ));
  } catch (error) { fail(error, "Delivery history unavailable."); }
}

async function rotateWebhook(hook) {
  const confirmed = await confirmAction({ title: "Rotate signing secret?", message: "The previous secret stops signing immediately. Update the receiver after this rotation.", confirmLabel: "Rotate secret", tone: "warn" });
  if (!confirmed.confirmed) return;
  try {
    const result = await api.rotateWebhook(hook.id);
    showSecretReveal("Rotated webhook secret", [{ key: result.secret, key_hint: `v${result.secret_version}`, fingerprint: hook.id }], result.secret_notice);
  } catch (error) { fail(error, "Rotation failed."); }
}

async function deleteWebhook(hook) {
  const confirmed = await confirmAction({ title: "Delete webhook?", message: "The webhook and its delivery history are deleted.", confirmLabel: "Delete webhook", tone: "danger" });
  if (!confirmed.confirmed) return;
  try { await api.deleteWebhook(hook.id); toast("Webhook deleted.", { type: "success" }); showView("webhooks", { reload: true }); }
  catch (error) { fail(error, "Deletion failed."); }
}

async function renderNotifications(root) {
  const data = await api.notifications({ limit: "100" });
  const items = data.notifications || [];
  root.append(
    pageHeader("Notification center", "Server-generated expiry warnings, delivery failures, and security signals.", [
      button("Mark all read", async () => {
        try { await api.markAllNotifications(); toast("Notifications marked read.", { type: "success" }); showView("notifications", { reload: true }); }
        catch (error) { fail(error, "Update failed."); }
      }, "btn btn-ghost btn-sm"),
    ]),
    el("p", { class: "muted", text: `${data.unread || 0} unread notification(s).` }),
    items.length
      ? el("div", { class: "stack" }, items.map((item) => el("article", { class: "platform-card glass" }, [
          el("div", { class: "card-head" }, [
            el("div", {}, [node("h3", "", item.title), node("p", "", item.body)]),
            el("div", { class: "card-actions" }, [
              pill(item.severity, statusTone(item.severity)),
              item.read_at ? pill("read", "") : button("Mark read", async () => {
                try { await api.markNotification(item.id); showView("notifications", { reload: true }); }
                catch (error) { fail(error, "Update failed."); }
              }),
            ]),
          ]),
          node("p", "mono", `${item.type} · ${formatDate(item.created_at)}`),
        ])))
      : emptyState("No notifications", "Expiry and security notices will appear here.", "◔")
  );
}

async function renderSearch(root) {
  const query = input({ placeholder: "Search projects, key hints, fingerprints, types, devices, audit…", maxlength: "100" });
  const results = el("div", { class: "search-results" });
  const submit = async () => {
    clearNode(results);
    if (query.value.trim().length < 2) return toast("Enter at least two characters.", { type: "error" });
    results.append(emptyState("Searching…", "Querying the authorized inventory.", "⌕"));
    try {
      const data = await api.universalSearch(query.value.trim());
      clearNode(results);
      const groups = data.groups || {};
      const renderGroup = (title, items, mapper) => el("section", { class: "result-group" }, [
        el("h3", { text: `${title} · ${items.length}` }),
        ...(items.length ? items.map(mapper) : [el("div", { class: "empty", text: `No ${title.toLowerCase()} matched.` })]),
      ]);
      results.append(
        renderGroup("Projects", groups.projects || [], (item) => el("div", { class: "result-row" }, [
          el("strong", { text: item.project_name }),
          el("span", { class: "mono", text: `${item.project_type} · ${item.status}` }),
          button("Open", () => showView("projects"), "btn btn-ghost btn-sm"),
        ])),
        renderGroup("Keys", groups.keys || [], (item) => el("div", { class: "result-row" }, [
          el("strong", { class: "mono", text: item.key_hint }),
          el("span", { class: "mono", text: `${item.project_name} · ${item.status}` }),
          button("Details", () => showKeyDetails(item.id), "btn btn-ghost btn-sm"),
        ])),
        renderGroup("Key types", groups.key_types || [], (item) => el("div", { class: "result-row" }, [
          el("strong", { text: item.name }),
          el("span", { class: "mono", text: `${item.id} · ${item.is_builtin ? "built-in" : "custom"}` }),
          button("Open", () => showView("key-types"), "btn btn-ghost btn-sm"),
        ])),
        renderGroup("Devices", groups.devices || [], (item) => el("div", { class: "result-row" }, [
          el("strong", { text: item.device_name || item.device_id }),
          el("span", { class: "mono", text: `${item.project_name} · ${item.status}` }),
          button("Open", () => showView("devices"), "btn btn-ghost btn-sm"),
        ])),
        renderGroup("Audit", groups.audit || [], (item) => el("div", { class: "result-row" }, [
          el("strong", { text: item.action }),
          el("span", { class: "mono", text: `${item.actor_email || "system"} · ${formatDate(item.created_at)}` }),
          pill(item.outcome, statusTone(item.outcome)),
        ]))
      );
    } catch (error) {
      clearNode(results);
      results.append(emptyState("Search unavailable", error.message || "Request failed."));
      fail(error, "Search failed.");
    }
  };
  query.addEventListener("keydown", (event) => { if (event.key === "Enter") submit(); });
  root.append(
    pageHeader("Global search", "Search is scoped to the authenticated platform inventory and never returns key material."),
    el("div", { class: "page-toolbar" }, [el("div", { class: "filter-row" }, [query, button("Search", submit, "btn btn-primary btn-sm")])]),
    results
  );
  query.focus();
}

async function renderAudit(root) {
  const data = await api.logs({ page: 1, page_size: 100 });
  const logs = data.items || data.logs || [];
  root.append(
    pageHeader("Audit trail", "Existing server-side audit history, including universal project, key, device, webhook, and role operations."),
    table(
      ["Time", "Action", "Outcome", "Actor", "Target", "IP"],
      logs.map((item) => el("tr", {}, [
        cell(formatDate(item.created_at)),
        cell(el("span", { class: "mono", text: item.action })),
        cell(pill(item.outcome, statusTone(item.outcome))),
        cell(item.actor_email || "system"),
        cell(`${item.target_type || "—"} ${item.target_id || ""}`.trim()),
        cell(item.ip_address || "—"),
      ])),
      "No audit events"
    )
  );
}

async function renderImportExport(root) {
  await loadKeyFilters();
  const project = select([["", "Choose a project"]].concat(state.projects.map((p) => [p.id, p.project_name])));
  const type = select(state.keyTypes.map((t) => [t.id, t.name]));
  const textarea = el("textarea", { rows: "8", placeholder: "Paste one key per line. Plaintext is sent once for hashing and is never returned." });
  const previewBox = el("div", { class: "stack" });
  const importPayload = () => ({
    project_id: project.value,
    key_type_id: type.value,
    keys: textarea.value.split("\n").map((key) => key.trim()).filter(Boolean).map((key) => ({ key })),
  });

  const exportProject = select(state.projects.map((p) => [p.id, p.project_name]));
  const projectPreview = el("div", { class: "stack" });
  const bundleFile = el("input", { type: "file", accept: ".json,.zip,application/json,application/zip" });
  const importMode = select([
    ["keep_existing", "Keep existing (merge non-conflicting children)"],
    ["import_as_new", "Import as a new project (rename)"],
    ["replace_existing", "Replace existing project (destructive)"],
  ]);
  const newName = input({ placeholder: "Optional imported project name", maxlength: "200" });
  const importPreview = el("div", { class: "stack" });
  const confirmName = input({ placeholder: "Type the existing project name", maxlength: "200", autocomplete: "off" });
  const replaceFlag = el("input", { type: "checkbox" });
  const replaceBlock = el("div", { class: "stack", hidden: "hidden" }, [
    el("p", { class: "muted", text: "Replacement permanently deletes the existing project and its keys, devices, webhooks and imported metadata in the same transaction. Nothing is overwritten silently." }),
    field("Type the existing project name", confirmName),
    el("label", { class: "field" }, [replaceFlag, el("span", { text: " I understand this permanently replaces the existing project." })]),
  ]);
  let previewAction = "";
  const buildForm = () => {
    const form = new FormData();
    if (bundleFile.files && bundleFile.files[0]) form.append("file", bundleFile.files[0]);
    form.append("mode", importMode.value);
    if (newName.value.trim()) form.append("project_name", newName.value.trim());
    return form;
  };

  root.append(
    pageHeader("Import and export", "Project bundles are validated, transactional and secret-free. Key metadata exports never contain plaintext or HMAC digests."),
    el("div", { class: "platform-grid" }, [
      card("Project backup", [
        el("p", { class: "muted", text: "Export the project, custom key types, key metadata, devices and webhook configuration with an integrity manifest." }),
        field("Project", exportProject),
        el("div", { class: "card-actions" }, [
          button("Preview", async () => {
            clearNode(projectPreview);
            if (!exportProject.value) return toast("Choose a project.", { type: "error" });
            try {
              const preview = await api.universalProjectExportPreview(exportProject.value);
              projectPreview.append(
                el("p", { class: "muted", text: `${preview.project.project_name} · ${preview.counts.keys} keys · ${preview.counts.devices} devices · ${preview.counts.webhooks} webhooks` }),
                el("p", { class: "muted", text: `Secrets exposed: ${preview.security.secrets_exposed ? "YES" : "No"} · key material included: ${preview.security.key_material_included ? "YES" : "No"}` }),
                ...(preview.warnings || []).map((warning) => node("p", "muted", `• ${warning}`))
              );
            } catch (error) { fail(error, "Export preview failed."); }
          }, "btn btn-ghost btn-sm"),
          button("Download JSON", () => {
            if (!exportProject.value) return toast("Choose a project.", { type: "error" });
            window.location.assign(api.universalProjectExportUrl(exportProject.value, "json"));
          }, "btn btn-primary btn-sm"),
          button("Download ZIP", () => {
            if (!exportProject.value) return toast("Choose a project.", { type: "error" });
            window.location.assign(api.universalProjectExportUrl(exportProject.value, "zip"));
          }, "btn btn-ghost btn-sm"),
        ]),
        projectPreview,
      ]),
      card("Project restore", [
        el("p", { class: "muted", text: "Upload a .json or .zip bundle. Nothing is written until you review the preview and confirm." }),
        field("Bundle file", bundleFile),
        field("Conflict policy", importMode),
        field("New project name (optional)", newName),
        el("div", { class: "card-actions" }, [
          button("Validate bundle", async () => {
            clearNode(importPreview);
            previewAction = "";
            if (!bundleFile.files || !bundleFile.files[0]) return toast("Choose a bundle file.", { type: "error" });
            try {
              const result = await api.universalProjectImportPreview(buildForm());
              previewAction = result.action;
              replaceBlock.hidden = result.action !== "replace";
              importPreview.append(
                el("p", { class: "muted", text: `${result.project.project_name} · schema ${result.bundle.version} · action: ${result.action}` }),
                el("p", { class: "muted", text: `${result.counts.key_types} key types · ${result.counts.keys} keys · ${result.counts.devices} devices · ${result.counts.webhooks} webhooks` }),
                result.existing_project ? el("p", { class: "muted", text: `Existing project: ${result.existing_project.project_name}` }) : null,
                ...(result.warnings || []).map((warning) => node("p", "muted", `• ${warning}`)),
                ...(result.key_types || []).map((item) => node("p", "muted", `Key type ${item.id}: ${item.permissions.join(", ") || "no permissions"}`))
              );
            } catch (error) { fail(error, "Bundle validation failed."); }
          }, "btn btn-ghost btn-sm"),
          button("Import bundle", async () => {
            if (!previewAction) return toast("Validate the bundle first.", { type: "error" });
            if (previewAction === "replace" && (!replaceFlag.checked || !confirmName.value.trim())) {
              return toast("Confirm replacement with the existing project name.", { type: "error" });
            }
            const form = buildForm();
            form.append("confirm", confirmName.value.trim());
            form.append("replace_confirmed", replaceFlag.checked ? "true" : "false");
            try {
              const result = await api.universalProjectImport(form);
              toast(`Imported '${result.project.project_name}' (${result.counts.keys_imported} key metadata records).`, { type: "success" });
              showView("projects", { reload: true });
            } catch (error) { fail(error, "Project import failed."); }
          }, "btn btn-primary btn-sm"),
        ]),
        replaceBlock,
        importPreview,
      ]),
    ]),
    card("Key metadata export", [
      el("p", { class: "muted", text: "Download key inventory for reconciliation. Plaintext and HMAC digests are excluded." }),
      el("div", { class: "card-actions" }, [
        link("JSON export", api.universalKeysExportUrl({ format: "json" }), "btn btn-primary btn-sm"),
        link("CSV export", api.universalKeysExportUrl({ format: "csv" }), "btn btn-ghost btn-sm"),
      ]),
    ]),
    card("Import keys", el("div", { class: "stack" }, [
      el("div", { class: "form-grid" }, [
        field("Project", project),
        field("Key type", type),
        field("Keys (one per line)", textarea, "Up to the configured bulk limit. All rows commit in one transaction."),
      ]),
      el("div", { class: "card-actions" }, [
        button("Preview validation", async () => {
          clearNode(previewBox);
          try {
            const result = await api.universalImportPreview(importPayload());
            previewBox.append(
              el("p", { class: "muted", text: `${result.valid_count} valid · ${result.invalid_count} invalid · secrets echoed: ${result.secrets_echoed}` }),
              result.invalid.length ? el("div", { class: "stack" }, result.invalid.slice(0, 10).map((row) => node("p", "muted", `Row ${row.index + 1}: ${row.reason}`))) : node("p", "muted", "All rows passed validation.")
            );
          } catch (error) { fail(error, "Preview failed."); }
        }, "btn btn-ghost btn-sm"),
        button("Import validated keys", async () => {
          const confirmed = await confirmAction({ title: "Import keys?", message: "All rows must be valid or the transaction rolls back. Plaintext is hashed immediately and not returned.", confirmLabel: "Import", tone: "warn" });
          if (!confirmed.confirmed) return;
          try {
            const result = await api.universalImport(importPayload());
            toast(`${result.imported} key(s) imported.`, { type: "success" });
            textarea.value = "";
            clearNode(previewBox);
            showView("keys", { reload: true });
          } catch (error) { fail(error, "Import failed."); }
        }, "btn btn-primary btn-sm"),
      ]),
      previewBox,
    ]))
  );
}

async function renderImportedKeys(root) {
  let projectData = state.projects;
  if (!projectData.length) {
    projectData = (await api.universalProjects({ page_size: 100 })).items || [];
    state.projects = projectData;
  }
  const selected = new Set();
  const projectFilter = select([["", "All projects"]].concat(projectData.map((p) => [p.id, p.project_name])));
  const statusFilter = select([["", "All imported records"], ["imported", "Imported"], ["replaced", "Replaced"]]);
  const search = input({ placeholder: "Search label, hint, source id or type" });
  const allowDuplicate = el("input", { type: "checkbox" });
  const selectAll = el("input", { type: "checkbox", "aria-label": "Select all filtered imported keys" });
  const countLabel = node("span", "muted", "0 selected");
  const host = el("div", { class: "stack" });
  let rows = [];

  const selectedIds = () => Array.from(selected);
  const updateCount = () => {
    countLabel.textContent = `${selected.size} selected`;
    previewBtn.disabled = selected.size === 0;
  };

  const load = async () => {
    const data = await api.importedKeys({
      page_size: 100,
      project_id: projectFilter.value || undefined,
      status: statusFilter.value || undefined,
      search: search.value.trim() || undefined,
    });
    rows = data.items || [];
    for (const id of Array.from(selected)) {
      if (!rows.some((row) => row.id === id)) selected.delete(id);
    }
    selectAll.checked = false;
    renderRows();
    updateCount();
  };

  const preview = async () => {
    if (!selected.size) return toast("Select at least one imported key.", { type: "error" });
    setButtonBusy(previewBtn, true, "Validating…");
    try {
      const result = await api.importedKeyReplacementPreview({
        record_ids: selectedIds(),
        allow_duplicate: allowDuplicate.checked,
      });
      const plans = result.records || [];
      const batch = result.batch || {};
      const generate = button("Confirm and generate", async () => {
        const confirmation = await confirmAction({
          title: `Generate ${batch.eligible || 0} replacement key(s)?`,
          message: "Each replacement is a new credential. Original plaintext keys are never restored, and revoked or expired credentials are not reactivated.",
          confirmLabel: "Generate replacements",
          tone: "primary",
        });
        if (!confirmation.confirmed) return;
        setButtonBusy(generate, true, "Generating…");
        try {
          const generated = await api.generateImportedKeyReplacements({
            record_ids: selectedIds(),
            allow_duplicate: allowDuplicate.checked,
            confirm: true,
          });
          showReplacementResult(generated);
        } catch (error) {
          fail(error, "Replacement generation failed.");
          setButtonBusy(generate, false);
        }
      }, "btn btn-primary btn-sm");
      generate.disabled = !batch.eligible;
      showPanel("Replacement preview", el("div", { class: "stack" }, [
        el("p", { class: "muted", text: `${batch.requested || 0} requested · ${batch.eligible || 0} eligible · ${batch.ineligible || 0} blocked. Plaintext is not shown in preview.` }),
        table(
          ["Record", "Project", "Eligibility", "Warnings"],
          plans.map((plan) => el("tr", {}, [
            cell(el("div", {}, [el("strong", { text: plan.label || plan.record_id }), el("div", { class: "mono", text: plan.record_id })])),
            cell(plan.project_name || "—"),
            cell(plan.eligible ? pill("eligible", "good") : el("div", {}, [pill("blocked", "warn"), el("div", { class: "muted", text: plan.reason || "" })])),
            cell((plan.warnings || []).join(" ") || "—"),
          ])),
          "No records in preview"
        ),
        el("div", { class: "card-actions" }, [generate]),
      ]));
    } catch (error) { fail(error, "Replacement preview failed."); }
    finally { setButtonBusy(previewBtn, false); updateCount(); }
  };

  const showReplacementResult = (result) => {
    const keys = result.keys || [];
    const failures = result.failures || [];
    const reveal = keys.length ? button("Reveal one-time keys", () => {
      showSecretReveal("Replacement keys — one-time reveal", keys, `${keys.length} new credential(s). Copy or download them now.`);
    }, "btn btn-primary btn-sm") : null;
    showPanel("Replacement result", el("div", { class: "stack" }, [
      el("div", { class: "stat-strip" }, [
        stat("Generated", result.generated_count || 0, "is-good"),
        stat("Failed", result.failed_count || 0, result.failed_count ? "is-bad" : ""),
      ]),
      el("p", { class: "secret-warning", text: "Plaintext is available only in this result view. It is never stored or logged. Reveal it before closing." }),
      keys.length ? table(
        ["Record", "Project", "Key hint", "Warnings"],
        keys.map((key) => el("tr", {}, [
          cell(key.imported_record_id || "—"),
          cell(key.project_id || "—"),
          cell(el("span", { class: "mono", text: key.key_hint || "" })),
          cell((key.warnings || []).join(" ") || "—"),
        ])),
        "No successful replacements"
      ) : null,
      failures.length ? table(
        ["Record", "Code", "Failure"],
        failures.map((item) => el("tr", {}, [
          cell(item.record_id || "—"),
          cell(el("span", { class: "mono", text: item.code || "" })),
          cell(item.message || ""),
        ])),
        "No failures"
      ) : null,
      el("div", { class: "card-actions" }, [
        reveal,
        button("Reload inventory", async () => { closeSecretDialog(); selected.clear(); await load(); }, "btn btn-ghost btn-sm"),
      ].filter(Boolean)),
    ]));
  };

  const history = async (row) => {
    try {
      const data = await api.importedKeyHistory(row.id);
      showPanel(`Replacement history — ${row.label || row.key_hint || row.id}`, el("div", { class: "stack" }, [
        el("p", { class: "muted", text: "History contains key hints and metadata only. Plaintext is never stored." }),
        table(
          ["Generated", "Key hint", "Status", "By", "Warnings"],
          (data.history || []).map((item) => el("tr", {}, [
            cell(formatDate(item.created_at)),
            cell(el("span", { class: "mono", text: item.key_hint || "—" })),
            cell(pill(item.key_status || "unknown", statusTone(item.key_status))),
            cell(item.generated_by_email || "—"),
            cell((item.warnings || []).join(" ") || "—"),
          ])),
          "No replacements yet"
        ),
      ]));
    } catch (error) { fail(error, "Replacement history failed to load."); }
  };

  function renderRows() {
    clearNode(host);
    host.append(table(
      ["", "Imported record", "Project", "Original status", "Replacements", "Imported", ""],
      rows.map((row) => {
        const box = el("input", { type: "checkbox", "aria-label": `Select ${row.label || row.id}` });
        box.checked = selected.has(row.id);
        box.addEventListener("change", () => {
          if (box.checked) selected.add(row.id); else selected.delete(row.id);
          updateCount();
        });
        return el("tr", {}, [
          cell(box),
          cell(el("div", {}, [el("strong", { text: row.label || "Imported key" }), el("div", { class: "mono", text: `${row.key_hint || ""} · ${row.id}` })])),
          cell(row.project_name || "—"),
          cell(pill(row.status || "imported", statusTone(row.status))),
          cell(String(row.replacement_count || 0)),
          cell(formatDate(row.imported_at)),
          cell(button("History", () => history(row)), "align-right"),
        ]);
      }),
      "No imported key metadata"
    ));
  }

  selectAll.addEventListener("change", async () => {
    if (!selectAll.checked) {
      selected.clear();
      renderRows();
      updateCount();
      return;
    }
    selectAll.disabled = true;
    try {
      let page = 1;
      let total = rows.length;
      while (page <= 100) {
        const data = await api.importedKeys({
          page,
          page_size: 100,
          project_id: projectFilter.value || undefined,
          status: statusFilter.value || undefined,
          search: search.value.trim() || undefined,
        });
        for (const row of data.items || []) selected.add(row.id);
        total = data.pagination?.total || 0;
        if (page * 100 >= total) break;
        page += 1;
      }
      renderRows();
      updateCount();
    } catch (error) {
      fail(error, "Could not select all filtered records.");
    } finally {
      selectAll.disabled = false;
    }
  });
  projectFilter.addEventListener("change", () => load().catch((error) => fail(error)));
  statusFilter.addEventListener("change", () => load().catch((error) => fail(error)));
  search.addEventListener("keydown", (event) => { if (event.key === "Enter") load().catch((error) => fail(error)); });
  const previewBtn = button("Preview selected", preview, "btn btn-primary btn-sm");
  previewBtn.disabled = true;

  root.append(
    pageHeader("Imported key recovery", "Generate independent replacement credentials from imported metadata. Original plaintext and revoked credentials are never restored.", []),
    el("div", { class: "toolbar" }, [
      field("Project", projectFilter),
      field("Status", statusFilter),
      field("Search", search),
      el("label", { class: "field-inline" }, [allowDuplicate, node("span", "", "Allow duplicate replacement")]),
      el("label", { class: "field-inline" }, [selectAll, node("span", "", "Select all filtered")]),
      countLabel,
      previewBtn,
    ]),
    host
  );
  await load();
}

async function renderAutomation(root) {
  const [data, scheduler] = await Promise.all([api.automationRuns(), api.schedulerStatus()]);
  const runButton = button("Run automation now", async () => {
    setButtonBusy(runButton, true, "Running…");
    try {
      const result = await api.automationRun();
      toast(`Automation ${result.status}.`, { type: "success" });
      showView("automation", { reload: true });
    } catch (error) { fail(error, "Automation run failed."); }
    finally { setButtonBusy(runButton, false); }
  }, "btn btn-primary btn-sm");
  const counts = scheduler.counts || {};
  root.append(
    pageHeader("Automation", "Idempotent expiry sweeps, key retention cleanup, device inactivity, notifications, and webhook retries.", [runButton]),
    el("div", { class: "platform-grid" }, [
      card("Scheduler worker", [
        el("p", { class: "muted", text: "The web process never runs an infinite loop. A separate worker executes these jobs under a PostgreSQL advisory lock." }),
        el("dl", { class: "record-grid" }, [
          el("div", { class: "record-item" }, [node("dt", "", "Jobs"), el("dd", { text: (scheduler.jobs || []).join(", ") })]),
          el("div", { class: "record-item" }, [node("dt", "", "Configured interval"), el("dd", { text: `${scheduler.interval_seconds}s` })]),
          el("div", { class: "record-item" }, [node("dt", "", "Batch size"), el("dd", { text: String(scheduler.batch_size) })]),
          el("div", { class: "record-item" }, [node("dt", "", "Last success"), el("dd", { text: scheduler.last_success ? formatDate(scheduler.last_success) : "Never" })]),
        ]),
        el("p", { class: "muted", text: "CLI: python -m backend.cli universal-scheduler-run --once" }),
      ]),
      card("Queued work", el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Projects"), el("dd", { text: String(counts.projects || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Keys"), el("dd", { text: String(counts.keys || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Webhook queue"), el("dd", { text: String(counts.webhook_queue || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Projects due for purge"), el("dd", { text: String(counts.projects_due || 0) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Unread notifications"), el("dd", { text: String(counts.unread_notifications || 0) })]),
      ])),
    ]),
    table(
      ["Started", "Job", "Status", "Finished", "Details", "Error"],
      (data.runs || []).map((run) => el("tr", {}, [
        cell(formatDate(run.started_at)),
        cell(el("span", { class: "mono", text: run.job })),
        cell(statusBadge(run.status)),
        cell(run.finished_at ? formatDate(run.finished_at) : "—"),
        cell(el("span", { class: "mono truncate", text: JSON.stringify(run.details || {}) })),
        cell(run.status === "failed" ? run.error : "—"),
      ])),
      "No automation runs"
    ),
    el("p", { class: "muted", text: "Deployment, cron, retry policy and monitoring are documented in docs/SCHEDULER.md." })
  );
}

async function renderScheduler(root) {
  const [scheduler, runs] = await Promise.all([api.schedulerStatus(), api.automationRuns()]);
  const counts = scheduler.counts || {};
  const runNow = button("Run automation now", async () => {
    setButtonBusy(runNow, true, "Running…");
    try {
      const result = await api.automationRun();
      toast(`Automation ${result.status}.`, { type: "success" });
      showView("scheduler", { reload: true });
    } catch (error) { fail(error, "Automation run failed."); }
    finally { setButtonBusy(runNow, false); }
  }, "btn btn-primary btn-sm");
  const workerTone = scheduler.worker_status === "running" ? "good" : scheduler.worker_status === "stale" ? "warn" : scheduler.worker_status === "unknown" ? "bad" : "info";
  root.append(
    pageHeader("Scheduler worker", "One advisory-locked worker executes bounded, idempotent jobs. No infinite loop runs inside the Flask web process.", [runNow]),
    el("div", { class: "stat-strip" }, [
      stat("Worker", scheduler.worker_status || "unknown", workerTone === "good" ? "is-good" : workerTone === "warn" ? "is-warn" : ""),
      stat("Webhook queue", counts.webhook_queue || 0),
      stat("Dead deliveries", counts.webhook_dead || 0),
      stat("Projects due", counts.projects_due || 0),
    ]),
    el("div", { class: "platform-grid" }, [
      card("Worker state", el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Status"), el("dd", {}, [pill(scheduler.worker_status || "unknown", workerTone)])]),
        el("div", { class: "record-item" }, [node("dt", "", "Worker id"), el("dd", { class: "mono", text: scheduler.worker_id || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Advisory lock"), el("dd", {}, [pill(scheduler.lock_held ? "held" : "available", scheduler.lock_held ? "warn" : "good")])]),
        el("div", { class: "record-item" }, [node("dt", "", "Interval"), el("dd", { text: `${scheduler.interval_seconds}s` })]),
        el("div", { class: "record-item" }, [node("dt", "", "Batch size"), el("dd", { text: String(scheduler.batch_size) })]),
        el("div", { class: "record-item" }, [node("dt", "", "Last run"), el("dd", { text: scheduler.last_run ? formatDate(scheduler.last_run) : "Never" })]),
      ])),
      card("Success and failure", el("div", { class: "stack" }, [
        el("dl", { class: "record-grid" }, [
          el("div", { class: "record-item" }, [node("dt", "", "Last success"), el("dd", { text: scheduler.last_success ? formatDate(scheduler.last_success) : "Never" })]),
          el("div", { class: "record-item" }, [node("dt", "", "Last failure"), el("dd", { text: scheduler.last_failure ? formatDate(scheduler.last_failure) : "None" })]),
        ]),
        scheduler.last_failure_reason ? el("p", { class: "secret-warning", text: scheduler.last_failure_reason }) : el("p", { class: "muted", text: "No recorded failure in the current run history." }),
      ])),
    ]),
    card("Per-job health", table(
      ["Job", "Last run", "Status", "Duration", "Processed", "Failed", "Worker", "Error"],
      (scheduler.per_job || []).map((job) => el("tr", {}, [
        cell(el("span", { class: "mono", text: job.job })),
        cell(job.last_run ? formatDate(job.last_run) : "Never"),
        cell(statusBadge(job.status)),
        cell(`${job.duration_ms || 0} ms`),
        cell(String(job.processed || 0)),
        cell(String(job.failed || 0)),
        cell(job.worker_id || "—"),
        cell(job.error || "—"),
      ])),
      "No scheduler runs"
    )),
    card("Recent runs", table(
      ["Started", "Job", "Status", "Finished", "Details", "Error"],
      (runs.runs || []).slice(0, 10).map((run) => el("tr", {}, [
        cell(formatDate(run.started_at)),
        cell(el("span", { class: "mono", text: run.job })),
        cell(statusBadge(run.status)),
        cell(run.finished_at ? formatDate(run.finished_at) : "—"),
        cell(el("span", { class: "mono truncate", text: JSON.stringify(run.details || {}) })),
        cell(run.error || "—"),
      ])),
      "No runs"
    )),
    el("p", { class: "muted", text: "CLI: python -m backend.cli universal-scheduler-status · dry-run: universal-scheduler-run --once --dry-run" })
  );
}

function roleEditor(role, templates, projects = []) {
  const editing = Boolean(role && role.custom);
  const selected = new Set(role?.permissions || []);
  const name = input({ value: role?.name || "", placeholder: "Role name", maxlength: "80" });
  const roleId = input({ value: role?.id || "", placeholder: "lowercase_id", maxlength: "50" });
  if (editing) roleId.disabled = true;
  const description = el("textarea", { rows: "2", placeholder: "What is this role for?" });
  description.value = role?.description || "";
  const scope = select([["", "Global role"]].concat(projects.map((project) => [project.id, `Project: ${project.project_name}`])), { value: role?.project_id || "" });
  if (editing) scope.disabled = true;
  const template = select([["", "Start blank"], ...(templates || []).map((item) => [item.id, item.name])]);
  const grid = el("div", { class: "form-grid span-2" });
  const allowedPermissions = () => (state.rolePermissions || []).filter((permission) => !scope.value || permission.project_allowed);
  const drawPermissions = () => {
    clearNode(grid);
    const allowed = new Set(allowedPermissions().map((permission) => permission.id));
    for (const permissionId of Array.from(selected)) {
      if (!allowed.has(permissionId)) selected.delete(permissionId);
    }
    allowedPermissions().forEach((permission) => {
      const box = el("input", { type: "checkbox", checked: selected.has(permission.id) });
      box.addEventListener("change", () => {
        if (box.checked) selected.add(permission.id);
        else selected.delete(permission.id);
      });
      grid.append(el("label", { class: "field" }, [
        box,
        el("span", { text: ` ${permission.id} — ${permission.description || ""}` }),
        el("small", { text: `risk: ${permission.risk || "standard"}${permission.project_allowed ? " · project-safe" : " · global only"}` }),
      ]));
    });
  };
  drawPermissions();
  scope.addEventListener("change", drawPermissions);
  template.addEventListener("change", () => {
    const item = (templates || []).find((candidate) => candidate.id === template.value);
    if (!item) return;
    const allowed = new Set(allowedPermissions().map((permission) => permission.id));
    selected.clear();
    (item.permissions || []).forEach((permission) => { if (allowed.has(permission)) selected.add(permission); });
    drawPermissions();
    if (!name.value.trim()) name.value = item.name;
    if (!description.value.trim()) description.value = item.description || "";
  });
  const submit = button(editing ? "Save role" : "Create role", async () => {
    if (!name.value.trim()) return toast("Enter a role name.", { type: "error" });
    if (!selected.size) return toast("Select at least one permission.", { type: "error" });
    const body = {
      name: name.value.trim(),
      description: description.value.trim(),
      permissions: Array.from(selected),
    };
    if (!editing) {
      body.id = roleId.value.trim() || undefined;
      if (scope.value) body.project_id = scope.value;
    }
    setButtonBusy(submit, true, editing ? "Saving…" : "Creating…");
    try {
      if (editing) await api.updateRole(role.id, body);
      else await api.createRole(body);
      toast(editing ? "Role updated." : "Role created.", { type: "success" });
      closeSecretDialog();
      showView("roles", { reload: true });
    } catch (error) { fail(error, "Role save failed."); }
    finally { setButtonBusy(submit, false); }
  }, "btn btn-primary btn-sm");
  showPanel(editing ? `Edit role '${role.name}'` : "Create a custom role", el("div", { class: "stack" }, [
    el("p", { class: "muted", text: "Global roles use the full platform catalogue. Project roles may only use project-scoped capabilities and can never grant global administration or delete-all authority." }),
    el("div", { class: "form-grid" }, [
      field("Role name", name),
      field("Role id", roleId, editing ? "Role ids are immutable." : "3-50 lowercase letters, digits or underscores."),
      field("Description", description),
      field("Scope", scope, editing ? "Role scope is immutable." : "Select a project for a project-scoped role."),
      field("Template (new roles)", template),
    ]),
    grid,
    el("div", { class: "card-actions" }, [submit]),
  ]));
}

async function manageProjectRole(role, projects) {
  const [adminData, assignmentData] = await Promise.all([
    api.universalAdmins(),
    api.projectRoleAssignments(role.project_id),
  ]);
  const admins = (adminData.admins || []).filter((admin) => admin.is_active);
  const assignments = (assignmentData.assignments || []).filter(
    (item) => item.role_id === role.id
  );
  const assignedIds = new Set(assignments.map((item) => item.admin_id));
  const available = admins.filter((admin) => !assignedIds.has(admin.id));
  const adminSelect = select(available.map((admin) => [admin.id, `${admin.full_name || admin.email} · ${admin.email}`]));
  const assign = button("Assign project role", async () => {
    if (!adminSelect.value) return toast("Select an administrator.", { type: "error" });
    setButtonBusy(assign, true, "Assigning…");
    try {
      await api.assignProjectRole(role.project_id, { admin_id: adminSelect.value, role_id: role.id });
      toast("Project role assigned.", { type: "success" });
      await manageProjectRole(role, projects);
    } catch (error) { fail(error, "Project role assignment failed."); }
    finally { setButtonBusy(assign, false); }
  }, "btn btn-primary btn-sm");
  assign.disabled = !available.length;
  showPanel(`Project role — ${role.name}`, el("div", { class: "stack" }, [
    el("p", { class: "muted", text: "Project roles add permissions only inside this project. They never change the administrator's global platform role." }),
    available.length ? el("div", { class: "form-grid" }, [field("Administrator", adminSelect)]) : el("p", { class: "muted", text: "Every active administrator already has this role." }),
    el("div", { class: "card-actions" }, [assign]),
    table(
      ["Administrator", "Permissions", "Assigned", ""],
      assignments.map((item) => el("tr", {}, [
        cell(el("div", {}, [el("strong", { text: item.admin_name || item.admin_email }), el("div", { class: "mono", text: item.admin_email })])),
        cell(String((item.permissions || []).length)),
        cell(formatDate(item.created_at)),
        cell(button("Remove", async () => {
          const confirmed = await confirmAction({ title: "Remove project role?", message: `${item.admin_email} will lose this project role immediately.`, confirmLabel: "Remove role", tone: "danger" });
          if (!confirmed.confirmed) return;
          try {
            await api.removeProjectRoleAssignment(role.project_id, item.id);
            toast("Project role removed.", { type: "success" });
            await manageProjectRole(role, projects);
          } catch (error) { fail(error, "Project role removal failed."); }
        }, "btn btn-danger btn-sm"), "align-right"),
      ])),
      "No assignments"
    ),
  ]));
}

async function renderRoles(root) {
  const [data, projectData] = await Promise.all([
    api.universalRoles(),
    api.universalProjects({ page_size: 100 }),
  ]);
  const roles = data.roles || [];
  const projects = projectData.items || [];
  const perms = data.permissions || [];
  state.rolePermissions = perms;
  const roleNames = roles.map((role) => role.id);
  root.append(
    pageHeader("Platform roles", "Built-in roles are immutable. Global and project-scoped custom roles are evaluated server-side on every request.", [
      button("New custom role", () => roleEditor(null, data.templates || [], projects), "btn btn-primary btn-sm"),
    ]),
    table(
      ["Role", "Id", "Scope", "Permissions", "Assigned", "Actions"],
      roles.map((role) => el("tr", {}, [
        cell(el("div", {}, [el("strong", { text: role.name }), el("div", { class: "muted", text: role.description || "" })])),
        cell(el("span", { class: "mono", text: role.id })),
        cell(el("div", {}, [pill(role.built_in ? "built-in" : role.scope || "global", role.project_id ? "info" : "good"), role.project_name ? el("div", { class: "muted", text: role.project_name }) : null])),
        cell(String((role.permissions || []).length)),
        cell(role.built_in ? "—" : role.project_id ? `${role.project_assigned_count || 0} project` : String(role.assigned_count || 0)),
        cell(el("div", { class: "row-actions" }, [
          role.custom ? button("Edit", () => roleEditor(role, data.templates || [], projects)) : null,
          role.custom && role.project_id ? button("Assign", () => manageProjectRole(role, projects)) : null,
          button("Clone", () => {
            const name = input({ placeholder: `Copy of ${role.name}`, maxlength: "80" });
            showPanel(`Clone '${role.name}'`, el("div", { class: "stack" }, [
              field("New role name", name),
              button("Create clone", async () => {
                try {
                  await api.cloneRole(role.id, { name: name.value.trim() || undefined });
                  toast("Role cloned.", { type: "success" });
                  closeSecretDialog();
                  showView("roles", { reload: true });
                } catch (error) { fail(error, "Role clone failed."); }
              }, "btn btn-primary btn-sm"),
            ]));
          }),
          role.custom ? button("Delete", async () => {
            const confirmed = await confirmAction({ title: `Delete '${role.name}'?`, message: "A role that is still assigned to an administrator cannot be deleted.", confirmLabel: "Delete role", tone: "danger" });
            if (!confirmed.confirmed) return;
            try {
              await api.deleteRole(role.id);
              toast("Role deleted.", { type: "success" });
              showView("roles", { reload: true });
            } catch (error) { fail(error, "Role deletion failed."); }
          }, "btn btn-danger btn-sm") : null,
        ]), "align-right"),
      ])),
      "No roles"
    ),
    card("Permission matrix", el("div", { class: "platform-table-wrap" }, [
      el("table", { class: "matrix" }, [
        el("thead", {}, [el("tr", {}, [el("th", { text: "Permission" }), ...roleNames.map((name) => el("th", { text: name }))])]),
        el("tbody", {}, perms.map((permission) => el("tr", {}, [
          el("td", {}, [el("strong", { text: permission.id }), el("div", { class: "muted", text: permission.description })]),
          ...roles.map((role) => el("td", {
            class: role.permissions.includes("*") || role.permissions.includes(permission.id) ? "yes" : "no",
            text: role.permissions.includes("*") || role.permissions.includes(permission.id) ? "✓" : "·",
          })),
        ]))),
      ]),
    ]))
  );
}

async function renderAdmins(root) {
  const [data, roleData] = await Promise.all([api.universalAdmins(), api.universalRoles()]);
  const admins = data.admins || [];
  const roles = roleData.roles || [];
  const builtins = roles.filter((role) => role.built_in && role.id !== "custom");
  const customs = roles.filter((role) => role.custom && !role.project_id);
  root.append(
    pageHeader("Administrators", "Only an Owner can assign the Owner role. A non-owner cannot grant permissions beyond their own authority. Every change is audited."),
    table(
      ["Administrator", "Base role", "Platform role", "Active sessions", "Last login", "Status", "Actions"],
      admins.map((admin) => {
        const roleSelect = select([
          ...builtins.map((role) => [role.id, role.name]),
          ...customs.map((role) => [role.id, `${role.name} (custom)`]),
        ], { value: admin.platform_role_id || admin.platform_role || "read_only" });
        return el("tr", {}, [
          cell(el("div", {}, [el("strong", { text: admin.full_name || admin.email }), el("div", { class: "mono", text: admin.email })])),
          cell(admin.role),
          cell(el("div", {}, [roleSelect, admin.platform_role_id ? el("div", { class: "mono", text: `custom: ${admin.platform_role_id}` }) : null])),
          cell(String(admin.active_sessions || 0)),
          cell(admin.last_login_at ? formatDate(admin.last_login_at) : "Never"),
          cell(statusBadge(admin.is_active ? "active" : "inactive")),
          cell(el("div", { class: "row-actions" }, [
            button("Apply role", async () => {
              const selected = roles.find((role) => role.id === roleSelect.value);
              const body = selected && selected.custom
                ? { platform_role: "custom", platform_role_id: selected.id }
                : { platform_role: roleSelect.value };
              try {
                await api.setAdminRole(admin.id, body);
                toast("Platform role updated.", { type: "success" });
                showView("admins", { reload: true });
              } catch (error) { fail(error, "Role update failed."); }
            }, "btn btn-primary btn-sm"),
            button("Revoke sessions", async () => {
              try { await api.revokeAdminSessions(admin.id); toast("Administrator sessions revoked.", { type: "success" }); showView("admins", { reload: true }); }
              catch (error) { fail(error, "Session revocation failed."); }
            }, "btn btn-danger btn-sm"),
          ]), "align-right"),
        ]);
      }),
      "No administrators"
    )
  );
}

async function renderHealth(root) {
  const [health, overview, security] = await Promise.allSettled([
    api.health(), api.universalOverview(), api.securityStatus(),
  ]);
  const healthData = health.status === "fulfilled" ? health.value : {};
  const overviewData = overview.status === "fulfilled" ? overview.value : {};
  const securityData = security.status === "fulfilled" ? security.value : {};
  root.append(
    pageHeader("Service health", "Live database and API readiness. Failed probes are shown as unavailable, never as healthy."),
    el("div", { class: "stat-strip" }, [
      stat("API", healthData.status === "ok" ? 1 : 0, healthData.status === "ok" ? "is-good" : "is-bad"),
      stat("Database", healthData.database === "connected" ? 1 : 0, healthData.database === "connected" ? "is-good" : "is-bad"),
      stat("Projects", overviewData.projects?.total || 0),
      stat("Keys", overviewData.keys?.total || 0),
    ]),
    el("div", { class: "platform-grid" }, [
      card("Readiness", el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "API status"), el("dd", {}, [pill(healthData.status || "unknown", statusTone(healthData.status))])]),
        el("div", { class: "record-item" }, [node("dt", "", "Database"), el("dd", {}, [pill(healthData.database || "unknown", statusTone(healthData.database))])]),
        el("div", { class: "record-item" }, [node("dt", "", "Version"), el("dd", { text: healthData.version || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Server time"), el("dd", { text: formatDate(overviewData.health?.server_time) })]),
      ])),
      card("Security posture", el("div", { class: "stack" }, [
        node("p", "muted", security.status === "fulfilled" ? "The authenticated security status endpoint responded." : "Security status endpoint is unavailable; no placeholder posture is shown."),
        el("pre", { class: "code-block", text: JSON.stringify(securityData, null, 2).slice(0, 1800) }),
      ])),
    ])
  );
}

let liteButton = null;

async function renderSettings(root) {
  const [prefs, session, system] = await Promise.all([
    api.notificationPreferences(),
    api.session(),
    api.settings(),
  ]);
  const preferences = prefs.preferences || {};
  const canEditSystem = state.user?.role === "owner";
  const keyPrefix = input({ value: system.key_prefix || "", maxlength: "12" });
  const defaultExpiry = input({ type: "number", min: "1", max: "3650", value: String(system.default_key_expiry_days ?? 30) });
  const maxBulk = input({ type: "number", min: "1", max: "5000", value: String(system.max_bulk_keys ?? 500) });
  const heartbeat = input({ type: "number", min: "30", max: "86400", value: String(system.heartbeat_interval_seconds ?? 300) });
  const rateLimits = input({ value: system.api_rate_limits || "60/min" });
  const timezone = input({ value: system.display_timezone || "UTC" });
  const autoDelete = input({ type: "number", min: "0", max: "3650", value: String(system.auto_delete_days ?? 0) });
  const maintenance = el("input", { type: "checkbox", checked: Boolean(system.maintenance_mode) });
  const saveSystem = button("Save system settings", async () => {
    setButtonBusy(saveSystem, true, "Saving…");
    try {
      await api.updateSettings({
        key_prefix: keyPrefix.value.trim(),
        default_key_expiry_days: Number(defaultExpiry.value),
        max_bulk_keys: Number(maxBulk.value),
        heartbeat_interval_seconds: Number(heartbeat.value),
        api_rate_limits: rateLimits.value.trim(),
        display_timezone: timezone.value.trim() || "UTC",
        maintenance_mode: maintenance.checked,
        auto_delete_days: Number(autoDelete.value),
      });
      toast("System settings updated.", { type: "success" });
      showView("settings", { reload: true });
    } catch (error) { fail(error, "System settings update failed."); }
    finally { setButtonBusy(saveSystem, false); }
  }, "btn btn-primary btn-sm");
  [keyPrefix, defaultExpiry, maxBulk, heartbeat, rateLimits, timezone, autoDelete, maintenance].forEach((control) => { control.disabled = !canEditSystem; });
  saveSystem.disabled = !canEditSystem;
  const checks = el("div", { class: "check-grid" }, Object.entries(preferences).map(([key, value]) =>
    el("label", { class: "check" }, [
      el("input", { type: "checkbox", checked: value ? "checked" : null, value: key }),
      el("span", { text: key.replace(/_/g, " ") }),
    ])
  ));
  const savePrefs = button("Save preferences", async () => {
    const body = {};
    checks.querySelectorAll("input").forEach((node) => { body[node.value] = node.checked; });
    setButtonBusy(savePrefs, true, "Saving…");
    try { await api.updateNotificationPreferences(body); toast("Preferences saved.", { type: "success" }); }
    catch (error) { fail(error, "Preferences update failed."); }
    finally { setButtonBusy(savePrefs, false); }
  }, "btn btn-primary btn-sm");
  liteButton = button(isLiteMode() ? "Disable reduced motion mode" : "Enable reduced motion mode", () => {
    const next = !isLiteMode();
    setLiteMode(next);
    liteButton.textContent = next ? "Disable reduced motion mode" : "Enable reduced motion mode";
    toast(next ? "Lite mode enabled." : "Full motion restored.", { type: "success" });
  }, "btn btn-ghost btn-sm");
  root.append(
    pageHeader("Platform preferences", "System configuration, notification routing, and display motion in one place."),
    el("div", { class: "platform-grid" }, [
      card("System configuration", el("div", { class: "form-grid" }, [
        field("Key prefix", keyPrefix),
        field("Default key expiry (days)", defaultExpiry),
        field("Maximum bulk keys", maxBulk),
        field("Heartbeat interval (seconds)", heartbeat),
        field("API rate limits", rateLimits),
        field("Display timezone", timezone),
        field("Auto-delete days", autoDelete, "0 disables scheduled project deletion."),
        el("label", { class: "check" }, [maintenance, el("span", { text: " Maintenance mode" })]),
        el("p", { class: "muted span-2", text: canEditSystem ? "System changes are audited server-side." : "Owner role required to change system configuration. Values are read-only for this account." }),
        el("div", { class: "span-2 card-actions" }, [saveSystem]),
      ])),
      card("Notification events", [checks, el("div", { class: "card-actions" }, [savePrefs])]),
      card("Session and display", [
        el("dl", { class: "record-grid" }, [
          el("div", { class: "record-item" }, [node("dt", "", "Signed in as"), el("dd", { text: session.user?.email || state.user?.email || "—" })]),
          el("div", { class: "record-item" }, [node("dt", "", "Role"), el("dd", { text: session.user?.role || "—" })]),
          el("div", { class: "record-item" }, [node("dt", "", "CSRF"), el("dd", { text: "Cookie mutations require the session CSRF token." })]),
        ]),
        el("div", { class: "card-actions" }, [liteButton]),
      ]),
    ])
  );
}

async function renderDocs(root) {
  const data = await api.universalOpenapi();
  const paths = Object.keys(data.paths || {});
  root.append(
    pageHeader("API documentation", "The OpenAPI surface is generated from the live Flask URL map. Management routes require a session; access authentication requires a universal key."),
    el("div", { class: "stat-strip" }, [
      stat("Documented API paths", paths.length),
      stat("API version", data.info?.version || "v1"),
    ]),
    card("Authenticate a universal key", el("pre", { class: "code-block", text:
`POST /api/v1/access/authenticate
Content-Type: application/json

{
  "key": "<universal-key>",
  "permission": "read",
  "project_id": "<optional-project-id>",
  "device_id": "<required only for bound key types>"
}

Response 200:
{
  "authenticated": true,
  "project": { "id": "...", "name": "..." },
  "key": { "type": "reader", "permissions": ["read"], "expires_at": null },
  "human_admin_session": false
}` })),
    card("Management paths", paths.length
      ? el("div", { class: "stack" }, paths.map((path) => el("div", { class: "result-row" }, [
          el("strong", { class: "mono", text: path }),
          node("span", "muted", "Session + platform permission required"),
          pill("OpenAPI", "info"),
        ])))
      : emptyState("No paths returned", "The OpenAPI endpoint returned no management paths.", "▤"))
  );
}

async function renderInstallations(root) {
  const [data, projectData] = await Promise.all([
    api.installations({ page: 1, page_size: 100 }),
    api.universalProjects({ page_size: 100 }),
  ]);
  state.projects = projectData.items || [];
  const items = data.items || [];
  root.append(
    pageHeader("Reader installations", "Reader App installations appear when a Reader Key is redeemed. Revocation invalidates the bound device credential immediately."),
    table(
      ["Project", "Redeemed key", "Device", "Status", "Activated", "Last heartbeat", "IP", "Actions"],
      items.map((installation) => el("tr", {}, [
        cell(installation.project_name),
        cell(el("span", { class: "mono", text: installation.key_hint || "—" })),
        cell(el("span", { class: "mono", text: installation.device_id || "—" })),
        cell(statusBadge(installation.status)),
        cell(formatDate(installation.activated_at)),
        cell(formatRelative(installation.last_seen_at)),
        cell(installation.ip_address || "—"),
        cell(el("div", { class: "row-actions" }, [
          installation.status === "active"
            ? button("Revoke", () => revokeInstallation(installation), "btn btn-danger btn-sm")
            : null,
        ]), "align-right"),
      ])),
      "No reader installations"
    )
  );
}

async function revokeInstallation(installation) {
  const confirmed = await confirmAction({
    title: `Revoke installation for '${installation.project_name}'?`,
    message: "The device token is invalidated immediately. The Reader App cannot reconnect or send heartbeats until a new key is redeemed.",
    confirmLabel: "Revoke installation",
    tone: "danger",
  });
  if (!confirmed.confirmed) return;
  try {
    await api.revokeInstallation(installation.id, "Revoked from unified platform console");
    toast("Installation revoked.", { type: "success" });
    showView("installations", { reload: true });
  } catch (error) { fail(error, "Installation revocation failed."); }
}

async function renderSecurity(root) {
  const data = await api.securityStatus();
  const auth = data.auth || {};
  const session = data.session || {};
  const apiSec = data.api_security || {};
  const database = data.database || {};
  const keySec = data.key_security || {};
  const row = (label, value) => el("div", { class: "record-item" }, [
    node("dt", "", label),
    el("dd", { text: String(value === undefined || value === null || value === "" ? "—" : value) }),
  ]);
  root.append(
    pageHeader("Security center", "Authentication, session, API, database, and key-storage posture. Secrets are never returned."),
    el("div", { class: "stat-strip" }, [
      stat("Database", database.status === "connected" ? 1 : 0, database.status === "connected" ? "is-good" : "is-bad"),
      stat("Account locked", auth.is_locked ? 1 : 0, auth.is_locked ? "is-warn" : ""),
      stat("Failed logins", auth.failed_login_attempts || 0, auth.failed_login_attempts ? "is-warn" : ""),
      stat("Rate limiting", apiSec.rate_limit_enabled ? 1 : 0, apiSec.rate_limit_enabled ? "is-good" : "is-warn"),
    ]),
    el("div", { class: "platform-grid" }, [
      card("Authentication", el("dl", { class: "record-grid" }, [
        row("Administrator", auth.admin_email),
        row("Role", auth.admin_role),
        row("Account active", auth.account_active ? "Yes" : "No"),
        row("Password algorithm", auth.password_algorithm),
        row("Public registration", auth.public_registration ? "Enabled" : "Disabled"),
      ])),
      card("Session", el("dl", { class: "record-grid" }, [
        row("Storage", session.session_storage),
        row("TTL", `${session.session_ttl_seconds}s`),
        row("Cookie", session.cookie_name),
        row("HttpOnly", session.cookie_httponly ? "Yes" : "No"),
        row("SameSite", session.cookie_samesite),
        row("CSRF", session.csrf_protection),
      ])),
      card("API security", el("dl", { class: "record-grid" }, [
        row("Rate limiting", apiSec.rate_limiting),
        row("Trust proxy", apiSec.trust_proxy ? "Yes" : "No"),
        row("CSP", apiSec.csp_policy),
        row("HSTS", apiSec.hsts_enabled ? "Enabled" : "Disabled"),
        row("Frames", apiSec.x_frame_options),
        row("MIME sniffing", apiSec.x_content_type_options),
      ])),
      card("Key storage", el("dl", { class: "record-grid" }, [
        row("Method", keySec.storage_method),
        row("Plaintext stored", keySec.plaintext_storage ? "Yes" : "No"),
        row("Domain separation", keySec.domain_separation),
        row("Bulk limit", keySec.max_bulk_limit),
        row("Database SSL", database.ssl_mode),
      ])),
    ]),
    card("Recent security events", table(
      ["Time", "Action", "Outcome", "IP"],
      (data.recent_events || []).map((event) => el("tr", {}, [
        cell(formatDate(event.created_at)),
        cell(el("span", { class: "mono", text: event.action })),
        cell(pill(event.outcome, statusTone(event.outcome))),
        cell(event.ip_address || "—"),
      ])),
      "No security events"
    ))
  );
}

/* ----------------------------------------------------------------- router */

const LOADERS = {
  overview: renderOverview,
  analytics: renderAnalytics,
  projects: renderProjects,
  keys: renderKeys,
  generator: renderGenerator,
  "key-types": renderKeyTypes,
  builder: renderBuilder,
  installations: renderInstallations,
  devices: renderDevices,
  "api-usage": renderApiUsage,
  webhooks: renderWebhooks,
  notifications: renderNotifications,
  search: renderSearch,
  audit: renderAudit,
  "import-export": renderImportExport,
  "imported-keys": renderImportedKeys,
  automation: renderAutomation,
  scheduler: renderScheduler,
  roles: renderRoles,
  admins: renderAdmins,
  security: renderSecurity,
  health: renderHealth,
  settings: renderSettings,
  docs: renderDocs,
};

let renderToken = 0;

export async function showView(name, { updateHash = true, reload = true } = {}) {
  const view = LOADERS[name] ? name : "overview";
  state.view = view;
  setMeta(view);
  $$(".nav-item").forEach((item) => {
    const active = item.dataset.view === view;
    item.classList.toggle("is-active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  if (updateHash) window.location.hash = view;
  closeMobileNav(false);
  clearError();
  if (!reload) return;

  const root = $("#platform-view");
  const token = ++renderToken;
  root.setAttribute("aria-busy", "true");
  const loader = LOADERS[view];
  try {
    const staging = el("div", { class: "stack" }, [
      emptyState("Loading authenticated data…", "The platform never substitutes placeholder records.", "◌"),
    ]);
    clearNode(root);
    root.append(staging);
    const fragment = document.createDocumentFragment();
    await loader(fragment);
    if (token !== renderToken) return;
    clearNode(root);
    root.append(fragment);
    announce(`${VIEWS[view][0]} loaded.`);
  } catch (error) {
    if (token !== renderToken) return;
    clearNode(root);
    root.append(emptyState("This view could not load", error.message || "The request failed.", "!"));
    showError(error.message || "The view could not load.");
    fail(error, "View failed to load.");
  } finally {
    root.removeAttribute("aria-busy");
    $("#refresh-btn").disabled = false;
  }
}

function closeMobileNav(restoreFocus = true) {
  document.body.classList.remove("nav-open");
  $("#sidebar")?.classList.remove("is-open");
  const toggle = $("#mobile-nav-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", "false");
  if (restoreFocus) toggle?.focus();
}

async function bootstrap() {
  initMotion();
  try {
    const session = await api.session();
    state.user = session.user;
    setCsrfToken(session.csrf_token);
    applyIdentity();
    $("#logout-btn").disabled = false;
  } catch (error) {
    redirectToLogin();
    return;
  }
  await loadHealth();
  const hash = window.location.hash.replace("#", "");
  await showView(hash || "overview", { updateHash: false });
  dismissBootScreen();
}

$("#logout-btn")?.addEventListener("click", async () => {
  const button = $("#logout-btn");
  if (button.disabled) return;
  setButtonBusy(button, true, "Signing out…");
  try {
    // Server revocation happens before the animation starts. The animation is
    // bounded and navigation still proceeds if it fails or motion is reduced.
    await secureSignOut({
      logout: api.logoutSafe,
      onRevoked: () => { setCsrfToken(""); state.user = null; },
      navigateTo: "/login",
    });
  } catch (error) {
    setButtonBusy(button, false);
    fail(error, "Sign out failed. Your session is still active.");
  }
});

$("#refresh-btn")?.addEventListener("click", () => {
  $("#refresh-btn").disabled = true;
  showView(state.view, { updateHash: false });
});
$("#quick-generate")?.addEventListener("click", () => showView("generator"));

$("#mobile-nav-toggle")?.addEventListener("click", () => {
  const open = $("#sidebar").classList.toggle("is-open");
  document.body.classList.toggle("nav-open", open);
  $("#mobile-nav-toggle").setAttribute("aria-expanded", String(open));
});
$("#sidebar-close")?.addEventListener("click", () => closeMobileNav());

$$(".nav-item").forEach((item) => {
  item.addEventListener("click", () => showView(item.dataset.view));
});

window.addEventListener("hashchange", () => {
  const hash = window.location.hash.replace("#", "");
  if (hash && hash !== state.view) showView(hash, { updateHash: false });
});

$$("[data-close-dialog]").forEach((button) => {
  button.addEventListener("click", closeSecretDialog);
});
$("#reveal-dialog")?.addEventListener("close", () => {
  const body = $("#reveal-body");
  if (body) clearNode(body);
});
$("#reveal-dialog")?.addEventListener("click", (event) => {
  if (event.target === $("#reveal-dialog")) closeSecretDialog();
});

bootstrap();
