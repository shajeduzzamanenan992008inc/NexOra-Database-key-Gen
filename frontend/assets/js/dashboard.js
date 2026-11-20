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
  playLeaveScreen,
  pulseElement,
  setLiteMode,
} from "./motion.js";

const VIEW_META = {
  overview: {
    title: "Overview",
    subtitle: "Real-time system pulse, database connectivity, and key metrics.",
    eyebrow: "OMEGA NEX-ORA / Command Center",
  },
  projects: {
    title: "Projects",
    subtitle: "Projects can own unlimited Reader Keys. Activate, schedule deletion, or permanently remove a project and every key it owns.",
    eyebrow: "OMEGA NEX-ORA / Project Ops",
  },
  generator: {
    title: "Key Generator",
    subtitle: "Select an existing project or create a new one, choose an expiration, and issue a one-time Reader Key.",
    eyebrow: "OMEGA NEX-ORA / Cryptographic Generator",
  },
  keys: {
    title: "Key Management",
    subtitle: "Masked reader key records. Plaintext revealed once; digests stored at rest.",
    eyebrow: "OMEGA NEX-ORA / Reader Keys",
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

function emptyRow(colspan, title, subtitle, icon = "◇") {
  return el("tr", {}, [
    el("td", { colspan: String(colspan), class: "empty-cell" }, [
      el("div", { class: "empty-state" }, [
        el("span", { class: "empty-icon", "aria-hidden": "true", text: icon }),
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
    "!"
  );
}

function loadingBlock(label) {
  return el("div", { class: "loading-block" }, [
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

function autoDeleteDateLabel(project) {
  return project.auto_delete_at
    ? formatDate(project.auto_delete_at, { time: false })
    : "No Auto Delete Scheduled";
}

function remainingDaysLabel(project) {
  if (!project.auto_delete_at) return "—";
  if (project.auto_delete_due) return "Due now";
  const days = Number(project.days_remaining);
  if (!Number.isFinite(days)) return "—";
  return `${days} day${days === 1 ? "" : "s"}`;
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
      tableCell("Status", statusBadge(project.status)),
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
    if (project.auto_delete_at) {
      dateInput.value = new Date(project.auto_delete_at).toISOString().slice(0, 10);
    }

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
        recordItem("Status", statusBadge(project.status)),
        recordItem("Total Reader Keys", String(project.total_keys || 0)),
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
    el("h3", { id: "project-keys-title", text: "Reader Keys for this Project" }),
    el("p", {
      class: "muted",
      text: "Every Reader Key owned by this project. Revocation applies to one key only and can never be undone.",
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
          "No Reader Keys yet",
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

async function saveProjectAutoDelete(project, dateText) {
  let iso = null;
  if (dateText) {
    const local = new Date(`${dateText}T23:59:59`);
    if (Number.isNaN(local.getTime())) {
      toast("Choose a valid date.", { type: "error" });
      return;
    }
    iso = local.toISOString();
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
      "This permanently removes the project, every Reader Key it owns, reader installations, device tokens, and other project-owned records. This cannot be undone. Audit history is preserved, and the project name becomes reusable.",
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
  return key.expires_at ? `${label} · ${formatDate(key.expires_at)}` : label;
}

async function loadGeneratorProjects() {
  const select = $("#gen-project-select");
  if (!select) return;
  const current = select.value;
  try {
    const data = await api.projects({ include_deleted: false });
    const projects = (data.projects || []).filter((project) => !project.deleted_at);
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

const EXPIRATION_PRESETS = [
  ["1_day", "1 Day"],
  ["7_days", "7 Days"],
  ["30_days", "30 Days"],
  ["90_days", "90 Days"],
  ["1_year", "1 Year"],
  ["never", "Never"],
];

function buildPresetChips() {
  const rail = $("#gen-expiration-presets");
  if (!rail || rail.dataset.ready === "true") return;
  rail.dataset.ready = "true";

  EXPIRATION_PRESETS.forEach(([value, label]) => {
    const chip = el("button", {
      class: "preset-chip",
      type: "button",
      text: label,
      "data-preset": value,
      "aria-pressed": "false",
    });
    chip.addEventListener("click", () => {
      const select = $("#gen-expiration-select");
      if (!select) return;
      if (select.value === value) {
        pulseElement(chip);
        return;
      }
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    rail.append(chip);
  });
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

function setGeneratorMode(mode, { focus = true } = {}) {
  state.generatorMode = mode === "new" ? "new" : "existing";
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
  if (existingField) existingField.hidden = !existing;
  if (newField) newField.hidden = existing;
  if (select) select.required = existing;
  if (nameInput) nameInput.required = !existing;

  replayMotion(existing ? existingField : newField, "is-field-in", 320);
  replayMotion(existing ? existingButton : newButton, "is-selected", 320);

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
  if (preset === "never") {
    text = "Never expires — stored explicitly on the key.";
  } else if (isCustom) {
    const value = customInput ? customInput.value : "";
    text = value
      ? `Expires ${new Date(value).toLocaleString()}`
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
  buildPresetChips();
  await loadGeneratorProjects();
  setGeneratorMode(state.generatorMode, { focus: false });
  updateExpirationPreview();
  const target =
    state.generatorMode === "existing"
      ? $("#gen-project-select")
      : $("#gen-project-name-input");
  if (target && window.matchMedia("(pointer: fine)").matches) {
    target.focus({ preventScroll: true });
  }
}

async function handleGenerateKeys(event) {
  event.preventDefault();

  const nameInput = $("#gen-project-name-input");
  const projectSelect = $("#gen-project-select");
  const labelInput = $("#gen-label-input");
  const expirationSelect = $("#gen-expiration-select");
  const customInput = $("#gen-expiration-custom");
  const errorNode = $("#generator-error");
  const submitButton = $("#generate-keys-btn");
  const cryptoVisual = $(".crypto-visual");
  const projectName = nameInput ? nameInput.value.trim() : "";
  const projectId = projectSelect ? projectSelect.value : "";
  const label = labelInput ? labelInput.value.trim() : "";
  const expiration = expirationSelect ? expirationSelect.value : "30_days";

  if (submitButton && submitButton.disabled) return;

  setToneMessage(errorNode, "", "error");
  if (nameInput) nameInput.classList.remove("is-invalid");
  if (projectSelect) projectSelect.classList.remove("is-invalid");
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

  let expiresAt = null;
  if (expiration === "custom") {
    const raw = customInput ? customInput.value : "";
    const parsed = raw ? new Date(raw) : null;
    if (!parsed || Number.isNaN(parsed.getTime())) {
      setToneMessage(errorNode, "Choose a valid custom expiration date and time.", "error");
      if (customInput) {
        customInput.classList.add("is-invalid");
        customInput.focus();
      }
      return;
    }
    if (parsed.getTime() <= Date.now()) {
      setToneMessage(errorNode, "The custom expiration must be in the future.", "error");
      if (customInput) {
        customInput.classList.add("is-invalid");
        customInput.focus();
      }
      return;
    }
    expiresAt = parsed.toISOString();
  }

  const generatorForm = $("#key-generator-form");
  if (generatorForm) generatorForm.setAttribute("aria-busy", "true");

  setButtonBusy(submitButton, true, "Generating securely…");
  const generatorPanel = generatorForm ? generatorForm.closest(".generator-form-panel") : null;
  if (submitButton) submitButton.classList.add("is-surging");
  if (cryptoVisual) cryptoVisual.classList.add("is-processing");

  try {
    const body = { label, expiration, expires_at: expiresAt };
    const result =
      state.generatorMode === "existing"
        ? await api.generateKeyForProject(projectId, body)
        : await api.generateProjectKey({
            project_name: projectName,
            ...body,
          });

    state.generatedKeys = result.reader_key ? [result.reader_key] : [];
    replayMotion(generatorPanel, "is-success", 520);
    renderGeneratedKeysModal(result.reader_key);
    toast(
      result.project_created
        ? `Project '${result.project.project_name}' created with its first Reader Key.`
        : `New Reader Key issued for '${result.project.project_name}'.`,
      { type: "success" }
    );
    if (nameInput) nameInput.value = "";
    if (labelInput) labelInput.value = "";
    await loadGeneratorProjects();
    loadOverview().catch(() => {});
  } catch (error) {
    if (error instanceof ApiProblem && error.status === 409) {
      state.generatedKeys = [];
      renderExistingProjectModal(error.details || {}, projectName);
      await loadGeneratorProjects();
      return;
    }

    const message = error && error.message ? error.message : "Failed to generate the reader key.";
    setToneMessage(errorNode, message, "error");
    replayMotion(errorNode, "is-invalid", 460);
    replayMotion(generatorPanel, "is-shaking", 520);
    fail(error, "Failed to generate the reader key.");
    const invalidTarget =
      state.generatorMode === "new" ? nameInput : projectSelect;
    if (invalidTarget) {
      invalidTarget.classList.add("is-invalid");
      invalidTarget.focus();
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
    fail(new Error("The server response did not include a one-time reader key."));
    return;
  }

  clearNode(container);
  setText("#result-title", "Reader Key Generated");
  setText(
    "#result-summary",
    `One-time key for '${key.project_name}'. Plaintext is shown only now and is cleared when this dialog closes.`
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
    copyWithFeedback(key.plaintext_key, copyButton, "Plaintext reader key copied to clipboard.")
  );

  const reveal = el("article", { class: "key-reveal" }, [
    el("div", { class: "key-reveal-head" }, [
      el("span", { class: "key-founder-mark", "aria-hidden": "true" }),
      el("span", { class: "key-reveal-label", text: "Plaintext reader key / one-time reveal" }),
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

  announce(`Reader key generated for ${key.project_name}. Copy it now; it is shown only once.`);
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
    `'${name}' already exists with ${details.key_count || (key ? 1 : 0)} Reader Key(s). No new project was created. Choose it under Existing Project to issue another key.`
  );

  const warning = $("#result-warning");
  if (warning) {
    warning.hidden = false;
    warning.textContent =
      "For security, an existing Reader Key is never displayed again. Use Key Management for its masked status.";
  }
  setResultButtonsVisible(false);

  container.append(
    recordGrid([
      recordItem("Project Name", name),
      recordItem("Project Status", project.status || "—"),
      recordItem("Reader Key Status", key ? key.status || "—" : "No key exists"),
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

async function viewKeyDetails(keyId) {
  const modal = $("#detail-modal");
  const body = $("#detail-body");
  if (!modal || !body) return;

  clearNode(body);
  body.append(loadingBlock("Loading authenticated key record…"));
  openDialog(modal);

  try {
    const data = await api.readerKey(keyId);
    const key = data.key;
    const items = [
      recordItem("Key ID", key.id, { mono: true }),
      recordItem("Key Hint (Masked)", key.key_hint, { mono: true }),
      recordItem("Status", statusBadge(key.status)),
      recordItem("Project", key.project_name),
      recordItem("Label", key.label || "None"),
      recordItem(
        "Expiration Setting",
        EXPIRATION_LABELS[key.expiration_preset] || (key.is_permanent ? "Never Expires" : "Custom")
      ),
      recordItem("Expires At", key.is_permanent ? "Never" : formatDate(key.expires_at)),
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
  } catch (error) {
    closeDialog(modal, { immediate: true });
    fail(error, "Failed to load key details.");
  }
}

async function revokeKeyPrompt(key, { onDone } = {}) {
  const confirmed = await confirmAction({
    title: `Revoke Reader Key '${key.key_hint}'?`,
    message:
      "Revoking this key is permanent. If it has an active Reader installation, that installation and its device tokens are revoked immediately. A revoked key can never become active again.",
    confirmLabel: "Revoke Key",
    tone: "danger",
    requireReason: true,
    reasonLabel: "Revocation Reason (Required for Audit Trail)",
    reasonPlaceholder: "Explain why this reader key is being revoked…",
  });
  if (!confirmed.confirmed) return;

  try {
    await api.revokeReaderKey(key.id, confirmed.reason);
    toast(`Reader key '${key.key_hint}' revoked.`, { type: "success" });
    await loadKeys();
    await loadOverview();
    if (typeof onDone === "function") await onDone();
  } catch (error) {
    fail(error, "Failed to revoke reader key.");
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
  if (timezone) timezone.value = data.display_timezone || "UTC";

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
    await api.updateSettings(updates);
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
    "reader_key",
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
    fail(error, "Failed to export reader keys.");
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

  try {
    await playLeaveScreen({
      kicker: "OMEGA NEX-ORA / SECURE HANDSHAKE",
      title: "Session terminated",
      status: "REVOKING SERVER SESSION / CLEARING LOCAL STATE",
      duration: 260,
    });
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
  if (modeExisting) modeExisting.addEventListener("click", () => setGeneratorMode("existing"));
  const modeNew = $("#gen-mode-new");
  if (modeNew) modeNew.addEventListener("click", () => setGeneratorMode("new"));

  buildPresetChips();
  const expirationSelect = $("#gen-expiration-select");
  if (expirationSelect) {
    expirationSelect.addEventListener("change", () =>
      updateExpirationPreview({ animate: true })
    );
  }
  const customExpiration = $("#gen-expiration-custom");
  if (customExpiration) {
    customExpiration.addEventListener("input", () =>
      updateExpirationPreview({ animate: true })
    );
  }

  const keysSearch = $("#keys-search-input");
  if (keysSearch) {
    keysSearch.addEventListener(
      "input",
      debounce(() => {
        state.keysPagination.page = 1;
        reportLoad(loadKeys, "Failed to search reader keys.");
      }, 320)
    );
  }

  ["#keys-filter-project", "#keys-filter-status", "#keys-filter-expiry", "#keys-page-size"].forEach(
    (selector) => {
      const control = $(selector);
      if (!control) return;
      control.addEventListener("change", () => {
        state.keysPagination.page = 1;
        reportLoad(loadKeys, "Failed to filter reader keys.");
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
      copyWithFeedback(values.join("\n"), copyAll, "Plaintext reader key copied to clipboard.");
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
