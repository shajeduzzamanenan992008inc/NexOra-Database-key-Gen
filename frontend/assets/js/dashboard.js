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

const ROLE_LABEL = { owner: "Owner", admin: "Administrator", viewer: "Viewer" };
const VIEW_META = {
  overview: {
    title: "Overview",
    subtitle: "Live license posture across the ecosystem.",
  },
  keys: {
    title: "License keys",
    subtitle: "Issue, inspect, deactivate, revoke and export keys.",
  },
  logs: {
    title: "Activity logs",
    subtitle: "Generation batches, activations and the administrative audit trail.",
  },
};

const state = {
  user: null,
  view: "overview",
  keysLoaded: false,
  statsLoaded: false,
  page: 1,
  pageSize: 15,
  status: "",
  search: "",
  sort: "created_at",
  order: "desc",
  pagination: null,
  keys: [],
  logsKind: "generation",
  logsPage: 1,
  logsPagination: null,
  generated: [],
  generatedFormat: "",
  currentKeyId: null,
  searchTimer: null,
};

const numberFormat = new Intl.NumberFormat();

function isAdmin() {
  return state.user && ["owner", "admin"].includes(state.user.role);
}

function isOwner() {
  return state.user && state.user.role === "owner";
}

function fail(error, fallback = "The request could not be completed.") {
  if (error instanceof ApiProblem && error.status === 401) {
    window.location.replace("/");
    return;
  }
  const detail =
    error instanceof ApiProblem && error.details && error.details.fields
      ? Object.entries(error.details.fields)
          .map(([field, message]) => `${field}: ${message}`)
          .join(" · ")
      : "";
  toast(error && error.message ? error.message : fallback, {
    type: "error",
    detail,
  });
}

function showGlobalError(message) {
  const banner = $("#global-error");
  banner.textContent = message;
  banner.hidden = false;
}

function setBusyOverlay(visible) {
  const overlay = $("#busy-overlay");
  if (overlay) overlay.hidden = !visible;
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value;
}

function setStat(name, value) {
  $$(`[data-stat="${name}"]`).forEach((node) => {
    node.textContent =
      typeof value === "number" ? numberFormat.format(value) : String(value);
  });
}

function shortId(value) {
  if (!value) return "—";
  return String(value).slice(0, 8);
}

function emptyState(title, description, colspan) {
  return el("tr", {}, [
    el("td", { colspan: String(colspan) }, [
      el("div", { class: "empty-state" }, [
        el("strong", { text: title }),
        el("span", { text: description }),
      ]),
    ]),
  ]);
}

/* ------------------------------------------------------------------ identity */

function applyIdentity() {
  const user = state.user;
  if (!user) return;
  setText("#user-name", user.full_name || user.email);
  setText("#user-role", ROLE_LABEL[user.role] || user.role);
  const initials = (user.full_name || user.email || "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
  setText("#user-initials", initials || "NX");

  if (!isAdmin()) {
    const generate = $("#open-generate");
    if (generate) generate.hidden = true;
  }
}

/* --------------------------------------------------------------------- views */

function showView(view, { pushHash = true, load = true } = {}) {
  if (!VIEW_META[view]) view = "overview";
  state.view = view;
  $$(".nav-item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
  });
  $$(".view").forEach((section) => {
    section.classList.toggle("is-active", section.id === `view-${view}`);
  });
  setText("#page-title", VIEW_META[view].title);
  setText("#page-subtitle", VIEW_META[view].subtitle);
  document.title = `${VIEW_META[view].title} · NexOra Database Key Generator`;
  if (pushHash && window.location.hash !== `#${view}`) {
    window.history.replaceState(null, "", `#${view}`);
  }

  if (!load) return;
  if (view === "keys" && !state.keysLoaded) loadKeys();
  if (view === "logs") loadLogs();
  if (view === "overview") loadStats();
}

/* --------------------------------------------------------------------- stats */

async function loadStats() {
  try {
    const stats = await api.stats();
    state.statsLoaded = true;
    const totals = stats.totals || {};
    const activity = stats.activity || {};
    ["total", "active", "inactive", "pending", "used", "expired", "revoked"].forEach(
      (key) => setStat(key, totals[key] ?? 0)
    );
    ["generated_24h", "activations_total", "activations_24h", "expiring_soon"].forEach(
      (key) => setStat(key, activity[key] ?? 0)
    );
    setStat("activation_rate", activity.activation_rate ?? 0);
    renderRecentGenerations(stats.recent_generations || []);
    renderRecentAudit(stats.recent_audit || []);
  } catch (error) {
    fail(error, "Dashboard statistics could not be loaded.");
  }
}

function renderRecentGenerations(rows) {
  const body = $("#recent-generations");
  if (!body) return;
  clearNode(body);
  if (!rows.length) {
    body.append(emptyState("No generations yet", "Issued batches will appear here.", 5));
    return;
  }
  rows.forEach((row) => {
    body.append(
      el("tr", {}, [
        el("td", {}, [el("code", { class: "key-hint", text: shortId(row.batch_id) })]),
        el("td", { text: numberFormat.format(row.quantity) }),
        el("td", {}, [el("code", { class: "key-hint", text: row.key_format })]),
        el("td", { text: row.generated_by || "System" }),
        el("td", { text: formatRelative(row.created_at) }),
      ])
    );
  });
}

function renderRecentAudit(rows) {
  const list = $("#recent-audit");
  if (!list) return;
  clearNode(list);
  if (!rows.length) {
    list.append(
      el("li", {}, [
        el("div", { class: "empty-state" }, [
          el("strong", { text: "No audit entries yet" }),
          el("span", { text: "Administrative actions are recorded automatically." }),
        ]),
      ])
    );
    return;
  }
  rows.forEach((row) => {
    list.append(
      el("li", { class: "audit-row" }, [
        el("span", {
          class: `audit-mark ${row.outcome === "failure" ? "failure" : "success"}`,
          "aria-hidden": "true",
        }),
        el("div", { class: "audit-copy" }, [
          el("strong", { text: row.action }),
          el("span", {
            text: `${row.actor_email || "system"} · ${row.outcome}${
              row.target_type ? ` · ${row.target_type}` : ""
            }`,
          }),
        ]),
        el("time", {
          text: formatRelative(row.created_at),
          title: formatDate(row.created_at),
        }),
      ])
    );
  });
}

/* ---------------------------------------------------------------------- keys */

function keyQuery() {
  return {
    page: state.page,
    page_size: state.pageSize,
    status: state.status,
    search: state.search,
    sort: state.sort,
    order: state.order,
  };
}

async function loadKeys({ silent = false } = {}) {
  const body = $("#keys-body");
  if (!silent && body && !state.keysLoaded) {
    skeletonRows($("#keys-body").closest("table"), 7, 6);
  }
  try {
    const result = await api.keys(keyQuery());
    state.keysLoaded = true;
    state.keys = result.items || [];
    state.pagination = result.pagination || null;
    renderKeys();
  } catch (error) {
    fail(error, "License keys could not be loaded.");
  }
}

function renderKeys() {
  const body = $("#keys-body");
  if (!body) return;
  clearNode(body);

  if (!state.keys.length) {
    body.append(
      emptyState(
        "No keys match this view",
        "Adjust the filters or issue a new batch from the Generate keys panel.",
        7
      )
    );
    renderKeyPagination();
    return;
  }

  state.keys.forEach((key) => {
    const label = key.label || "Untitled batch";
    const row = el("tr", { dataset: { id: key.id } }, [
      el("td", {}, [
        el("span", { class: "key-label" }, [
          el("strong", { text: label }),
          el("span", { text: key.created_by_email || "System" }),
        ]),
      ]),
      el("td", {}, [el("code", { class: "key-hint", text: key.key_hint })]),
      el("td", {}, [statusBadge(key.status)]),
      el("td", {
        text: `${numberFormat.format(key.activation_count)} / ${numberFormat.format(
          key.max_activations
        )}`,
      }),
      el("td", {
        text: key.expires_at ? formatDate(key.expires_at, { time: false }) : "Never",
      }),
      el("td", {
        text: formatRelative(key.created_at),
        title: formatDate(key.created_at),
      }),
      el("td", {}, [
        el("div", { class: "actions-cell" }, [
          el("button", {
            class: "btn btn-ghost btn-sm",
            type: "button",
            dataset: { action: "manage", id: key.id },
            text: "Manage",
          }),
        ]),
      ]),
    ]);
    body.append(row);
  });

  renderKeyPagination();
}

function renderKeyPagination() {
  const pagination = state.pagination;
  const summary = $("#keys-summary");
  if (pagination && summary) {
    const from = pagination.total === 0 ? 0 : (pagination.page - 1) * pagination.page_size + 1;
    const to = Math.min(pagination.page * pagination.page_size, pagination.total);
    summary.textContent = `Showing ${from}–${to} of ${numberFormat.format(
      pagination.total
    )} keys`;
    setText(
      "#page-info",
      `Page ${pagination.page} of ${Math.max(1, pagination.total_pages)}`
    );
    $("#prev-page").disabled = !pagination.has_previous;
    $("#next-page").disabled = !pagination.has_next;
  }
}

/* ---------------------------------------------------------------------- logs */

const LOG_COLUMNS = {
  generation: ["Batch", "Qty", "Format", "Label", "Generated by", "Created"],
  activations: ["Key hint", "Device", "Account", "Status", "Activated", "Deactivated"],
  audit: ["Action", "Actor", "Target", "Outcome", "When"],
};

async function loadLogs() {
  const head = $("#logs-head");
  const body = $("#logs-body");
  if (head) {
    clearNode(head);
    const tr = el("tr");
    LOG_COLUMNS[state.logsKind].forEach((title) => tr.append(el("th", { text: title })));
    head.append(tr);
  }
  if (body) {
    clearNode(body);
    body.append(emptyState("Loading logs…", "Fetching secure records.", 6));
  }

  try {
    const result = await api.logs({
      kind: state.logsKind,
      page: state.logsPage,
      page_size: 15,
    });
    state.logsPagination = result.pagination || null;
    renderLogs(result.items || []);
  } catch (error) {
    fail(error, "Logs could not be loaded.");
  }
}

function renderLogs(rows) {
  const body = $("#logs-body");
  if (!body) return;
  clearNode(body);
  const columns = LOG_COLUMNS[state.logsKind].length;

  if (!rows.length) {
    body.append(
      emptyState("No records", "There is no activity in this log yet.", columns)
    );
    renderLogsPagination();
    return;
  }

  rows.forEach((row) => {
    let cells = [];
    if (state.logsKind === "generation") {
      cells = [
        el("code", { class: "key-hint", text: shortId(row.batch_id) }),
        numberFormat.format(row.quantity),
        el("code", { class: "key-hint", text: row.key_format }),
        row.label || "—",
        row.generated_by || "System",
        formatDate(row.created_at),
      ];
    } else if (state.logsKind === "activations") {
      cells = [
        el("code", { class: "key-hint", text: row.key_hint }),
        row.device_hint || "—",
        row.account_hint || "—",
        statusBadge(row.status),
        formatDate(row.activated_at),
        row.deactivated_at ? formatDate(row.deactivated_at) : "—",
      ];
    } else {
      cells = [
        el("code", { class: "key-hint", text: row.action }),
        row.actor_email || "system",
        row.target_type ? `${row.target_type} · ${shortId(row.target_id)}` : "—",
        statusBadge(row.outcome),
        formatDate(row.created_at),
      ];
    }

    body.append(
      el(
        "tr",
        {},
        cells.map((cell) =>
          cell instanceof Node ? el("td", {}, [cell]) : el("td", { text: String(cell) })
        )
      )
    );
  });

  renderLogsPagination();
}

function renderLogsPagination() {
  const pagination = state.logsPagination;
  if (!pagination) return;
  setText(
    "#logs-summary",
    `${numberFormat.format(pagination.total)} record(s) · page ${pagination.page} of ${Math.max(
      1,
      pagination.total_pages
    )}`
  );
  setText("#logs-page-info", `Page ${pagination.page}`);
  $("#logs-prev").disabled = !pagination.has_previous;
  $("#logs-next").disabled = !pagination.has_next;
}

/* ---------------------------------------------------------------- generation */

function updateFormatPreview() {
  const prefix = $("#gen-prefix").value.trim().toUpperCase();
  const suffix = $("#gen-suffix").value.trim().toUpperCase();
  const groups = Math.max(1, Math.min(8, Number($("#gen-groups").value) || 4));
  const length = Math.max(3, Math.min(10, Number($("#gen-group-length").value) || 4));
  const separator = $("#gen-separator").value;
  const parts = [];
  if (prefix) parts.push(prefix);
  for (let index = 0; index < groups; index += 1) parts.push("*".repeat(length));
  if (suffix) parts.push(suffix);
  $("#format-preview").textContent = parts.join(separator);
  $("#entropy-preview").textContent = `${groups * length * 5} bits of entropy per key`;
}

function openGenerate() {
  $("#generate-error").textContent = "";
  updateFormatPreview();
  openDialog($("#generate-modal"));
}

function generatedFilename() {
  const stamp = new Date()
    .toISOString()
    .replace(/[:T]/g, "-")
    .replace(/\..+$/, "");
  return `nexora-keys-${stamp}.txt`;
}

function renderGenerated(result) {
  state.generated = result.keys.map((key) => key.key);
  state.generatedFormat = result.key_format || "";
  const list = $("#generated-list");
  clearNode(list);
  result.keys.forEach((entry) => {
    list.append(
      el("div", { class: "key-row" }, [
        el("code", { class: "key-code", text: entry.key }),
        el("button", {
          class: "btn btn-ghost btn-sm",
          type: "button",
          dataset: { copy: entry.key },
          text: "Copy",
        }),
      ])
    );
  });
  $("#result-summary").textContent = `${result.quantity} key(s) · ${result.key_format} · batch ${shortId(
    result.batch_id
  )}`;
  openDialog($("#result-modal"));
}

async function submitGenerate(event) {
  event.preventDefault();
  const submit = $("#generate-submit");
  const errorNode = $("#generate-error");
  errorNode.textContent = "";

  const quantity = Number($("#gen-quantity").value);
  const maxActivations = Number($("#gen-max").value);
  const groups = Number($("#gen-groups").value);
  const groupLength = Number($("#gen-group-length").value);
  const prefix = $("#gen-prefix").value.trim().toUpperCase();
  const suffix = $("#gen-suffix").value.trim().toUpperCase();
  const separator = $("#gen-separator").value;
  const expires = $("#gen-expires").value;
  const label = $("#gen-label").value.trim();

  if (!Number.isInteger(quantity) || quantity < 1) {
    errorNode.textContent = "Quantity must be a positive whole number.";
    return;
  }
  if (!Number.isInteger(maxActivations) || maxActivations < 1) {
    errorNode.textContent = "Max activations must be at least 1.";
    return;
  }
  const randomized = groups * groupLength;
  if (randomized < 12 || randomized > 64) {
    errorNode.textContent =
      "Groups × group length must produce between 12 and 64 random characters.";
    return;
  }

  const body = {
    quantity,
    max_activations: maxActivations,
    prefix,
    suffix,
    groups,
    group_length: groupLength,
    separator,
    label,
  };
  if (expires) body.expires_at = expires;

  setButtonBusy(submit, true, "Generating…");
  try {
    const result =
      quantity > 1 ? await api.bulkGenerate(body) : await api.generate(body);
    closeDialog($("#generate-modal"));
    renderGenerated(result);
    toast(`${result.quantity} key(s) generated`, {
      type: "success",
      detail: "Plaintext is available only in the window that just opened.",
    });
    state.keysLoaded = false;
    await Promise.all([loadKeys({ silent: true }), loadStats()]);
  } catch (error) {
    errorNode.textContent = error.message || "Generation failed.";
    if (error.details && error.details.fields) {
      const messages = Object.entries(error.details.fields)
        .map(([field, message]) => `${field}: ${message}`)
        .join(" · ");
      if (messages) errorNode.textContent = messages;
    }
    if (error instanceof ApiProblem && error.status === 401) {
      window.location.replace("/");
    }
  } finally {
    setButtonBusy(submit, false);
  }
}

/* ---------------------------------------------------------------- key detail */

function detailMeta(label, value) {
  return el("div", { class: "meta-item" }, [
    el("span", { text: label }),
    el("strong", { text: value }),
  ]);
}

async function openKeyDetail(keyId) {
  state.currentKeyId = keyId;
  const body = $("#detail-body");
  clearNode(body);
  body.append(
    el("div", { class: "empty-state" }, [
      el("span", { class: "spinner spinner-lg" }),
      el("span", { text: "Loading license record…" }),
    ])
  );
  clearNode($("#detail-actions"));
  openDialog($("#detail-modal"));
  try {
    const key = await api.key(keyId);
    renderDetail(key);
  } catch (error) {
    closeDialog($("#detail-modal"));
    fail(error, "License record could not be loaded.");
  }
}

function renderDetail(key) {
  const body = $("#detail-body");
  const actions = $("#detail-actions");
  clearNode(body);
  clearNode(actions);

  body.append(
    el("div", { class: "detail-hero" }, [
      el("div", {}, [
        el("code", { text: key.key_hint }),
        el("small", { text: key.key_format }),
      ]),
      statusBadge(key.status),
    ])
  );

  const meta = el("div", { class: "detail-meta" }, [
    detailMeta("Label", key.label || "Untitled batch"),
    detailMeta(
      "Activations",
      `${numberFormat.format(key.activation_count)} / ${numberFormat.format(
        key.max_activations
      )}`
    ),
    detailMeta(
      "Expires",
      key.expires_at ? formatDate(key.expires_at) : "Never"
    ),
    detailMeta("Created", formatDate(key.created_at)),
    detailMeta("Created by", key.created_by_email || "System"),
    detailMeta("Batch", key.batch_id ? shortId(key.batch_id) : "—"),
  ]);
  body.append(meta);

  if (key.revoked_at) {
    body.append(
      el("div", { class: "alert alert-error" }, [
        `Revoked ${formatDate(key.revoked_at)} · ${key.revoked_reason || "No reason recorded"}`,
      ])
    );
  }

  const admin = isAdmin();
  const statusSelect = el("select", { "aria-label": "Change key status" });
  const allowed =
    {
      active: ["active", "inactive", "pending"],
      inactive: ["inactive", "active", "pending"],
      pending: ["pending", "active", "inactive"],
      expired: ["expired", "active", "inactive"],
      used: ["used", "inactive"],
      revoked: ["revoked"],
    }[key.status] || [key.status];
  allowed.forEach((status) => {
    statusSelect.append(
      el("option", {
        value: status,
        text: status[0].toUpperCase() + status.slice(1),
        selected: status === key.status,
      })
    );
  });

  const applyStatus = el("button", {
    class: "btn btn-ghost btn-sm",
    type: "button",
    dataset: { action: "status" },
    text: "Apply status",
    disabled: !admin || key.status === "revoked",
  });
  const deactivate = el("button", {
    class: "btn btn-ghost btn-sm",
    type: "button",
    dataset: { action: "deactivate" },
    text: "Deactivate all",
    disabled: !admin || !["active", "used", "inactive", "pending"].includes(key.status),
  });
  const revoke = el("button", {
    class: "btn btn-danger btn-sm",
    type: "button",
    dataset: { action: "revoke" },
    text: "Revoke key",
    disabled: !admin || key.status === "revoked",
  });
  const remove = el("button", {
    class: "btn btn-ghost btn-sm",
    type: "button",
    dataset: { action: "delete" },
    text: "Delete permanently",
    disabled: !isOwner(),
    hidden: !isOwner(),
  });

  if (admin) {
    body.append(
      el("div", { class: "detail-section" }, [
        el("h3", { text: "Administrative controls" }),
        el("div", { class: "action-row" }, [
          statusSelect,
          applyStatus,
          deactivate,
          revoke,
          remove,
        ]),
      ])
    );
  }

  const activations = key.activations || [];
  body.append(
    el("div", { class: "detail-section" }, [
      el("h3", { text: `Activations (${activations.length})` }),
      activations.length
        ? el(
            "div",
            { class: "event-list" },
            activations.map((activation) =>
              el("div", { class: "event-row" }, [
                el("span", { class: "event-dot", "aria-hidden": "true" }),
                el("span", {
                  text: `${activation.device_hint || "device"} · ${
                    activation.account_hint || "no account"
                  }`,
                }),
                statusBadge(activation.status),
                el("time", { text: formatDate(activation.activated_at) }),
              ])
            )
          )
        : el("p", { class: "muted", text: "No activations yet." }),
    ])
  );

  const events = key.events || [];
  body.append(
    el("div", { class: "detail-section" }, [
      el("h3", { text: "Audit trail" }),
      events.length
        ? el(
            "div",
            { class: "event-list" },
            events.slice(0, 12).map((event) =>
              el("div", { class: "event-row" }, [
                el("span", {
                  class:
                    event.outcome === "failure"
                      ? "event-dot is-failure"
                      : "event-dot is-success",
                  "aria-hidden": "true",
                }),
                el("span", {
                  text: `${event.action} · ${event.actor_email || "system"}`,
                }),
                el("time", { text: formatDate(event.created_at) }),
              ])
            )
          )
        : el("p", { class: "muted", text: "No audit entries for this key." }),
    ])
  );

  if (key.batch) {
    body.append(
      el("div", { class: "detail-section" }, [
        el("h3", { text: "Generation batch" }),
        el("p", {
          class: "muted",
          text: `${key.batch.quantity} key(s) · ${key.batch.key_format} · generated by ${
            key.batch.generated_by || "system"
          } on ${formatDate(key.batch.created_at)}`,
        }),
      ])
    );
  }

  statusSelect.dataset.current = key.status;
  actions.append(
    el("button", {
      class: "btn btn-ghost",
      type: "button",
      text: "Close",
      onclick: () => closeDialog($("#detail-modal")),
    })
  );
}

async function handleDetailAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button || !state.currentKeyId) return;
  const action = button.dataset.action;
  const keyId = state.currentKeyId;

  if (action === "status") {
    const select = $("#detail-body select");
    if (!select) return;
    const status = select.value;
    if (status === select.dataset.current) {
      toast("Choose a different status first.", { type: "info" });
      return;
    }
    setButtonBusy(button, true, "Saving…");
    try {
      await api.updateKey(keyId, { status });
      toast("Key status updated", { type: "success" });
      await Promise.all([refreshDetail(), loadKeys({ silent: true }), loadStats()]);
    } catch (error) {
      fail(error, "Status could not be updated.");
    } finally {
      setButtonBusy(button, false);
    }
    return;
  }

  if (action === "deactivate") {
    const result = await confirmAction({
      title: "Deactivate activations",
      message:
        "All active device activations for this key will be released. The key keeps its history and can be reactivated if slots remain.",
      confirmLabel: "Deactivate all",
      requireReason: true,
      reasonLabel: "Deactivation reason",
      reasonPlaceholder: "e.g. Customer requested device reset",
    });
    if (!result.confirmed) return;
    try {
      await api.deactivate({ key_id: keyId, reason: result.reason });
      toast("Activations deactivated", { type: "success" });
      await Promise.all([refreshDetail(), loadKeys({ silent: true }), loadStats()]);
    } catch (error) {
      fail(error, "Deactivation failed.");
    }
    return;
  }

  if (action === "revoke") {
    const result = await confirmAction({
      title: "Revoke license key",
      message:
        "Revocation is permanent for this key. All active activations are terminated and the key can never be reactivated.",
      confirmLabel: "Revoke permanently",
      requireReason: true,
      reasonLabel: "Revocation reason",
      reasonPlaceholder: "Why is this key being revoked?",
    });
    if (!result.confirmed) return;
    try {
      await api.revoke(keyId, result.reason);
      toast("Key revoked", { type: "success" });
      await Promise.all([refreshDetail(), loadKeys({ silent: true }), loadStats()]);
    } catch (error) {
      fail(error, "Revocation failed.");
    }
    return;
  }

  if (action === "delete") {
    if (!isOwner()) {
      toast("Only the owner role can delete keys.", { type: "error" });
      return;
    }
    const result = await confirmAction({
      title: "Delete license key",
      message:
        "This removes the key and its activation history permanently. The audit record of the deletion is retained.",
      confirmLabel: "Delete permanently",
    });
    if (!result.confirmed) return;
    try {
      await api.deleteKey(keyId);
      closeDialog($("#detail-modal"));
      toast("Key deleted", { type: "success" });
      await Promise.all([loadKeys({ silent: true }), loadStats()]);
    } catch (error) {
      fail(error, "Key could not be deleted.");
    }
  }
}

async function refreshDetail() {
  if (!state.currentKeyId) return;
  try {
    const key = await api.key(state.currentKeyId);
    renderDetail(key);
  } catch (error) {
    fail(error, "License record could not be refreshed.");
  }
}

/* ------------------------------------------------------------------- actions */

async function logout() {
  const result = await confirmAction({
    title: "Sign out",
    message: "End this administrative session and return to the login screen?",
    confirmLabel: "Sign out",
    tone: "primary",
  });
  if (!result.confirmed) return;
  try {
    await api.logout();
  } catch (error) {
    fail(error, "Sign-out request failed.");
  } finally {
    setCsrfToken("");
    window.location.replace("/");
  }
}

async function refreshAll() {
  const button = $("#refresh-btn");
  setButtonBusy(button, true, "Refreshing…");
  try {
    if (state.view === "logs") await loadLogs();
    else if (state.view === "keys") {
      state.keysLoaded = false;
      await Promise.all([loadKeys({ silent: true }), loadStats()]);
    } else await loadStats();
    toast("Console refreshed", { type: "success" });
  } finally {
    setButtonBusy(button, false);
  }
}

/* ---------------------------------------------------------------- event bind */

function bindEvents() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $$("[data-go]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.go));
  });
  window.addEventListener("hashchange", () => {
    const view = window.location.hash.replace("#", "");
    if (view && view !== state.view) showView(view, { pushHash: false });
  });

  $("#logout-btn")?.addEventListener("click", logout);
  $("#refresh-btn")?.addEventListener("click", refreshAll);
  $("#open-generate")?.addEventListener("click", openGenerate);

  $$("[data-close]").forEach((button) => {
    button.addEventListener("click", () => {
      closeDialog(document.getElementById(button.dataset.close));
    });
  });

  $$("dialog.modal").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
  });

  ["#gen-prefix", "#gen-suffix", "#gen-groups", "#gen-group-length", "#gen-separator"].forEach(
    (selector) => {
      $(selector)?.addEventListener("input", updateFormatPreview);
      $(selector)?.addEventListener("change", updateFormatPreview);
    }
  );

  $("#generate-form")?.addEventListener("submit", submitGenerate);

  $("#key-search")?.addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      state.page = 1;
      loadKeys({ silent: true });
    }, 320);
  });

  $("#status-filter")?.addEventListener("change", (event) => {
    state.status = event.target.value;
    state.page = 1;
    loadKeys({ silent: true });
  });

  $("#page-size")?.addEventListener("change", (event) => {
    state.pageSize = Number(event.target.value) || 15;
    state.page = 1;
    loadKeys({ silent: true });
  });

  $("#prev-page")?.addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      loadKeys({ silent: true });
    }
  });

  $("#next-page")?.addEventListener("click", () => {
    if (state.pagination && state.pagination.has_next) {
      state.page += 1;
      loadKeys({ silent: true });
    }
  });

  $$(".sort-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const sort = button.dataset.sort;
      if (state.sort === sort) {
        state.order = state.order === "desc" ? "asc" : "desc";
      } else {
        state.sort = sort;
        state.order = sort === "label" ? "asc" : "desc";
      }
      state.page = 1;
      loadKeys({ silent: true });
    });
  });

  $("#keys-body")?.addEventListener("click", (event) => {
    const manage = event.target.closest('[data-action="manage"]');
    const row = event.target.closest("tr[data-id]");
    const keyId = manage ? manage.dataset.id : row ? row.dataset.id : null;
    if (keyId) openKeyDetail(keyId);
  });

  $("#export-btn")?.addEventListener("click", () => {
    const url = api.exportUrl({
      status: state.status,
      search: state.search,
    });
    window.location.assign(url);
    toast("CSV export started", {
      type: "info",
      detail: "Masked keys only — plaintext is never exported.",
    });
  });

  $$(".tab[data-log]").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab[data-log]").forEach((item) => {
        item.classList.toggle("is-active", item === tab);
        item.setAttribute("aria-selected", String(item === tab));
      });
      state.logsKind = tab.dataset.log;
      state.logsPage = 1;
      loadLogs();
    });
  });

  $("#logs-prev")?.addEventListener("click", () => {
    if (state.logsPage > 1) {
      state.logsPage -= 1;
      loadLogs();
    }
  });

  $("#logs-next")?.addEventListener("click", () => {
    if (state.logsPagination && state.logsPagination.has_next) {
      state.logsPage += 1;
      loadLogs();
    }
  });

  $("#detail-actions")?.addEventListener("click", handleDetailAction);
  $("#detail-body")?.addEventListener("click", handleDetailAction);

  $("#generated-list")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const copied = await copyText(button.dataset.copy);
    toast(copied ? "Key copied" : "Copy failed — select the key manually", {
      type: copied ? "success" : "error",
    });
  });

  $("#copy-all")?.addEventListener("click", async () => {
    if (!state.generated.length) return;
    const copied = await copyText(state.generated.join("\n"));
    toast(copied ? "All keys copied" : "Copy failed", {
      type: copied ? "success" : "error",
    });
  });

  $("#download-keys")?.addEventListener("click", () => {
    if (!state.generated.length) return;
    const header = [
      "NexOra-Database-key-Gen — Database Key Generator",
      `Generated: ${new Date().toISOString()}`,
      `Format: ${state.generatedFormat}`,
      "Plaintext keys are shown once. Store securely.",
      "",
    ].join("\n");
    downloadText(generatedFilename(), header + state.generated.join("\n") + "\n");
  });

  $("#generate-modal")?.addEventListener("close", () => {
    $("#generate-error").textContent = "";
  });
}

/* ---------------------------------------------------------------------- boot */

async function init() {
  bindEvents();
  setBusyOverlay(true);
  try {
    const session = await api.session();
    state.user = session.user;
    setCsrfToken(session.csrf_token);
    applyIdentity();
    const initialView = window.location.hash.replace("#", "");
    const view = VIEW_META[initialView] ? initialView : "overview";
    showView(view, { pushHash: false, load: false });
    if (view === "keys") await loadKeys();
    if (view === "logs") await loadLogs();
    await loadStats();
  } catch (error) {
    if (error instanceof ApiProblem && [401, 403].includes(error.status)) {
      window.location.replace("/");
      return;
    }
    showGlobalError(
      (error && error.message) || "The console could not initialize."
    );
  } finally {
    setBusyOverlay(false);
  }
}

init();
