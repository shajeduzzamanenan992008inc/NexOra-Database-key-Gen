import { api, ApiProblem, setCsrfToken } from "./api.js";
import {
  $,
  $$,
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
  skeletonRows,
  statusBadge,
  toast,
} from "./ui.js";

const VIEW_META = {
  overview: {
    title: "Overview",
    subtitle: "Real-time system pulse, database connectivity, and key metrics.",
    eyebrow: "OMEGA NEX-ORA / Command Center",
  },
  projects: {
    title: "Projects",
    subtitle: "Manage isolated database target projects with case-insensitive collision defense.",
    eyebrow: "OMEGA NEX-ORA / Project Ops",
  },
  generator: {
    title: "Key Generator",
    subtitle: "Issue cryptographically hardened reader activation keys for projects.",
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

const state = {
  user: null,
  view: "overview",
  stats: null,
  projects: [],
  keys: [],
  keysPagination: { page: 1, pageSize: 25, total: 0, totalPages: 1 },
  installations: [],
  installationsPagination: { page: 1, pageSize: 25, total: 0, totalPages: 1 },
  auditLogs: [],
  auditPagination: { page: 1, pageSize: 25, total: 0, totalPages: 1 },
  securityData: null,
  settingsData: null,
  generatedKeys: [],
};

const numberFormat = new Intl.NumberFormat();

function fail(error, fallback = "The request could not be completed.") {
  if (error instanceof ApiProblem && error.status === 401) {
    window.location.replace("/login");
    return;
  }
  const detail =
    error instanceof ApiProblem && error.details && typeof error.details === "object"
      ? Object.entries(error.details)
          .map(([f, m]) => `${f}: ${m}`)
          .join(" · ")
      : "";
  toast(error && error.message ? error.message : fallback, {
    type: "error",
    detail,
  });
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value !== undefined && value !== null ? String(value) : "—";
}

function setStat(name, value) {
  $$(`[data-stat="${name}"]`).forEach((node) => {
    node.textContent =
      typeof value === "number" ? numberFormat.format(value) : String(value ?? "—");
  });
}

function emptyRow(colspan, title, subtitle) {
  return el("tr", {}, [
    el("td", { colspan: String(colspan), class: "text-center" }, [
      el("div", { class: "empty-state", style: "padding: 32px 16px; text-align: center;" }, [
        el("strong", { text: title, style: "display: block; font-size: 1.05rem; margin-bottom: 4px;" }),
        subtitle ? el("span", { class: "muted", text: subtitle }) : null,
      ]),
    ]),
  ]);
}

/* ------------------------------------------------------------------ Identity */

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

/* --------------------------------------------------------------------- Views */

export function showView(viewName, { updateHash = true, reload = true } = {}) {
  if (!VIEW_META[viewName]) viewName = "overview";
  state.view = viewName;

  $$(".nav-item").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.view === viewName);
  });

  $$(".view").forEach((v) => {
    v.classList.toggle("is-active", v.id === `view-${viewName}`);
  });

  const meta = VIEW_META[viewName];
  setText("#page-title", meta.title);
  setText("#page-subtitle", meta.subtitle);
  setText("#topbar-eyebrow", meta.eyebrow);

  // Update topbar quick action button
  const topAction = $("#topbar-quick-action");
  if (topAction) {
    if (viewName === "projects") {
      topAction.textContent = "+ Create Project";
      topAction.onclick = () => openDialog($("#project-modal"));
    } else {
      topAction.textContent = "⚡ Generate Key";
      topAction.onclick = () => showView("generator");
    }
  }

  if (updateHash) {
    window.location.hash = viewName;
  }

  if (reload) {
    loadViewData(viewName);
  }
}

async function loadViewData(viewName) {
  try {
    if (viewName === "overview") await loadOverview();
    else if (viewName === "projects") await loadProjects();
    else if (viewName === "generator") await loadGenerator();
    else if (viewName === "keys") await loadKeys();
    else if (viewName === "installations") await loadInstallations();
    else if (viewName === "security") await loadSecurity();
    else if (viewName === "logs") await loadAuditLogs();
    else if (viewName === "settings") await loadSettings();
  } catch (err) {
    fail(err, `Failed to load ${viewName} data.`);
  }
}

/* ------------------------------------------------------------- View: Overview */

async function loadOverview() {
  const [statsData, healthData] = await Promise.all([
    api.stats().catch(() => null),
    api.health().catch(() => null),
  ]);

  if (statsData) {
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
    const dbText = $("#db-health-text");
    if (dbText) dbText.textContent = isConnected ? "DB: CONNECTED" : "DB: UNAVAILABLE";
    const dbChip = $("#db-health-chip");
    if (dbChip) {
      dbChip.classList.toggle("chip-danger", !isConnected);
    }
    const pulseDb = $("#pulse-database-status");
    if (pulseDb) {
      pulseDb.textContent = isConnected ? "CONNECTED" : "UNAVAILABLE";
      pulseDb.className = `badge ${isConnected ? "badge-success" : "badge-danger"}`;
    }
    const pulseHealth = $("#pulse-backend-health");
    if (pulseHealth) {
      const isOk = statsData.backend_health === "ok";
      pulseHealth.textContent = isOk ? "OK" : "DEGRADED";
      pulseHealth.className = `badge ${isOk ? "badge-success" : "badge-warn"}`;
    }

    renderOverviewAudit(statsData.recent_audit || []);
  }
}

function renderOverviewAudit(items) {
  const list = $("#overview-audit-list");
  if (!list) return;
  clearNode(list);

  if (!items.length) {
    list.append(el("li", { class: "muted text-center", text: "No recent audit activity." }));
    return;
  }

  items.slice(0, 7).forEach((item) => {
    const isSuccess = item.outcome === "success";
    const li = el("li", { class: "audit-item" }, [
      el("span", {
        class: `audit-dot ${isSuccess ? "dot-success" : "dot-danger"}`,
        style: `width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: ${isSuccess ? "var(--success)" : "var(--danger)"}; margin-right: 8px;`,
      }),
      el("div", { class: "audit-meta", style: "flex: 1;" }, [
        el("strong", { text: item.action, style: "font-size: 0.9rem;" }),
        el("small", {
          class: "muted",
          text: ` · ${formatRelative(item.created_at)} ${item.project_name ? `(${item.project_name})` : ""}`,
        }),
      ]),
      statusBadge(item.outcome),
    ]);
    list.append(li);
  });
}

/* ------------------------------------------------------------- View: Projects */

async function loadProjects() {
  const search = $("#project-search-input") ? $("#project-search-input").value.trim() : "";
  const includeDeleted = $("#project-filter-deleted")
    ? $("#project-filter-deleted").value === "true"
    : false;

  const data = await api.projects({ search, include_deleted: includeDeleted });
  state.projects = data.projects || [];
  renderProjectsTable(state.projects);
}

function renderProjectsTable(projects) {
  const tbody = $("#projects-table-body");
  if (!tbody) return;
  clearNode(tbody);

  if (!projects.length) {
    tbody.append(emptyRow(8, "No projects found", "Create a new project to start issuing reader keys."));
    setText("#projects-summary", "0 projects");
    return;
  }

  setText("#projects-summary", `Showing ${projects.length} project(s)`);

  projects.forEach((proj) => {
    const isDeleted = Boolean(proj.deleted_at);
    const isActive = proj.status === "active";

    const tr = el("tr", {}, [
      el("td", {}, [
        el("a", {
          href: "#",
          style: "color: var(--cyan); font-weight: 600;",
          text: proj.project_name,
          onclick: (e) => {
            e.preventDefault();
            viewProjectDetails(proj.id);
          },
        }),
      ]),
      el("td", {}, [el("code", { text: proj.database_url_ref || "—" })]),
      el("td", {}, [statusBadge(proj.status)]),
      el("td", {}, [
        el("span", { class: "badge badge-success", text: String(proj.available_keys || 0) }),
      ]),
      el("td", {}, [
        el("span", { class: "badge badge-info", text: String(proj.used_keys || 0) }),
      ]),
      el("td", {}, [
        proj.active_device_id
          ? el("span", { class: "badge badge-success", text: proj.active_device_id.slice(0, 16) })
          : el("span", { class: "muted", text: "None" }),
      ]),
      el("td", { text: formatDate(proj.created_at) }),
      el("td", { class: "align-right" }, [
        el("div", { class: "row-actions", style: "display: flex; gap: 6px; justify-content: flex-end;" }, [
          !isDeleted
            ? el("button", {
                class: "btn btn-ghost btn-sm",
                type: "button",
                text: isActive ? "Deactivate" : "Activate",
                onclick: () => toggleProjectStatus(proj),
              })
            : null,
          !isDeleted && isActive
            ? el("button", {
                class: "btn btn-ghost btn-sm",
                type: "button",
                text: "⚡ Key",
                onclick: () => openGeneratorForProject(proj.id),
              })
            : null,
          !isDeleted
            ? el("button", {
                class: "btn btn-danger btn-sm",
                type: "button",
                text: "Delete",
                onclick: () => deleteProjectPrompt(proj),
              })
            : null,
        ]),
      ]),
    ]);
    tbody.append(tr);
  });
}

async function viewProjectDetails(projectId) {
  try {
    const data = await api.project(projectId);
    const proj = data.project;
    const body = $("#project-detail-body");
    clearNode(body);

    body.append(
      el("div", { class: "detail-grid", style: "display: grid; grid-template-columns: 1fr 1fr; gap: 16px;" }, [
        el("div", {}, [el("span", { class: "muted", text: "Project Name:" }), el("h3", { text: proj.project_name })]),
        el("div", {}, [el("span", { class: "muted", text: "Status:" }), el("div", {}, [statusBadge(proj.status)])]),
        el("div", {}, [el("span", { class: "muted", text: "Database Ref:" }), el("p", {}, [el("code", { text: proj.database_url_ref || "—" })])]),
        el("div", {}, [el("span", { class: "muted", text: "Project ID:" }), el("p", {}, [el("code", { text: proj.id })])]),
        el("div", {}, [el("span", { class: "muted", text: "Created At:" }), el("p", { text: formatDate(proj.created_at) })]),
        el("div", {}, [
          el("span", { class: "muted", text: "Active Device:" }),
          el("p", { text: proj.active_device_id || "None connected" }),
        ]),
        el("div", { style: "grid-column: span 2;" }, [
          el("span", { class: "muted", text: "Key Inventory:" }),
          el("p", {
            text: `Available: ${proj.available_keys || 0} · Used: ${proj.used_keys || 0} · Revoked: ${proj.revoked_keys || 0} · Expired: ${proj.expired_keys || 0} · Total: ${proj.total_keys || 0}`,
          }),
        ]),
      ])
    );
    openDialog($("#project-detail-modal"));
  } catch (err) {
    fail(err, "Failed to load project details.");
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
    await loadProjects();
    await loadOverview();
  } catch (err) {
    fail(err, "Failed to update project status.");
  }
}

async function deleteProjectPrompt(project) {
  const confirmed = await confirmAction({
    title: "Delete Project?",
    message: `Are you sure you want to soft-delete '${project.project_name}'? This will IMMEDIATELY revoke all active Reader installations, invalidate device credentials, and revoke unused keys. Audit logs will be preserved.`,
    confirmLabel: "Delete Project & Revoke All",
    tone: "danger",
    requireReason: false,
  });
  if (!confirmed.confirmed) return;

  try {
    await api.deleteProject(project.id);
    toast(`Project '${project.project_name}' has been soft-deleted and all credentials revoked.`, {
      type: "success",
    });
    await loadProjects();
    await loadOverview();
  } catch (err) {
    fail(err, "Failed to delete project.");
  }
}

function openGeneratorForProject(projectId) {
  showView("generator");
  setTimeout(() => {
    const sel = $("#gen-project-select");
    if (sel) sel.value = projectId;
  }, 100);
}

/* ----------------------------------------------------------- View: Generator */

async function loadGenerator() {
  const data = await api.projects({ include_deleted: false });
  const activeProjects = (data.projects || []).filter((p) => p.status === "active");
  const sel = $("#gen-project-select");
  if (!sel) return;

  const currentVal = sel.value;
  clearNode(sel);
  sel.append(el("option", { value: "", text: "Select target project…" }));
  activeProjects.forEach((p) => {
    sel.append(el("option", { value: p.id, text: p.project_name }));
  });
  if (currentVal && activeProjects.some((p) => p.id === currentVal)) {
    sel.value = currentVal;
  }
}

async function handleGenerateKeys(event) {
  event.preventDefault();
  const projectId = $("#gen-project-select").value;
  const quantity = parseInt($("#gen-quantity-input").value, 10) || 1;
  const expiryType = $("#gen-expiry-type-select").value;
  const customDate = $("#gen-custom-expiry-input").value;
  const label = $("#gen-label-input").value.trim();
  const errEl = $("#generator-error");
  errEl.textContent = "";

  if (!projectId) {
    errEl.textContent = "Please select a target project.";
    return;
  }

  const isPermanent = expiryType === "permanent";
  let expiresAt = null;
  if (!isPermanent) {
    if (!customDate) {
      errEl.textContent = "Please pick a valid expiry date.";
      return;
    }
    expiresAt = new Date(`${customDate}T23:59:59Z`).toISOString();
  }

  const submitBtn = $("#generate-keys-btn");
  setButtonBusy(submitBtn, true, "Generating securely…");

  try {
    let result;
    if (quantity === 1) {
      result = await api.generateReaderKey(projectId, {
        is_permanent: isPermanent,
        expires_at: expiresAt,
        label,
      });
      state.generatedKeys = [result];
    } else {
      const bulkRes = await api.generateBulkReaderKeys(projectId, {
        quantity,
        is_permanent: isPermanent,
        expires_at: expiresAt,
        label,
      });
      state.generatedKeys = bulkRes.keys || [];
    }

    renderGeneratedKeysModal(state.generatedKeys);
    toast(`Successfully generated ${state.generatedKeys.length} reader key(s).`, {
      type: "success",
    });
    await loadOverview();
  } catch (err) {
    fail(err, "Failed to generate reader keys.");
    errEl.textContent = err.message || "Failed to generate keys.";
  } finally {
    setButtonBusy(submitBtn, false);
  }
}

function renderGeneratedKeysModal(keys) {
  const container = $("#generated-list");
  if (!container) return;
  clearNode(container);

  setText("#result-summary", `Generated ${keys.length} key(s) for target project.`);

  keys.forEach((k) => {
    const row = el("div", {
      class: "key-item glass",
      style: "display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; margin-bottom: 8px; border: 1px solid var(--line); border-radius: var(--radius-sm);",
    }, [
      el("div", {}, [
        el("code", {
          style: "font-size: 1.15rem; color: var(--cyan); letter-spacing: 0.05em; font-weight: 700;",
          text: k.plaintext_key,
        }),
        el("div", {
          class: "muted",
          style: "font-size: 0.8rem; margin-top: 4px;",
          text: `${k.key_hint} · ${k.is_permanent ? "Permanent" : `Expires ${formatDate(k.expires_at)}`}`,
        }),
      ]),
      el("button", {
        class: "btn btn-ghost btn-sm",
        type: "button",
        text: "Copy",
        onclick: async () => {
          await copyText(k.plaintext_key);
          toast("Plaintext key copied to clipboard.", { type: "info" });
        },
      }),
    ]);
    container.append(row);
  });

  openDialog($("#result-modal"));
}

/* ------------------------------------------------------------- View: Keys */

async function loadKeys() {
  const projectId = $("#keys-filter-project") ? $("#keys-filter-project").value : "";
  const status = $("#keys-filter-status") ? $("#keys-filter-status").value : "";
  const expiryFilter = $("#keys-filter-expiry") ? $("#keys-filter-expiry").value : "";
  const search = $("#keys-search-input") ? $("#keys-search-input").value.trim() : "";
  const pageSize = $("#keys-page-size") ? parseInt($("#keys-page-size").value, 10) : 25;
  const page = state.keysPagination.page || 1;

  // Populate projects filter if empty
  populateProjectsFilter("#keys-filter-project");

  const data = await api.readerKeys({
    project_id: projectId,
    status,
    expiry_filter: expiryFilter,
    search,
    page,
    page_size: pageSize,
  });

  state.keys = data.items || [];
  state.keysPagination = data.pagination || { page, pageSize, total: 0, totalPages: 1 };
  renderKeysTable(state.keys);
}

function renderKeysTable(keys) {
  const tbody = $("#keys-body");
  if (!tbody) return;
  clearNode(tbody);

  const pag = state.keysPagination;
  if (!keys.length) {
    tbody.append(emptyRow(9, "No reader keys found", "Adjust filters or generate keys for an active project."));
    setText("#keys-summary", "0 keys");
    setText("#keys-page-info", "Page 1 of 1");
    return;
  }

  setText(
    "#keys-summary",
    `Showing ${(pag.page - 1) * pag.page_size + 1}–${Math.min(
      pag.page * pag.page_size,
      pag.total
    )} of ${pag.total} key(s)`
  );
  setText("#keys-page-info", `Page ${pag.page} of ${pag.total_pages || 1}`);

  $("#keys-prev-page").disabled = pag.page <= 1;
  $("#keys-next-page").disabled = pag.page >= (pag.total_pages || 1);

  keys.forEach((k) => {
    const isGenerated = k.status === "generated";
    const tr = el("tr", {}, [
      el("td", {}, [
        el("a", {
          href: "#",
          style: "font-weight: 600; color: var(--cyan);",
          text: k.key_hint,
          onclick: (e) => {
            e.preventDefault();
            viewKeyDetails(k.id);
          },
        }),
      ]),
      el("td", { text: k.project_name }),
      el("td", { text: k.label || "—" }),
      el("td", {}, [statusBadge(k.status)]),
      el("td", {}, [
        k.is_permanent
          ? el("span", { class: "badge badge-info", text: "Yes" })
          : el("span", { class: "muted", text: "No" }),
      ]),
      el("td", { text: k.is_permanent ? "Never" : formatDate(k.expires_at) }),
      el("td", { text: formatDate(k.used_at) }),
      el("td", { text: formatDate(k.created_at) }),
      el("td", { class: "align-right" }, [
        isGenerated
          ? el("button", {
              class: "btn btn-danger btn-sm",
              type: "button",
              text: "Revoke",
              onclick: () => revokeKeyPrompt(k),
            })
          : null,
      ]),
    ]);
    tbody.append(tr);
  });
}

async function viewKeyDetails(keyId) {
  try {
    const data = await api.readerKey(keyId);
    const key = data.key;
    const body = $("#detail-body");
    clearNode(body);

    body.append(
      el("div", { class: "detail-grid", style: "display: grid; grid-template-columns: 1fr 1fr; gap: 16px;" }, [
        el("div", {}, [el("span", { class: "muted", text: "Key Hint (Masked):" }), el("h3", { text: key.key_hint })]),
        el("div", {}, [el("span", { class: "muted", text: "Status:" }), el("div", {}, [statusBadge(key.status)])]),
        el("div", {}, [el("span", { class: "muted", text: "Project:" }), el("p", { text: key.project_name })]),
        el("div", {}, [el("span", { class: "muted", text: "Label:" }), el("p", { text: key.label || "None" })]),
        el("div", {}, [el("span", { class: "muted", text: "Is Permanent:" }), el("p", { text: key.is_permanent ? "Yes" : "No" })]),
        el("div", {}, [el("span", { class: "muted", text: "Expires At:" }), el("p", { text: key.is_permanent ? "Never" : formatDate(key.expires_at) })]),
        el("div", {}, [el("span", { class: "muted", text: "Created At:" }), el("p", { text: formatDate(key.created_at) })]),
        el("div", {}, [el("span", { class: "muted", text: "Used At:" }), el("p", { text: formatDate(key.used_at) })]),
        key.status === "revoked"
          ? el("div", { style: "grid-column: span 2;" }, [
              el("span", { class: "muted", text: "Revoked Reason:" }),
              el("p", { style: "color: var(--danger);", text: key.revoked_reason || "Revoked by admin" }),
            ])
          : null,
        key.installation_id
          ? el("div", { style: "grid-column: span 2;" }, [
              el("span", { class: "muted", text: "Connected Reader Device:" }),
              el("p", { text: `${key.device_id || "Unknown device"} (${key.installation_status})` }),
            ])
          : null,
      ])
    );
    openDialog($("#detail-modal"));
  } catch (err) {
    fail(err, "Failed to load key details.");
  }
}

async function revokeKeyPrompt(key) {
  const confirmed = await confirmAction({
    title: `Revoke Reader Key '${key.key_hint}'?`,
    message: "Revoking this key is permanent. It will no longer be redeemable by any Reader App.",
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
  } catch (err) {
    fail(err, "Failed to revoke reader key.");
  }
}

async function populateProjectsFilter(selector) {
  const sel = $(selector);
  if (!sel || sel.children.length > 1) return;
  const data = await api.projects({ include_deleted: false });
  (data.projects || []).forEach((p) => {
    sel.append(el("option", { value: p.id, text: p.project_name }));
  });
}

/* ----------------------------------------------------- View: Installations */

async function loadInstallations() {
  const projectId = $("#installations-filter-project")
    ? $("#installations-filter-project").value
    : "";
  const status = $("#installations-filter-status") ? $("#installations-filter-status").value : "";
  const search = $("#installations-search-input")
    ? $("#installations-search-input").value.trim()
    : "";
  const page = state.installationsPagination.page || 1;

  populateProjectsFilter("#installations-filter-project");

  const data = await api.installations({
    project_id: projectId,
    status,
    search,
    page,
    page_size: 25,
  });

  state.installations = data.items || [];
  state.installationsPagination = data.pagination || { page, pageSize: 25, total: 0, totalPages: 1 };
  renderInstallationsTable(state.installations);
}

function renderInstallationsTable(installations) {
  const tbody = $("#installations-table-body");
  if (!tbody) return;
  clearNode(tbody);

  const pag = state.installationsPagination;
  if (!installations.length) {
    tbody.append(emptyRow(8, "No installations found", "Reader installations appear here when a Reader App redeems a key."));
    setText("#installations-summary", "0 installations");
    setText("#installations-page-info", "Page 1 of 1");
    return;
  }

  setText(
    "#installations-summary",
    `Showing ${(pag.page - 1) * pag.page_size + 1}–${Math.min(
      pag.page * pag.page_size,
      pag.total
    )} of ${pag.total} installation(s)`
  );
  setText("#installations-page-info", `Page ${pag.page} of ${pag.total_pages || 1}`);

  $("#installations-prev-page").disabled = pag.page <= 1;
  $("#installations-next-page").disabled = pag.page >= (pag.total_pages || 1);

  installations.forEach((inst) => {
    const isActive = inst.status === "active";
    const tr = el("tr", {}, [
      el("td", {}, [
        el("a", {
          href: "#",
          style: "color: var(--cyan); font-weight: 600;",
          text: (inst.id || "").slice(0, 8),
          onclick: (e) => {
            e.preventDefault();
            viewInstallationDetails(inst.id);
          },
        }),
      ]),
      el("td", { text: inst.project_name }),
      el("td", {}, [el("code", { text: inst.key_hint })]),
      el("td", { text: inst.device_id || "—" }),
      el("td", {}, [statusBadge(inst.status)]),
      el("td", { text: formatDate(inst.activated_at) }),
      el("td", { text: formatRelative(inst.last_seen_at) }),
      el("td", { class: "align-right" }, [
        isActive
          ? el("button", {
              class: "btn btn-danger btn-sm",
              type: "button",
              text: "Revoke",
              onclick: () => revokeInstallationPrompt(inst),
            })
          : null,
      ]),
    ]);
    tbody.append(tr);
  });
}

async function viewInstallationDetails(installationId) {
  try {
    const data = await api.installation(installationId);
    const inst = data.installation;
    const body = $("#installation-detail-body");
    clearNode(body);

    body.append(
      el("div", { class: "detail-grid", style: "display: grid; grid-template-columns: 1fr 1fr; gap: 16px;" }, [
        el("div", {}, [el("span", { class: "muted", text: "Installation ID:" }), el("p", {}, [el("code", { text: inst.id })])]),
        el("div", {}, [el("span", { class: "muted", text: "Status:" }), el("div", {}, [statusBadge(inst.status)])]),
        el("div", {}, [el("span", { class: "muted", text: "Project:" }), el("h3", { text: inst.project_name })]),
        el("div", {}, [el("span", { class: "muted", text: "Device Identifier:" }), el("p", { text: inst.device_id || "Unspecified" })]),
        el("div", {}, [el("span", { class: "muted", text: "Activated At:" }), el("p", { text: formatDate(inst.activated_at) })]),
        el("div", {}, [el("span", { class: "muted", text: "Last Heartbeat:" }), el("p", { text: formatDate(inst.last_seen_at) })]),
        el("div", {}, [el("span", { class: "muted", text: "Client IP:" }), el("p", { text: inst.ip_address || "—" })]),
        el("div", {}, [el("span", { class: "muted", text: "Redeemed Key:" }), el("p", {}, [el("code", { text: inst.key_hint })])]),
      ])
    );
    openDialog($("#installation-detail-modal"));
  } catch (err) {
    fail(err, "Failed to load installation details.");
  }
}

async function revokeInstallationPrompt(inst) {
  const confirmed = await confirmAction({
    title: `Revoke Installation for '${inst.project_name}'?`,
    message: "Revoking this installation will immediately invalidate its device token credentials. The Reader App will no longer be authorized to reconnect or record heartbeats. A new Reader Key must be generated to connect a new device.",
    confirmLabel: "Revoke Installation & Invalidate Token",
    tone: "danger",
    requireReason: true,
    reasonLabel: "Revocation Reason (Audit Log)",
    reasonPlaceholder: "Explain why this device installation is being revoked…",
  });
  if (!confirmed.confirmed) return;

  try {
    await api.revokeInstallation(inst.id, confirmed.reason);
    toast("Reader installation revoked successfully.", { type: "success" });
    await loadInstallations();
    await loadOverview();
  } catch (err) {
    fail(err, "Failed to revoke installation.");
  }
}

/* ---------------------------------------------------------- View: Security */

async function loadSecurity() {
  const data = await api.securityStatus();
  state.securityData = data;

  const auth = data.auth || {};
  setText("#sec-admin-email", auth.admin_email || "admin@example.com");
  setText("#sec-admin-role", (auth.admin_role || "owner").toUpperCase());
  setText("#sec-failed-attempts", auth.failed_login_attempts ?? 0);

  const session = data.session || {};
  setText("#sec-cookie-name", session.cookie_name || "nexora_session");
  setText("#sec-session-ttl", `${Math.round((session.session_ttl_seconds || 28800) / 3600)} hours`);

  const db = data.database || {};
  const isDbConnected = db.status === "connected";
  const secDbStatus = $("#sec-db-status");
  if (secDbStatus) {
    secDbStatus.textContent = isDbConnected ? "CONNECTED" : "UNAVAILABLE";
    secDbStatus.className = `badge ${isDbConnected ? "badge-success" : "badge-danger"}`;
  }
  setText("#sec-db-ssl", db.ssl_mode || "require");

  renderSecurityEvents(data.recent_events || []);
}

function renderSecurityEvents(events) {
  const tbody = $("#sec-events-table-body");
  if (!tbody) return;
  clearNode(tbody);

  if (!events.length) {
    tbody.append(emptyRow(5, "No recent security events", "Events appear as logins and revocations take place."));
    return;
  }

  events.forEach((ev) => {
    const tr = el("tr", {}, [
      el("td", { text: formatDate(ev.created_at) }),
      el("td", {}, [el("strong", { text: ev.action })]),
      el("td", {}, [statusBadge(ev.outcome)]),
      el("td", { text: ev.target_type || "—" }),
      el("td", {}, [el("code", { text: ev.ip_address || "—" })]),
    ]);
    tbody.append(tr);
  });
}

/* -------------------------------------------------------- View: Audit Logs */

async function loadAuditLogs() {
  const search = $("#audit-search-input") ? $("#audit-search-input").value.trim() : "";
  const action = $("#audit-filter-action") ? $("#audit-filter-action").value : "";
  const outcome = $("#audit-filter-outcome") ? $("#audit-filter-outcome").value : "";
  const page = state.auditPagination.page || 1;

  const data = await api.auditLogs({
    search,
    action,
    outcome,
    page,
    page_size: 25,
  });

  state.auditLogs = data.items || [];
  state.auditPagination = data.pagination || { page, pageSize: 25, total: 0, totalPages: 1 };
  renderAuditTable(state.auditLogs);
}

function renderAuditTable(items) {
  const tbody = $("#audit-table-body");
  if (!tbody) return;
  clearNode(tbody);

  const pag = state.auditPagination;
  if (!items.length) {
    tbody.append(emptyRow(8, "No audit logs found", "Administrative events will be automatically recorded here."));
    setText("#audit-summary", "0 records");
    setText("#audit-page-info", "Page 1 of 1");
    return;
  }

  setText(
    "#audit-summary",
    `Showing ${(pag.page - 1) * pag.page_size + 1}–${Math.min(
      pag.page * pag.page_size,
      pag.total
    )} of ${pag.total} event(s)`
  );
  setText("#audit-page-info", `Page ${pag.page} of ${pag.total_pages || 1}`);

  $("#audit-prev-page").disabled = pag.page <= 1;
  $("#audit-next-page").disabled = pag.page >= (pag.total_pages || 1);

  items.forEach((item) => {
    const tr = el("tr", {}, [
      el("td", { text: formatDate(item.created_at) }),
      el("td", {}, [el("strong", { text: item.action })]),
      el("td", { text: item.target_type || "—" }),
      el("td", {}, [el("code", { text: (item.target_id || "—").slice(0, 8) })]),
      el("td", {}, [statusBadge(item.outcome)]),
      el("td", { text: item.actor_email || "System / Admin" }),
      el("td", {}, [el("code", { text: item.ip_address || "—" })]),
      el("td", { class: "align-right" }, [
        el("button", {
          class: "btn btn-ghost btn-sm",
          type: "button",
          text: "View",
          onclick: () => viewLogDetails(item),
        }),
      ]),
    ]);
    tbody.append(tr);
  });
}

function viewLogDetails(item) {
  const body = $("#log-detail-body");
  clearNode(body);

  body.append(
    el("div", {}, [
      el("p", {}, [el("strong", { text: "Action: " }), el("span", { text: item.action })]),
      el("p", {}, [el("strong", { text: "Timestamp: " }), el("span", { text: formatDate(item.created_at) })]),
      el("p", {}, [el("strong", { text: "Outcome: " }), statusBadge(item.outcome)]),
      el("p", {}, [el("strong", { text: "Target: " }), el("span", { text: `${item.target_type || ""} (${item.target_id || ""})` })]),
      el("p", {}, [el("strong", { text: "Actor: " }), el("span", { text: item.actor_email || "System" })]),
      el("p", {}, [el("strong", { text: "IP Address: " }), el("code", { text: item.ip_address || "—" })]),
      el("p", {}, [el("strong", { text: "User Agent: " }), el("span", { class: "muted", text: item.user_agent || "—" })]),
      el("hr", { style: "border: none; border-top: 1px solid var(--line); margin: 12px 0;" }),
      el("strong", { text: "Event Details Payload:" }),
      el("pre", {
        class: "glass",
        style: "padding: 12px; border-radius: var(--radius-sm); max-height: 200px; overflow-y: auto; font-size: 0.85rem;",
        text: JSON.stringify(item.details || {}, null, 2),
      }),
    ])
  );
  openDialog($("#log-detail-modal"));
}

/* ---------------------------------------------------------- View: Settings */

async function loadSettings() {
  const data = await api.settings();
  state.settingsData = data;

  const prefix = $("#setting-key-prefix");
  if (prefix) prefix.value = data.key_prefix || "NXR";

  const expiry = $("#setting-default-expiry");
  if (expiry) expiry.value = data.default_key_expiry_days ?? 30;

  const bulk = $("#setting-max-bulk");
  if (bulk) bulk.value = data.max_bulk_keys ?? 500;

  const hb = $("#setting-heartbeat-interval");
  if (hb) hb.value = data.heartbeat_interval_seconds ?? 300;

  const rl = $("#setting-rate-limits");
  if (rl) rl.value = data.api_rate_limits || "60/min";

  const tz = $("#setting-timezone");
  if (tz) tz.value = data.display_timezone || "UTC";

  const maint = $("#setting-maintenance-mode");
  if (maint) maint.checked = Boolean(data.maintenance_mode);
}

async function handleSaveSettings(event) {
  event.preventDefault();
  const btn = $("#save-settings-btn");
  setButtonBusy(btn, true, "Saving…");

  const updates = {
    key_prefix: $("#setting-key-prefix").value.trim(),
    default_key_expiry_days: parseInt($("#setting-default-expiry").value, 10),
    max_bulk_keys: parseInt($("#setting-max-bulk").value, 10),
    heartbeat_interval_seconds: parseInt($("#setting-heartbeat-interval").value, 10),
    api_rate_limits: $("#setting-rate-limits").value.trim(),
    display_timezone: $("#setting-timezone").value,
    maintenance_mode: $("#setting-maintenance-mode").checked,
  };

  try {
    await api.updateSettings(updates);
    toast("System settings updated successfully.", { type: "success" });
    await loadSettings();
  } catch (err) {
    fail(err, "Failed to save settings.");
  } finally {
    setButtonBusy(btn, false);
  }
}

/* ------------------------------------------------- Event Listeners & Boot */

function setupEventListeners() {
  // Navigation
  $$(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => showView(btn.dataset.view));
  });

  // Deep linking via hash
  window.addEventListener("hashchange", () => {
    const hash = window.location.hash.replace("#", "");
    if (hash && VIEW_META[hash] && hash !== state.view) {
      showView(hash, { updateHash: false });
    }
  });

  // Buttons with data-go
  $$( "[data-go]" ).forEach((btn) => {
    btn.addEventListener("click", () => showView(btn.dataset.go));
  });

  // Refresh button
  const refreshBtn = $("#refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadViewData(state.view));
  }

  // Logout
  const logoutBtn = $("#logout-btn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async () => {
      try {
        await api.logout();
      } finally {
        window.location.replace("/login");
      }
    });
  }

  // Modals close button handler
  $$("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const modal = $(`#${btn.dataset.close}`);
      if (modal) closeDialog(modal);
    });
  });

  // Projects View: Create Project Form
  const openCreateProjBtn = $("#open-create-project-btn");
  if (openCreateProjBtn) {
    openCreateProjBtn.addEventListener("click", () => {
      $("#proj-name-input").value = "";
      $("#proj-ref-input").value = "";
      $("#project-modal-error").textContent = "";
      openDialog($("#project-modal"));
    });
  }

  const createProjForm = $("#create-project-form");
  if (createProjForm) {
    createProjForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = $("#proj-name-input").value.trim();
      const ref = $("#proj-ref-input").value.trim();
      const errEl = $("#project-modal-error");
      errEl.textContent = "";

      if (!name) {
        errEl.textContent = "Project name is required.";
        return;
      }

      const submitBtn = $("#create-project-submit");
      setButtonBusy(submitBtn, true, "Creating…");

      try {
        await api.createProject({ project_name: name, database_url_ref: ref });
        closeDialog($("#project-modal"));
        toast(`Project '${name}' created successfully.`, { type: "success" });
        await loadProjects();
        await loadOverview();
      } catch (err) {
        errEl.textContent = err.message || "Failed to create project.";
      } finally {
        setButtonBusy(submitBtn, false);
      }
    });
  }

  // Project search & filter
  const projSearch = $("#project-search-input");
  if (projSearch) {
    projSearch.addEventListener("input", () => loadProjects());
  }
  const projFilter = $("#project-filter-deleted");
  if (projFilter) {
    projFilter.addEventListener("change", () => loadProjects());
  }
  const projRefBtn = $("#projects-refresh-btn");
  if (projRefBtn) {
    projRefBtn.addEventListener("click", () => loadProjects());
  }

  // Key Generator Form
  const keyGenForm = $("#key-generator-form");
  if (keyGenForm) {
    keyGenForm.addEventListener("submit", handleGenerateKeys);
  }

  const expiryTypeSel = $("#gen-expiry-type-select");
  if (expiryTypeSel) {
    expiryTypeSel.addEventListener("change", () => {
      const dateGroup = $("#gen-custom-date-group");
      if (dateGroup) {
        dateGroup.style.display = expiryTypeSel.value === "custom" ? "flex" : "none";
      }
    });
  }

  // Result Modal Copy & Download
  const copyAllBtn = $("#copy-all");
  if (copyAllBtn) {
    copyAllBtn.addEventListener("click", async () => {
      const texts = state.generatedKeys.map((k) => k.plaintext_key).join("\n");
      await copyText(texts);
      toast("All plaintext keys copied to clipboard.", { type: "info" });
    });
  }

  const downloadCsvBtn = $("#download-keys-csv");
  if (downloadCsvBtn) {
    downloadCsvBtn.addEventListener("click", () => {
      const lines = [
        "Key ID,Project Name,Plaintext Key,Key Hint,Is Permanent,Expires At",
        ...state.generatedKeys.map(
          (k) =>
            `"${k.id}","${k.project_name || ""}","${k.plaintext_key}","${k.key_hint}","${
              k.is_permanent
            }","${k.expires_at || ""}"`
        ),
      ];
      downloadText("nexora-generated-keys.csv", lines.join("\n"));
    });
  }

  // Keys View Filters & Pagination
  const keysSearch = $("#keys-search-input");
  if (keysSearch) keysSearch.addEventListener("input", () => loadKeys());
  const keysProj = $("#keys-filter-project");
  if (keysProj) keysProj.addEventListener("change", () => loadKeys());
  const keysStatus = $("#keys-filter-status");
  if (keysStatus) keysStatus.addEventListener("change", () => loadKeys());
  const keysExpiry = $("#keys-filter-expiry");
  if (keysExpiry) keysExpiry.addEventListener("change", () => loadKeys());
  const keysPageSize = $("#keys-page-size");
  if (keysPageSize) keysPageSize.addEventListener("change", () => loadKeys());

  const keysPrev = $("#keys-prev-page");
  if (keysPrev) {
    keysPrev.addEventListener("click", () => {
      if (state.keysPagination.page > 1) {
        state.keysPagination.page -= 1;
        loadKeys();
      }
    });
  }
  const keysNext = $("#keys-next-page");
  if (keysNext) {
    keysNext.addEventListener("click", () => {
      if (state.keysPagination.page < state.keysPagination.totalPages) {
        state.keysPagination.page += 1;
        loadKeys();
      }
    });
  }

  const exportKeysBtn = $("#export-keys-btn");
  if (exportKeysBtn) {
    exportKeysBtn.addEventListener("click", () => {
      const projectId = $("#keys-filter-project") ? $("#keys-filter-project").value : "";
      const status = $("#keys-filter-status") ? $("#keys-filter-status").value : "";
      const search = $("#keys-search-input") ? $("#keys-search-input").value.trim() : "";
      const expiry = $("#keys-filter-expiry") ? $("#keys-filter-expiry").value : "";
      const url = api.exportReaderKeysUrl({
        project_id: projectId,
        status,
        search,
        expiry_filter: expiry,
      });
      window.location.href = url;
    });
  }

  // Installations View Filters & Pagination
  const instSearch = $("#installations-search-input");
  if (instSearch) instSearch.addEventListener("input", () => loadInstallations());
  const instProj = $("#installations-filter-project");
  if (instProj) instProj.addEventListener("change", () => loadInstallations());
  const instStatus = $("#installations-filter-status");
  if (instStatus) instStatus.addEventListener("change", () => loadInstallations());

  const instPrev = $("#installations-prev-page");
  if (instPrev) {
    instPrev.addEventListener("click", () => {
      if (state.installationsPagination.page > 1) {
        state.installationsPagination.page -= 1;
        loadInstallations();
      }
    });
  }
  const instNext = $("#installations-next-page");
  if (instNext) {
    instNext.addEventListener("click", () => {
      if (state.installationsPagination.page < state.installationsPagination.totalPages) {
        state.installationsPagination.page += 1;
        loadInstallations();
      }
    });
  }

  // Audit Logs View Filters & Pagination
  const auditSearch = $("#audit-search-input");
  if (auditSearch) auditSearch.addEventListener("input", () => loadAuditLogs());
  const auditAction = $("#audit-filter-action");
  if (auditAction) auditAction.addEventListener("change", () => loadAuditLogs());
  const auditOutcome = $("#audit-filter-outcome");
  if (auditOutcome) auditOutcome.addEventListener("change", () => loadAuditLogs());
  const auditRefresh = $("#audit-refresh-btn");
  if (auditRefresh) auditRefresh.addEventListener("click", () => loadAuditLogs());

  const auditPrev = $("#audit-prev-page");
  if (auditPrev) {
    auditPrev.addEventListener("click", () => {
      if (state.auditPagination.page > 1) {
        state.auditPagination.page -= 1;
        loadAuditLogs();
      }
    });
  }
  const auditNext = $("#audit-next-page");
  if (auditNext) {
    auditNext.addEventListener("click", () => {
      if (state.auditPagination.page < state.auditPagination.totalPages) {
        state.auditPagination.page += 1;
        loadAuditLogs();
      }
    });
  }

  // Settings View Form
  const settingsForm = $("#system-settings-form");
  if (settingsForm) {
    settingsForm.addEventListener("submit", handleSaveSettings);
  }
}

async function boot() {
  setupEventListeners();

  try {
    const sessionData = await api.session();
    state.user = sessionData.user;
    if (sessionData.csrf_token) {
      setCsrfToken(sessionData.csrf_token);
    }
    applyIdentity();

    // Determine initial view from hash
    const initialView = window.location.hash.replace("#", "") || "overview";
    showView(initialView, { updateHash: false });
  } catch (error) {
    if (error instanceof ApiProblem && error.status === 401) {
      window.location.replace("/login");
      return;
    }
    fail(error, "Failed to initialize administrator session.");
  }
}

boot();
