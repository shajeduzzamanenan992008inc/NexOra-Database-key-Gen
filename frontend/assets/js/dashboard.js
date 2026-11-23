import { api, ApiProblem, setCsrfToken } from "./api.js";
import {
  $,
  $$,
  announce,
  clearNode,
  closeDialog,
  confirmAction,
  copyText,
  downloadText,
  el,
  formatDate,
  formatRelative,
  openDialog,
  setButtonBusy,
  setToneMessage,
  skeletonRows,
  statusBadge,
  toast,
} from "./ui.js";
import {
  animateNumber,
  animateTableRows,
  dismissBootScreen,
  hideLeaveScreen,
  initMotion,
  isLiteMode,
  pulseElement,
  setLiteMode,
} from "./motion.js";
import { omegaAsset } from "./omega-assets.js";
import { renderOperations } from "./operations.js";
import { clearSensitiveState, runShutdownAnimation } from "./shutdown.js";
import { CustomSelect, initCustomSelects, injectCustomSelectStyles, refreshCustomSelects } from "./components/CustomSelect.js";
import { CustomDatePicker, initCustomDatePickers, injectCustomDatePickerStyles } from "./components/CustomDatePicker.js";
import {
  applyTimezoneConfig,
  formatDateInZone,
  formatLocalInput,
  formatUtcOffset,
  getTimezoneOffsetSeconds,
  initTimezone,
  localDateEndOfDayToUtcIso,
  localInputToUtcIso,
  refreshTimezone,
  subscribeTimezone,
  toLocalDateValue,
  toLocalInputValue,
} from "./timezone.js";

const VIEW_META = {
  overview: {
    title: "Overview",
    subtitle: "Real-time system pulse, database connectivity, and key metrics.",
    eyebrow: "OMEGA NEX-ORA / Command Center",
  },
  projects: {
    title: "Projects",
    subtitle: "Projects can own unlimited keys. Activate, schedule deletion, or permanently remove a project and every key it owns.",
    eyebrow: "OMEGA NEX-ORA / Project Ops",
  },
  generator: {
    title: "Key Generator",
    subtitle: "Select a project and any Universal key type, configure its policy, then generate from this one form.",
    eyebrow: "OMEGA NEX-ORA / Universal Key Generator",
  },
  keys: {
    title: "Key Management",
    subtitle: "Universal key records with type, permissions, expiration, and lifecycle. Plaintext is revealed once.",
    eyebrow: "OMEGA NEX-ORA / Universal Keys",
  },
  "key-types": {
    title: "Key Types",
    subtitle: "Built-in and project-owned type policies. Create, edit, disable, or safely remove custom types.",
    eyebrow: "OMEGA NEX-ORA / Universal Type Engine",
  },
  operations: {
    title: "Operations",
    subtitle: "Device bindings, API telemetry, signed webhooks, notifications, data transfer, automation, and access control.",
    eyebrow: "OMEGA NEX-ORA / Universal Control Plane",
  },
  installations: {
    title: "Installations",
    subtitle: "Reader App installations. Enforced strictly: one active reader per project.",
    eyebrow: "OMEGA NEX-ORA / Reader Installations",
  },
  security: {
    title: "Security Center",
    subtitle: "Hardware-level, network and cryptographic security posture of the platform.",
    eyebrow: "OMEGA NEX-ORA / Hardened Security Posture",
  },
  logs: {
    title: "Audit Logs",
    subtitle: "Server-side immutable audit log of all project, key, and authentication events.",
    eyebrow: "OMEGA NEX-ORA / Immutable Audit Trail",
  },
  settings: {
    title: "Settings",
    subtitle: "Runtime configuration for the OMEGA NEX-ORA platform.",
    eyebrow: "OMEGA NEX-ORA / System Configuration",
  },
};

const PROJECT_LABELS = [
  "Project Name",
  "Status",
  "Keys",
  "Created",
  "Auto Delete Date",
  "Remaining",
  "Installation",
  "Actions",
];
const KEY_LABELS = [
  "Key / Key ID",
  "Project",
  "Status",
  "Created",
  "Expires",
  "First Used",
  "Revoked",
  "Actions",
];
const KEY_TYPE_LABELS = [
  "Type",
  "Scope",
  "Permissions",
  "Format",
  "Expiration",
  "Keys",
  "Status",
  "Actions",
];
const EXPIRATION_LABELS = {
  "1_day": "1 Day",
  "7_days": "7 Days",
  "30_days": "30 Days",
  "90_days": "90 Days",
  "1_year": "1 Year",
  custom: "Custom Expiration",
  never: "Never Expires",
};
const INSTALLATION_LABELS = [
  "Installation ID",
  "Project",
  "Redeemed Key Hint",
  "Device ID",
  "Status",
  "Activated At",
  "Last Heartbeat",
  "Actions",
];
const SECURITY_LABELS = ["Timestamp", "Event Action", "Outcome", "Target Type", "IP Address"];
const AUDIT_LABELS = [
  "Timestamp",
  "Action",
  "Target Type",
  "Target ID",
  "Outcome",
  "Actor",
  "IP Address",
  "Details",
];

const state = {
  user: null,
  view: "overview",
  stats: null,
  projects: [],
  keys: [],
  keysPagination: { page: 1, page_size: 25, total: 0, total_pages: 1 },
  installations: [],
  installationsPagination: { page: 1, page_size: 25, total: 0, total_pages: 1 },
  auditLogs: [],
  auditPagination: { page: 1, page_size: 25, total: 0, total_pages: 1 },
  securityData: null,
  settingsData: null,
  generatedKeys: [],
  generatorMode: "existing",
  keyTypes: [],
  keyTypesById: new Map(),
  allKeyTypes: [],
  permissionsCatalog: [],
  keyTypesLoaded: false,
  keyTypeBuilderId: null,
  generatorBatchId: null,
  generatorRecovery: null,
  loggingOut: false,
  sessionCheck: null,
};

const numberFormat = new Intl.NumberFormat();
const copyLabels = new WeakMap();
const mobileQuery = window.matchMedia("(max-width: 980px)");
const viewLoads = new Map();
let mobileNavOpen = false;
let sessionRedirecting = false;

/* ---------------------------------------------------------------- helpers */

function redirectToLogin() {
  if (sessionRedirecting) return;
  sessionRedirecting = true;
  window.clearInterval(statsTimer);
  dismissBootScreen();
  window.location.replace("/login");
}

function fail(error, fallback = "The request could not be completed.") {
  if (error instanceof ApiProblem && error.status === 401) {
    // An already in-flight read can be rejected the moment logout revokes the
    // session. That is expected and must not duplicate the logout navigation.
    if (state.loggingOut) return;
    redirectToLogin();
    return;
  }

  const detail =
    error instanceof ApiProblem && error.details && typeof error.details === "object"
      ? Object.entries(error.details)
          .map(([field, message]) => `${field}: ${message}`)
          .join(" · ")
      : "";

  toast(error && error.message ? error.message : fallback, {
    type: "error",
    detail,
  });
}

function showGlobalError(message) {
  const node = $("#global-error");
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
}

function clearGlobalError() {
  const node = $("#global-error");
  if (!node) return;
  node.hidden = true;
  node.textContent = "";
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value !== undefined && value !== null ? String(value) : "—";
}

function setStat(name, value) {
  $$(`[data-stat="${name}"]`).forEach((node) => {
    if (typeof value === "number" && Number.isFinite(value)) {
      animateNumber(node, value);
    } else {
      node.textContent = value !== undefined && value !== null ? String(value) : "—";
    }
  });
}

function setBadge(selector, text, tone) {
  const node = $(selector);
  if (!node) return;
  node.textContent = text;
  node.className = `badge badge-${tone}`;
}

function debounce(fn, wait = 260) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}

function tableCell(label, content, className) {
  let children;
  if (content instanceof Node) children = [content];
  else if (Array.isArray(content)) children = content;
  else children = [content === undefined || content === null || content === "" ? "—" : String(content)];

  return el(
    "td",
    {
      "data-label": label,
      class: className || undefined,
    },
    children
  );
}

const EMPTY_ASSET_KINDS = new Set([
  "database-key",
  "reader-key",
  "license-key",
  "other-key",
  "key-generating",
  "key-verified",
  "logo-mark",
  "readme-icon",
]);

function emptyAssetKind(icon, title) {
  if (EMPTY_ASSET_KINDS.has(icon)) return icon;
  const text = String(title || "").toLowerCase();
  if (text.includes("project")) return "database-key";
  if (text.includes("installation")) return "reader-key";
  if (text.includes("audit") || text.includes("log") || text.includes("security")) {
    return "readme-icon";
  }
  if (text.includes("type")) return "other-key";
  if (text.includes("key")) return "license-key";
  return "database-key";
}

function emptyRow(colspan, title, subtitle, icon = "") {
  return el("tr", {}, [
    el("td", { colspan: String(colspan), class: "empty-cell" }, [
      el("div", { class: "empty-state" }, [
        el("span", { class: "empty-icon", "aria-hidden": "true" }, [
          el("img", {
            class: "empty-glyph",
            src: omegaAsset(emptyAssetKind(icon, title), { animated: false }),
            alt: "",
            width: "96",
            height: "96",
            loading: "lazy",
            decoding: "async",
          }),
        ]),
        el("strong", { text: title }),
        subtitle ? el("span", { class: "muted", text: subtitle }) : null,
      ]),
    ]),
  ]);
}

function errorRow(colspan, title = "Data unavailable") {
  return emptyRow(
    colspan,
    title,
    "The authenticated request failed. Use Refresh to try again. No placeholder data is shown.",
    "other-key"
  );
}

function loadingBlock(label) {
  return el("div", { class: "loading-block" }, [
    el("img", {
      class: "loading-glyph",
      src: omegaAsset("key-generating"),
      alt: "",
      width: "72",
      height: "72",
      decoding: "async",
    }),
    el("span", { class: "spinner spinner-lg", "aria-hidden": "true" }),
    el("p", { class: "muted", text: label }),
  ]);
}

function recordItem(label, value, { full = false, mono = false } = {}) {
  const dd = el("dd");
  if (value instanceof Node) dd.append(value);
  else if (mono) dd.append(el("code", { text: value === undefined || value === null || value === "" ? "—" : String(value) }));
  else dd.textContent = value === undefined || value === null || value === "" ? "—" : String(value);

  return el("div", { class: `record-item${full ? " span-2" : ""}` }, [
    el("dt", { text: label }),
    dd,
  ]);
}

function recordGrid(items) {
  return el("dl", { class: "record-grid" }, items);
}

function normalizePagination(raw, fallbackPage = 1, fallbackSize = 25) {
  const pagination = raw || {};
  return {
    page: Number(pagination.page) || fallbackPage,
    page_size: Number(pagination.page_size || pagination.pageSize) || fallbackSize,
    total: Number(pagination.total) || 0,
    total_pages: Number(pagination.total_pages || pagination.totalPages) || 1,
  };
}

function beginTableLoad(tbody, columns, labels, summarySelector) {
  if (tbody) {
    skeletonRows(tbody, columns, 4, labels);
    const table = tbody.closest("table");
    if (table) table.setAttribute("aria-busy", "true");
  }
  if (summarySelector) setText(summarySelector, "Loading…");
}

function endTableLoad(tbody) {
  const table = tbody ? tbody.closest("table") : null;
  if (table) table.removeAttribute("aria-busy");
}

async function copyWithFeedback(value, button, message) {
  try {
    await copyText(value);
    if (button) {
      if (!copyLabels.has(button)) copyLabels.set(button, button.textContent);
      button.textContent = "Copied ✓";
      button.classList.add("is-copied");
      window.setTimeout(() => {
        button.classList.remove("is-copied");
        button.textContent = copyLabels.get(button) || "Copy";
      }, 1800);
    }
    toast(message, { type: "success" });
    announce(message);
  } catch (error) {
    toast(error && error.message ? error.message : "Clipboard copy failed.", { type: "error" });
  }
}

function csvCell(value) {
  return `"${String(value === undefined || value === null ? "" : value).replace(/"/g, '""')}"`;
}

/* --------------------------------------------------------------- identity */

function applyIdentity() {
  const user = state.user;
  if (!user) return;
  setText("#user-name", user.full_name || user.email || "Super Admin");
  setText("#user-role", (user.role || "owner").toUpperCase());
  const initials = (user.full_name || user.email || "NX")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
  setText("#user-initials", initials || "NX");
}

/* ----------------------------------------------------------------- views */

export function showView(viewName, { updateHash = true, reload = true } = {}) {
  if (!VIEW_META[viewName]) viewName = "overview";
  state.view = viewName;

  $$(".nav-item").forEach((button) => {
    const isActive = button.dataset.view === viewName;
    const becameActive = isActive && !button.classList.contains("is-active");
    button.classList.toggle("is-active", isActive);
    if (isActive) {
      button.setAttribute("aria-current", "page");
      if (becameActive) replayMotion(button, "is-just-active", 500);
    } else {
      button.removeAttribute("aria-current");
    }
  });

  $$(".view").forEach((view) => {
    view.classList.toggle("is-active", view.id === `view-${viewName}`);
  });

  const meta = VIEW_META[viewName];
  setText("#page-title", meta.title);
  setText("#page-subtitle", meta.subtitle);
  setText("#topbar-eyebrow", meta.eyebrow);
  document.title = `${meta.title} · NexOra Database Key Generator`;

  const topAction = $("#topbar-quick-action");
  if (topAction && viewName === "generator") {
    topAction.textContent = "Generator active";
  } else if (topAction) {
    topAction.textContent = "Generate key";
  }

  if (updateHash) window.location.hash = viewName;

  closeMobileNav({ restoreFocus: false });
  clearGlobalError();
  announce(`${meta.title} view opened.`);

  if (reload) loadViewData(viewName);
}

async function loadViewData(viewName) {
  // Deduplicate concurrent loads of the same view. A second click, a polling
  // tick and a visibility event must never stack authenticated requests.
  if (viewLoads.has(viewName)) return viewLoads.get(viewName);

  const view = $(`#view-${viewName}`);
  if (view) view.setAttribute("aria-busy", "true");

  const task = (async () => {
    try {
      if (viewName === "overview") await loadOverview();
      else if (viewName === "projects") await loadProjects();
      else if (viewName === "generator") await loadGenerator();
      else if (viewName === "keys") await loadKeys();
      else if (viewName === "key-types") await loadKeyTypeCatalogue();
      else if (viewName === "operations") await renderOperations();
      else if (viewName === "installations") await loadInstallations();
      else if (viewName === "security") await loadSecurity();
      else if (viewName === "logs") await loadAuditLogs();
      else if (viewName === "settings") await loadSettings();
      return true;
    } catch (error) {
      fail(error, `Failed to load ${viewName} data.`);
      return false;
    } finally {
      if (view) view.removeAttribute("aria-busy");
    }
  })();

  viewLoads.set(viewName, task);
  try {
    return await task;
  } finally {
    viewLoads.delete(viewName);
  }
}

/* -------------------------------------------------------------- overview */

async function loadOverview() {
  const statsData = await api.stats();
  lastOverviewRefreshAt = Date.now();
  state.stats = statsData;

  setStat("total_projects", statsData.total_projects);
  setStat("active_projects", statsData.active_projects);
  setStat("total_keys", statsData.total_keys);
  setStat("unused_keys", statsData.unused_keys);
  setStat("used_keys", statsData.used_keys);
  setStat("expired_keys", statsData.expired_keys);
  setStat("revoked_keys", statsData.revoked_keys);
  setStat("active_installations", statsData.active_installations);

  const isConnected = statsData.database_status === "connected";
  setText("#db-health-text", isConnected ? "DB: CONNECTED" : "DB: UNAVAILABLE");
  const dbChip = $("#db-health-chip");
  if (dbChip) dbChip.classList.toggle("chip-danger", !isConnected);

  setText("#pulse-database-status", isConnected ? "CONNECTED" : "UNAVAILABLE");
  setBadge(
    "#pulse-database-status",
    isConnected ? "CONNECTED" : "UNAVAILABLE",
    isConnected ? "success" : "danger"
  );

  const backendOk = statsData.backend_health === "ok";
  setBadge(
    "#pulse-backend-health",
    backendOk ? "OK" : "DEGRADED",
    backendOk ? "success" : "warn"
  );

  renderLifecycle(statsData);
  renderOverviewAudit(statsData.recent_audit || []);
}

function renderLifecycle(stats) {
  const values = {
    unused: Number(stats.unused_keys) || 0,
    used: Number(stats.used_keys) || 0,
    expired: Number(stats.expired_keys) || 0,
    revoked: Number(stats.revoked_keys) || 0,
  };
  const total = values.unused + values.used + values.expired + values.revoked;

  const totalNode = $("#lifecycle-total");
  if (totalNode) animateNumber(totalNode, total);

  Object.entries(values).forEach(([key, value]) => {
    const node = $(`[data-lifecycle-value="${key}"]`);
    if (node) node.textContent = numberFormat.format(value);
  });

  const circles = {
    unused: $("#donut-unused"),
    used: $("#donut-used"),
    expired: $("#donut-expired"),
    revoked: $("#donut-revoked"),
  };

  let offset = 0;
  Object.entries(values).forEach(([key, value]) => {
    const circle = circles[key];
    const share = total > 0 ? (value / total) * 100 : 0;
    const gap = share > 0 && share < 100 ? Math.min(0.9, share * 0.16) : 0;
    const visible = Math.max(0, share - gap);

    if (circle) {
      circle.setAttribute("stroke-dasharray", `${visible} ${Math.max(0, 100 - visible)}`);
      circle.setAttribute("stroke-dashoffset", String(-offset));
      circle.setAttribute(
        "aria-label",
        `${key}: ${numberFormat.format(value)} key${value === 1 ? "" : "s"}, ${
          total ? ((value / total) * 100).toFixed(1) : "0.0"
        } percent`
      );
    }
    offset += share;
  });

  const redeemable = values.unused + values.used;
  const activation = redeemable > 0 ? (values.used / redeemable) * 100 : null;
  setText("#lifecycle-activation", activation === null ? "—" : `${activation.toFixed(1)}%`);

  const legend = $("#lifecycle-legend");
  if (legend) {
    legend.classList.remove("is-empty");
    if (total === 0) legend.classList.add("is-empty");
  }
}

function renderOverviewAudit(items) {
  const list = $("#overview-audit-list");
  if (!list) return;
  clearNode(list);

  if (!items.length) {
    list.append(
      el("li", {}, [
        el("div", { class: "empty-state" }, [
          el("span", { class: "empty-icon", "aria-hidden": "true", text: "≡" }),
          el("strong", { text: "No recent audit activity" }),
          el("span", { class: "muted", text: "Administrative events will appear here as they occur." }),
        ]),
      ])
    );
    return;
  }

  items.slice(0, 7).forEach((item, index) => {
    const isSuccess = item.outcome === "success";
    const li = el("li", { class: "audit-item" }, [
      el("span", {
        class: `audit-dot ${isSuccess ? "success" : "failure"}`,
        "aria-hidden": "true",
      }),
      el("div", { class: "audit-copy" }, [
        el("strong", { text: item.action }),
        el("span", {
          text: `${item.actor_email || "Administrator"} · ${formatRelative(item.created_at)}${
            item.project_name ? ` · ${item.project_name}` : ""
          }`,
        }),
      ]),
      statusBadge(item.outcome),
    ]);
    li.style.setProperty("animation-delay", `${Math.min(index * 35, 240)}ms`);
    list.append(li);
  });
}

/* -------------------------------------------------------------- projects */

async function loadProjects() {
  const searchInput = $("#project-search-input");
  const search = searchInput ? searchInput.value.trim() : "";
  const tbody = $("#projects-table-body");
  beginTableLoad(tbody, PROJECT_LABELS.length, PROJECT_LABELS, "#projects-summary");

  try {
    const data = await api.projects({ search });
    state.projects = data.projects || [];
    renderProjectsTable(state.projects);
  } catch (error) {
    if (tbody) {
      clearNode(tbody);
      tbody.append(errorRow(PROJECT_LABELS.length, "Projects unavailable"));
    }
    throw error;
  } finally {
    endTableLoad(tbody);
  }
}

function getExpiryUrgency(dateValue) {
  if (!dateValue) return { urgency: "never", msRemaining: Infinity };
  const targetTime = new Date(dateValue).getTime();
  if (Number.isNaN(targetTime)) return { urgency: "unknown", msRemaining: Infinity };
  const now = Date.now();
  const msRemaining = targetTime - now;

  if (msRemaining <= 0) {
    return { urgency: "expired", msRemaining };
  }
  // Within 24 hours -> Critical (Red / Pulsing)
  if (msRemaining <= 24 * 60 * 60 * 1000) {
    return { urgency: "critical", msRemaining };
  }
  // Within 7 days -> Warning (Amber / Gold)
  if (msRemaining <= 7 * 24 * 60 * 60 * 1000) {
    return { urgency: "warning", msRemaining };
  }
  return { urgency: "safe", msRemaining };
}

function autoDeleteDateLabel(project) {
  const target = project.auto_delete_at || project.expires_at;
  if (!target) return "No Auto Delete Scheduled";
  const { urgency } = getExpiryUrgency(target);
  const node = el("span", { class: `expiry-indicator expiry-${urgency}` }, [
    el("span", { class: "expiry-main-text", text: formatDate(target, { time: false }) }),
  ]);
  node.toString = () => formatDate(target, { time: false });
  return node;
}

function remainingDaysLabel(project) {
  const target = project.auto_delete_at || project.expires_at;
  if (!target) return "—";
  const { urgency, msRemaining } = getExpiryUrgency(target);
  if (urgency === "expired" || project.auto_delete_due) {
    const node = el("span", { class: "badge badge-danger", text: "Due now" });
    node.toString = () => "Due now";
    return node;
  }
  const days = Number(project.days_remaining) || Math.max(0, Math.ceil(msRemaining / (86400 * 1000)));
  const text = `${days} day${days === 1 ? "" : "s"}`;
  if (urgency === "critical") {
    const hours = Math.max(1, Math.ceil(msRemaining / (3600 * 1000)));
    const node = el("span", { class: "badge badge-danger expiry-pulse", text: `${hours}h left` });
    node.toString = () => `${hours}h left`;
    return node;
  }
  if (urgency === "warning") {
    const node = el("span", { class: "badge badge-warn", text });
    node.toString = () => text;
    return node;
  }
  return text;
}

function renderProjectsTable(projects) {
  const tbody = $("#projects-table-body");
  if (!tbody) return;
  clearNode(tbody);

  if (!projects.length) {
    tbody.append(
      emptyRow(
        PROJECT_LABELS.length,
        "No projects found",
        "Open Key Generator and enter a project name to create the first project.",
        "◫"
      )
    );
    setText("#projects-summary", "0 projects");
    return;
  }

  setText("#projects-summary", `Showing ${projects.length} project(s)`);

  projects.forEach((project) => {
    const isActive = project.status === "active";

    const nameButton = el("a", {
      href: "#",
      class: "table-link",
      text: project.project_name,
      onclick: (event) => {
        event.preventDefault();
        viewProjectDetails(project.id);
      },
    });

    const row = el("tr", {}, [
      tableCell("Project Name", nameButton, "table-primary"),
      tableCell(
        "Status",
        el("div", { class: "key-count-cell" }, [
          statusBadge(project.effective_status || project.status),
          el("span", { class: "muted", text: projectExpiryLabel(project) }),
        ])
      ),
      tableCell(
        "Keys",
        el("div", { class: "key-count-cell" }, [
          el("span", {
            class: "badge badge-info",
            text: `${Number(project.total_keys) || 0} key${
              Number(project.total_keys) === 1 ? "" : "s"
            }`,
          }),
          project.reader_key_status
            ? el("span", {
                class: "muted",
                text: `Latest: ${project.reader_key_status}`,
              })
            : el("span", { class: "muted", text: "No keys yet" }),
        ])
      ),
      tableCell("Created", formatDate(project.created_at, { time: false })),
      tableCell("Auto Delete Date", autoDeleteDateLabel(project)),
      tableCell("Remaining", remainingDaysLabel(project)),
      tableCell(
        "Installation",
        project.active_installation_id
          ? el("span", { class: "badge badge-success", text: "Active" })
          : el("span", { class: "muted", text: "None" })
      ),
      tableCell(
        "Actions",
        el("div", { class: "row-actions" }, [
          el("button", {
            class: "btn btn-ghost btn-sm",
            type: "button",
            text: isActive ? "Deactivate" : "Activate",
            onclick: () => toggleProjectStatus(project),
          }),
          el("button", {
            class: "btn btn-danger btn-sm",
            type: "button",
            text: "Delete",
            onclick: () => deleteProjectPrompt(project),
          }),
        ]),
        "align-right"
      ),
    ]);

    tbody.append(row);
  });
}

async function viewProjectDetails(projectId) {
  const modal = $("#project-detail-modal");
  const body = $("#project-detail-body");
  if (!modal || !body) return;

  clearNode(body);
  body.append(loadingBlock("Loading authenticated project record…"));
  openDialog(modal);

  try {
    const data = await api.project(projectId);
    const project = data.project;
    const dateInput = el("input", {
      type: "date",
      id: "project-auto-delete-input",
      "aria-label": "Auto delete date",
    });
    const targetAutoDelete = project.auto_delete_at || project.expires_at;
    if (targetAutoDelete) {
      dateInput.value = toLocalDateValue(targetAutoDelete);
    }

    const expirySelect = el("select", {
      id: "project-expiry-select",
      "aria-label": "Project expiration",
      "data-custom-select": true,
      "data-placeholder": "Project expiration",
    });
    rebuildExpirationSelect(expirySelect, PROJECT_EXPIRY_PRESETS, {
      includeKeep: Boolean(project.expires_at),
      selected: project.expires_at ? "keep" : "never",
    });
    const expiryCustom = el("input", {
      type: "datetime-local",
      id: "project-expiry-custom",
      value: localDateTimeValue(project.expires_at),
      "aria-label": "Custom project expiration",
    });
    const expiryCustomField = el("label", { class: "field", hidden: true }, [
      el("span", { class: "field-label", text: "Custom project expiration" }),
      expiryCustom,
      el("span", { class: "muted", text: "Local time; stored in UTC. Must be in the future." }),
    ]);
    const expiryPreview = el("div", { class: "expiration-preview", "aria-live": "polite" });
    const syncExpiry = () => {
      const custom = expirySelect.value === "custom";
      expiryCustomField.hidden = !custom;
      let text = "Keep the current project expiration.";
      if (expirySelect.value === "never") text = "Never expires.";
      else if (custom) {
        text = expiryCustom.value
          ? `Expires ${formatLocalInput(expiryCustom.value)}`
          : "Choose a project expiration date and time.";
      } else if (expirySelect.value !== "keep") {
        text = EXPIRATION_LABELS[expirySelect.value] || expirySelect.value;
      }
      clearNode(expiryPreview);
      expiryPreview.append(el("span", { class: "muted", text: "Project expiration:" }));
      expiryPreview.append(el("strong", { text }));
    };
    expirySelect.addEventListener("change", syncExpiry);
    expiryCustom.addEventListener("input", syncExpiry);
    syncExpiry();

    const expirySection = el("section", { class: "detail-section" }, [
      el("h3", { text: "Project Expiration" }),
      el("div", { class: "project-expiry-controls" }, [
        expirySelect,
        el("button", {
          class: "btn btn-primary btn-sm",
          type: "button",
          text: "Save expiration",
          onclick: () => saveProjectExpiration(project, expirySelect.value, expiryCustom.value),
        }),
      ]),
      expiryCustomField,
      expiryPreview,
      el("p", {
        class: "muted",
        text: "Project expiration and automatic permanent deletion are identical. When this project expires, it is automatically purged from the database.",
      }),
    ]);

    const schedule = el("section", { class: "detail-section" }, [
      el("h3", { text: "Schedule Permanent Deletion" }),
      el("div", { class: "auto-delete-controls" }, [
        dateInput,
        el("button", {
          class: "btn btn-primary btn-sm",
          type: "button",
          text: "Save schedule",
          onclick: () => saveProjectAutoDelete(project, dateInput.value),
        }),
        el("button", {
          class: "btn btn-ghost btn-sm",
          type: "button",
          text: "Clear schedule",
          onclick: () => saveProjectAutoDelete(project, ""),
        }),
      ]),
      el("p", {
        class: "muted",
        text: "Automatic deletion runs only when the server-side purge command runs and the date is due. Leave empty to disable.",
      }),
    ]);

    clearNode(body);
    body.append(
      recordGrid([
        recordItem("Project Name", project.project_name),
        recordItem("Status", statusBadge(project.effective_status || project.status)),
        recordItem("Project Expiration", projectExpiryLabel(project)),
        recordItem("Total Keys", String(project.total_keys || 0)),
        recordItem(
          "Latest Key Status",
          project.reader_key_status
            ? statusBadge(project.reader_key_status)
            : el("span", { class: "badge badge-muted", text: "No Key" })
        ),
        recordItem("Latest Key Hint", project.reader_key_hint || "—", { mono: true }),
        recordItem("Latest Key Expires", project.reader_key_expires_at
          ? formatDate(project.reader_key_expires_at)
          : project.reader_key_id
            ? "Never"
            : "—"),
        recordItem("Project ID", project.id, { mono: true }),
        recordItem("Created At", formatDate(project.created_at)),
        recordItem("Auto Delete Date", autoDeleteDateLabel(project)),
        recordItem("Remaining Days", remainingDaysLabel(project)),
        recordItem("Active Installation", project.active_installation_id ? "Active" : "None"),
        recordItem("Active Device", project.active_device_id || "None connected"),
      ]),
      expirySection,
      schedule
    );

    const projectKeys = el("section", {
      class: "detail-section project-keys-section",
      "aria-labelledby": "project-keys-title",
    });
    body.append(projectKeys);
    await renderProjectKeyList(projectKeys, project);
  } catch (error) {
    closeDialog(modal, { immediate: true });
    fail(error, "Failed to load project details.");
  }
}

async function renderProjectKeyList(container, project) {
  clearNode(container);
  container.append(
    el("h3", { id: "project-keys-title", text: "Keys for this Project" }),
    el("p", {
      class: "muted",
      text: "Every key owned by this project. Revocation applies to one key only and can never be undone.",
    })
  );

  const tbody = el("tbody");
  const table = el("table", { class: "data-table responsive-table" }, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", { text: "Key / Key ID" }),
        el("th", { text: "Status" }),
        el("th", { text: "Created" }),
        el("th", { text: "Expiration" }),
        el("th", { text: "First Used" }),
        el("th", { text: "Revoked" }),
        el("th", { class: "align-right", text: "Actions" }),
      ]),
    ]),
    tbody,
  ]);
  container.append(el("div", { class: "table-wrap" }, [table]));
  skeletonRows(tbody, 7, 3);

  try {
    const data = await api.readerKeys({
      project_id: project.id,
      page: 1,
      page_size: 100,
      sort: "created_at",
      order: "desc",
    });
    const keys = data.items || [];
    clearNode(tbody);

    if (!keys.length) {
      tbody.append(
        emptyRow(
          7,
          "No keys yet",
          "Generate a key to bind a reader to this project.",
          "⌘"
        )
      );
      return;
    }

    keys.forEach((key) => {
      const revoked = key.revoked_at
        ? el("div", { class: "key-revocation" }, [
            el("span", { text: formatDate(key.revoked_at) }),
            el("span", { class: "muted", text: key.revoked_reason || "Revoked by admin" }),
          ])
        : el("span", { class: "muted", text: "—" });

      tbody.append(
        el("tr", {}, [
          tableCell(
            "Key / Key ID",
            el("div", { class: "key-identity" }, [
              el("a", {
                href: "#",
                class: "table-link key-hint",
                text: key.key_hint,
                onclick: (event) => {
                  event.preventDefault();
                  viewKeyDetails(key.id);
                },
              }),
              el("span", { class: "key-id", text: key.id }),
            ]),
            "table-primary"
          ),
          tableCell("Status", statusBadge(key.status)),
          tableCell("Created", formatDate(key.created_at)),
          tableCell("Expiration", expirationText(key)),
          tableCell("First Used", formatDate(key.first_used_at || key.used_at)),
          tableCell("Revoked", revoked),
          tableCell(
            "Actions",
            key.status === "revoked"
              ? el("span", { class: "badge badge-danger", text: "Revoked" })
              : el("button", {
                  class: "btn btn-danger btn-sm",
                  type: "button",
                  text: "Revoke",
                  onclick: () =>
                    revokeKeyPrompt(key, {
                      onDone: () => renderProjectKeyList(container, project),
                    }),
                }),
            "align-right"
          ),
        ])
      );
    });
    animateTableRows(tbody);
  } catch {
    clearNode(tbody);
    tbody.append(errorRow(7, "Project keys unavailable"));
  }
}

async function saveProjectExpiration(project, preset, customValue) {
  if (preset === "keep") {
    toast("Project expiration was left unchanged.", { type: "info" });
    return;
  }

  let expiresAt = null;
  if (preset === "custom") {
    try {
      expiresAt = customValue ? localInputToUtcIso(customValue) : null;
    } catch (error) {
      toast(error.message, { type: "error" });
      return;
    }
    if (!expiresAt || new Date(expiresAt).getTime() <= Date.now()) {
      toast("Choose a future custom project expiration.", { type: "error" });
      return;
    }
  }

  try {
    await api.updateUniversalProject(project.id, {
      expiration_preset: preset,
      ...(expiresAt ? { expires_at: expiresAt } : {}),
    });
    toast(
      preset === "never"
        ? `'${project.project_name}' will never expire.`
        : `Project expiration and auto-delete scheduled for '${project.project_name}'.`,
      { type: "success" }
    );
    closeDialog($("#project-detail-modal"));
    await loadProjects().catch(() => {});
    await loadOverview().catch(() => {});
  } catch (error) {
    fail(error, "Failed to update the project expiration.");
  }
}

async function saveProjectAutoDelete(project, dateText) {
  let iso = null;
  if (dateText) {
    try {
      iso = localDateEndOfDayToUtcIso(dateText);
    } catch (error) {
      toast(error.message, { type: "error" });
      return;
    }
    if (!iso) {
      toast("Choose a valid date.", { type: "error" });
      return;
    }
  }

  try {
    await api.setProjectAutoDelete(project.id, iso);
    toast(
      iso
        ? `Auto-delete scheduled for '${project.project_name}'.`
        : `Auto-delete cleared for '${project.project_name}'.`,
      { type: "success" }
    );
    closeDialog($("#project-detail-modal"));
    await loadProjects().catch(() => {});
    await loadOverview().catch(() => {});
  } catch (error) {
    fail(error, "Failed to update the auto-delete schedule.");
  }
}

async function toggleProjectStatus(project) {
  const newStatus = project.status === "active" ? "inactive" : "active";
  const confirmed = await confirmAction({
    title: `${newStatus === "active" ? "Activate" : "Deactivate"} Project?`,
    message: `Setting '${project.project_name}' to ${newStatus} will ${
      newStatus === "inactive"
        ? "temporarily prevent the Reader App from connecting or sending heartbeats."
        : "allow Reader App connections."
    }`,
    confirmLabel: newStatus === "active" ? "Activate" : "Deactivate",
    tone: newStatus === "active" ? "info" : "warn",
  });
  if (!confirmed.confirmed) return;

  try {
    await api.setProjectStatus(project.id, newStatus);
    toast(`Project '${project.project_name}' set to ${newStatus}.`, { type: "success" });
    await loadProjects().catch(() => {});
    await loadOverview().catch(() => {});
  } catch (error) {
    fail(error, "Failed to update project status.");
  }
}

async function deleteProjectPrompt(project) {
  const confirmed = await confirmAction({
    title: `Permanently delete '${project.project_name}'?`,
    message:
      "This permanently removes the project, every key it owns, reader installations, device tokens, and other project-owned records. This cannot be undone. Audit history is preserved, and the project name becomes reusable.",
    confirmLabel: "Permanently Delete Project",
    tone: "danger",
    requireText: project.project_name,
    textLabel: `Type the exact project name (${project.project_name}) to confirm`,
  });
  if (!confirmed.confirmed) return;

  try {
    await api.deleteProject(project.id, confirmed.text);
    toast(`Project '${project.project_name}' permanently deleted.`, { type: "success" });
    await loadProjects().catch(() => {});
    await loadOverview().catch(() => {});
  } catch (error) {
    fail(error, "Failed to permanently delete the project.");
  }
}

/* ------------------------------------------------------------- generator */

function expirationText(key) {
  if (!key) return "—";
  if (key.is_permanent || key.expiration_preset === "never") return "Never expires";
  const label = EXPIRATION_LABELS[key.expiration_preset] || "Expires";
  if (!key.expires_at) return label;

  const { urgency, msRemaining } = getExpiryUrgency(key.expires_at);
  const formatted = formatDate(key.expires_at);

  let urgencyTag = "";
  if (urgency === "expired") {
    urgencyTag = "EXPIRED";
  } else if (urgency === "critical") {
    const hours = Math.max(1, Math.ceil(msRemaining / (1000 * 60 * 60)));
    urgencyTag = `${hours}h left`;
  } else if (urgency === "warning") {
    const days = Math.ceil(msRemaining / (1000 * 60 * 60 * 24));
    urgencyTag = `${days}d left`;
  }

  const node = el("span", { class: `expiry-indicator expiry-${urgency}` }, [
    el("span", { class: "expiry-main-text", text: `${label} · ${formatted}` }),
    urgencyTag ? el("span", { class: "expiry-badge", text: urgencyTag }) : null,
  ]);
  node.toString = () => `${label} · ${formatted}`;
  return node;
}

async function loadGeneratorProjects() {
  const select = $("#gen-project-select");
  if (!select) return;
  const current = select.value;
  try {
    const data = await api.projects({ include_deleted: false });
    const projects = (data.projects || []).filter((project) => !project.deleted_at);
    state.projects = projects;
    clearNode(select);
    select.append(el("option", { value: "", text: "Choose a project…" }));
    projects.forEach((project) => {
      select.append(
        el("option", {
          value: project.id,
          text: `${project.project_name} · ${project.total_keys || 0} key${
            Number(project.total_keys) === 1 ? "" : "s"
          }`,
        })
      );
    });
    if (current && Array.from(select.options).some((option) => option.value === current)) {
      select.value = current;
    }
    if (!projects.length && state.generatorMode === "existing") {
      setGeneratorMode("new", { focus: false });
    }
  } catch {
    // The generator reports its own submit error; keep any existing options.
  }
}

function replayMotion(node, className, duration = 420) {
  if (!node) return;
  node.classList.remove(className);
  void node.offsetWidth;
  node.classList.add(className);
  window.setTimeout(() => node.classList.remove(className), duration);
}

const PROJECT_EXPIRY_PRESETS = [
  "1_day",
  "7_days",
  "30_days",
  "90_days",
  "1_year",
  "custom",
  "never",
];
const EXPIRATION_PRESETS = [
  ["1_day", "1 Day"],
  ["7_days", "7 Days"],
  ["30_days", "30 Days"],
  ["90_days", "90 Days"],
  ["1_year", "1 Year"],
  ["custom", "Custom Expiration"],
  ["never", "Never"],
];

function buildPresetChips() {
  const rail = $("#gen-expiration-presets");
  const select = $("#gen-expiration-select");
  if (!rail || !select) return;

  clearNode(rail);
  const presets = (select.dataset.presets || "")
    .split(",")
    .filter(Boolean)
    .filter((value) => value !== "custom");

  presets.forEach((value) => {
    const chip = el("button", {
      class: "preset-chip",
      type: "button",
      text: EXPIRATION_LABELS[value] || value,
      "data-preset": value,
      "aria-pressed": "false",
    });
    chip.addEventListener("click", () => {
      if (select.value === value) {
        pulseElement(chip);
        return;
      }
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    rail.append(chip);
  });
  syncPresetChips();
}

function syncPresetChips() {
  const select = $("#gen-expiration-select");
  const value = select ? select.value : "";
  $$("#gen-expiration-presets .preset-chip").forEach((chip) => {
    const active = chip.dataset.preset === value;
    chip.classList.toggle("is-active", active);
    chip.setAttribute("aria-pressed", String(active));
  });
}

function keyTypePresets(type) {
  const configured = type && type.expiration_policy ? type.expiration_policy.presets : null;
  const values = Array.isArray(configured) && configured.length
    ? configured
    : EXPIRATION_PRESETS.map(([value]) => value);
  return [...new Set(values.filter((value) => EXPIRATION_LABELS[value]))];
}

function defaultKeyTypePreset(type) {
  const presets = keyTypePresets(type);
  const preferred = type && type.expiration_policy ? type.expiration_policy.default : null;
  if (preferred && presets.includes(preferred)) return preferred;
  if (presets.includes("30_days")) return "30_days";
  return presets[0] || "never";
}

function rebuildExpirationSelect(
  select,
  presets,
  { includeKeep = false, selected = "" } = {}
) {
  if (!select) return "";
  clearNode(select);
  if (includeKeep) {
    select.append(el("option", { value: "keep", text: "Keep current expiration" }));
  }
  const values = [...new Set((presets || []).filter((value) => EXPIRATION_LABELS[value]))];
  values.forEach((value) => {
    select.append(el("option", { value, text: EXPIRATION_LABELS[value] }));
  });
  if (!values.length) {
    select.append(el("option", { value: "never", text: "Never Expires" }));
  }
  select.dataset.presets = values.join(",");
  const hasSelected = selected && Array.from(select.options).some((option) => option.value === selected);
  select.value = hasSelected
    ? selected
    : includeKeep
      ? "keep"
      : select.options[0]?.value || "never";
  return select.value;
}

async function loadKeyTypes({ force = false } = {}) {
  if (!force && state.keyTypesLoaded) return state.keyTypes;
  const data = await api.keyTypes({});
  state.keyTypes = Array.isArray(data.key_types) ? data.key_types : [];
  state.permissionsCatalog = Array.isArray(data.permissions) ? data.permissions : [];
  state.keyTypesById = new Map(state.keyTypes.map((type) => [type.id, type]));
  state.keyTypesLoaded = true;
  return state.keyTypes;
}

function permissionLabel(id) {
  const entry = state.permissionsCatalog.find((permission) => permission.id === id);
  return entry ? entry.name : id;
}

function keyTypeExpirySummary(type) {
  const presets = type && type.expiration_policy && type.expiration_policy.presets;
  if (!Array.isArray(presets) || !presets.length) return "—";
  return presets.map((value) => EXPIRATION_LABELS[value] || value).join(", ");
}

function keyTypeFormatSummary(type) {
  const format = type && type.key_format && typeof type.key_format === "object" ? type.key_format : {};
  const groups = Number(format.groups) || 4;
  const length = Number(format.group_length) || 4;
  const separator = String(format.separator ?? "-");
  return `${type.key_prefix || "NX"} · ${groups}×${length} · ${separator || "no separator"}`;
}

function keyTypeMatchesSearch(type, term) {
  if (!term) return true;
  const haystack = [
    type.id,
    type.name,
    type.description,
    type.project_name,
    ...(type.permissions || []),
    ...(type.scopes || []),
    ...((type.expiration_policy && type.expiration_policy.presets) || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(term);
}

async function loadKeyTypeCatalogue() {
  const tbody = $("#key-types-table-body");
  beginTableLoad(tbody, KEY_TYPE_LABELS.length, KEY_TYPE_LABELS, "#key-types-summary");
  try {
    const data = await api.keyTypes({ include_inactive: "true" });
    state.allKeyTypes = Array.isArray(data.key_types) ? data.key_types : [];
    state.permissionsCatalog = Array.isArray(data.permissions) ? data.permissions : [];
    renderKeyTypeCatalogue();
  } catch (error) {
    if (tbody) {
      clearNode(tbody);
      tbody.append(errorRow(KEY_TYPE_LABELS.length, "Key types unavailable"));
    }
    throw error;
  } finally {
    endTableLoad(tbody);
  }
}

function renderKeyTypeCatalogue() {
  const tbody = $("#key-types-table-body");
  if (!tbody) return;
  clearNode(tbody);
  const term = ($("#key-types-search-input")?.value || "").trim().toLowerCase();
  const rows = state.allKeyTypes.filter((type) => keyTypeMatchesSearch(type, term));

  if (!rows.length) {
    tbody.append(
      emptyRow(
        KEY_TYPE_LABELS.length,
        term ? "No matching key types" : "No key types found",
        term ? "Try a different search term." : "Built-in types should be seeded by the Universal migration.",
        "◉"
      )
    );
    setText("#key-types-summary", term ? "0 matching key types" : "0 key types");
    return;
  }

  rows.forEach((type) => {
    const scope = type.project_id ? type.project_name || "Project type" : "Global";
    const typeCell = el("div", { class: "key-type-name-cell" }, [
      el("strong", { text: type.name || type.id }),
      el("code", { class: "mono", text: type.id }),
      el("span", {
        class: `badge ${type.is_builtin ? "badge-info" : "badge-success"}`,
        text: type.is_builtin ? "BUILT-IN" : "CUSTOM",
      }),
    ]);
    const actions = el("div", { class: "row-actions" });
    if (type.is_builtin) {
      actions.append(el("span", { class: "badge badge-muted", text: "Protected" }));
    } else {
      actions.append(
        el("button", {
          class: "btn btn-ghost btn-sm",
          type: "button",
          text: "Edit",
          onclick: () => openTypeBuilder(type),
        }),
        el("button", {
          class: "btn btn-ghost btn-sm",
          type: "button",
          text: type.is_active ? "Disable" : "Enable",
          onclick: () => toggleKeyType(type),
        })
      );
      const deleteButton = el("button", {
        class: "btn btn-danger btn-sm",
        type: "button",
        text: "Delete",
        disabled: Number(type.key_count) > 0,
        title:
          Number(type.key_count) > 0
            ? "This type is referenced by existing keys. Disable it instead."
            : "Delete this unused custom type.",
        onclick: () => deleteKeyTypePrompt(type),
      });
      actions.append(deleteButton);
    }

    tbody.append(
      el("tr", {}, [
        tableCell("Type", typeCell, "table-primary"),
        tableCell("Scope", scope),
        tableCell("Permissions", (type.permissions || []).join(", ") || "None"),
        tableCell("Format", el("code", { class: "mono", text: keyTypeFormatSummary(type) })),
        tableCell("Expiration", keyTypeExpirySummary(type), "key-type-expiry-cell"),
        tableCell("Keys", String(type.key_count || 0)),
        tableCell("Status", statusBadge(type.is_active ? "active" : "inactive")),
        tableCell("Actions", actions, "align-right"),
      ])
    );
  });

  const builtIn = rows.filter((type) => type.is_builtin).length;
  const custom = rows.length - builtIn;
  setText(
    "#key-types-summary",
    `${rows.length} key type${rows.length === 1 ? "" : "s"} · ${builtIn} built-in · ${custom} custom`
  );
}

function syncTypeDefaultExpiry(preferred = "") {
  const container = $("#type-expiry-presets");
  const select = $("#type-default-expiry");
  if (!container || !select) return;
  const checked = readCheckedValues(container, "data-type-expiry");
  clearNode(select);
  checked.forEach((value) => {
    select.append(el("option", { value, text: EXPIRATION_LABELS[value] || value }));
  });
  if (!checked.length) {
    select.append(el("option", { value: "", text: "Choose at least one preset" }));
    select.value = "";
    return;
  }
  const current = preferred || select.value;
  select.value = checked.includes(current) ? current : checked[0];
}

async function fillTypeProjectSelect(selectedId = "") {
  const select = $("#type-project-select");
  if (!select) return;
  const current = selectedId || select.value;
  clearNode(select);
  select.append(el("option", { value: "", text: "Global — available to every project" }));
  let projects = state.projects || [];
  try {
    const data = await api.projects({ include_deleted: false });
    projects = (data.projects || []).filter((project) => !project.deleted_at);
    state.projects = projects;
  } catch {
    // The table still exposes the current project when the project list is unavailable.
  }
  projects.forEach((project) => {
    select.append(el("option", { value: project.id, text: project.project_name }));
  });
  if (current && !Array.from(select.options).some((option) => option.value === current)) {
    select.append(el("option", { value: current, text: "Current project scope" }));
  }
  select.value = current || "";
}

async function openTypeBuilder(type = null) {
  const panel = $("#type-builder-panel");
  const form = $("#key-type-form");
  if (!panel || !form) return;
  form.reset();
  state.keyTypeBuilderId = type ? type.id : null;
  setText("#type-builder-title", type ? `Edit ${type.name}` : "Create Custom Key Type");
  setText("#type-builder-submit .btn-label", type ? "Save Changes" : "Create Key Type");
  setToneMessage($("#type-builder-error"), "", "error");

  const idInput = $("#type-id-input");
  if (idInput) {
    idInput.value = type ? type.id : "";
    idInput.disabled = Boolean(type);
  }
  $("#type-name-input").value = type ? type.name || "" : "";
  $("#type-description-input").value = type ? type.description || "" : "";
  $("#type-prefix-input").value = type ? type.key_prefix || "CUS" : "CUS";
  const format = type && type.key_format && typeof type.key_format === "object" ? type.key_format : {};
  $("#type-groups-input").value = String(format.groups || 4);
  $("#type-group-length-input").value = String(format.group_length || 4);
  $("#type-separator-select").value = String(format.separator ?? "-");
  $("#type-suffix-input").value = format.suffix || "";
  $("#type-scopes-input").value = type && Array.isArray(type.scopes) ? type.scopes.join(", ") : "";
  $("#type-metadata-input").value = JSON.stringify(
    type && type.metadata_schema ? type.metadata_schema : { fields: {} },
    null,
    2
  );
  $("#type-validation-input").value = JSON.stringify(
    type && type.validation_rules ? type.validation_rules : {},
    null,
    2
  );
  $("#type-active-input").checked = type ? Boolean(type.is_active) : true;

  await fillTypeProjectSelect(type && type.project_id ? type.project_id : "");
  const permissionIds = [
    ...new Set([
      ...state.permissionsCatalog.map((permission) => permission.id),
      ...((type && type.permissions) || []),
    ]),
  ];
  renderCheckChoices(
    $("#type-permissions-grid"),
    permissionIds,
    type ? type.permissions || [] : permissionIds.includes("read") ? ["read"] : [],
    "data-type-permission",
    permissionLabel
  );

  const presetValues = EXPIRATION_PRESETS.map(([value]) => value);
  const selectedPresets =
    type && type.expiration_policy && Array.isArray(type.expiration_policy.presets)
      ? type.expiration_policy.presets
      : presetValues;
  renderCheckChoices(
    $("#type-expiry-presets"),
    presetValues,
    selectedPresets,
    "data-type-expiry",
    (value) => EXPIRATION_LABELS[value] || value
  );
  syncTypeDefaultExpiry(
    type && type.expiration_policy ? type.expiration_policy.default || "" : "never"
  );

  panel.hidden = false;
  panel.scrollIntoView({ behavior: isLiteMode() ? "auto" : "smooth", block: "start" });
  window.setTimeout(() => idInput && !idInput.disabled && idInput.focus({ preventScroll: true }), 120);
}

function closeTypeBuilder() {
  const panel = $("#type-builder-panel");
  const form = $("#key-type-form");
  if (panel) panel.hidden = true;
  if (form) form.reset();
  state.keyTypeBuilderId = null;
  setToneMessage($("#type-builder-error"), "", "error");
}

function parseJsonObject(value, label) {
  const text = String(value || "").trim();
  if (!text) return {};
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`${label} must contain valid JSON.`);
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed;
}

function keyTypeFormError(message, target) {
  setToneMessage($("#type-builder-error"), message, "error");
  if (target) target.focus({ preventScroll: false });
}

async function handleKeyTypeSubmit(event) {
  event.preventDefault();
  const form = $("#key-type-form");
  const button = $("#type-builder-submit");
  if (!form || !button) return;
  setToneMessage($("#type-builder-error"), "", "error");

  const id = ($("#type-id-input")?.value || "").trim().toLowerCase();
  const name = ($("#type-name-input")?.value || "").trim();
  if (!state.keyTypeBuilderId && !/^[a-z][a-z0-9_]{2,49}$/.test(id)) {
    keyTypeFormError(
      "Use 3–50 lowercase letters, digits, or underscores, starting with a letter.",
      $("#type-id-input")
    );
    return;
  }
  if (!name) {
    keyTypeFormError("Enter a display name for this key type.", $("#type-name-input"));
    return;
  }

  const permissions = readCheckedValues($("#type-permissions-grid"), "data-type-permission");
  if (!permissions.length) {
    keyTypeFormError("Select at least one permission for the type ceiling.", $("#type-permissions-grid"));
    return;
  }
  const presets = readCheckedValues($("#type-expiry-presets"), "data-type-expiry");
  if (!presets.length) {
    keyTypeFormError("Select at least one allowed expiration preset.", $("#type-expiry-presets"));
    return;
  }
  const defaultExpiry = $("#type-default-expiry")?.value || "";
  if (!presets.includes(defaultExpiry)) {
    keyTypeFormError("Choose a default expiration that is allowed for this type.", $("#type-default-expiry"));
    return;
  }

  let metadataSchema;
  let validationRules;
  try {
    metadataSchema = parseJsonObject($("#type-metadata-input")?.value, "Metadata schema");
    validationRules = parseJsonObject($("#type-validation-input")?.value, "Validation rules");
  } catch (error) {
    keyTypeFormError(error.message, $("#type-validation-input"));
    return;
  }

  const payload = {
    id,
    name,
    description: ($("#type-description-input")?.value || "").trim(),
    project_id: $("#type-project-select")?.value || null,
    key_prefix: ($("#type-prefix-input")?.value || "").trim().toUpperCase(),
    key_format: {
      groups: Number($("#type-groups-input")?.value) || 4,
      group_length: Number($("#type-group-length-input")?.value) || 4,
      separator: $("#type-separator-select")?.value ?? "-",
      suffix: ($("#type-suffix-input")?.value || "").trim().toUpperCase(),
    },
    permissions,
    scopes: ($("#type-scopes-input")?.value || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
    expiration_policy: { presets, default: defaultExpiry },
    metadata_schema: metadataSchema,
    validation_rules: validationRules,
    is_active: Boolean($("#type-active-input")?.checked),
  };

  const editing = Boolean(state.keyTypeBuilderId);
  setButtonBusy(button, true, editing ? "Saving…" : "Creating…");
  try {
    const result = editing
      ? await api.updateKeyType(state.keyTypeBuilderId, payload)
      : await api.createKeyType(payload);
    const saved = result && result.key_type ? result.key_type : null;
    state.keyTypesLoaded = false;
    closeTypeBuilder();
    await loadKeyTypeCatalogue();
    toast(
      saved
        ? `Key type '${saved.name}' ${editing ? "updated" : "created"}.`
        : "Key type saved.",
      { type: "success" }
    );
  } catch (error) {
    const fields = error instanceof ApiProblem && error.details && error.details.fields;
    const detail = fields && typeof fields === "object"
      ? Object.entries(fields).map(([key, value]) => `${key}: ${value}`).join(" · ")
      : "";
    keyTypeFormError(error && error.message ? `${error.message}${detail ? ` ${detail}` : ""}` : "Key type save failed.");
    fail(error, "Key type save failed.");
  } finally {
    setButtonBusy(button, false);
  }
}

async function toggleKeyType(type) {
  try {
    await api.updateKeyType(type.id, { is_active: !type.is_active });
    state.keyTypesLoaded = false;
    await loadKeyTypeCatalogue();
    toast(`Key type '${type.name}' ${type.is_active ? "disabled" : "enabled"}.`, {
      type: "success",
    });
  } catch (error) {
    fail(error, "Key type status update failed.");
  }
}

async function deleteKeyTypePrompt(type) {
  if (Number(type.key_count) > 0) {
    toast("This key type is referenced by existing keys. Disable it instead.", { type: "error" });
    return;
  }
  const confirmed = await confirmAction({
    title: `Delete '${type.name}'?`,
    message:
      "This permanently removes the custom type definition. Existing keys or imported records block deletion and must be handled first. Built-in types are never deletable.",
    confirmLabel: "Delete Key Type",
    tone: "danger",
    requireText: type.id,
    textLabel: `Type the type ID (${type.id}) to confirm`,
  });
  if (!confirmed.confirmed) return;
  try {
    await api.deleteKeyType(type.id);
    state.keyTypesLoaded = false;
    if (state.keyTypeBuilderId === type.id) closeTypeBuilder();
    await loadKeyTypeCatalogue();
    toast(`Key type '${type.name}' deleted.`, { type: "success" });
  } catch (error) {
    fail(error, "Key type deletion failed.");
  }
}

function renderCheckChoices(container, values, selected, dataAttribute, labeler) {
  if (!container) return;
  clearNode(container);
  [...new Set(values || [])].forEach((value) => {
    const input = el("input", {
      type: "checkbox",
      [dataAttribute]: value,
      checked: (selected || []).includes(value),
    });
    const label = el("label", { class: "check-option" }, [
      input,
      el("span", {}, [
        el("strong", { text: labeler(value) }),
        el("small", { text: value }),
      ]),
    ]);
    container.append(label);
  });
}

function renderMetadataFields(container, type) {
  if (!container) return;
  clearNode(container);
  const schema = type && type.metadata_schema && type.metadata_schema.fields;
  const required = type && type.validation_rules && type.validation_rules.required_metadata;
  const requiredNames = Array.isArray(required) ? required : [];
  const names = [
    ...new Set([
      ...Object.keys(schema && typeof schema === "object" ? schema : {}),
      ...requiredNames,
    ]),
  ];

  names.forEach((name) => {
    const spec = schema && schema[name] && typeof schema[name] === "object" ? schema[name] : {};
    const kind = spec.type || "string";
    const isRequired = Boolean(spec.required) || requiredNames.includes(name);
    let control;

    if (kind === "boolean") {
      control = el("input", {
        type: "checkbox",
        "data-metadata-key": name,
        "data-metadata-type": kind,
      });
      container.append(
        el("label", { class: "check-option" }, [
          control,
          el("span", {}, [
            el("strong", { text: name }),
            el("small", { text: "Boolean" }),
          ]),
        ])
      );
      return;
    }

    if (kind === "array" || kind === "object") {
      control = el("textarea", {
        rows: "2",
        placeholder: kind === "array" ? '[\"value\"]' : '{\"key\": \"value\"}',
        "data-metadata-key": name,
        "data-metadata-type": kind,
      });
    } else {
      control = el("input", {
        type: kind === "number" ? "number" : "text",
        step: kind === "number" ? "any" : undefined,
        maxlength: kind === "string" ? spec.max_length || 500 : undefined,
        "data-metadata-key": name,
        "data-metadata-type": kind,
      });
    }
    if (isRequired) control.required = true;
    container.append(
      el("label", { class: "field" }, [
        el("span", { class: "field-label", text: `${name}${isRequired ? " *" : ""}` }),
        control,
        el("small", { class: "muted", text: `${kind}${isRequired ? " · required" : ""}` }),
      ])
    );
  });
}

function readCheckedValues(container, attribute) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(`[${attribute}]:checked`)).map(
    (input) => input.getAttribute(attribute)
  );
}

function readScopeValues() {
  const checked = readCheckedValues($("#gen-scopes-grid"), "data-scope");
  const free = ($("#gen-scopes-free")?.value || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  return [...new Set([...checked, ...free])];
}

function readMetadataValues() {
  const values = {};
  $$("#gen-metadata-fields [data-metadata-key]").forEach((control) => {
    const name = control.dataset.metadataKey;
    const kind = control.dataset.metadataType;
    if (kind === "boolean") {
      values[name] = Boolean(control.checked);
      return;
    }
    const raw = control.value.trim();
    if (!raw) return;
    if (kind === "number") {
      const parsed = Number(raw);
      if (Number.isNaN(parsed)) throw new Error(`${name} must be a number.`);
      values[name] = parsed;
    } else if (kind === "array" || kind === "object") {
      try {
        values[name] = JSON.parse(raw);
      } catch {
        throw new Error(`${name} must contain valid JSON.`);
      }
    } else {
      values[name] = raw;
    }
  });
  return values;
}

function localDateTimeValue(iso) {
  return toLocalInputValue(iso);
}

function projectExpiryLabel(project) {
  if (!project || !project.expires_at) return "Never expires";
  const { urgency, msRemaining } = getExpiryUrgency(project.expires_at);
  const formatted = formatDate(project.expires_at);

  let urgencyTag = "";
  let prefix = "Expires";
  if (urgency === "expired" || project.effective_status === "expired") {
    prefix = "Expired";
    urgencyTag = "EXPIRED";
  } else if (urgency === "critical") {
    const hours = Math.max(1, Math.ceil(msRemaining / (1000 * 60 * 60)));
    urgencyTag = `${hours}h left`;
  } else if (urgency === "warning") {
    const days = Math.ceil(msRemaining / (1000 * 60 * 60 * 24));
    urgencyTag = `${days}d left`;
  }

  const node = el("span", { class: `expiry-indicator expiry-${urgency}` }, [
    el("span", { class: "expiry-main-text", text: `${prefix} ${formatted}` }),
    urgencyTag ? el("span", { class: "expiry-badge", text: urgencyTag }) : null,
  ]);
  node.toString = () => `${prefix} ${formatted}`;
  return node;
}

function updateProjectExpirationPreview({ animate = false } = {}) {
  const select = $("#gen-project-expiration-select");
  const field = $("#gen-project-expiration-field");
  const customField = $("#gen-project-expiration-custom-field");
  const customInput = $("#gen-project-expiration-custom");
  const preview = $("#gen-project-expiration-preview");
  const isNew = state.generatorMode === "new";
  if (field) field.hidden = !isNew;
  if (preview) preview.hidden = !isNew;
  if (!select) return;
  const preset = select.value;
  const wasCustomHidden = Boolean(customField && customField.hidden);
  if (customField) customField.hidden = !isNew || preset !== "custom";
  if (!preview) return;

  if (animate) {
    replayMotion(preview, "is-flash", 540);
    if (isNew && preset === "custom" && wasCustomHidden) {
      replayMotion(customField, "is-field-in", 320);
    }
  }

  let text = EXPIRATION_LABELS[preset] || "Never Expires";
  if (preset === "never") text = "Never expires — stored explicitly on the project.";
  if (preset === "custom") {
    text = customInput && customInput.value
      ? `Expires ${formatLocalInput(customInput.value)}`
      : "Choose a project expiration date and time.";
  }
  clearNode(preview);
  preview.append(el("span", { class: "muted", text: "Project expiration:" }));
  preview.append(el("strong", { text }));
}

async function loadGeneratorKeyTypes() {
  const select = $("#gen-key-type-select");
  if (!select) return;
  let types = [];
  try {
    types = await loadKeyTypes();
  } catch (error) {
    clearNode(select);
    select.append(el("option", { value: "", text: "Key types unavailable" }));
    return;
  }
  const projectId = state.generatorMode === "existing"
    ? $("#gen-project-select")?.value || ""
    : "";
  const visible = types.filter(
    (type) => !type.project_id || String(type.project_id) === String(projectId)
  );
  const current = select.value;
  clearNode(select);
  if (!visible.length) {
    select.append(el("option", { value: "", text: "No key types available" }));
    select.disabled = true;
    updateGeneratorType();
    return;
  }
  select.disabled = false;
  visible.forEach((type) => {
    select.append(
      el("option", {
        value: type.id,
        text: type.project_id ? `${type.name} · project custom` : type.name,
      })
    );
  });
  select.value = visible.some((type) => type.id === current)
    ? current
    : visible.some((type) => type.id === "reader")
      ? "reader"
      : visible[0].id;
  updateGeneratorType();
}

function updateGeneratorType({ animate = false } = {}) {
  const select = $("#gen-key-type-select");
  const summary = $("#gen-key-type-summary");
  const config = $("#gen-type-config");
  if (!select || !summary || !config) return;
  const type = state.keyTypesById.get(select.value);
  if (!type) {
    summary.hidden = true;
    config.hidden = true;
    return;
  }

  clearNode(summary);
  summary.hidden = false;
  summary.append(
    el("div", { class: "key-type-summary-head" }, [
      el("strong", { text: type.name || type.id }),
      el("span", {
        class: `badge ${type.is_builtin ? "badge-info" : "badge-success"}`,
        text: type.is_builtin ? "BUILT-IN" : "PROJECT CUSTOM",
      }),
    ]),
    el("p", { class: "muted", text: type.description || "Universal key type." }),
    el("p", {
      class: "muted key-type-meta",
      text: `Prefix ${type.key_prefix || "NX"} · ${keyTypePresets(type).map((value) => EXPIRATION_LABELS[value]).join(", ")}`,
    })
  );
  config.hidden = false;

  const presets = keyTypePresets(type);
  rebuildExpirationSelect($("#gen-expiration-select"), presets, {
    selected: defaultKeyTypePreset(type),
  });
  buildPresetChips();
  updateExpirationPreview({ animate });

  const permissions = Array.isArray(type.permissions) ? type.permissions.map(String) : [];
  const permissionSection = $("#gen-permissions-section");
  if (permissionSection) permissionSection.hidden = !permissions.length;
  renderCheckChoices(
    $("#gen-permissions-grid"),
    permissions,
    permissions,
    "data-permission",
    permissionLabel
  );

  const scopes = Array.isArray(type.scopes) ? type.scopes.map(String) : [];
  const scopeSection = $("#gen-scopes-section");
  if (scopeSection) scopeSection.hidden = false;
  renderCheckChoices($("#gen-scopes-grid"), scopes, scopes, "data-scope", (value) => value);
  const freeScopes = $("#gen-scopes-free");
  if (freeScopes) {
    freeScopes.hidden = scopes.length > 0;
    freeScopes.value = "";
  }

  const metadataContainer = $("#gen-metadata-fields");
  renderMetadataFields(metadataContainer, type);
  const metadataSection = $("#gen-metadata-section");
  if (metadataSection) metadataSection.hidden = !metadataContainer?.children.length;

  const format = type.key_format && typeof type.key_format === "object" ? type.key_format : {};
  const groups = Math.max(1, Number(format.groups) || 4);
  const groupLength = Math.max(1, Number(format.group_length) || 4);
  const separator = String(format.separator || "-");
  const pattern = [
    type.key_prefix || "NX",
    ...Array.from({ length: groups }, () => "*".repeat(groupLength)),
  ].join(separator);
  const formatCode = $("#gen-format-code");
  if (formatCode) formatCode.textContent = pattern;
  const formatHint = $("#gen-format-hint");
  if (formatHint) {
    formatHint.textContent = `${groups * groupLength} random characters · OS CSPRNG · HMAC-SHA256 keyed digest at rest`;
  }
  const submitLabel = $("#gen-submit-label");
  if (submitLabel) submitLabel.textContent = `Generate ${type.name || "Key"}`;

  if (animate) {
    replayMotion(select, "is-picked", 340);
    replayMotion(summary, "is-picked", 420);
  }
}

function setGeneratorMode(mode, { focus = true, animate = false } = {}) {
  const nextMode = mode === "new" ? "new" : "existing";
  const changed = state.generatorMode !== nextMode;
  state.generatorMode = nextMode;
  const existingButton = $("#gen-mode-existing");
  const newButton = $("#gen-mode-new");
  const existingField = $("#gen-existing-field");
  const newField = $("#gen-new-field");
  const select = $("#gen-project-select");
  const nameInput = $("#gen-project-name-input");

  const existing = state.generatorMode === "existing";
  if (existingButton) {
    existingButton.classList.toggle("is-active", existing);
    existingButton.setAttribute("aria-pressed", String(existing));
  }
  if (newButton) {
    newButton.classList.toggle("is-active", !existing);
    newButton.setAttribute("aria-pressed", String(!existing));
  }
  if (animate && changed) {
    replayMotion(existing ? existingButton : newButton, "is-selected", 300);
  }
  if (existingField) existingField.hidden = !existing;
  if (newField) newField.hidden = existing;
  if (select) select.required = existing;
  if (nameInput) nameInput.required = !existing;

  updateProjectExpirationPreview();
  replayMotion(existing ? existingField : newField, "is-field-in", 320);
  replayMotion(existing ? existingButton : newButton, "is-selected", 320);
  loadGeneratorKeyTypes().catch(() => {});

  if (focus) {
    const target = existing ? select : nameInput;
    if (target) target.focus({ preventScroll: true });
  }
}

function updateExpirationPreview({ animate = false } = {}) {
  const select = $("#gen-expiration-select");
  const customField = $("#gen-custom-expiration-field");
  const customInput = $("#gen-expiration-custom");
  const preview = $("#gen-expiration-preview");
  if (!select) return;

  const preset = select.value;
  const isCustom = preset === "custom";
  const wasHidden = Boolean(customField && customField.hidden);
  if (customField) customField.hidden = !isCustom;
  if (preview) {
    preview.classList.toggle("is-custom", isCustom);
    preview.classList.toggle("is-never", preset === "never");
  }
  syncPresetChips();

  if (animate) {
    replayMotion(preview, "is-flash", 540);
    replayMotion(
      document.querySelector(`#gen-expiration-presets .preset-chip[data-preset="${preset}"]`),
      "is-selected",
      320
    );
    if (isCustom && wasHidden) replayMotion(customField, "is-field-in", 320);
  }

  let text = EXPIRATION_LABELS[preset] || "30 Days";
  if (preset === "keep") {
    text = "Current expiration is unchanged.";
  } else if (preset === "never") {
    text = "Never expires — stored explicitly on the key.";
  } else if (isCustom) {
    const value = customInput ? customInput.value : "";
    text = value
      ? `Expires ${formatLocalInput(value)}`
      : "Choose a date and time below.";
  }
  if (preview) {
    clearNode(preview);
    preview.append(el("span", { class: "muted", text: "Expiration:" }));
    preview.append(el("strong", { text }));
    preview.append(
      el("span", {
        class: "muted",
        text: "Server-enforced · UTC storage · status updates automatically",
      })
    );
  }
}

async function loadGenerator() {
  await loadGeneratorProjects();
  setGeneratorMode(state.generatorMode, { focus: false });
  await loadGeneratorKeyTypes();
  updateProjectExpirationPreview();
  updateExpirationPreview();
  const target =
    state.generatorMode === "existing"
      ? $("#gen-project-select")
      : $("#gen-project-name-input");
  if (target && window.matchMedia("(pointer: fine)").matches) {
    target.focus({ preventScroll: true });
  }
}

function createGeneratorBatchId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(
    16,
    20
  )}-${hex.slice(20)}`;
}

function showGeneratorRecovery({ title, message, retry = true, viewKeys = false }) {
  const panel = $("#generator-recovery");
  if (!panel) return;
  state.generatorRecovery = { retry, viewKeys };
  setText("#generator-recovery-title", title || "Key generation needs attention");
  setText("#generator-recovery-message", message || "");
  const retryButton = $("#generator-retry-btn");
  const viewButton = $("#generator-recovery-view-btn");
  if (retryButton) retryButton.hidden = !retry;
  if (viewButton) viewButton.hidden = !viewKeys;
  panel.hidden = false;
}

function hideGeneratorRecovery() {
  const panel = $("#generator-recovery");
  if (panel) panel.hidden = true;
  state.generatorRecovery = null;
}

async function handleGenerateKeys(event) {
  event.preventDefault();

  const nameInput = $("#gen-project-name-input");
  const projectSelect = $("#gen-project-select");
  const typeSelect = $("#gen-key-type-select");
  const labelInput = $("#gen-label-input");
  const tagsInput = $("#gen-tags-input");
  const notesInput = $("#gen-notes-input");
  const expirationSelect = $("#gen-expiration-select");
  const customInput = $("#gen-expiration-custom");
  const projectExpirationSelect = $("#gen-project-expiration-select");
  const projectCustomInput = $("#gen-project-expiration-custom");
  const errorNode = $("#generator-error");
  const submitButton = $("#generate-keys-btn");
  const cryptoVisual = $(".crypto-visual");
  const projectName = nameInput ? nameInput.value.trim() : "";
  const projectId = projectSelect ? projectSelect.value : "";
  const type = state.keyTypesById.get(typeSelect ? typeSelect.value : "");
  const label = labelInput ? labelInput.value.trim() : "";
  const expiration = expirationSelect ? expirationSelect.value : "30_days";

  if (submitButton && submitButton.disabled) return;

  setToneMessage(errorNode, "", "error");
  if (nameInput) nameInput.classList.remove("is-invalid");
  if (projectSelect) projectSelect.classList.remove("is-invalid");
  if (typeSelect) typeSelect.classList.remove("is-invalid");
  if (customInput) customInput.classList.remove("is-invalid");

  if (state.generatorMode === "existing" && !projectId) {
    setToneMessage(errorNode, "Select an existing project before generating a key.", "error");
    if (projectSelect) {
      projectSelect.classList.add("is-invalid");
      projectSelect.focus();
    }
    announce("Select an existing project before generating a key.");
    return;
  }

  if (state.generatorMode === "new" && !projectName) {
    setToneMessage(errorNode, "Enter a project name before generating a key.", "error");
    if (nameInput) {
      nameInput.classList.add("is-invalid");
      nameInput.focus();
    }
    announce("Enter a project name before generating a key.");
    return;
  }

  if (!type) {
    setToneMessage(errorNode, "Choose a key type before generating a key.", "error");
    if (typeSelect) {
      typeSelect.classList.add("is-invalid");
      typeSelect.focus();
    }
    return;
  }

  const allowedPresets = (expirationSelect?.dataset.presets || "").split(",").filter(Boolean);
  if (allowedPresets.length && !allowedPresets.includes(expiration)) {
    setToneMessage(errorNode, "This expiration is not allowed for the selected key type.", "error");
    if (expirationSelect) expirationSelect.focus();
    return;
  }

  let expiresAt = null;
  if (expiration === "custom") {
    const raw = customInput ? customInput.value : "";
    try {
      expiresAt = raw ? localInputToUtcIso(raw) : null;
    } catch (error) {
      setToneMessage(errorNode, error.message, "error");
      if (customInput) {
        customInput.classList.add("is-invalid");
        customInput.focus();
      }
      return;
    }
    if (!expiresAt) {
      setToneMessage(errorNode, "Choose a valid custom expiration date and time.", "error");
      if (customInput) {
        customInput.classList.add("is-invalid");
        customInput.focus();
      }
      return;
    }
    if (new Date(expiresAt).getTime() <= Date.now()) {
      setToneMessage(errorNode, "The custom expiration must be in the future.", "error");
      if (customInput) {
        customInput.classList.add("is-invalid");
        customInput.focus();
      }
      return;
    }
  }

  let projectExpiration = "never";
  let projectExpiresAt = null;
  if (state.generatorMode === "new") {
    projectExpiration = projectExpirationSelect ? projectExpirationSelect.value : "never";
    if (projectExpiration === "custom") {
      const raw = projectCustomInput ? projectCustomInput.value : "";
      try {
        projectExpiresAt = raw ? localInputToUtcIso(raw) : null;
      } catch (error) {
        setToneMessage(errorNode, error.message, "error");
        if (projectCustomInput) projectCustomInput.focus();
        return;
      }
      if (!projectExpiresAt || new Date(projectExpiresAt).getTime() <= Date.now()) {
        setToneMessage(
          errorNode,
          "Choose a future custom project expiration date and time.",
          "error"
        );
        if (projectCustomInput) {
          projectCustomInput.classList.add("is-invalid");
          projectCustomInput.focus();
        }
        return;
      }
    }
  }

  const permissions = readCheckedValues($("#gen-permissions-grid"), "data-permission");
  const scopes = readScopeValues();
  let metadata = {};
  try {
    metadata = readMetadataValues();
  } catch (metadataError) {
    setToneMessage(errorNode, metadataError.message, "error");
    return;
  }
  const tags = ($("#gen-tags-input")?.value || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 20);
  const notes = notesInput ? notesInput.value.trim() : "";

  const generatorForm = $("#key-generator-form");
  const batchId = state.generatorBatchId || createGeneratorBatchId();
  state.generatorBatchId = batchId;
  if (generatorForm) generatorForm.setAttribute("aria-busy", "true");

  setButtonBusy(submitButton, true, "Generating securely…");
  const generatorPanel = generatorForm ? generatorForm.closest(".generator-form-panel") : null;
  if (submitButton) submitButton.classList.add("is-surging");
  if (cryptoVisual) cryptoVisual.classList.add("is-processing");
  let projectCreated = false;
  let createdProject = null;
  let keyRequestStarted = false;

  try {
    let project;
    if (state.generatorMode === "new") {
      const created = await api.createProject(projectName, {
        expiration: projectExpiration,
        expires_at: projectExpiresAt,
      });
      project = created.project;
      createdProject = project;
      projectCreated = true;
    } else {
      project = state.projects.find((item) => String(item.id) === String(projectId)) || {
        id: projectId,
        project_name: projectSelect?.selectedOptions?.[0]?.textContent || "Selected project",
      };
    }

    const body = {
      project_id: project.id,
      key_type_id: type.id,
      batch_id: batchId,
      label,
      expiration_preset: expiration,
      expires_at: expiresAt,
      permissions,
      scopes,
      tags,
      notes,
      metadata,
    };
    keyRequestStarted = true;
    const result = await api.generateUniversalKeys(body);
    const generated = (result.keys || []).map((item) => ({
      ...item,
      plaintext_key: item.key,
      project_name: project.project_name,
      key_type_id: type.id,
      key_type_name: type.name || type.id,
      expiration_preset: expiration,
      is_permanent: expiration === "never" || !item.expires_at,
      created_at: new Date().toISOString(),
      label,
    }));
    if (!generated.length) {
      throw new Error("The server did not return a generated key.");
    }

    state.generatorBatchId = null;
    hideGeneratorRecovery();
    state.generatedKeys = generated;
    replayMotion(generatorPanel, "is-success", 520);
    renderGeneratedKeysModal(generated[0]);
    toast(
      projectCreated
        ? `Project '${project.project_name}' created with its first ${type.name || "key"}.`
        : `${type.name || "Key"} issued for '${project.project_name}'.`,
      { type: "success" }
    );
    if (nameInput) nameInput.value = "";
    if (labelInput) labelInput.value = "";
    if (tagsInput) tagsInput.value = "";
    if (notesInput) notesInput.value = "";
    await loadGeneratorProjects();
    await loadGeneratorKeyTypes();
    loadOverview().catch(() => {});
  } catch (error) {
    if (
      error instanceof ApiProblem &&
      error.status === 409 &&
      error.code === "PROJECT_ALREADY_EXISTS" &&
      !keyRequestStarted
    ) {
      state.generatorBatchId = null;
      state.generatedKeys = [];
      renderExistingProjectModal(error.details || {}, projectName);
      await loadGeneratorProjects();
      return;
    }

    if (projectCreated) {
      await loadGeneratorProjects().catch(() => {});
      setGeneratorMode("existing", { focus: false });
      if (createdProject && projectSelect) {
        const optionExists = Array.from(projectSelect.options).some(
          (option) => option.value === createdProject.id
        );
        if (optionExists) projectSelect.value = createdProject.id;
      }
      await loadGeneratorKeyTypes().catch(() => {});
    }

    const fallback = projectCreated
      ? "The project was created, but the key could not be generated. Retry below without creating another project."
      : "Failed to generate the key.";
    const message = error && error.message ? error.message : fallback;
    setToneMessage(errorNode, message, "error");
    replayMotion(errorNode, "is-invalid", 460);
    replayMotion(generatorPanel, "is-shaking", 520);

    if (
      error instanceof ApiProblem &&
      error.code === "BATCH_ALREADY_PROCESSED"
    ) {
      state.generatorBatchId = null;
      const hints = Array.isArray(error.details?.key_hints)
        ? error.details.key_hints.filter(Boolean).join(", ")
        : "";
      showGeneratorRecovery({
        title: "This key was already created",
        message:
          `The server already processed this generation batch, so no duplicate key was created. ` +
          `The plaintext cannot be revealed again.${hints ? ` Existing key hint: ${hints}.` : ""}`,
        retry: false,
        viewKeys: true,
      });
      toast("Already processed — no duplicate key was created.", { type: "warning" });
    } else {
      if (!keyRequestStarted) state.generatorBatchId = null;
      if (keyRequestStarted) {
        showGeneratorRecovery({
          title: projectCreated
            ? "Project created; key generation failed"
            : "Key generation did not complete",
          message:
            `${message} ` +
            "Retry uses this same project and generation batch, so an uncertain network result cannot silently create a duplicate.",
          retry: true,
          viewKeys: false,
        });
      } else {
        hideGeneratorRecovery();
      }
      fail(error, fallback);
    }

    if (!keyRequestStarted) {
      const invalidTarget = state.generatorMode === "new" ? nameInput : projectSelect;
      if (invalidTarget) {
        invalidTarget.classList.add("is-invalid");
        invalidTarget.focus();
      }
    }
  } finally {
    if (generatorForm) generatorForm.removeAttribute("aria-busy");
    if (submitButton) submitButton.classList.remove("is-surging");
    setButtonBusy(submitButton, false);
    if (cryptoVisual) cryptoVisual.classList.remove("is-processing");
  }
}

function setResultButtonsVisible(visible) {
  ["#copy-all", "#download-keys-csv"].forEach((selector) => {
    const button = $(selector);
    if (button) button.hidden = !visible;
  });
}

function buildKeySegments(plaintext) {
  const code = el("code", { class: "key-plaintext" });
  code.setAttribute("aria-label", plaintext);
  const parts = String(plaintext).split("-");

  parts.forEach((part, index) => {
    if (index > 0) {
      code.append(el("span", { class: "key-dash", "aria-hidden": "true", text: "-" }));
    }
    const segment = el("span", { class: "key-segment", text: part });
    segment.style.setProperty("--seg-i", String(index));
    code.append(segment);
  });

  return code;
}

function renderGeneratedKeysModal(key) {
  const container = $("#generated-list");
  const modal = $("#result-modal");
  if (!container || !modal) return;

  if (!key || !key.plaintext_key) {
    fail(new Error("The server response did not include a one-time key."));
    return;
  }

  clearNode(container);  setText("#result-title", "Key Generated");
  setText(
    "#result-summary",
    `One-time ${key.key_type_name || "key"} for '${key.project_name}'. Plaintext is shown only now and is cleared when this dialog closes.`
  );

  const warning = $("#result-warning");
  if (warning) {
    warning.hidden = false;
    warning.textContent =
      "CRITICAL: Store this plaintext key right now. It is cryptographically hashed and cannot be retrieved again after closing this dialog.";
  }
  setResultButtonsVisible(true);

  const copyButton = el("button", {
    class: "btn btn-primary btn-sm key-copy",
    type: "button",
    text: "Copy key",
  });
  copyButton.addEventListener("click", () =>
    copyWithFeedback(key.plaintext_key, copyButton, "Plaintext key copied to clipboard.")
  );

  const reveal = el("article", { class: "key-reveal" }, [
    el("div", { class: "key-reveal-head" }, [
      el("span", { class: "key-founder-mark", "aria-hidden": "true" }),
      el("span", { class: "key-reveal-label", text: "Plaintext key / one-time reveal" }),
      el("span", { class: "badge badge-info", text: "ONE-TIME" }),
    ]),
    buildKeySegments(key.plaintext_key),
    el("p", { class: "key-reveal-meta key-reveal-expiration" }, [
      el("span", { text: "Expiration setting: " }),
      el("strong", {
        text:
          EXPIRATION_LABELS[key.expiration_preset] ||
          (key.is_permanent ? "Never Expires" : "Custom Expiration"),
      }),
      el("span", { text: " · Resulting expiration: " }),
      el("strong", {
        text:
          key.is_permanent || key.expiration_preset === "never"
            ? "Never (explicit)"
            : formatDate(key.expires_at),
      }),
    ]),
    el("p", {
      class: "key-reveal-meta",
      text: `${key.key_hint} · Created ${formatDate(key.created_at)}${
        key.label ? ` · ${key.label}` : ""
      }`,
    }),
    el("p", { class: "key-reveal-meta", text: `Key ID ${key.id} · Project ${key.project_name}` }),
    el("div", { class: "key-reveal-actions" }, [copyButton]),
  ]);

  openDialog(modal);

  // Defer appending the key-reveal until the dialog is visible so the
  // entrance and segment animations play while the modal is on-screen.
  window.requestAnimationFrame(() => {
    container.append(reveal);
    // The reveal element is now visible inside the open modal.
    // Replay the entrance so the cascade plays even if the dialog
    // was already open (e.g. round-trip to the same view).
    if (reveal) replayMotion(reveal, "is-key-reveal", 600);
  });

  announce(`Key generated for ${key.project_name}. Copy it now; it is shown only once.`);
}

function renderExistingProjectModal(details, requestedName) {
  const container = $("#generated-list");
  const modal = $("#result-modal");
  if (!container || !modal) return;

  clearNode(container);
  const project = details.project || {};
  const key = details.reader_key || null;
  const name = project.project_name || requestedName;

  setText("#result-title", "Project Already Exists");
  setText(
    "#result-summary",
    `'${name}' already exists with ${details.key_count || (key ? 1 : 0)} key(s). No new project was created. Choose it under Existing Project to issue another key.`
  );

  const warning = $("#result-warning");
  if (warning) {
    warning.hidden = false;
    warning.textContent =
      "For security, an existing key is never displayed again. Use Key Management for its masked status.";
  }
  setResultButtonsVisible(false);

  container.append(
    recordGrid([
      recordItem("Project Name", name),
      recordItem("Project Status", project.status || "—"),
      recordItem("Key Status", key ? key.status || "—" : "No key exists"),
      recordItem("Key Hint", key ? key.key_hint || "—" : "—", { mono: true }),
      recordItem("Key Created", key ? formatDate(key.created_at) : "—"),
      recordItem("Key Expires", key ? expirationText(key) : "—"),
      recordItem("Key Used At", key ? formatDate(key.used_at) : "—"),
    ])
  );

  openDialog(modal);
  announce(`Project ${name} already exists. No new key was generated.`);
}

function purgeGeneratedKeys() {
  const container = $("#generated-list");
  if (container) clearNode(container);
  state.generatedKeys = [];
  setText("#result-summary", "");
}

/* -------------------------------------------------------------- key list */

async function populateProjectsFilter(selector) {
  const select = $(selector);
  if (!select) return;

  const current = select.value;
  try {
    const data = await api.projects({ include_deleted: false });
    const projects = data.projects || [];
    clearNode(select);
    select.append(el("option", { value: "", text: "All Projects" }));
    projects.forEach((project) => {
      select.append(el("option", { value: project.id, text: project.project_name }));
    });
    if (current && Array.from(select.options).some((option) => option.value === current)) {
      select.value = current;
    }
  } catch {
    // Filters are secondary; the key table reports the authoritative error.
  }
}

async function loadKeys() {
  const projectId = $("#keys-filter-project") ? $("#keys-filter-project").value : "";
  const status = $("#keys-filter-status") ? $("#keys-filter-status").value : "";
  const expiryFilter = $("#keys-filter-expiry") ? $("#keys-filter-expiry").value : "";
  const search = $("#keys-search-input") ? $("#keys-search-input").value.trim() : "";
  const pageSize = $("#keys-page-size") ? parseInt($("#keys-page-size").value, 10) : 25;
  const page = state.keysPagination.page || 1;
  const tbody = $("#keys-body");

  beginTableLoad(tbody, KEY_LABELS.length, KEY_LABELS, "#keys-summary");
  await populateProjectsFilter("#keys-filter-project");

  try {
    const data = await api.readerKeys({
      project_id: projectId,
      status,
      expiry_filter: expiryFilter,
      search,
      page,
      page_size: pageSize,
    });

    state.keys = data.items || [];
    state.keysPagination = normalizePagination(data.pagination, page, pageSize);
    renderKeysTable(state.keys);
  } catch (error) {
    if (tbody) {
      clearNode(tbody);
      tbody.append(errorRow(KEY_LABELS.length, "Reader keys unavailable"));
    }
    throw error;
  } finally {
    endTableLoad(tbody);
  }
}

function renderKeysTable(keys) {
  const tbody = $("#keys-body");
  if (!tbody) return;
  clearNode(tbody);

  const pagination = state.keysPagination;
  const prev = $("#keys-prev-page");
  const next = $("#keys-next-page");

  if (!keys.length) {
    tbody.append(
      emptyRow(
        KEY_LABELS.length,
        "No reader keys found",
        "Adjust the filters or generate a key for a new project.",
        "⌘"
      )
    );
    setText("#keys-summary", "0 keys");
    setText("#keys-page-info", "Page 1 of 1");
    if (prev) prev.disabled = true;
    if (next) next.disabled = true;
    return;
  }

  const start = (pagination.page - 1) * pagination.page_size + 1;
  const end = Math.min(pagination.page * pagination.page_size, pagination.total);
  setText(
    "#keys-summary",
    `Showing ${start}–${end} of ${pagination.total} key(s)`
  );
  setText("#keys-page-info", `Page ${pagination.page} of ${pagination.total_pages}`);
  if (prev) prev.disabled = pagination.page <= 1;
  if (next) next.disabled = pagination.page >= pagination.total_pages;

  keys.forEach((key) => {
    const revocable = key.status !== "revoked";
    const hint = el("a", {
      href: "#",
      class: "table-link key-hint",
      text: key.key_hint,
      onclick: (event) => {
        event.preventDefault();
        viewKeyDetails(key.id);
      },
    });

    const identity = el("div", { class: "key-identity" }, [
      hint,
      el("span", { class: "key-id", text: key.id }),
      el("span", {
        class: "badge badge-info",
        text: key.key_type_name || key.key_type_id || "reader",
      }),
      key.label ? el("span", { class: "key-label", text: key.label }) : null,
    ]);

    const revoked = key.revoked_at
      ? el("div", { class: "key-revocation" }, [
          el("span", { text: formatDate(key.revoked_at) }),
          el("span", { class: "muted", text: key.revoked_reason || "Revoked by admin" }),
        ])
      : el("span", { class: "muted", text: "—" });

    const row = el("tr", {}, [
      tableCell("Key / Key ID", identity, "table-primary"),
      tableCell("Project", key.project_name, "table-primary"),
      tableCell("Status", statusBadge(key.status)),
      tableCell("Created", formatDate(key.created_at)),
      tableCell("Expires", expirationText(key)),
      tableCell("First Used", formatDate(key.first_used_at || key.used_at)),
      tableCell("Revoked", revoked),
      tableCell(
        "Actions",
        el(
          "div",
          { class: "row-actions" },
          revocable
            ? [
                el("button", {
                  class: "btn btn-danger btn-sm",
                  type: "button",
                  text: "Revoke",
                  onclick: () => revokeKeyPrompt(key),
                }),
              ]
            : [el("span", { class: "badge badge-danger", text: "Revoked" })]
        ),
        "align-right"
      ),
    ]);

    tbody.append(row);
  });
}

function buildKeyEditSection(key, type) {
  const expirySelect = el("select", {
    id: "key-expiry-select",
    "aria-label": "Key expiration",
    "data-custom-select": true,
    "data-placeholder": "Key expiration",
  });
  rebuildExpirationSelect(expirySelect, keyTypePresets(type), {
    includeKeep: true,
    selected: "keep",
  });
  const customInput = el("input", {
    type: "datetime-local",
    id: "key-expiry-custom",
    value: localDateTimeValue(key.expires_at),
    "aria-label": "Custom key expiration",
  });
  const customField = el("label", { class: "field", hidden: true }, [
    el("span", { class: "field-label", text: "Custom key expiration" }),
    customInput,
    el("span", { class: "muted", text: "Local time; stored in UTC. Must be in the future." }),
  ]);
  const expiryPreview = el("div", { class: "expiration-preview", "aria-live": "polite" });
  const syncExpiry = () => {
    const custom = expirySelect.value === "custom";
    customField.hidden = !custom;
    let text = "Current expiration is unchanged.";
    if (expirySelect.value === "never") text = "Never expires.";
    else if (custom) {
      text = customInput.value
        ? `Expires ${formatLocalInput(customInput.value)}`
        : "Choose a key expiration date and time.";
    }
    clearNode(expiryPreview);
    expiryPreview.append(el("span", { class: "muted", text: "Key expiration:" }));
    expiryPreview.append(el("strong", { text }));
  };
  expirySelect.addEventListener("change", syncExpiry);
  customInput.addEventListener("input", syncExpiry);
  syncExpiry();

  const permissionSection = el("section", { class: "generator-config-section" }, [
    el("span", { class: "field-label", text: "Permissions" }),
  ]);
  const permissionGrid = el("div", { class: "check-grid" });
  const allowedPermissions = Array.isArray(type.permissions)
    ? type.permissions.map(String)
    : [];
  if (allowedPermissions.length) {
    renderCheckChoices(
      permissionGrid,
      allowedPermissions,
      Array.isArray(key.permissions) ? key.permissions : [],
      "data-permission",
      permissionLabel
    );
    permissionSection.append(permissionGrid);
  } else {
    permissionSection.hidden = true;
  }

  const scopeSection = el("section", { class: "generator-config-section" }, [
    el("span", { class: "field-label", text: "Scopes" }),
  ]);
  const scopeGrid = el("div", { class: "check-grid" });
  const freeScopes = el("input", {
    type: "text",
    placeholder: "comma-separated scopes",
    maxlength: "500",
  });
  const allowedScopes = Array.isArray(type.scopes) ? type.scopes.map(String) : [];
  if (allowedScopes.length) {
    renderCheckChoices(
      scopeGrid,
      allowedScopes,
      Array.isArray(key.scopes) ? key.scopes : [],
      "data-scope",
      (value) => value
    );
    scopeSection.append(scopeGrid);
  } else {
    scopeSection.append(freeScopes);
  }

  const labelInput = el("input", {
    type: "text",
    value: key.label || "",
    maxlength: "100",
    placeholder: "Key label",
  });
  const tagsInput = el("input", {
    type: "text",
    value: (key.tags || []).join(", "),
    maxlength: "500",
    placeholder: "comma-separated tags",
  });
  const notesInput = el("textarea", {
    rows: "2",
    maxlength: "2000",
    placeholder: "Internal notes",
  });
  notesInput.value = key.notes || "";
  const metadataInput = el("textarea", {
    rows: "2",
    maxlength: "8000",
    placeholder: "{}",
  });
  metadataInput.value = JSON.stringify(key.metadata || {}, null, 2);
  const errorNode = el("p", { class: "form-message", role: "alert" });
  const saveButton = el("button", {
    class: "btn btn-primary btn-sm",
    type: "button",
    text: "Save key changes",
  });
  saveButton.addEventListener("click", () =>
    saveKeyEdits(key, {
      expirySelect,
      customInput,
      permissionGrid,
      scopeGrid,
      freeScopes,
      labelInput,
      tagsInput,
      notesInput,
      metadataInput,
      errorNode,
      saveButton,
    })
  );

  return el("section", { class: "detail-section key-edit-section" }, [
    el("h3", { text: "Edit Key Settings" }),
    el("div", { class: "form-grid" }, [
      el("label", { class: "field" }, [
        el("span", { class: "field-label", text: "Expiration" }),
        expirySelect,
        customField,
        expiryPreview,
      ]),
      el("label", { class: "field" }, [
        el("span", { class: "field-label", text: "Label" }),
        labelInput,
      ]),
      permissionSection,
      scopeSection,
      el("label", { class: "field" }, [
        el("span", { class: "field-label", text: "Tags" }),
        tagsInput,
      ]),
      el("label", { class: "field span-2" }, [
        el("span", { class: "field-label", text: "Notes" }),
        notesInput,
      ]),
      el("label", { class: "field span-2" }, [
        el("span", { class: "field-label", text: "Metadata (JSON)" }),
        metadataInput,
      ]),
      errorNode,
      el("div", { class: "span-2 modal-actions" }, [saveButton]),
    ]),
  ]);
}

async function saveKeyEdits(
  key,
  {
    expirySelect,
    customInput,
    permissionGrid,
    scopeGrid,
    freeScopes,
    labelInput,
    tagsInput,
    notesInput,
    metadataInput,
    errorNode,
    saveButton,
  }
) {
  setToneMessage(errorNode, "", "error");
  const preset = expirySelect.value;
  let expiresAt = null;
  if (preset === "custom") {
    try {
      expiresAt = customInput.value ? localInputToUtcIso(customInput.value) : null;
    } catch (error) {
      setToneMessage(errorNode, error.message, "error");
      return;
    }
    if (!expiresAt || new Date(expiresAt).getTime() <= Date.now()) {
      setToneMessage(errorNode, "Choose a future custom expiration.", "error");
      return;
    }
  }
  let metadata = {};
  try {
    metadata = metadataInput.value.trim() ? JSON.parse(metadataInput.value) : {};
    if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
      throw new Error("Metadata must be a JSON object.");
    }
  } catch (error) {
    setToneMessage(errorNode, "Metadata must be a valid JSON object.", "error");
    return;
  }
  const scopes = [
    ...readCheckedValues(scopeGrid, "data-scope"),
    ...(scopeGrid.children.length ? [] : freeScopes.value.split(",")),
  ]
    .map((value) => value.trim())
    .filter(Boolean);
  const body = {
    label: labelInput.value.trim(),
    permissions: readCheckedValues(permissionGrid, "data-permission"),
    scopes: [...new Set(scopes)],
    tags: tagsInput.value.split(",").map((value) => value.trim()).filter(Boolean),
    notes: notesInput.value.trim(),
    metadata,
  };
  if (preset !== "keep") {
    body.expiration_preset = preset;
    if (preset === "custom") body.expires_at = expiresAt;
  }

  setButtonBusy(saveButton, true, "Saving…");
  try {
    await api.updateUniversalKey(key.id, body);
    toast(`Key ${key.key_hint} updated.`, { type: "success" });
    closeDialog($("#detail-modal"));
    await loadKeys().catch(() => {});
    await loadOverview().catch(() => {});
  } catch (error) {
    setToneMessage(errorNode, error.message || "Failed to update the key.", "error");
  } finally {
    setButtonBusy(saveButton, false);
  }
}

async function viewKeyDetails(keyId) {
  const modal = $("#detail-modal");
  const body = $("#detail-body");
  if (!modal || !body) return;

  clearNode(body);
  body.append(loadingBlock("Loading authenticated key record…"));
  openDialog(modal);

  try {
    await loadKeyTypes().catch(() => {});
    let key;
    try {
      const data = await api.universalKey(keyId);
      key = data.key;
    } catch {
      const data = await api.readerKey(keyId);
      key = data.key;
    }
    const type = state.keyTypesById.get(key.key_type_id) || null;
    const items = [
      recordItem("Key ID", key.id, { mono: true }),
      recordItem("Key Hint (Masked)", key.key_hint, { mono: true }),
      recordItem("Status", statusBadge(key.status)),
      recordItem("Project", key.project_name),
      recordItem("Key Type", key.key_type_name || key.key_type_id || "reader"),
      recordItem("Label", key.label || "None"),
      recordItem(
        "Expiration Setting",
        EXPIRATION_LABELS[key.expiration_preset] || (key.is_permanent ? "Never Expires" : "Custom")
      ),
      recordItem("Expires At", key.is_permanent ? "Never" : formatDate(key.expires_at)),
      recordItem("Permissions", (key.permissions || []).join(", ") || "None", { full: true }),
      recordItem("Scopes", (key.scopes || []).join(", ") || "None", { full: true }),
      recordItem("Tags", (key.tags || []).join(", ") || "None", { full: true }),
      recordItem("Notes", key.notes || "None", { full: true }),
      recordItem("Created At", formatDate(key.created_at)),
      recordItem("First Used At", formatDate(key.first_used_at || key.used_at)),
    ];

    if (key.status === "revoked") {
      items.push(
        recordItem("Revoked At", formatDate(key.revoked_at)),
        recordItem("Revoked By", key.revoked_by_email || "Administrator"),
        recordItem("Revoked Reason", key.revoked_reason || "Revoked by admin", { full: true })
      );
    }
    if (key.installation_id) {
      items.push(
        recordItem(
          "Connected Reader Device",
          `${key.device_id || "Unknown device"} (${key.installation_status || "unknown"})`,
          { full: true }
        )
      );
    }

    clearNode(body);
    body.append(recordGrid(items));
    if (type && key.status !== "revoked") {
      body.append(buildKeyEditSection(key, type));
    }
  } catch (error) {
    closeDialog(modal, { immediate: true });
    fail(error, "Failed to load key details.");
  }
}

async function revokeKeyPrompt(key, { onDone } = {}) {
  const confirmed = await confirmAction({
    title: `Revoke Key '${key.key_hint}'?`,
    message:
      "Revoking this key is permanent. If it has an active installation, that installation and its device tokens are revoked immediately. A revoked key can never become active again.",
    confirmLabel: "Revoke Key",
    tone: "danger",
    requireReason: true,
    reasonLabel: "Revocation Reason (Required for Audit Trail)",
    reasonPlaceholder: "Explain why this key is being revoked…",
  });
  if (!confirmed.confirmed) return;

  try {
    await api.revokeReaderKey(key.id, confirmed.reason);
    toast(`Reader key '${key.key_hint}' revoked.`, { type: "success" });
    await loadKeys();
    await loadOverview();
    if (typeof onDone === "function") await onDone();
  } catch (error) {
    fail(error, "Failed to revoke key.");
  }
}

/* ---------------------------------------------------------- installations */

async function loadInstallations() {
  const projectId = $("#installations-filter-project")
    ? $("#installations-filter-project").value
    : "";
  const status = $("#installations-filter-status")
    ? $("#installations-filter-status").value
    : "";
  const search = $("#installations-search-input")
    ? $("#installations-search-input").value.trim()
    : "";
  const page = state.installationsPagination.page || 1;
  const tbody = $("#installations-table-body");

  beginTableLoad(
    tbody,
    INSTALLATION_LABELS.length,
    INSTALLATION_LABELS,
    "#installations-summary"
  );
  await populateProjectsFilter("#installations-filter-project");

  try {
    const data = await api.installations({
      project_id: projectId,
      status,
      search,
      page,
      page_size: 25,
    });

    state.installations = data.items || [];
    state.installationsPagination = normalizePagination(data.pagination, page, 25);
    renderInstallationsTable(state.installations);
  } catch (error) {
    if (tbody) {
      clearNode(tbody);
      tbody.append(errorRow(INSTALLATION_LABELS.length, "Installations unavailable"));
    }
    throw error;
  } finally {
    endTableLoad(tbody);
  }
}

function renderInstallationsTable(installations) {
  const tbody = $("#installations-table-body");
  if (!tbody) return;
  clearNode(tbody);

  const pagination = state.installationsPagination;
  const prev = $("#installations-prev-page");
  const next = $("#installations-next-page");

  if (!installations.length) {
    tbody.append(
      emptyRow(
        INSTALLATION_LABELS.length,
        "No installations found",
        "Reader installations appear here when a Reader App redeems a key.",
        "✇"
      )
    );
    setText("#installations-summary", "0 installations");
    setText("#installations-page-info", "Page 1 of 1");
    if (prev) prev.disabled = true;
    if (next) next.disabled = true;
    return;
  }

  const start = (pagination.page - 1) * pagination.page_size + 1;
  const end = Math.min(pagination.page * pagination.page_size, pagination.total);
  setText(
    "#installations-summary",
    `Showing ${start}–${end} of ${pagination.total} installation(s)`
  );
  setText("#installations-page-info", `Page ${pagination.page} of ${pagination.total_pages}`);
  if (prev) prev.disabled = pagination.page <= 1;
  if (next) next.disabled = pagination.page >= pagination.total_pages;

  installations.forEach((installation) => {
    const isActive = installation.status === "active";
    const idLink = el("a", {
      href: "#",
      class: "table-link",
      text: (installation.id || "").slice(0, 8),
      onclick: (event) => {
        event.preventDefault();
        viewInstallationDetails(installation.id);
      },
    });

    const row = el("tr", {}, [
      tableCell("Installation ID", idLink, "table-primary"),
      tableCell("Project", installation.project_name),
      tableCell("Redeemed Key Hint", el("code", { class: "key-hint", text: installation.key_hint || "—" })),
      tableCell("Device ID", installation.device_id || "—"),
      tableCell("Status", statusBadge(installation.status)),
      tableCell("Activated At", formatDate(installation.activated_at)),
      tableCell("Last Heartbeat", formatRelative(installation.last_seen_at)),
      tableCell(
        "Actions",
        isActive
          ? el("button", {
              class: "btn btn-danger btn-sm",
              type: "button",
              text: "Revoke",
              onclick: () => revokeInstallationPrompt(installation),
            })
          : null,
        "align-right"
      ),
    ]);

    tbody.append(row);
  });
}

async function viewInstallationDetails(installationId) {
  const modal = $("#installation-detail-modal");
  const body = $("#installation-detail-body");
  if (!modal || !body) return;

  clearNode(body);
  body.append(loadingBlock("Loading authenticated installation record…"));
  openDialog(modal);

  try {
    const data = await api.installation(installationId);
    const installation = data.installation;

    clearNode(body);
    body.append(
      recordGrid([
        recordItem("Installation ID", installation.id, { mono: true }),
        recordItem("Status", statusBadge(installation.status)),
        recordItem("Project", installation.project_name),
        recordItem("Device Identifier", installation.device_id || "Unspecified"),
        recordItem("Activated At", formatDate(installation.activated_at)),
        recordItem("Last Heartbeat", formatDate(installation.last_seen_at)),
        recordItem("Client IP", installation.ip_address || "—", { mono: true }),
        recordItem("Redeemed Key", installation.key_hint || "—", { mono: true }),
      ])
    );
  } catch (error) {
    closeDialog(modal, { immediate: true });
    fail(error, "Failed to load installation details.");
  }
}

async function revokeInstallationPrompt(installation) {
  const confirmed = await confirmAction({
    title: `Revoke Installation for '${installation.project_name}'?`,
    message:
      "Revoking this installation will immediately invalidate its device token credentials. The Reader App will no longer be authorized to reconnect or record heartbeats. A new Reader Key must be generated to connect a new device.",
    confirmLabel: "Revoke Installation & Invalidate Token",
    tone: "danger",
    requireReason: true,
    reasonLabel: "Revocation Reason (Audit Log)",
    reasonPlaceholder: "Explain why this device installation is being revoked…",
  });
  if (!confirmed.confirmed) return;

  try {
    await api.revokeInstallation(installation.id, confirmed.reason);
    toast("Reader installation revoked successfully.", { type: "success" });
    await loadInstallations();
    await loadOverview();
  } catch (error) {
    fail(error, "Failed to revoke installation.");
  }
}

/* --------------------------------------------------------------- security */

async function loadSecurity() {
  const data = await api.securityStatus();
  state.securityData = data;

  const auth = data.auth || {};
  const session = data.session || {};
  const database = data.database || {};

  setText("#sec-admin-email", auth.admin_email || "—");
  setText("#sec-admin-role", (auth.admin_role || "owner").toUpperCase());
  setText("#sec-failed-attempts", auth.failed_login_attempts ?? 0);
  setText("#sec-cookie-name", session.cookie_name || "nexora_session");
  setText(
    "#sec-session-ttl",
    `${Math.round((session.session_ttl_seconds || 28800) / 3600)} hours`
  );

  const isDbConnected = database.status === "connected";
  setBadge(
    "#sec-db-status",
    isDbConnected ? "CONNECTED" : "UNAVAILABLE",
    isDbConnected ? "success" : "danger"
  );
  setText("#sec-db-ssl", database.ssl_mode || "require");

  renderSecurityEvents(data.recent_events || []);
}

function renderSecurityEvents(events) {
  const tbody = $("#sec-events-table-body");
  if (!tbody) return;
  clearNode(tbody);

  if (!events.length) {
    tbody.append(
      emptyRow(
        SECURITY_LABELS.length,
        "No recent security events",
        "Events appear as logins, key revocations, and installation changes take place.",
        "🛡"
      )
    );
    return;
  }

  events.forEach((event) => {
    const row = el("tr", {}, [
      tableCell("Timestamp", formatDate(event.created_at)),
      tableCell("Event Action", event.action, "table-primary"),
      tableCell("Outcome", statusBadge(event.outcome)),
      tableCell("Target Type", event.target_type || "—"),
      tableCell("IP Address", el("code", { class: "key-hint", text: event.ip_address || "—" })),
    ]);
    tbody.append(row);
  });
}

/* ------------------------------------------------------------- audit logs */

async function loadAuditLogs() {
  const search = $("#audit-search-input") ? $("#audit-search-input").value.trim() : "";
  const action = $("#audit-filter-action") ? $("#audit-filter-action").value : "";
  const outcome = $("#audit-filter-outcome") ? $("#audit-filter-outcome").value : "";
  const page = state.auditPagination.page || 1;
  const tbody = $("#audit-table-body");

  beginTableLoad(tbody, AUDIT_LABELS.length, AUDIT_LABELS, "#audit-summary");

  try {
    const data = await api.auditLogs({
      search,
      action,
      outcome,
      page,
      page_size: 25,
    });

    state.auditLogs = data.items || [];
    state.auditPagination = normalizePagination(data.pagination, page, 25);
    renderAuditTable(state.auditLogs);
  } catch (error) {
    if (tbody) {
      clearNode(tbody);
      tbody.append(errorRow(AUDIT_LABELS.length, "Audit trail unavailable"));
    }
    throw error;
  } finally {
    endTableLoad(tbody);
  }
}

function renderAuditTable(items) {
  const tbody = $("#audit-table-body");
  if (!tbody) return;
  clearNode(tbody);

  const pagination = state.auditPagination;
  const prev = $("#audit-prev-page");
  const next = $("#audit-next-page");

  if (!items.length) {
    tbody.append(
      emptyRow(
        AUDIT_LABELS.length,
        "No audit logs found",
        "Administrative events are automatically recorded here.",
        "≡"
      )
    );
    setText("#audit-summary", "0 records");
    setText("#audit-page-info", "Page 1 of 1");
    if (prev) prev.disabled = true;
    if (next) next.disabled = true;
    return;
  }

  const start = (pagination.page - 1) * pagination.page_size + 1;
  const end = Math.min(pagination.page * pagination.page_size, pagination.total);
  setText("#audit-summary", `Showing ${start}–${end} of ${pagination.total} event(s)`);
  setText("#audit-page-info", `Page ${pagination.page} of ${pagination.total_pages}`);
  if (prev) prev.disabled = pagination.page <= 1;
  if (next) next.disabled = pagination.page >= pagination.total_pages;

  items.forEach((item) => {
    const row = el("tr", {}, [
      tableCell("Timestamp", formatDate(item.created_at)),
      tableCell("Action", item.action, "table-primary"),
      tableCell("Target Type", item.target_type || "—"),
      tableCell(
        "Target ID",
        el("code", { class: "key-hint", text: item.target_id ? item.target_id.slice(0, 8) : "—" })
      ),
      tableCell("Outcome", statusBadge(item.outcome)),
      tableCell("Actor", item.actor_email || "System / Admin"),
      tableCell("IP Address", el("code", { class: "key-hint", text: item.ip_address || "—" })),
      tableCell(
        "Details",
        el("button", {
          class: "btn btn-ghost btn-sm",
          type: "button",
          text: "View",
          onclick: () => viewLogDetails(item),
        }),
        "align-right"
      ),
    ]);
    tbody.append(row);
  });
}

function viewLogDetails(item) {
  const body = $("#log-detail-body");
  if (!body) return;

  clearNode(body);
  body.append(
    recordGrid([
      recordItem("Action", item.action),
      recordItem("Timestamp", formatDate(item.created_at)),
      recordItem("Outcome", statusBadge(item.outcome)),
      recordItem("Target Type", item.target_type || "—"),
      recordItem("Target ID", item.target_id || "—", { mono: true }),
      recordItem("Actor", item.actor_email || "System"),
      recordItem("IP Address", item.ip_address || "—", { mono: true }),
      recordItem("User Agent", item.user_agent || "—", { full: true }),
    ]),
    el("hr", { class: "modal-divider" }),
    el("h3", { class: "detail-section", text: "Event Details Payload" }),
    el("pre", {
      class: "code-block",
      text: JSON.stringify(item.details || {}, null, 2),
    })
  );

  openDialog($("#log-detail-modal"));
}

/* --------------------------------------------------------------- settings */

let timezoneOptionsCache = null;
let timezonePreviewTimer = 0;

async function loadTimezoneOptions() {
  if (timezoneOptionsCache) return timezoneOptionsCache;
  try {
    const data = await api.timezones();
    timezoneOptionsCache = Array.isArray(data.timezones) ? data.timezones : [];
  } catch {
    timezoneOptionsCache = [];
  }
  return timezoneOptionsCache;
}

function updateTimezonePreview() {
  const select = $("#setting-timezone");
  const preview = $("#timezone-preview");
  if (!select || !preview) return;
  const name = select.value || "UTC";
  const option = (timezoneOptionsCache || []).find((zone) => zone.id === name);
  const now = new Date();
  const offset = option ? option.offset : formatUtcOffset(getTimezoneOffsetSeconds(now, name));
  clearNode(preview);
  preview.append(
    el("div", { class: "timezone-preview-row" }, [
      el("span", { class: "muted", text: "Current local time" }),
      el("strong", { class: "timezone-preview-time", text: formatDateInZone(now, name, { time: true, seconds: true }) }),
    ]),
    el("div", { class: "timezone-preview-row" }, [
      el("span", { class: "muted", text: "UTC offset" }),
      el("strong", { class: "timezone-preview-offset", text: offset }),
    ]),
    el("p", {
      class: "muted timezone-preview-meta",
      text: `IANA ${name}${name === "UTC" ? " · canonical default" : ""}`,
    })
  );
}

function startTimezonePreview() {
  window.clearInterval(timezonePreviewTimer);
  timezonePreviewTimer = window.setInterval(() => {
    const select = $("#setting-timezone");
    if (!select || !select.isConnected) {
      window.clearInterval(timezonePreviewTimer);
      return;
    }
    updateTimezonePreview();
  }, 1000);
}

async function loadSettings() {
  const data = await api.settings();
  state.settingsData = data;

  const prefix = $("#setting-key-prefix");
  if (prefix) prefix.value = data.key_prefix || "NXR";

  const expiry = $("#setting-default-expiry");
  if (expiry) expiry.value = data.default_key_expiry_days ?? 30;

  const bulk = $("#setting-max-bulk");
  if (bulk) bulk.value = data.max_bulk_keys ?? 500;

  const heartbeat = $("#setting-heartbeat-interval");
  if (heartbeat) heartbeat.value = data.heartbeat_interval_seconds ?? 300;

  const rateLimits = $("#setting-rate-limits");
  if (rateLimits) rateLimits.value = data.api_rate_limits || "60/min";

  const timezone = $("#setting-timezone");
  if (timezone) {
    const zones = await loadTimezoneOptions();
    const current = data.display_timezone || "UTC";
    if (!zones.some((zone) => zone.id === current)) {
      zones.unshift({
        id: current,
        offset: formatUtcOffset(getTimezoneOffsetSeconds(new Date(), current)),
      });
    }
    timezone.replaceChildren();
    zones.forEach((zone) => {
      timezone.append(
        el("option", { value: zone.id, text: `${zone.id} · ${zone.offset}` })
      );
    });
    timezone.value = current;
    refreshCustomSelects(timezone);
    updateTimezonePreview();
    startTimezonePreview();
  }

  const maintenance = $("#setting-maintenance-mode");
  if (maintenance) maintenance.checked = Boolean(data.maintenance_mode);

  const autoDelete = $("#setting-auto-delete-days");
  if (autoDelete) autoDelete.value = data.auto_delete_days ?? 0;
}

async function handleSaveSettings(event) {
  event.preventDefault();

  const form = $("#system-settings-form");
  const message = $("#settings-message");
  const button = $("#save-settings-btn");

  if (form && !form.reportValidity()) {
    setToneMessage(message, "Complete all required fields with valid values.", "error");
    return;
  }

  const expiry = parseInt($("#setting-default-expiry")?.value, 10);
  const bulk = parseInt($("#setting-max-bulk")?.value, 10);
  const heartbeat = parseInt($("#setting-heartbeat-interval")?.value, 10);
  const autoDeleteDays = parseInt($("#setting-auto-delete-days")?.value, 10);

  if (![expiry, bulk, heartbeat, autoDeleteDays].every((value) => Number.isFinite(value))) {
    setToneMessage(message, "Numeric settings must contain valid numbers.", "error");
    return;
  }

  const updates = {
    key_prefix: $("#setting-key-prefix").value.trim(),
    default_key_expiry_days: expiry,
    max_bulk_keys: bulk,
    heartbeat_interval_seconds: heartbeat,
    api_rate_limits: $("#setting-rate-limits").value.trim(),
    display_timezone: $("#setting-timezone").value,
    maintenance_mode: $("#setting-maintenance-mode").checked,
    auto_delete_days: autoDeleteDays,
  };

  setButtonBusy(button, true, "Saving…");
  setToneMessage(message, "Saving configuration…", "info");

  try {
    const saved = await api.updateSettings(updates);
    applyTimezoneConfig(saved);
    await refreshTimezone().catch(() => {});
    setToneMessage(message, "System settings updated successfully.", "success");
    toast("System settings updated successfully.", { type: "success" });
    const panel = $(".form-panel-contained");
    if (panel) {
      panel.classList.remove("is-saved");
      void panel.offsetWidth;
      panel.classList.add("is-saved");
      window.setTimeout(() => panel.classList.remove("is-saved"), 1200);
    }
    await loadSettings();
  } catch (error) {
    setToneMessage(
      message,
      error && error.message ? error.message : "Failed to save settings.",
      "error"
    );
    fail(error, "Failed to save settings.");
  } finally {
    setButtonBusy(button, false);
  }
}

/* --------------------------------------------------------------- shell UX */

const DELETE_ALL_PHRASE = "DELETE ALL PROJECTS";
let lastNavFocus = null;
let statsTimer = 0;
let lastOverviewRefreshAt = 0;

function openMobileNav() {
  if (!mobileQuery.matches || mobileNavOpen) return;
  const sidebar = $("#sidebar");
  const backdrop = $("#sidebar-backdrop");
  const toggle = $("#mobile-nav-toggle");
  if (!sidebar) return;

  lastNavFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  mobileNavOpen = true;
  sidebar.classList.add("is-open");
  if (backdrop) backdrop.hidden = false;
  document.body.classList.add("nav-open");
  if (toggle) toggle.setAttribute("aria-expanded", "true");
  const close = $("#sidebar-close");
  if (close) close.focus({ preventScroll: true });
}

function closeMobileNav({ restoreFocus = true } = {}) {
  const sidebar = $("#sidebar");
  const backdrop = $("#sidebar-backdrop");
  const toggle = $("#mobile-nav-toggle");
  const wasOpen = mobileNavOpen || Boolean(sidebar && sidebar.classList.contains("is-open"));

  mobileNavOpen = false;
  if (sidebar) sidebar.classList.remove("is-open");
  if (backdrop) backdrop.hidden = true;
  document.body.classList.remove("nav-open");
  if (toggle) toggle.setAttribute("aria-expanded", "false");

  if (wasOpen && restoreFocus) {
    const target = lastNavFocus && document.contains(lastNavFocus) ? lastNavFocus : toggle;
    if (target) target.focus({ preventScroll: true });
  }
  lastNavFocus = null;
}

function resolveHashView() {
  const raw = window.location.hash.replace(/^#\/?/, "").toLowerCase();
  if (VIEW_META[raw]) return raw;
  return "overview";
}

function reportLoad(task, fallback) {
  task().catch((error) => fail(error, fallback));
}

/* ------------------------------------------------------------ destructive */

function openDeleteAllDialog() {
  const modal = $("#delete-all-modal");
  if (!modal) return;

  const count = Number(state.stats && state.stats.total_projects);
  const resolved = Number.isFinite(count) ? count : state.projects.length;
  setText(
    "#delete-all-count",
    resolved
      ? `${resolved} project${resolved === 1 ? "" : "s"} will be permanently deleted in one transaction.`
      : "No projects are currently registered. The request will complete without deleting records."
  );

  const confirmInput = $("#delete-all-confirm-input");
  const passwordInput = $("#delete-all-password-input");
  if (confirmInput) confirmInput.value = "";
  if (passwordInput) passwordInput.value = "";
  setToneMessage($("#delete-all-error"), "", "error");
  openDialog(modal);
  window.setTimeout(() => {
    if (confirmInput) confirmInput.focus({ preventScroll: true });
  }, 60);
}

async function submitDeleteAll() {
  const confirmInput = $("#delete-all-confirm-input");
  const passwordInput = $("#delete-all-password-input");
  const errorNode = $("#delete-all-error");
  const button = $("#delete-all-submit");
  const confirmation = confirmInput ? confirmInput.value.trim() : "";
  const password = passwordInput ? passwordInput.value : "";

  if (confirmation !== DELETE_ALL_PHRASE) {
    setToneMessage(
      errorNode,
      `Type ${DELETE_ALL_PHRASE} exactly to confirm permanent deletion.`,
      "error"
    );
    if (confirmInput) confirmInput.focus();
    return;
  }
  if (!password) {
    setToneMessage(errorNode, "Enter the administrator password to re-authenticate.", "error");
    if (passwordInput) passwordInput.focus();
    return;
  }

  setToneMessage(errorNode, "", "error");
  setButtonBusy(button, true, "Deleting…");

  try {
    const result = await api.deleteAllProjects(confirmation, password);
    const deleted = Number(result && result.deleted_count) || 0;
    toast(
      deleted
        ? `${deleted} project${deleted === 1 ? "" : "s"} permanently deleted.`
        : "Delete All completed. No projects were present.",
      { type: "success" }
    );
    announce("All projects permanently deleted.");
    if (confirmInput) confirmInput.value = "";
    if (passwordInput) passwordInput.value = "";
    closeDialog($("#delete-all-modal"));
    state.projects = [];
    await loadOverview().catch(() => {});
    if (state.view === "projects") await loadProjects().catch(() => {});
  } catch (error) {
    if (passwordInput) passwordInput.value = "";
    setToneMessage(
      errorNode,
      error && error.message ? error.message : "Delete All failed.",
      "error"
    );
    fail(error, "Failed to delete all projects.");
  } finally {
    setButtonBusy(button, false);
  }
}

/* -------------------------------------------------------------- key flows */

function exportGeneratedKeysCsv() {
  const keys = state.generatedKeys.filter((key) => key && key.plaintext_key);
  if (!keys.length) {
    toast("No plaintext key is available in this session.", { type: "error" });
    return;
  }

  const header = [
    "key_id",
    "project_name",
    "key_type",
    "key",
    "key_hint",
    "created_at",
    "expiration_preset",
    "expires_at",
    "is_permanent",
    "label",
  ];
  const lines = [header.map(csvCell).join(",")];
  keys.forEach((key) => {
    lines.push(
      [
        key.id,
        key.project_name,
        key.key_type_name || key.key_type_id || "reader",
        key.plaintext_key,
        key.key_hint,
        key.created_at,
        key.expiration_preset || (key.is_permanent ? "never" : "custom"),
        key.expires_at || "",
        key.is_permanent ? "yes" : "no",
        key.label || "",
      ]
        .map(csvCell)
        .join(",")
    );
  });

  downloadText(
    `nexora-reader-key-${new Date().toISOString().slice(0, 10)}.csv`,
    `${lines.join("\r\n")}\r\n`,
    "text/csv;charset=utf-8"
  );
  toast("CSV downloaded. It contains the one-time plaintext key — store it securely.", {
    type: "warning",
    timeout: 8000,
  });
}

async function exportReaderKeysCsv() {
  const button = $("#export-keys-btn");
  setButtonBusy(button, true, "Exporting…");

  try {
    const params = {
      project_id: $("#keys-filter-project") ? $("#keys-filter-project").value : "",
      status: $("#keys-filter-status") ? $("#keys-filter-status").value : "",
      expiry_filter: $("#keys-filter-expiry") ? $("#keys-filter-expiry").value : "",
      search: $("#keys-search-input") ? $("#keys-search-input").value.trim() : "",
    };
    const response = await fetch(api.exportReaderKeysUrl(params), {
      credentials: "same-origin",
      headers: { Accept: "text/csv" },
    });

    if (response.status === 401) {
      window.location.replace("/login");
      return;
    }
    if (!response.ok) {
      throw new Error(`The server rejected the export request (${response.status}).`);
    }

    const text = await response.text();
    downloadText(
      `nexora-reader-keys-${new Date().toISOString().slice(0, 10)}.csv`,
      text,
      "text/csv;charset=utf-8"
    );
    toast("Reader key CSV exported.", { type: "success" });
    announce("Reader key CSV exported.");
  } catch (error) {
    fail(error, "Failed to export keys.");
  } finally {
    setButtonBusy(button, false);
  }
}

/* ---------------------------------------------------------------- actions */

async function refreshActiveView() {
  const button = $("#refresh-btn");
  setButtonBusy(button, true, "Refreshing…");
  const ok = await loadViewData(state.view);
  setButtonBusy(button, false);
  if (ok) {
    toast("Console data refreshed.", { type: "success" });
    announce("Console data refreshed.");
  }
}

async function handleLogout() {
  if (state.loggingOut) return;
  state.loggingOut = true;

  const button = $("#logout-btn");
  setButtonBusy(button, true, "Signing out…");
  closeMobileNav({ restoreFocus: false });
  window.clearInterval(statsTimer);

  // Let the in-flight session bootstrap finish so its CSRF rotation cannot
  // race the revocation request.
  if (state.sessionCheck) {
    try {
      await state.sessionCheck;
    } catch {
      // The bootstrap path already reports its own failure.
    }
  }

  // Revoke the server session FIRST. Navigating before the revocation could
  // leave the cookie valid, and the login page would then restore it.
  try {
    await api.logoutSafe();
  } catch (error) {
    state.loggingOut = false;
    setButtonBusy(button, false);
    startStatsPolling();
    hideLeaveScreen();
    fail(error, "Sign-out failed. Your session is still active.");
    return;
  }

  setCsrfToken("");
  state.user = null;

  clearSensitiveState();
  try {
    await runShutdownAnimation({ duration: 1500 });
  } catch {
    // The visual transition is best-effort; navigation is not.
  }

  window.location.replace("/login");
}

function bindRefreshButton(selector, loader, fallback) {
  const button = $(selector);
  if (!button) return;
  button.addEventListener("click", async () => {
    setButtonBusy(button, true, "Refreshing…");
    try {
      await loader();
      toast("Data refreshed.", { type: "success" });
    } catch (error) {
      fail(error, fallback);
    } finally {
      setButtonBusy(button, false);
    }
  });
}

function bindPagination(prevSelector, nextSelector, stateKey, loader, fallback) {
  const prev = $(prevSelector);
  const next = $(nextSelector);

  if (prev) {
    prev.addEventListener("click", () => {
      const pagination = state[stateKey];
      if (!pagination || pagination.page <= 1) return;
      const previous = pagination.page;
      pagination.page -= 1;
      loader().catch((error) => {
        state[stateKey].page = previous;
        fail(error, fallback);
      });
    });
  }

  if (next) {
    next.addEventListener("click", () => {
      const pagination = state[stateKey];
      if (!pagination || pagination.page >= pagination.total_pages) return;
      const previous = pagination.page;
      pagination.page += 1;
      loader().catch((error) => {
        state[stateKey].page = previous;
        fail(error, fallback);
      });
    });
  }
}

/* ----------------------------------------------------------- event wiring */

function setupEventListeners() {
  const mobileToggle = $("#mobile-nav-toggle");
  if (mobileToggle) {
    mobileToggle.addEventListener("click", () => {
      if (mobileNavOpen) closeMobileNav();
      else openMobileNav();
    });
  }

  const sidebarClose = $("#sidebar-close");
  if (sidebarClose) sidebarClose.addEventListener("click", () => closeMobileNav());

  const backdrop = $("#sidebar-backdrop");
  if (backdrop) backdrop.addEventListener("click", () => closeMobileNav());

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mobileNavOpen) {
      event.preventDefault();
      closeMobileNav();
      return;
    }

    if (event.key !== "Tab" || !mobileNavOpen) return;

    const sidebar = $("#sidebar");
    if (!sidebar) return;
    const focusable = Array.from(
      sidebar.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    ).filter((node) => node instanceof HTMLElement && node.offsetParent !== null);
    if (!focusable.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && (active === first || !sidebar.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !sidebar.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  });

  if (typeof mobileQuery.addEventListener === "function") {
    mobileQuery.addEventListener("change", (event) => {
      if (!event.matches) closeMobileNav({ restoreFocus: false });
    });
  }

  $$(".nav-item[data-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });

  $$("[data-go]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.go));
  });

  const quickAction = $("#topbar-quick-action");
  if (quickAction) {
    quickAction.addEventListener("click", () => {
      if (state.view === "generator") {
        const target =
          state.generatorMode === "existing"
            ? $("#gen-project-select")
            : $("#gen-project-name-input");
        if (target) target.focus({ preventScroll: true });
      } else {
        showView("generator");
      }
    });
  }

  const refresh = $("#refresh-btn");
  if (refresh) refresh.addEventListener("click", refreshActiveView);

  const logout = $("#logout-btn");
  if (logout) {
    // The static shell is visible before this module runs; keep the control
    // disabled until its real handler exists so an early click cannot no-op.
    logout.disabled = false;
    logout.addEventListener("click", handleLogout);
  }

  const projectSearch = $("#project-search-input");
  if (projectSearch) {
    projectSearch.addEventListener(
      "input",
      debounce(() => reportLoad(loadProjects, "Failed to search projects."), 300)
    );
  }

  bindRefreshButton("#projects-refresh-btn", loadProjects, "Failed to refresh projects.");

  const deleteAllButton = $("#delete-all-projects-btn");
  if (deleteAllButton) deleteAllButton.addEventListener("click", openDeleteAllDialog);

  const deleteAllSubmit = $("#delete-all-submit");
  if (deleteAllSubmit) deleteAllSubmit.addEventListener("click", submitDeleteAll);

  const generatorForm = $("#key-generator-form");
  if (generatorForm) generatorForm.addEventListener("submit", handleGenerateKeys);

  const modeExisting = $("#gen-mode-existing");
  if (modeExisting) {
    modeExisting.addEventListener("click", () =>
      setGeneratorMode("existing", { animate: true })
    );
  }
  const modeNew = $("#gen-mode-new");
  if (modeNew) {
    modeNew.addEventListener("click", () => setGeneratorMode("new", { animate: true }));
  }

  const generatorProject = $("#gen-project-select");
  if (generatorProject) {
    generatorProject.addEventListener("change", () => {
      replayMotion(generatorProject, "is-picked", 340);
      loadGeneratorKeyTypes().catch(() => {});
    });
  }
  const keyTypeSelect = $("#gen-key-type-select");
  if (keyTypeSelect) {
    keyTypeSelect.addEventListener("change", () =>
      updateGeneratorType({ animate: true })
    );
  }
  const projectExpirySelect = $("#gen-project-expiration-select");
  if (projectExpirySelect) {
    projectExpirySelect.addEventListener("change", () => {
      replayMotion(projectExpirySelect, "is-picked", 340);
      updateProjectExpirationPreview({ animate: true });
    });
  }
  const projectExpiryCustom = $("#gen-project-expiration-custom");
  if (projectExpiryCustom) {
    projectExpiryCustom.addEventListener("input", () =>
      updateProjectExpirationPreview({ animate: true })
    );
  }

  buildPresetChips();
  const expirationSelect = $("#gen-expiration-select");
  if (expirationSelect) {
    expirationSelect.addEventListener("change", () => {
      replayMotion(expirationSelect, "is-picked", 340);
      updateExpirationPreview({ animate: true });
    });
  }
  const customExpiration = $("#gen-expiration-custom");
  if (customExpiration) {
    customExpiration.addEventListener("input", () =>
      updateExpirationPreview({ animate: true })
    );
  }

  // Every other platform select replays the shared selection animation on
  // change (filters, type builder, settings). The four generator selects
  // above keep their specific replay wiring so feedback stays consistent
  // without double-firing the same class.
  const replayHandledIds = new Set([
    "gen-project-select",
    "gen-key-type-select",
    "gen-project-expiration-select",
    "gen-expiration-select",
  ]);
  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) return;
    if (replayHandledIds.has(target.id)) return;
    if (!target.closest(".field, .select")) return;
    replayMotion(target, "is-picked", 340);
  });

  const generatorRetry = $("#generator-retry-btn");
  if (generatorRetry) {
    generatorRetry.addEventListener("click", () => {
      const form = $("#key-generator-form");
      if (form) form.requestSubmit();
    });
  }
  const generatorRecoveryView = $("#generator-recovery-view-btn");
  if (generatorRecoveryView) {
    generatorRecoveryView.addEventListener("click", () => showView("keys"));
  }
  const generatorRecoveryDismiss = $("#generator-recovery-dismiss");
  if (generatorRecoveryDismiss) {
    generatorRecoveryDismiss.addEventListener("click", () => {
      state.generatorBatchId = null;
      hideGeneratorRecovery();
      setToneMessage($("#generator-error"), "", "error");
    });
  }

  const newKeyType = $("#new-key-type-btn");
  if (newKeyType) newKeyType.addEventListener("click", () => openTypeBuilder());
  bindRefreshButton(
    "#key-types-refresh-btn",
    loadKeyTypeCatalogue,
    "Failed to refresh key types."
  );
  bindRefreshButton(
    "#ops-refresh-btn",
    renderOperations,
    "Failed to refresh operations."
  );
  const keyTypesSearch = $("#key-types-search-input");
  if (keyTypesSearch) {
    keyTypesSearch.addEventListener("input", debounce(renderKeyTypeCatalogue, 180));
  }
  const keyTypeForm = $("#key-type-form");
  if (keyTypeForm) keyTypeForm.addEventListener("submit", handleKeyTypeSubmit);
  const typeBuilderClose = $("#type-builder-close");
  if (typeBuilderClose) typeBuilderClose.addEventListener("click", closeTypeBuilder);
  const typeBuilderCancel = $("#type-builder-cancel");
  if (typeBuilderCancel) typeBuilderCancel.addEventListener("click", closeTypeBuilder);
  const typeExpiryPresets = $("#type-expiry-presets");
  if (typeExpiryPresets) {
    typeExpiryPresets.addEventListener("change", () => syncTypeDefaultExpiry());
  }

  const keysSearch = $("#keys-search-input");
  if (keysSearch) {
    keysSearch.addEventListener(
      "input",
      debounce(() => {
        state.keysPagination.page = 1;
        reportLoad(loadKeys, "Failed to search keys.");
      }, 320)
    );
  }

  ["#keys-filter-project", "#keys-filter-status", "#keys-filter-expiry", "#keys-page-size"].forEach(
    (selector) => {
      const control = $(selector);
      if (!control) return;
      control.addEventListener("change", () => {
        state.keysPagination.page = 1;
        reportLoad(loadKeys, "Failed to filter keys.");
      });
    }
  );

  bindPagination(
    "#keys-prev-page",
    "#keys-next-page",
    state.keysPagination,
    loadKeys,
    "Failed to load the previous key page."
  );

  const exportButton = $("#export-keys-btn");
  if (exportButton) exportButton.addEventListener("click", exportReaderKeysCsv);

  const installationsSearch = $("#installations-search-input");
  if (installationsSearch) {
    installationsSearch.addEventListener(
      "input",
      debounce(() => {
        state.installationsPagination.page = 1;
        reportLoad(loadInstallations, "Failed to search installations.");
      }, 320)
    );
  }

  ["#installations-filter-project", "#installations-filter-status"].forEach((selector) => {
    const control = $(selector);
    if (!control) return;
    control.addEventListener("change", () => {
      state.installationsPagination.page = 1;
      reportLoad(loadInstallations, "Failed to filter installations.");
    });
  });

  bindPagination(
    "#installations-prev-page",
    "#installations-next-page",
    state.installationsPagination,
    loadInstallations,
    "Failed to load the previous installation page."
  );

  const auditSearch = $("#audit-search-input");
  if (auditSearch) {
    auditSearch.addEventListener(
      "input",
      debounce(() => {
        state.auditPagination.page = 1;
        reportLoad(loadAuditLogs, "Failed to search audit logs.");
      }, 320)
    );
  }

  ["#audit-filter-action", "#audit-filter-outcome"].forEach((selector) => {
    const control = $(selector);
    if (!control) return;
    control.addEventListener("change", () => {
      state.auditPagination.page = 1;
      reportLoad(loadAuditLogs, "Failed to filter audit logs.");
    });
  });

  bindRefreshButton("#audit-refresh-btn", loadAuditLogs, "Failed to refresh audit logs.");

  bindPagination(
    "#audit-prev-page",
    "#audit-next-page",
    state.auditPagination,
    loadAuditLogs,
    "Failed to load the previous audit page."
  );

  const settingsForm = $("#system-settings-form");
  if (settingsForm) settingsForm.addEventListener("submit", handleSaveSettings);

  const timezoneSelect = $("#setting-timezone");
  if (timezoneSelect) {
    timezoneSelect.addEventListener("change", updateTimezonePreview);
  }

  const resetTimezone = $("#reset-timezone-btn");
  if (resetTimezone) {
    resetTimezone.addEventListener("click", () => {
      const select = $("#setting-timezone");
      if (!select) return;
      select.value = "UTC";
      updateTimezonePreview();
      const form = $("#system-settings-form");
      if (form) form.requestSubmit();
    });
  }

  const copyAll = $("#copy-all");
  if (copyAll) {
    copyAll.addEventListener("click", () => {
      const values = state.generatedKeys
        .filter((key) => key && key.plaintext_key)
        .map((key) => key.plaintext_key);
      if (!values.length) {
        toast("No plaintext key is available in this session.", { type: "error" });
        return;
      }
      copyWithFeedback(values.join("\n"), copyAll, "Plaintext key copied to clipboard.");
    });
  }

  const downloadCsv = $("#download-keys-csv");
  if (downloadCsv) downloadCsv.addEventListener("click", exportGeneratedKeysCsv);

  $$("[data-close]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(document.getElementById(button.dataset.close)));
  });

  $$("dialog.modal").forEach((dialog) => {
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDialog(dialog);
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
  });

  const resultModal = $("#result-modal");
  if (resultModal) resultModal.addEventListener("close", purgeGeneratedKeys);

  const deleteAllModal = $("#delete-all-modal");
  if (deleteAllModal) {
    deleteAllModal.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      if (!(event.target instanceof HTMLInputElement)) return;
      event.preventDefault();
      submitDeleteAll();
    });
    deleteAllModal.addEventListener("close", () => {
      const confirmInput = $("#delete-all-confirm-input");
      const passwordInput = $("#delete-all-password-input");
      if (confirmInput) confirmInput.value = "";
      if (passwordInput) passwordInput.value = "";
      setToneMessage($("#delete-all-error"), "", "error");
      setButtonBusy($("#delete-all-submit"), false);
    });
  }

  window.addEventListener("hashchange", () => {
    const next = resolveHashView();
    if (next !== state.view) showView(next, { updateHash: false });
  });

  window.addEventListener("pagehide", purgeGeneratedKeys);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (state.view !== "overview" || !state.user) return;
    if (viewLoads.has("overview")) return;
    // Returning to the tab must not trigger a request storm.
    if (Date.now() - lastOverviewRefreshAt < 30000) return;
    loadViewData("overview").catch(() => {});
  });

  window.addEventListener("pagehide", () => window.clearInterval(statsTimer));
}

function initMotionSettings() {
  const toggle = $("#setting-lite-mode");
  const profile = $("#motion-profile");
  const reduced = $("#motion-reduced");
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  const sync = () => {
    const lite = isLiteMode();
    if (toggle) toggle.checked = lite;
    if (profile) {
      profile.textContent = lite ? "LITE" : "FULL";
      profile.className = `badge ${lite ? "badge-warn" : "badge-info"}`;
    }
    if (reduced) {
      reduced.textContent = motionQuery.matches ? "REDUCED" : "FULL MOTION";
      reduced.className = `badge ${motionQuery.matches ? "badge-warn" : "badge-muted"}`;
    }
  };

  if (toggle) {
    toggle.addEventListener("change", () => {
      const active = setLiteMode(toggle.checked);
      sync();
      toast(
        active
          ? "Reduced visual effects enabled for this device."
          : "Full cinematic effects restored for this device.",
        { type: "info" }
      );
    });
  }

  window.addEventListener("nexora:litechange", sync);
  if (typeof motionQuery.addEventListener === "function") {
    motionQuery.addEventListener("change", sync);
  }
  sync();
}

function startStatsPolling() {
  window.clearInterval(statsTimer);
  statsTimer = window.setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (state.view !== "overview" || !state.user) return;
    if (viewLoads.has("overview")) return;
    loadViewData("overview").catch(() => {});
  }, 60000);
}

/* ------------------------------------------------------------------- boot */

async function boot() {
  // Hard safety net: the console veil is decorative and can never outlive the
  // first render, even if the session endpoint or the database stalls.
  const bootGuard = window.setTimeout(dismissBootScreen, 1500);

  try {
    initMotion();
  } catch (error) {
    // Motion is optional; never let it stop the console from wiring up.
  }

  // Initialize custom select and date picker components and inject styles
  try {
    injectCustomSelectStyles();
    initCustomSelects();
    injectCustomDatePickerStyles();
    initCustomDatePickers();
  } catch (error) {
    console.warn('Component initialization failed:', error);
  }

  dismissBootScreen();

  try {
    setupEventListeners();
  } catch (error) {
    showGlobalError(
      "Part of the console interface could not be initialized. Reload the page to try again."
    );
  }
  try {
    initMotionSettings();
  } catch (error) {
    // Display preferences are optional.
  }

  const requestedView = resolveHashView();
  let sessionReady = false;

  try {
    state.sessionCheck = api.session({ timeoutMs: 9000 });
    const session = await state.sessionCheck;
    if (!session || !session.user) {
      throw new ApiProblem("Session invalid.", 401, "session_expired");
    }
    setCsrfToken(session.csrf_token);
    state.user = session.user;
    applyIdentity();
    await initTimezone();
    subscribeTimezone(() => {
      if (!state.user) return;
      if (state.view !== "settings") loadViewData(state.view).catch(() => {});
    });
    sessionReady = true;
  } catch (error) {
    if (error instanceof ApiProblem && error.status === 401) {
      redirectToLogin();
      window.clearTimeout(bootGuard);
      return;
    }

    const message =
      error && error.message ? error.message : "Unable to verify the admin session.";
    dismissBootScreen();
    showGlobalError(`${message} Use Refresh after connectivity is restored.`);
    toast(message, { type: "error" });
  } finally {
    state.sessionCheck = null;
  }

  if (
    !window.location.hash ||
    !VIEW_META[window.location.hash.replace(/^#\/?/, "").toLowerCase()]
  ) {
    window.history.replaceState(null, "", `#${requestedView}`);
  }

  showView(requestedView, { updateHash: false, reload: false });
  startStatsPolling();
  dismissBootScreen();
  window.clearTimeout(bootGuard);

  // Data loads strictly after the shell is interactive. It is never awaited
  // behind the boot overlay, and every request carries a bounded timeout.
  if (sessionReady) {
    loadViewData(requestedView)
      .then((loaded) => {
        if (!loaded) {
          showGlobalError(
            "Some live console data could not be loaded. Your session is still active; use Refresh to retry."
          );
        }
      })
      .catch(() => {});
  }
}

boot();
