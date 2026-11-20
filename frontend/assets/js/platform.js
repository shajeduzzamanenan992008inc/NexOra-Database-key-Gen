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

initMotion();

const VIEWS = {
  overview: ["Overview", "Universal platform pulse, key inventory, and live access telemetry.", "COMMAND / LIVE PULSE"],
  analytics: ["Analytics", "Generation volume, API traffic, failure rates, and key-type distribution.", "COMMAND / TELEMETRY"],
  projects: ["Projects", "Universal projects own multiple key types, expiration policy, and access scopes.", "KEY ENGINE / PROJECTS"],
  keys: ["Universal Keys", "One key store for reader, license, API, service, device, and custom credentials.", "KEY ENGINE / LIFECYCLE"],
  generator: ["Generate Keys", "Issue one or many keys. Plaintext is revealed once and never stored.", "KEY ENGINE / CSPRNG"],
  "key-types": ["Key Types", "Built-in types plus project-owned declarative custom types.", "KEY ENGINE / TYPE CATALOGUE"],
  builder: ["Type Builder", "Create a declarative key type. No user code is executed.", "KEY ENGINE / CUSTOM TYPE"],
  devices: ["Devices", "Device bindings created by type-isolated authentication.", "ACCESS / DEVICES"],
  "api-usage": ["API Usage", "Requests, failures, latency, and endpoint activity for universal access.", "ACCESS / ANALYTICS"],
  webhooks: ["Webhooks", "Signed event delivery with retry and permanent-failure handling.", "ACCESS / WEBHOOKS"],
  notifications: ["Notifications", "Expiry warnings, security signals, and delivery failures.", "GOVERNANCE / INBOX"],
  search: ["Global Search", "Search projects, key hints, fingerprints, types, devices, and audit events.", "COMMAND / SEARCH"],
  audit: ["Audit Trail", "Server-side administrative and security history.", "GOVERNANCE / AUDIT"],
  "import-export": ["Import / Export", "Bulk key metadata export and validated one-time key import.", "GOVERNANCE / DATA"],
  automation: ["Automation", "Expiry sweeps, retention cleanup, notifications, and webhook retries.", "GOVERNANCE / SCHEDULER"],
  roles: ["Roles", "Platform permission matrix. Authorization remains server-side.", "GOVERNANCE / RBAC"],
  admins: ["Administrators", "Platform role assignment and session revocation.", "GOVERNANCE / ADMINS"],
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
        node("p", "eyebrow", "UNIVERSAL KEY ENGINE / ONLINE"),
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
  const name = input({ placeholder: "Project name", maxlength: "200", "aria-label": "Project name" });
  const submit = button("Create project", async () => {
    if (!name.value.trim()) return toast("Enter a project name.", { type: "error" });
    setButtonBusy(submit, true, "Creating…");
    try {
      await api.createProject(name.value.trim());
      toast("Project created.", { type: "success" });
      closeSecretDialog();
      showView("projects", { reload: true });
    } catch (error) {
      fail(error, "Could not create the project.");
    } finally {
      setButtonBusy(submit, false);
    }
  }, "btn btn-primary btn-sm");
  return el("div", { class: "inline-form" }, [name, submit]);
}

async function renderProjects(root) {
  const data = await api.universalProjects({ page_size: 100 });
  state.projects = data.items || [];
  root.append(
    pageHeader("Universal projects", "Each project is an isolation boundary for keys, permissions, scope, and device policy.", [
      button("New project", () => showPanel("Create a project", newProjectForm()), "btn btn-primary btn-sm"),
    ]),
    table(
      ["Project", "Type", "Status", "Keys", "Devices", "Expires", "Created", "Actions"],
      state.projects.map((project) => el("tr", {}, [
        cell(el("div", {}, [
          el("strong", { text: project.project_name }),
          el("div", { class: "mono", text: project.id }),
        ])),
        cell(project.project_type || "database"),
        cell(statusBadge(project.effective_status || project.status)),
        cell(`${project.active_keys || 0} active / ${project.total_keys || 0} total`),
        cell(String(project.active_devices || 0)),
        cell(project.expires_at ? formatDate(project.expires_at) : "Never"),
        cell(formatDate(project.created_at, { time: false })),
        cell(el("div", { class: "row-actions" }, [
          button("Edit", () => editProject(project)),
          button("Clone", () => cloneProject(project)),
          project.effective_status !== "archived"
            ? button("Archive", () => archiveProject(project), "btn btn-danger btn-sm")
            : null,
        ]), "align-right"),
      ])),
      "No universal projects"
    )
  );
}

function editProject(project) {
  const name = input({ value: project.project_name, maxlength: "200" });
  const description = el("textarea", { rows: "3", text: project.description || "" });
  const type = select([
    ["database", "Database"], ["api", "API"], ["service", "Service"],
    ["webhook", "Webhook"], ["ai", "AI service"], ["other", "Other"],
  ], { value: project.project_type || "database" });
  const status = select([["active", "Active"], ["inactive", "Inactive"]], { value: project.status === "inactive" ? "inactive" : "active" });
  const preset = select([
    ["never", "Never"], ["7_days", "7 days"], ["30_days", "30 days"],
    ["90_days", "90 days"], ["1_year", "1 year"],
  ], { value: "never" });
  const form = el("form", { class: "form-grid" }, [
    field("Project name", name),
    field("Project type", type),
    field("Description", description),
    field("Status", status),
    field("Expiration from now", preset, "Choosing a preset replaces the current expiration."),
    el("div", { class: "span-2 card-actions" }, [
      button("Save changes", async () => {
        try {
          await api.updateUniversalProject(project.id, {
            project_name: name.value.trim(),
            description: description.value,
            project_type: type.value,
            status: status.value,
            expiration_preset: preset.value,
          });
          toast("Project updated.", { type: "success" });
          closeSecretDialog();
          showView("projects", { reload: true });
        } catch (error) { fail(error, "Project update failed."); }
      }, "btn btn-primary btn-sm"),
    ]),
  ]);
  form.addEventListener("submit", (event) => event.preventDefault());
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
  const label = input({ placeholder: "Optional batch label", maxlength: "100" });
  const quantity = input({ type: "number", min: "1", max: "500", value: "1" });
  const expiration = select([
    ["never", "Never"], ["1_day", "1 day"], ["7_days", "7 days"],
    ["30_days", "30 days"], ["90_days", "90 days"], ["1_year", "1 year"],
  ], { value: "never" });
  const expires = input({ type: "datetime-local", "aria-label": "Custom expiration" });
  const permissions = input({ placeholder: "optional, comma-separated: read, api_access" });
  const scopes = input({ placeholder: "optional, comma-separated scopes" });
  const tags = input({ placeholder: "optional tags, comma-separated" });
  const notes = el("textarea", { rows: "2", placeholder: "Optional internal notes (never secret)" });
  const metadata = el("textarea", { rows: "2", placeholder: "Optional metadata JSON object" });
  const maxUsage = input({ type: "number", min: "1", placeholder: "Unlimited" });
  const hint = el("p", { class: "muted", text: "Type permissions and scopes are ceilings. The server rejects anything broader." });

  const typeInfo = () => {
    const selected = state.keyTypes.find((item) => item.id === typeSelect.value);
    hint.textContent = selected
      ? `Permissions: ${(selected.permissions || []).join(", ") || "none"} · Scopes: ${(selected.scopes || []).join(", ") || "none"} · Device binding: ${selected.device_policy?.binds ? "required" : "optional"}`
      : "";
  };
  typeSelect.addEventListener("change", typeInfo);
  typeInfo();

  const form = el("form", { class: "form-grid" }, [
    field("Project", projectSelect),
    field("Key type", typeSelect),
    field("Batch label", label),
    field("Quantity", quantity, "Maximum is server-configured (default 500)."),
    field("Expiration preset", expiration),
    field("Custom expiration (UTC local input)", expires),
    el("div", { class: "span-2" }, [hint]),
    field("Permission subset", permissions),
    field("Scope subset", scopes),
    field("Tags", tags),
    field("Max usage", maxUsage),
    field("Notes", notes),
    field("Metadata JSON", metadata),
    el("div", { class: "span-2 card-actions" }, [
      el("button", { class: "btn btn-primary btn-glow", type: "submit", text: "Generate secure keys" }),
    ]),
  ]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    if (!projectSelect.value) return toast("Choose a project.", { type: "error" });
    setButtonBusy(submit, true, "Generating…");
    const split = (value) => String(value || "").split(",").map((x) => x.trim()).filter(Boolean);
    try {
      let expiresAt = null;
      if (expires.value) {
        const parsed = new Date(expires.value);
        if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
          throw new Error("Custom expiration must be in the future.");
        }
        expiresAt = parsed.toISOString();
      }
      const body = {
        project_id: projectSelect.value,
        key_type_id: typeSelect.value,
        quantity: Number(quantity.value) || 1,
        label: label.value.trim(),
        notes: notes.value,
        expiration_preset: expiresAt ? "custom" : expiration.value,
        expires_at: expiresAt,
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
      typeInfo();
    } catch (error) {
      fail(error, "Key generation failed.");
    } finally {
      setButtonBusy(submit, false);
    }
  });
  root.append(
    pageHeader("Generate universal keys", "CSPRNG generation with one-time reveal. Choose a type, then narrow its permissions and scopes."),
    card("Generation policy", form),
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
  const projectFilter = select([["", "All projects"]].concat(state.projects.map((p) => [p.id, p.project_name])));
  const typeFilter = select([["", "All types"]].concat(state.keyTypes.map((t) => [t.id, t.name])));
  const statusFilter = select([
    ["", "All statuses"], ["generated", "Generated"], ["active", "Active"],
    ["deactivated", "Deactivated"], ["expired", "Expired"],
  ]);
  const search = input({ placeholder: "Search hint, fingerprint, label, tags" });
  const toolbar = el("div", { class: "page-toolbar" }, [
    el("div", { class: "filter-row" }, [projectFilter, typeFilter, statusFilter, search]),
    el("div", { class: "card-actions" }, [
      button("Apply", () => loadKeysTable(tbody, { projectFilter, typeFilter, statusFilter, search }), "btn btn-primary btn-sm"),
    ]),
  ]);
  const tbody = el("tbody");
  const tableWrap = el("div", { class: "platform-table-wrap" }, [
    el("table", { class: "platform-table" }, [
      el("thead", {}, [el("tr", {}, ["Key hint", "Project", "Type", "Status", "Usage", "Expires", "Created", "Actions"].map((h) => el("th", { text: h })))]),
      tbody,
    ]),
  ]);
  root.append(
    pageHeader("Universal key inventory", "All key types live in one store. Filtering and lifecycle actions are server-authorized.", [
      button("Generate keys", () => showView("generator"), "btn btn-primary btn-sm"),
    ]),
    toolbar,
    tableWrap
  );
  await loadKeysTable(tbody, { projectFilter, typeFilter, statusFilter, search });
}

async function loadKeysTable(tbody, filters) {
  clearNode(tbody);
  tbody.append(el("tr", {}, [el("td", { colspan: "8" }, [emptyState("Loading keys…", "Reading the authenticated inventory.", "⌘")])]));
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
    clearNode(tbody);
    if (!state.keys.length) {
      tbody.append(el("tr", {}, [el("td", { colspan: "8" }, [tableEmpty("No keys match")])]));
      return;
    }
    state.keys.forEach((key) => {
      tbody.append(el("tr", {}, [
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
  } catch (error) {
    clearNode(tbody);
    tbody.append(el("tr", {}, [el("td", { colspan: "8" }, [emptyState("Keys unavailable", error.message || "Request failed.")])]));
    throw error;
  }
}

async function showKeyDetails(id) {
  try {
    const data = await api.universalKey(id);
    const key = data.key;
    showPanel(`Key ${key.key_hint}`, el("div", { class: "stack" }, [
      el("dl", { class: "record-grid" }, [
        el("div", { class: "record-item" }, [node("dt", "", "Project"), el("dd", { text: key.project_name })]),
        el("div", { class: "record-item" }, [node("dt", "", "Type"), el("dd", {}, [pill(key.key_type_id, "info")])]),
        el("div", { class: "record-item" }, [node("dt", "", "Status"), el("dd", {}, [statusBadge(key.status)])]),
        el("div", { class: "record-item" }, [node("dt", "", "Fingerprint"), el("dd", { class: "mono", text: key.fingerprint || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Permissions"), el("dd", { text: (key.permissions || []).join(", ") || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Scopes"), el("dd", { text: (key.scopes || []).join(", ") || "—" })]),
        el("div", { class: "record-item" }, [node("dt", "", "Usage"), el("dd", { text: `${key.usage_count || 0}${key.max_usage ? ` / ${key.max_usage}` : ""}` })]),
        el("div", { class: "record-item" }, [node("dt", "", "Expires"), el("dd", { text: key.expires_at ? formatDate(key.expires_at) : "Never" })]),
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
  root.append(
    pageHeader("Import and export", "Exports contain metadata only. Imports hash supplied plaintext and never echo it back."),
    el("div", { class: "platform-grid" }, [
      card("Export", [
        el("p", { class: "muted", text: "Download key metadata for inventory and audit reconciliation. Plaintext and HMAC digests are excluded." }),
        el("div", { class: "card-actions" }, [
          link("JSON export", api.universalKeysExportUrl({ format: "json" }), "btn btn-primary btn-sm"),
          link("CSV export", api.universalKeysExportUrl({ format: "csv" }), "btn btn-ghost btn-sm"),
        ]),
      ]),
      card("Export a project", [
        el("p", { class: "muted", text: "Project configuration, statistics, and custom type definitions without secrets." }),
        el("div", { class: "card-actions" }, state.projects.slice(0, 1).map((p) => button(`Export '${p.project_name}'`, async () => {
          try {
            const data = await api.universalProjectExport(p.id);
            downloadText(`${p.project_name.replace(/\W+/g, "-").toLowerCase()}-export.json`, JSON.stringify(data, null, 2), "application/json");
          } catch (error) { fail(error, "Project export failed."); }
        }, "btn btn-ghost btn-sm"))),
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

async function renderAutomation(root) {
  const data = await api.automationRuns();
  const runButton = button("Run automation now", async () => {
    setButtonBusy(runButton, true, "Running…");
    try {
      const result = await api.automationRun();
      toast(`Automation ${result.status}.`, { type: "success" });
      showView("automation", { reload: true });
    } catch (error) { fail(error, "Automation run failed."); }
    finally { setButtonBusy(runButton, false); }
  }, "btn btn-primary btn-sm");
  root.append(
    pageHeader("Automation", "Idempotent expiry sweeps, key retention cleanup, device inactivity, notifications, and webhook retries.", [runButton]),
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
    el("p", { class: "muted", text: "Scheduling is performed by a server cron/CLI worker calling this same automation service. This button executes one real run; it does not create a scheduler." })
  );
}

async function renderRoles(root) {
  const data = await api.universalRoles();
  const roles = data.roles || [];
  const perms = data.permissions || [];
  const roleNames = roles.map((role) => role.id);
  root.append(
    pageHeader("Platform roles", "Owner and administrator roles include the permissions shown. Custom roles are limited to their explicit subset."),
    el("div", { class: "platform-table-wrap" }, [
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
    ])
  );
}

async function renderAdmins(root) {
  const data = await api.universalAdmins();
  const admins = data.admins || [];
  root.append(
    pageHeader("Administrators", "Only an Owner can change platform roles. Role changes and session revocations are audited."),
    table(
      ["Administrator", "Base role", "Platform role", "Active sessions", "Last login", "Status", "Actions"],
      admins.map((admin) => {
        const roleSelect = select([
          ["owner", "Owner"], ["administrator", "Administrator"],
          ["project_manager", "Project manager"], ["key_manager", "Key manager"],
          ["read_only", "Read only"], ["custom", "Custom"],
        ], { value: admin.platform_role || "read_only" });
        return el("tr", {}, [
          cell(el("div", {}, [el("strong", { text: admin.full_name || admin.email }), el("div", { class: "mono", text: admin.email })])),
          cell(admin.role),
          cell(roleSelect),
          cell(String(admin.active_sessions || 0)),
          cell(admin.last_login_at ? formatDate(admin.last_login_at) : "Never"),
          cell(statusBadge(admin.is_active ? "active" : "inactive")),
          cell(el("div", { class: "row-actions" }, [
            button("Apply role", async () => {
              try {
                await api.setAdminRole(admin.id, {
                  platform_role: roleSelect.value,
                  platform_permissions: roleSelect.value === "custom" ? ["analytics:read", "project:read", "key:read"] : [],
                });
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
  const [prefs, session] = await Promise.all([
    api.notificationPreferences(),
    api.session(),
  ]);
  const preferences = prefs.preferences || {};
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
    pageHeader("Platform preferences", "Notification routing and display motion preferences. Security toggles require server configuration."),
    el("div", { class: "platform-grid" }, [
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

/* ----------------------------------------------------------------- router */

const LOADERS = {
  overview: renderOverview,
  analytics: renderAnalytics,
  projects: renderProjects,
  keys: renderKeys,
  generator: renderGenerator,
  "key-types": renderKeyTypes,
  builder: renderBuilder,
  devices: renderDevices,
  "api-usage": renderApiUsage,
  webhooks: renderWebhooks,
  notifications: renderNotifications,
  search: renderSearch,
  audit: renderAudit,
  "import-export": renderImportExport,
  automation: renderAutomation,
  roles: renderRoles,
  admins: renderAdmins,
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
  setButtonBusy(button, true, "Signing out…");
  try {
    await api.logoutSafe();
    window.location.replace("/login");
  } catch (error) {
    setButtonBusy(button, false);
    fail(error, "Sign out failed.");
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
