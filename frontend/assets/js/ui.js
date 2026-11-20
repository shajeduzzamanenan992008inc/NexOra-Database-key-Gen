export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attributes).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = Boolean(value);
    else if (key === "dataset") {
      Object.entries(value).forEach(([dataKey, dataValue]) => {
        node.dataset[dataKey] = dataValue;
      });
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
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
  if (!node) return;
  while (node.firstChild) node.removeChild(node.firstChild);
}

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function announce(message) {
  const region = document.getElementById("live-region");
  if (!region) return;
  region.textContent = "";
  window.setTimeout(() => {
    region.textContent = message;
  }, 40);
}

export function setToneMessage(node, text, tone = "info") {
  if (!node) return;
  node.textContent = text || "";
  if (text) node.dataset.tone = tone;
  else delete node.dataset.tone;
}

/* --------------------------------------------------------------- tooltips */

let tooltipNode = null;
let tooltipTrigger = null;

function ensureTooltip() {
  if (tooltipNode && tooltipNode.isConnected) return tooltipNode;
  tooltipNode = el("div", {
    class: "tooltip-pop",
    id: "nx-tooltip",
    role: "tooltip",
    hidden: true,
  });
  document.body.append(tooltipNode);
  return tooltipNode;
}

function tooltipTarget(node) {
  return node instanceof Element ? node.closest("[data-tip]") : null;
}

function positionTooltip(trigger, node) {
  const anchor = trigger.getBoundingClientRect();
  const box = node.getBoundingClientRect();
  const margin = 8;
  const gap = 10;

  let left = anchor.left + anchor.width / 2 - box.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));

  let top = anchor.top - box.height - gap;
  if (top < margin) {
    top = Math.min(window.innerHeight - box.height - margin, anchor.bottom + gap);
  }

  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(Math.max(margin, top))}px`;
}

function showTooltip(trigger) {
  if (!trigger || trigger === tooltipTrigger) return;
  if (tooltipTrigger) hideTooltip(tooltipTrigger);

  const text = trigger.getAttribute("data-tip");
  if (!text || !document.body) return;

  const node = ensureTooltip();
  node.textContent = text;
  node.hidden = false;
  node.classList.remove("is-visible");
  void node.offsetWidth;
  node.classList.add("is-visible");
  positionTooltip(trigger, node);

  tooltipTrigger = trigger;
  const ids = new Set(
    (trigger.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean)
  );
  ids.add("nx-tooltip");
  trigger.setAttribute("aria-describedby", Array.from(ids).join(" "));
}

function hideTooltip(trigger) {
  const node = tooltipNode;
  if (!node) return;

  node.classList.remove("is-visible");
  node.hidden = true;

  const owner = trigger || tooltipTrigger;
  if (owner) {
    const ids = (owner.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((id) => id && id !== "nx-tooltip");
    if (ids.length) owner.setAttribute("aria-describedby", ids.join(" "));
    else owner.removeAttribute("aria-describedby");
  }

  tooltipTrigger = null;
}

if (typeof document !== "undefined" && document.body) {
  document.addEventListener(
    "pointerover",
    (event) => {
      const trigger = tooltipTarget(event.target);
      if (trigger) showTooltip(trigger);
    },
    { passive: true }
  );

  document.addEventListener(
    "pointerout",
    (event) => {
      const trigger = tooltipTarget(event.target);
      if (!trigger || trigger !== tooltipTrigger) return;
      const next = event.relatedTarget;
      if (next instanceof Node && trigger.contains(next)) return;
      hideTooltip(trigger);
    },
    { passive: true }
  );

  document.addEventListener("focusin", (event) => {
    const trigger = tooltipTarget(event.target);
    if (trigger) showTooltip(trigger);
  });

  document.addEventListener("focusout", (event) => {
    const trigger = tooltipTarget(event.target);
    if (trigger) hideTooltip(trigger);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTooltip();
  });

  window.addEventListener("scroll", () => hideTooltip(), true);
  window.addEventListener("resize", () => hideTooltip());
}

export function toast(message, { type = "info", detail = "", timeout = 6000 } = {}) {
  let container = $(".toast-stack");
  if (!container) {
    container = el("div", {
      class: "toast-stack",
      role: "region",
      "aria-label": "Notifications",
      "aria-live": "polite",
    });
    document.body.append(container);
  }

  const tone = ["success", "error", "info", "warning"].includes(type) ? type : "info";
  const card = el("div", {
    class: `toast toast-${tone}`,
    role: tone === "error" ? "alert" : "status",
  }, [
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

  if (timeout > 0) card.style.setProperty("--toast-duration", `${timeout}ms`);

  let removed = false;
  let timer = 0;

  function remove() {
    if (removed) return;
    removed = true;
    window.clearTimeout(timer);
    card.classList.add("toast-out");
    window.setTimeout(() => card.remove(), 230);
  }

  container.append(card);
  if (timeout > 0) timer = window.setTimeout(remove, timeout);
  announce(`${tone === "error" ? "Error" : "Notification"}: ${message}`);
  return remove;
}

export function setButtonBusy(button, busy, busyLabel = "Working…") {
  if (!button) return;
  const label = button.querySelector(".btn-label");
  const spinner = button.querySelector(".spinner");

  if (busy) {
    if (!button.dataset.idleLabel) {
      button.dataset.idleLabel = label ? label.textContent : button.textContent.trim();
    }
    button.disabled = true;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    if (label) label.textContent = busyLabel;
    else if (!spinner) button.textContent = busyLabel;
    if (spinner) spinner.hidden = false;
  } else {
    button.disabled = false;
    button.classList.remove("is-busy");
    button.removeAttribute("aria-busy");
    if (button.dataset.idleLabel) {
      if (label) label.textContent = button.dataset.idleLabel;
      else if (!spinner) button.textContent = button.dataset.idleLabel;
      delete button.dataset.idleLabel;
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
  connected: { label: "Connected", tone: "success" },
  unavailable: { label: "Unavailable", tone: "danger" },
};

export function statusBadge(status) {
  const key = String(status || "unknown").toLowerCase();
  const meta = STATUS_META[key] || { label: status || "—", tone: "muted" };
  const badge = el("span", {
    class: `badge badge-${meta.tone}`,
    text: meta.label,
  });
  badge.dataset.status = key;
  return badge;
}

const closeTimers = new WeakMap();

export function openDialog(dialog) {
  if (!dialog) return;

  const pending = closeTimers.get(dialog);
  if (pending) {
    window.clearTimeout(pending);
    closeTimers.delete(dialog);
  }

  dialog.classList.remove("is-closing");

  if (typeof dialog.showModal === "function") {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute("open", "");
  }

  if (!reducedMotion()) {
    dialog.classList.remove("is-opening");
    void dialog.offsetWidth;
    dialog.classList.add("is-opening");
    window.setTimeout(() => dialog.classList.remove("is-opening"), 360);
  }
}

export function closeDialog(dialog, { immediate = false } = {}) {
  if (!dialog) return;
  const isOpen = dialog.open || dialog.hasAttribute("open");
  if (!isOpen) return;

  const finish = () => {
    closeTimers.delete(dialog);
    dialog.classList.remove("is-closing", "is-opening");
    if (typeof dialog.close === "function" && dialog.open) dialog.close();
    else dialog.removeAttribute("open");
  };

  if (immediate || reducedMotion()) {
    finish();
    return;
  }

  if (dialog.classList.contains("is-closing")) return;
  dialog.classList.add("is-closing");
  closeTimers.set(dialog, window.setTimeout(finish, 175));
}

function buildConfirmDialog(options) {
  const {
    title,
    message,
    confirmLabel,
    tone,
    requireReason,
    reasonLabel,
    reasonPlaceholder,
    requireText,
    textLabel,
    requirePassword,
    passwordLabel,
  } = options;

  const dialog = el("dialog", {
    class: "modal confirm-modal",
    "aria-labelledby": "confirm-modal-title",
  });
  const form = el("form", { class: "modal-card modal-card-sm" });
  const head = el("div", { class: "modal-head" }, [
    el("div", {}, [
      el("p", { class: "eyebrow", text: "CONFIRM ACTION" }),
      el("h2", { id: "confirm-modal-title", text: title }),
    ]),
    el("button", {
      class: "icon-close",
      type: "button",
      "aria-label": "Close",
      text: "×",
      onclick: () => finish(false),
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

  let textInput = null;
  if (requireText) {
    textInput = el("input", {
      class: "confirm-text-input",
      type: "text",
      maxlength: "200",
      autocomplete: "off",
      "aria-label": textLabel || `Type ${requireText} to confirm`,
    });
    body.append(
      el("label", { class: "field" }, [
        el("span", {
          class: "field-label",
          text: textLabel || `Type ${requireText} to confirm`,
        }),
        textInput,
      ])
    );
  }

  let passwordInput = null;
  if (requirePassword) {
    passwordInput = el("input", {
      class: "confirm-password-input",
      type: "password",
      maxlength: "128",
      autocomplete: "current-password",
      "aria-label": passwordLabel || "Administrator password",
    });
    body.append(
      el("label", { class: "field" }, [
        el("span", {
          class: "field-label",
          text: passwordLabel || "Administrator password",
        }),
        passwordInput,
      ])
    );
  }

  const error = el("p", { class: "form-message", role: "alert" });
  const actions = el("div", { class: "modal-actions" }, [
    el("button", {
      class: "btn btn-ghost",
      type: "button",
      text: "Cancel",
      onclick: () => finish(false),
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
  let settled = false;

  function finish(confirmed) {
    if (settled) return;
    settled = true;

    const reason = textarea ? textarea.value.trim() : "";
    const text = textInput ? textInput.value.trim() : "";
    const password = passwordInput ? passwordInput.value : "";

    closeDialog(dialog);
    window.setTimeout(() => dialog.remove(), reducedMotion() ? 0 : 190);

    if (resolver) {
      resolver({ confirmed, reason, text, password });
      resolver = null;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const reason = textarea ? textarea.value.trim() : "";
    const text = textInput ? textInput.value.trim() : "";
    const password = passwordInput ? passwordInput.value : "";

    if (requireReason && reason.length < 3) {
      error.textContent = "Enter at least 3 characters so the audit entry is useful.";
      textarea.focus();
      return;
    }
    if (requireText && text !== requireText) {
      error.textContent = `Type ${requireText} exactly to confirm.`;
      textInput.focus();
      return;
    }
    if (requirePassword && !password) {
      error.textContent = "Enter your administrator password to continue.";
      passwordInput.focus();
      return;
    }
    finish(true);
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish(false);
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) finish(false);
  });

  openDialog(dialog);
  window.setTimeout(() => {
    if (textarea) textarea.focus();
    else if (textInput) textInput.focus();
    else if (passwordInput) passwordInput.focus();
    else actions.querySelector("button[type=submit]").focus();
  }, 50);

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
    requireText: options.requireText || "",
    textLabel: options.textLabel,
    requirePassword: Boolean(options.requirePassword),
    passwordLabel: options.passwordLabel,
  });
}

export async function copyText(text) {
  const value = String(text ?? "");
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Fall through to the selection-based fallback.
    }
  }

  const area = el("textarea", { class: "clipboard-fallback", "aria-hidden": "true" });
  area.value = value;
  document.body.append(area);
  area.select();
  area.setSelectionRange(0, value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();

  if (!copied) throw new Error("Clipboard access was denied by this browser.");
  return true;
}

export function downloadText(filename, text, mimeType = "text/plain;charset=utf-8") {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function skeletonRows(target, columns, rows = 5, labels = []) {
  const body = target && target.tagName === "TBODY" ? target : target && target.querySelector("tbody");
  if (!body) return;
  clearNode(body);

  for (let index = 0; index < rows; index += 1) {
    const tr = el("tr", { class: "skeleton-row" });
    for (let column = 0; column < columns; column += 1) {
      const td = el("td", {}, [el("span", { class: "skeleton", text: " " })]);
      if (labels[column]) td.dataset.label = labels[column];
      tr.append(td);
    }
    body.append(tr);
  }
}
