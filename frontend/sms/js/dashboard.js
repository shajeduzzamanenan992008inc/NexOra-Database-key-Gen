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
  toast,
} from "/assets/js/ui.js";

const NUMBER_KEYS = [
  "total",
  "generated_24h",
  "unused",
  "activated",
  "activation_rate",
  "online_readers",
  "devices",
  "revoked",
  "redemptions_24h",
  "authentications_24h",
];

const STATUS_META = {
  generated: { label: "Unused", tone: "muted" },
  activated: { label: "Active", tone: "success" },
  revoked: { label: "Revoked", tone: "danger" },
};

const VIEW_META = {
  overview: {
    title: "Overview",
    subtitle: "Live one-time key posture for the SMS Reader fleet.",
  },
  generate: {
    title: "Generate key",
    subtitle: "Issue one cryptographic activation key and copy it once.",
  },
  bulk: {
    title: "Bulk generate",
    subtitle: "Create a unique one-time key batch in one transaction.",
  },
};

const state = {
  view: "overview",
  status: "",
  online: false,
  page: 1,
  pageSize: 15,
  search: "",
  sort: "created_at",
  order: "desc",
  lastBatch: null,
  stats: null,
  loaded: false,
};

const pageTitle = $("#page-title");
const pageSubtitle = $("#page-subtitle");
const globalError = $("#global-error");
const keysBody = $("#keys-body");
const keysTable = $(".sms-keys-table");
const keySearch = $("#key-search");
const statusFilter = $("#status-filter");
const pageSize = $("#page-size");
const keysTitle = $("#keys-title");
const keysEyebrow = $("#keys-eyebrow");
const keysSubtitle = $("#keys-subtitle");
const keysCount = $("#keys-count");
const keysSummary = $("#keys-summary");
const pageInfo = $("#page-info");
const prevPage = $("#prev-page");
const nextPage = $("#next-page");
const resultModal = $("#result-modal");
const detailModal = $("#detail-modal");
const generatedList = $("#generated-list");
const resultSummary = $("#result-summary");
const detailBody = $("#detail-body");
const detailActions = $("#detail-actions");
const detailTitle = $("#detail-title");
const bulkQuantity = $("#bulk-quantity");

const generateForm = $("#generate-form");
const generateSubmit = $("#generate-submit");
const generateError = $("#generate-error");
const bulkForm = $("#bulk-form");
const bulkSubmit = $("#bulk-submit");
const bulkError = $("#bulk-error");

function statusBadge(status) {
  const meta = STATUS_META[status] || { label: status || "—", tone: "muted" };
  return el("span", { class: `badge badge-${meta.tone}`, text: meta.label });
}

function showGlobalError(message) {
  if (!message) {
    globalError.hidden = true;
    globalError.textContent = "";
    return;
  }
  globalError.textContent = message;
  globalError.hidden = false;
}

function errorText(error) {
  if (error instanceof ApiProblem) {
    const fields = error.details && error.details.fields;
    if (fields && typeof fields === "object") {
      const messages = Object.values(fields).filter(Boolean);
      if (messages.length) return messages.join(" ");
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "The request could not be completed.";
}

function handleError(error) {
  if (error instanceof ApiProblem && error.status === 401) {
    window.location.replace("/login");
    return;
  }
  showGlobalError(errorText(error));
}

function setBusy(button, busy, label) {
  setButtonBusy(button, busy, label);
}

function initialsFor(user) {
  const source = (user.full_name || user.email || "NX").trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

function renderUser(user) {
  $("#user-initials").textContent = initialsFor(user);
  $("#user-name").textContent = user.full_name || user.email;
  $("#user-role").textContent = user.role || "admin";
}

function updateTopbar(view) {
  const meta = VIEW_META[view] || VIEW_META.overview;
  pageTitle.textContent = meta.title;
  pageSubtitle.textContent = meta.subtitle;
}

function keyViewMeta() {
  if (state.online) {
    return {
      eyebrow: "LIVE READERS",
      title: "Active readers",
      subtitle: "Devices that authenticated or heartbeated in the last 15 minutes.",
    };
  }
  if (state.status === "generated") {
    return {
      eyebrow: "UNUSED INVENTORY",
      title: "Unused keys",
      subtitle: "One-time keys that have not been redeemed. These can be deleted.",
    };
  }
  if (state.status === "revoked") {
    return {
      eyebrow: "TRUST TERMINATED",
      title: "Revoked keys",
      subtitle: "Terminal records with invalidated device credentials.",
    };
  }
  if (state.status === "activated") {
    return {
      eyebrow: "BOUND READERS",
      title: "Activated keys",
      subtitle: "Readers with a bound device credential and activation history.",
    };
  }
  return {
    eyebrow: "KEY REGISTRY",
    title: "All SMS keys",
    subtitle: "Search, inspect, revoke readers, or remove unused keys.",
  };
}

function updateKeyHeading() {
  const meta = keyViewMeta();
  keysEyebrow.textContent = meta.eyebrow;
  keysTitle.textContent = meta.title;
  keysSubtitle.textContent = meta.subtitle;
  statusFilter.value = state.status;
}

function setNavActive() {
  $$(".nav-item").forEach((button) => button.classList.remove("is-active"));
  if (state.view !== "keys") {
    const direct = document.querySelector(`[data-nav="${state.view}"]`);
    if (direct) direct.classList.add("is-active");
    return;
  }
  const matches = $$('[data-nav="keys"]').filter((button) => {
    return (
      (button.dataset.status || "") === state.status &&
      (button.dataset.online === "1") === state.online
    );
  });
  if (matches.length) matches[0].classList.add("is-active");
}

function setView(view, { skipLoad = false } = {}) {
  state.view = view;
  $$(".view").forEach((section) => section.classList.remove("is-active"));
  const section = document.getElementById(`view-${view}`);
  if (section) section.classList.add("is-active");
  setNavActive();
  updateTopbar(view);

  if (view === "keys") {
    updateKeyHeading();
    if (!skipLoad) loadKeys();
  } else if (view === "overview" && !skipLoad) {
    loadStats();
    loadActivePreview();
  }
}

function renderStats(data) {
  state.stats = data;
  const values = {
    ...data.totals,
    ...data.activity,
  };
  $$("[data-stat]").forEach((node) => {
    const key = node.dataset.stat;
    if (!NUMBER_KEYS.includes(key)) return;
    const value = values[key];
    node.textContent = value === undefined || value === null ? "0" : String(value);
  });

  const max = data.configuration && data.configuration.max_bulk_keys;
  if (max) {
    bulkQuantity.max = String(max);
    $("#bulk-limit-hint").textContent = `Between 2 and ${max} keys per request.`;
  }
  renderEvents(data.recent_events || []);
}

function renderEvents(events) {
  const list = $("#recent-events");
  clearNode(list);
  if (!events.length) {
    list.append(
      el("li", {}, [
        el("p", { class: "event-feed-empty", text: "No reader activity yet." }),
      ])
    );
    return;
  }
  events.slice(0, 8).forEach((event) => {
    const button = el(
      "button",
      {
        class: "event-feed-item",
        type: "button",
        onclick: () => openDetail(event.key_id),
      },
      [
        el("span", {
          class: `event-feed-mark ${
            event.outcome === "failure" ? "is-failure" : "is-success"
          }`,
          "aria-hidden": "true",
        }),
        el("span", { class: "event-feed-copy" }, [
          el("strong", { text: event.label || event.event_type }),
          el("span", {
            text: [event.reader_name || "Unnamed reader", event.key_hint]
              .filter(Boolean)
              .join(" · "),
          }),
        ]),
        el("time", { text: formatRelative(event.created_at) }),
      ]
    );
    list.append(el("li", {}, [button]));
  });
}

async function loadStats() {
  try {
    const data = await api.stats();
    renderStats(data);
  } catch (error) {
    handleError(error);
  }
}

async function loadActivePreview() {
  const body = $("#reader-preview-body");
  clearNode(body);
  try {
    const data = await api.keys({
      status: "activated",
      online: 1,
      page_size: 5,
      sort: "last_seen_at",
      order: "desc",
    });
    if (!data.items.length) {
      body.append(
        el("tr", { class: "empty-state-row" }, [
          el("td", {
            colspan: "3",
            text: "No reader checked in during the last 15 minutes.",
          }),
        ])
      );
      return;
    }
    data.items.forEach((item) => {
      body.append(
        el("tr", {}, [
          el("td", {}, [
            el("div", { class: "reader-cell" }, [
              el("strong", { text: item.reader_name || "Unnamed reader" }),
              el("span", { text: item.key_hint }),
            ]),
          ]),
          el("td", {}, [
            el("code", {
              class: "device-code",
              text: item.device_id || "—",
            }),
          ]),
          el("td", { text: formatRelative(item.last_seen_at) }),
        ])
      );
    });
  } catch (error) {
    handleError(error);
    body.append(
      el("tr", { class: "empty-state-row" }, [
        el("td", { colspan: "3", text: "Reader preview unavailable." }),
      ])
    );
  }
}

function actionButton(label, tone, handler) {
  return el("button", {
    class: `btn ${tone || "btn-ghost"} btn-sm`,
    type: "button",
    text: label,
    onclick: handler,
  });
}

function renderKeys(data) {
  clearNode(keysBody);
  const { pagination } = data;
  keysCount.textContent = `${pagination.total} record${pagination.total === 1 ? "" : "s"}`;
  const first = pagination.total === 0 ? 0 : (pagination.page - 1) * pagination.page_size + 1;
  const last = Math.min(
    pagination.total,
    pagination.page * pagination.page_size
  );
  keysSummary.textContent = `Showing ${first}–${last} of ${pagination.total}`;
  pageInfo.textContent = `Page ${pagination.page} of ${Math.max(1, pagination.total_pages)}`;
  prevPage.disabled = !pagination.has_previous;
  nextPage.disabled = !pagination.has_next;

  if (!data.items.length) {
    keysBody.append(
      el("tr", { class: "empty-state-row" }, [
        el("td", { colspan: "7" }, [
          el("div", { class: "empty-state" }, [
            el("strong", { text: "No matching reader keys" }),
            el("span", {
              text: "Adjust the search or status filter, or generate a new one-time key.",
            }),
          ]),
        ]),
      ])
    );
    return;
  }

  data.items.forEach((item) => {
    const actions = el("div", { class: "table-actions" }, [
      actionButton("View", "btn-ghost", () => openDetail(item.id)),
    ]);
    if (item.status !== "revoked") {
      actions.append(
        actionButton("Revoke", "btn-danger", () => revokeKey(item))
      );
    }
    if (item.status === "generated") {
      actions.append(
        actionButton("Delete", "btn-ghost", () => deleteKey(item))
      );
    }

    keysBody.append(
      el("tr", {}, [
        el("td", {}, [
          el("div", { class: "reader-cell" }, [
            el("strong", { text: item.reader_name || "Unnamed reader" }),
            el("span", { text: item.created_by_email || "NexOra console" }),
          ]),
        ]),
        el("td", {}, [el("span", { class: "key-hint", text: item.key_hint })]),
        el("td", {}, [statusBadge(item.status)]),
        el("td", {}, [
          el("code", {
            class: "device-code",
            text: item.device_id || "—",
          }),
        ]),
        el("td", {
          text: item.last_seen_at ? formatRelative(item.last_seen_at) : "—",
        }),
        el("td", { text: formatDate(item.created_at) }),
        el("td", { class: "align-right" }, [actions]),
      ])
    );
  });
}

async function loadKeys() {
  clearNode(keysBody);
  keysBody.append(
    el("tr", { class: "empty-state-row" }, [
      el("td", { colspan: "7", text: "Loading reader keys…" }),
    ])
  );
  try {
    const params = {
      page: state.page,
      page_size: state.pageSize,
      status: state.status,
      search: state.search,
      sort: state.sort,
      order: state.order,
      online: state.online ? 1 : "",
    };
    const data = await api.keys(params);
    renderKeys(data);
  } catch (error) {
    handleError(error);
    clearNode(keysBody);
    keysBody.append(
      el("tr", { class: "empty-state-row" }, [
        el("td", { colspan: "7", text: "Reader keys could not be loaded." }),
      ])
    );
  }
}

function metaItem(label, value) {
  return el("div", { class: "meta-item" }, [
    el("span", { text: label }),
    el("strong", { text: value || "—" }),
  ]);
}

function eventDescription(event) {
  const bits = [];
  if (event.device_id) bits.push(`device ${event.device_id}`);
  const details = event.details || {};
  if (details.reason) bits.push(`reason: ${details.reason}`);
  if (details.app_version) bits.push(`app ${details.app_version}`);
  if (details.previous_status) bits.push(`from ${details.previous_status}`);
  if (!bits.length) bits.push(event.outcome === "failure" ? "failed" : "recorded");
  return bits.join(" · ");
}

function renderDetail(key) {
  clearNode(detailBody);
  detailTitle.textContent = key.reader_name || "Reader details";

  detailBody.append(
    el("div", { class: "detail-hero" }, [
      el("div", { class: "detail-hero-copy" }, [
        el("code", { text: key.key_hint }),
        el("small", { text: `Record ${key.id}` }),
      ]),
      statusBadge(key.status),
    ])
  );

  detailBody.append(
    el("div", { class: "detail-meta" }, [
      metaItem("Reader", key.reader_name || "Unnamed reader"),
      metaItem("Device", key.device_id),
      metaItem("Last seen", key.last_seen_at ? formatDate(key.last_seen_at) : "—"),
      metaItem("Created", formatDate(key.created_at)),
      metaItem(
        "Activated",
        key.activated_at ? formatDate(key.activated_at) : "—"
      ),
      metaItem("Revoked", key.revoked_at ? formatDate(key.revoked_at) : "—"),
      metaItem("Batch size", key.batch_count ? String(key.batch_count) : "—"),
      metaItem("Generated by", key.created_by_email),
      metaItem("Revoked by", key.revoked_by_email),
    ])
  );

  if (key.revoked_reason) {
    detailBody.append(
      el("p", { class: "detail-note", text: `Revocation reason: ${key.revoked_reason}` })
    );
  }

  const history = el("section", { class: "detail-section" }, [
    el("h3", { text: "Activation history" }),
  ]);
  const eventList = el("div", { class: "event-list detail-events" });
  if (!key.events || !key.events.length) {
    eventList.append(
      el("p", { class: "event-feed-empty", text: "No activation events recorded." })
    );
  } else {
    key.events.forEach((event) => {
      eventList.append(
        el("div", { class: "event-row" }, [
          el("span", {
            class: `event-dot ${
              event.outcome === "failure" ? "is-failure" : "is-success"
            }`,
            "aria-hidden": "true",
          }),
          el("div", { class: "event-copy" }, [
            el("strong", { text: event.label || event.event_type }),
            el("span", { text: eventDescription(event) }),
          ]),
          el("time", { text: formatDate(event.created_at) }),
        ])
      );
    });
  }
  history.append(eventList);
  detailBody.append(history);

  clearNode(detailActions);
  const close = el("button", {
    class: "btn btn-ghost",
    type: "button",
    text: "Close",
    onclick: () => closeDialog(detailModal),
  });
  const actions = [];
  if (key.status !== "revoked") {
    actions.push(
      el("button", {
        class: "btn btn-danger",
        type: "button",
        text: key.status === "activated" ? "Revoke reader" : "Revoke key",
        onclick: () => revokeKey(key),
      })
    );
  }
  if (key.status === "generated") {
    actions.push(
      el("button", {
        class: "btn btn-ghost",
        type: "button",
        text: "Delete unused key",
        onclick: () => deleteKey(key),
      })
    );
  }
  actions.push(close);
  detailActions.append(...actions);
}

async function openDetail(keyId) {
  try {
    const key = await api.key(keyId);
    renderDetail(key);
    openDialog(detailModal);
  } catch (error) {
    handleError(error);
  }
}

async function revokeKey(key) {
  const answer = await confirmAction({
    title: "Revoke reader key",
    message: `Revoke ${key.reader_name || key.key_hint}? Its device credential will be cleared immediately and cannot be restored.`,
    confirmLabel: "Revoke",
    requireReason: true,
    reasonLabel: "Revocation reason",
    reasonPlaceholder: "Explain why this reader is no longer trusted.",
  });
  if (!answer.confirmed) return;
  try {
    await api.revoke(key.id, answer.reason);
    toast("Reader revoked", {
      type: "success",
      detail: "The device credential was invalidated immediately.",
    });
    closeDialog(detailModal);
    await refreshData();
  } catch (error) {
    toast("Revocation failed", { type: "error", detail: errorText(error) });
  }
}

async function deleteKey(key) {
  const answer = await confirmAction({
    title: "Delete unused key",
    message: `Permanently delete ${key.key_hint}? This is only allowed for an unused, unactivated key.`,
    confirmLabel: "Delete key",
    tone: "danger",
  });
  if (!answer.confirmed) return;
  try {
    await api.deleteKey(key.id);
    toast("Unused key deleted", { type: "success" });
    closeDialog(detailModal);
    await refreshData();
  } catch (error) {
    toast("Delete failed", { type: "error", detail: errorText(error) });
  }
}

function resetResult() {
  clearNode(generatedList);
  resultSummary.textContent = "";
  state.lastBatch = null;
}

function showResults(result) {
  state.lastBatch = result;
  $("#result-title").textContent =
    result.quantity === 1 ? "One-time activation key" : "Generated one-time keys";
  resultSummary.textContent = `${result.quantity} key${
    result.quantity === 1 ? "" : "s"
  } · batch ${result.batch_id}${
    result.reader_name ? ` · ${result.reader_name}` : ""
  }`;
  clearNode(generatedList);

  result.keys.forEach((entry) => {
    const copyButton = el("button", {
      class: "btn btn-ghost btn-sm",
      type: "button",
      text: "Copy",
      onclick: async () => {
        const copied = await copyText(entry.key);
        toast(copied ? "Key copied" : "Copy failed", {
          type: copied ? "success" : "error",
          detail: copied ? "Store it in a safe place before closing." : "Select the key manually.",
        });
      },
    });
    generatedList.append(
      el("div", { class: "key-row" }, [
        el("div", {}, [
          el("code", { class: "key-code", text: entry.key }),
          el("small", { class: "muted", text: entry.key_hint }),
        ]),
        copyButton,
      ])
    );
  });
  openDialog(resultModal);
}

function batchText() {
  if (!state.lastBatch) return "";
  const lines = [
    "NexOra-Database-key-Gen — SMS Reader one-time activation keys",
    `Batch: ${state.lastBatch.batch_id}`,
    `Generated: ${new Date().toISOString()}`,
    "",
    ...state.lastBatch.keys.map((entry) => entry.key),
    "",
    "Store these keys securely. Plaintext keys cannot be retrieved again.",
  ];
  return lines.join("\r\n");
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
}

function showFormError(node, error) {
  node.textContent = errorText(error);
}

async function refreshData() {
  showGlobalError("");
  await Promise.all([
    loadStats(),
    loadActivePreview(),
    state.view === "keys" ? loadKeys() : Promise.resolve(),
  ]);
}

/* ---------------------------------------------------------------- wiring */

$$(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    const view = button.dataset.nav;
    if (view === "keys") {
      state.status = button.dataset.status || "";
      state.online = button.dataset.online === "1";
      state.page = 1;
      state.search = "";
      keySearch.value = "";
      statusFilter.value = state.status;
      setView("keys");
    } else {
      setView(view);
    }
  });
});

$$("[data-go]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.go;
    if (target === "active-readers") {
      state.status = "activated";
      state.online = true;
      state.page = 1;
      state.search = "";
      keySearch.value = "";
      statusFilter.value = "activated";
      setView("keys");
      return;
    }
    state.status = "";
    state.online = false;
    state.page = 1;
    state.search = "";
    keySearch.value = "";
    statusFilter.value = "";
    setView("keys");
  });
});

$("#open-generate").addEventListener("click", () => setView("generate"));
$("#refresh-btn").addEventListener("click", async () => {
  await refreshData();
  toast("Console refreshed", { type: "info" });
});

$("#logout-btn").addEventListener("click", async () => {
  try {
    await api.logout();
  } catch {
    // Clear the local surface even if the server session already expired.
  } finally {
    window.location.replace("/login");
  }
});

let searchTimer = null;
keySearch.addEventListener("input", () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    state.search = keySearch.value.trim();
    state.page = 1;
    loadKeys();
  }, 250);
});

statusFilter.addEventListener("change", () => {
  state.status = statusFilter.value;
  state.online = false;
  state.page = 1;
  setNavActive();
  updateKeyHeading();
  loadKeys();
});

pageSize.addEventListener("change", () => {
  state.pageSize = Number(pageSize.value) || 15;
  state.page = 1;
  loadKeys();
});

$("#clear-search").addEventListener("click", () => {
  keySearch.value = "";
  state.search = "";
  state.status = "";
  state.online = false;
  state.page = 1;
  statusFilter.value = "";
  setNavActive();
  updateKeyHeading();
  loadKeys();
});

$$(".sort-btn").forEach((button) => {
  button.addEventListener("click", () => {
    const sort = button.dataset.sort;
    if (!sort) return;
    if (state.sort === sort) {
      state.order = state.order === "asc" ? "desc" : "asc";
    } else {
      state.sort = sort;
      state.order = "desc";
    }
    state.page = 1;
    loadKeys();
  });
});

prevPage.addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    loadKeys();
  }
});

nextPage.addEventListener("click", () => {
  state.page += 1;
  loadKeys();
});

generateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  generateError.textContent = "";
  setBusy(generateSubmit, true, "Generating…");
  try {
    const result = await api.generate({
      reader_name: $("#gen-reader").value.trim(),
      quantity: 1,
    });
    showResults(result);
    toast("One-time key generated", {
      type: "success",
      detail: "Copy it now; it cannot be retrieved again.",
    });
    await loadStats();
  } catch (error) {
    showFormError(generateError, error);
  } finally {
    setBusy(generateSubmit, false);
  }
});

bulkForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  bulkError.textContent = "";
  const quantity = Number(bulkQuantity.value);
  const maximum = Number(bulkQuantity.max) || 200;
  if (!Number.isInteger(quantity) || quantity < 2 || quantity > maximum) {
    bulkError.textContent = `Enter a quantity between 2 and ${maximum}.`;
    bulkQuantity.focus();
    return;
  }
  setBusy(bulkSubmit, true, "Generating batch…");
  try {
    const result = await api.bulkGenerate({
      reader_name: $("#bulk-reader").value.trim(),
      quantity,
    });
    showResults(result);
    toast(`${result.quantity} one-time keys generated`, {
      type: "success",
      detail: "Copy or download the batch before closing.",
    });
    await loadStats();
  } catch (error) {
    showFormError(bulkError, error);
  } finally {
    setBusy(bulkSubmit, false);
  }
});

$("#copy-all").addEventListener("click", async () => {
  if (!state.lastBatch) return;
  const copied = await copyText(
    state.lastBatch.keys.map((entry) => entry.key).join("\r\n")
  );
  toast(copied ? "All keys copied" : "Copy failed", {
    type: copied ? "success" : "error",
    detail: copied ? "Paste them into your secure handoff channel." : "Use Download .txt instead.",
  });
});

$("#download-keys").addEventListener("click", () => {
  const text = batchText();
  if (!text) return;
  downloadText(`nexora-sms-keys-${timestamp()}.txt`, text);
});

$$("[data-close]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.close);
    closeDialog(dialog);
  });
});

resultModal.addEventListener("close", resetResult);

detailModal.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDialog(detailModal);
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeDialog(resultModal);
    closeDialog(detailModal);
  }
});

async function bootstrap() {
  try {
    const session = await api.session();
    setCsrfToken(session.csrf_token);
    renderUser(session.user);
    await loadStats();
    await loadActivePreview();
    state.loaded = true;
    setView("overview", { skipLoad: true });
  } catch (error) {
    handleError(error);
  }
}

bootstrap();
