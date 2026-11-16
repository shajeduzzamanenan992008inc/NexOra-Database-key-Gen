export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) =>
  Array.from(root.querySelectorAll(selector));

export function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attributes).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") {
      Object.entries(value).forEach(([dataKey, dataValue]) => {
        node.dataset[dataKey] = dataValue;
      });
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "html") {
      // Only used with static, developer-authored markup; never user input.
      node.innerHTML = value;
    } else {
      node.setAttribute(key, value === true ? "" : value);
    }
  });
  const list = Array.isArray(children) ? children : [children];
  list.filter(Boolean).forEach((child) => {
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  });
  return node;
}

export function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function toast(message, { type = "info", detail = "", timeout = 6000 } = {}) {
  let container = $(".toast-stack");
  if (!container) {
    container = el("div", { class: "toast-stack", role: "status", "aria-live": "polite" });
    document.body.append(container);
  }
  const tone = ["success", "error", "info", "warning"].includes(type) ? type : "info";
  const card = el("div", { class: `toast toast-${tone}` }, [
    el("span", { class: "toast-mark", "aria-hidden": "true" }),
    el("div", { class: "toast-copy" }, [
      el("strong", { text: message }),
      detail ? el("span", { text: detail }) : null,
    ]),
    el("button", {
      class: "toast-close",
      type: "button",
      "aria-label": "Dismiss notification",
      text: "×",
      onclick: () => remove(),
    }),
  ]);

  function remove() {
    card.classList.add("toast-out");
    setTimeout(() => card.remove(), 220);
  }

  container.append(card);
  if (timeout > 0) setTimeout(remove, timeout);
  return remove;
}

export function setButtonBusy(button, busy, busyLabel = "Working…") {
  if (!button) return;
  const label = button.querySelector(".btn-label");
  const spinner = button.querySelector(".spinner");
  if (busy) {
    button.dataset.idleLabel = label ? label.textContent : button.textContent;
    button.disabled = true;
    button.classList.add("is-busy");
    if (label) label.textContent = busyLabel;
    else if (!spinner) button.textContent = busyLabel;
    if (spinner) spinner.hidden = false;
  } else {
    button.disabled = false;
    button.classList.remove("is-busy");
    if (button.dataset.idleLabel) {
      if (label) label.textContent = button.dataset.idleLabel;
      else if (!spinner) button.textContent = button.dataset.idleLabel;
    }
    if (spinner) spinner.hidden = true;
  }
}

export function formatDate(value, { time = true } = {}) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const options = { year: "numeric", month: "short", day: "2-digit" };
  if (time) {
    options.hour = "2-digit";
    options.minute = "2-digit";
  }
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

export function formatRelative(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const units = [
    ["year", 31536000],
    ["month", 2592000],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return formatter.format(seconds, "second");
}

const STATUS_META = {
  active: { label: "Active", tone: "success" },
  inactive: { label: "Inactive", tone: "muted" },
  pending: { label: "Pending", tone: "warn" },
  generated: { label: "Unused", tone: "success" },
  used: { label: "Used", tone: "info" },
  expired: { label: "Expired", tone: "warn" },
  revoked: { label: "Revoked", tone: "danger" },
  deleted: { label: "Deleted", tone: "danger" },
  deactivated: { label: "Deactivated", tone: "muted" },
  unknown: { label: "Unknown", tone: "muted" },
  success: { label: "Success", tone: "success" },
  failure: { label: "Failure", tone: "danger" },
};

export function statusBadge(status) {
  const meta = STATUS_META[status] || { label: status || "—", tone: "muted" };
  return el("span", {
    class: `badge badge-${meta.tone}`,
    text: meta.label,
  });
}

export function openDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

export function closeDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.close === "function" && dialog.open) dialog.close();
  else dialog.removeAttribute("open");
}

function buildConfirmDialog({
  title,
  message,
  confirmLabel,
  tone,
  requireReason,
  reasonLabel,
  reasonPlaceholder,
}) {
  const dialog = el("dialog", { class: "modal confirm-modal" });
  const form = el("form", { class: "modal-card", method: "dialog" });
  const head = el("div", { class: "modal-head" }, [
    el("h2", { text: title }),
    el("button", {
      class: "icon-close",
      type: "button",
      "aria-label": "Close",
      text: "×",
      onclick: () => finish(false, ""),
    }),
  ]);
  const body = el("div", { class: "modal-body" }, [
    el("p", { class: "confirm-copy", text: message }),
  ]);
  let textarea = null;
  if (requireReason) {
    textarea = el("textarea", {
      class: "textarea",
      rows: "3",
      maxlength: "500",
      placeholder: reasonPlaceholder || "Explain the reason for the audit trail.",
      "aria-label": reasonLabel || "Reason",
    });
    body.append(
      el("label", { class: "field" }, [
        el("span", { class: "field-label", text: reasonLabel || "Reason" }),
        textarea,
      ])
    );
  }
  const error = el("p", { class: "form-message", role: "alert" });
  const actions = el("div", { class: "modal-actions" }, [
    el("button", {
      class: "btn btn-ghost",
      type: "button",
      text: "Cancel",
      onclick: () => finish(false, ""),
    }),
    el("button", {
      class: `btn ${tone === "danger" ? "btn-danger" : "btn-primary"}`,
      type: "submit",
      text: confirmLabel,
    }),
  ]);
  form.append(head, body, error, actions);
  dialog.append(form);
  document.body.append(dialog);

  let resolver = null;

  function finish(confirmed, reason) {
    closeDialog(dialog);
    dialog.remove();
    if (resolver) {
      resolver({ confirmed, reason });
      resolver = null;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const reason = textarea ? textarea.value.trim() : "";
    if (requireReason && reason.length < 3) {
      error.textContent = "Enter at least 3 characters so the audit entry is useful.";
      textarea.focus();
      return;
    }
    finish(true, reason);
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish(false, "");
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) finish(false, "");
  });

  openDialog(dialog);
  if (textarea) setTimeout(() => textarea.focus(), 40);
  else setTimeout(() => actions.querySelector("button[type=submit]").focus(), 40);

  return new Promise((resolve) => {
    resolver = resolve;
  });
}

export function confirmAction(options) {
  return buildConfirmDialog({
    title: options.title || "Confirm action",
    message: options.message || "This action will be recorded in the audit trail.",
    confirmLabel: options.confirmLabel || "Confirm",
    tone: options.tone || "danger",
    requireReason: Boolean(options.requireReason),
    reasonLabel: options.reasonLabel,
    reasonPlaceholder: options.reasonPlaceholder,
  });
}

export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  const area = el("textarea", { class: "clipboard-fallback" });
  area.value = text;
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  return copied;
}

export function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function skeletonRows(table, columns, rows = 5) {
  const body = table.querySelector("tbody");
  if (!body) return;
  clearNode(body);
  for (let index = 0; index < rows; index += 1) {
    const tr = el("tr", { class: "skeleton-row" });
    for (let column = 0; column < columns; column += 1) {
      tr.append(el("td", {}, [el("span", { class: "skeleton", text: " " })]));
    }
    body.append(tr);
  }
}
