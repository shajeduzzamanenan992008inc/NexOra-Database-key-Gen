import { api, ApiProblem } from "./api.js";
import {
  $,
  $$,
  clearNode,
  confirmAction,
  copyText,
  el,
  formatDate,
  formatRelative,
  setButtonBusy,
  statusBadge,
  toast,
} from "./ui.js";
import { omegaAsset } from "./omega-assets.js";

let activeTab = "devices";
let oneTimeSecret = null;
let replacementReveal = null;
let importedHistory = null;

/* ---------------------------------------------------------------- helpers */

function handleError(error, fallback = "The request could not be completed.") {
  if (error instanceof ApiProblem && error.status === 401) {
    window.location.replace("/login");
    return;
  }
  const detail =
    error instanceof ApiProblem && error.details && typeof error.details === "object"
      ? Object.entries(error.details.fields || error.details)
          .map(([field, message]) => `${field}: ${message}`)
          .join(" · ")
      : "";
  toast(error && error.message ? error.message : fallback, {
    type: "error",
    detail,
  });
}

function button(label, onclick, className = "btn btn-ghost btn-sm", extra = {}) {
  return el("button", {
    class: className,
    type: "button",
    text: label,
    onclick,
    ...extra,
  });
}

function link(label, href, className = "btn btn-ghost btn-sm", extra = {}) {
  return el("a", { class: className, href, text: label, ...extra });
}

function field(labelText, control, hint = "") {
  return el("label", { class: "field" }, [
    el("span", { class: "field-label", text: labelText }),
    control,
    hint ? el("small", { class: "muted", text: hint }) : null,
  ]);
}

function input(attributes = {}) {
  return el("input", { type: "text", ...attributes });
}

function select(options, attributes = {}) {
  const { value, ...rest } = attributes;
  const control = el("select", rest);
  options.forEach(([optionValue, labelText]) => {
    control.append(el("option", { value: optionValue, text: labelText }));
  });
  if (value !== undefined && value !== null) control.value = value;
  return control;
}

function panel(title, subtitle, actions, body = []) {
  const section = el("section", { class: "panel glass ops-panel" }, [
    el("header", { class: "panel-head" }, [
      el("div", {}, [
        el("h3", { text: title }),
        subtitle ? el("p", { class: "muted", text: subtitle }) : null,
      ]),
      actions && actions.filter(Boolean).length
        ? el("div", { class: "row-actions" }, actions.filter(Boolean))
        : null,
    ]),
  ]);
  (Array.isArray(body) ? body : [body]).filter(Boolean).forEach((child) => section.append(child));
  return section;
}

function cell(label, content, className = "") {
  let children;
  if (content instanceof Node) children = [content];
  else if (Array.isArray(content)) children = content;
  else children = [content === undefined || content === null || content === "" ? "—" : String(content)];
  return el("td", { "data-label": label, class: className || undefined }, children);
}

function dataTable(headers, rows, emptyTitle = "No records") {
  const table = el("table", { class: "data-table responsive-table" }, [
    el("thead", {}, [
      el("tr", {}, headers.map((header) => el("th", { text: header }))),
    ]),
    el("tbody", {}, []),
  ]);
  const tbody = table.querySelector("tbody");
  if (!rows.length) {
    tbody.append(
      el("tr", {}, [
        el("td", { colspan: String(headers.length), class: "empty-cell" }, [
          el("div", { class: "empty-state" }, [
            el("span", { class: "empty-icon" }, [
              el("img", {
                class: "empty-glyph",
                src: omegaAsset("database-key", { animated: false }),
                alt: "",
                width: "96",
                height: "96",
                loading: "lazy",
                decoding: "async",
              }),
            ]),
            el("strong", { text: emptyTitle }),
            el("span", { class: "muted", text: "There is nothing to display yet." }),
          ]),
        ]),
      ])
    );
  } else {
    rows.forEach((row) => tbody.append(row));
  }
  return el("div", { class: "table-wrap" }, [table]);
}

function pill(text, tone = "") {
  return el("span", {
    class: `badge ${tone ? `badge-${tone}` : "badge-muted"}`,
    text: String(text ?? "—"),
  });
}

function statusCodeTone(code) {
  const value = Number(code);
  if (value >= 200 && value < 300) return "success";
  if (value >= 400 && value < 500) return "warn";
  return "danger";
}

function loadingBlock(label = "Loading authenticated data…") {
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

function errorPanel(title, error) {
  return panel(title, "The authenticated request failed. No placeholder data is shown.", null, [
    el("p", { class: "form-message", "data-tone": "error", text: error?.message || "Request failed." }),
  ]);
}

function opsEmptyKind(title) {
  const text = String(title || "").toLowerCase();
  if (text.includes("device")) return "reader-key";
  if (text.includes("webhook") || text.includes("automation")) return "key-generating";
  if (text.includes("role") || text.includes("admin") || text.includes("access")) {
    return "key-verified";
  }
  if (text.includes("doc") || text.includes("inbox") || text.includes("notification")) {
    return "readme-icon";
  }
  if (text.includes("key")) return "license-key";
  return "database-key";
}

function emptyState(title, subtitle, icon = "") {
  return el("div", { class: "empty-state ops-empty" }, [
    el("span", { class: "empty-icon", "aria-hidden": "true" }, [
      el("img", {
        class: "empty-glyph",
        src: omegaAsset(icon || opsEmptyKind(title), { animated: false }),
        alt: "",
        width: "96",
        height: "96",
        loading: "lazy",
        decoding: "async",
      }),
    ]),
    el("strong", { text: title }),
    el("span", { class: "muted", text: subtitle }),
  ]);
}

function recordGrid(items) {
  return el(
    "dl",
    { class: "record-grid" },
    items.map(([labelText, value]) =>
      el("div", { class: "record-item" }, [
        el("dt", { text: labelText }),
        el("dd", {}, value instanceof Node ? [value] : [String(value ?? "—")]),
      ])
    )
  );
}

function statGrid(items) {
  return el(
    "div",
    { class: "ops-stat-grid" },
    items.map(([labelText, value, tone]) =>
      el("div", { class: `ops-stat ${tone || ""}`.trim() }, [
        el("span", { class: "muted", text: labelText }),
        el("strong", { text: String(value ?? 0) }),
      ])
    )
  );
}

function secretCard() {
  if (!oneTimeSecret) return null;
  return panel(
    oneTimeSecret.title,
    oneTimeSecret.notice || "Copy this value now. It is not stored in plaintext.",
    [
      button("Dismiss", () => {
        oneTimeSecret = null;
        renderOperations();
      }),
    ],
    [
      el("div", { class: "ops-secret" }, [
        el("code", { class: "mono", text: oneTimeSecret.value }),
        button(
          "Copy",
          async () => {
            try {
              await copyText(oneTimeSecret.value);
              toast("Secret copied.", { type: "success" });
            } catch (error) {
              handleError(error, "Clipboard access was denied.");
            }
          },
          "btn btn-primary btn-sm"
        ),
      ]),
    ]
  );
}

function replacementCard() {
  if (!replacementReveal) return null;
  const keys = replacementReveal.keys || [];
  return panel(
    "One-time replacement keys",
    replacementReveal.notice || "Copy or download these credentials now. They are never stored or shown again.",
    [
      button(
        "Copy all",
        async () => {
          try {
            await copyText(keys.map((key) => key.key || key.plaintext_key || "").join("\n"));
            toast("Replacement keys copied.", { type: "success" });
          } catch (error) {
            handleError(error, "Clipboard access was denied.");
          }
        },
        "btn btn-primary btn-sm"
      ),
      button("Dismiss", () => {
        replacementReveal = null;
        renderOperations();
      }),
    ],
    [
      el(
        "div",
        { class: "ops-secret-list" },
        keys.map((key) =>
          el("div", { class: "ops-secret" }, [
            el("code", { class: "mono", text: key.key || key.plaintext_key || key.key_hint || "—" }),
            el("span", { class: "muted", text: key.key_hint || key.imported_record_id || "" }),
          ])
        )
      ),
    ]
  );
}

function syncTabs() {
  $$("#operations-tabs [data-ops-tab]").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.opsTab === activeTab);
    tab.setAttribute("aria-selected", String(tab.dataset.opsTab === activeTab));
    tab.onclick = () => {
      if (activeTab === tab.dataset.opsTab) return;
      activeTab = tab.dataset.opsTab;
      renderOperations();
    };
  });
}

/* ---------------------------------------------------------------- devices */

async function renderDevices(root) {
  const data = await api.universalDevices({ page_size: 100 });
  const devices = data.items || [];
  const rows = devices.map((device) =>
    el("tr", {}, [
      cell(
        "Device",
        el("div", {}, [
          el("strong", { text: device.device_name || device.device_id }),
          el("div", { class: "mono", text: device.device_id }),
        ])
      ),
      cell("Project", device.project_name || "—"),
      cell("Key", el("span", { class: "mono", text: device.key_hint || "—" })),
      cell("Platform", device.platform || device.device_model || "—"),
      cell("Status", statusBadge(device.status)),
      cell("Last seen", formatRelative(device.last_seen_at)),
      cell("Created", formatDate(device.created_at, { time: false })),
      cell(
        "Actions",
        el("div", { class: "row-actions" }, [
          device.status !== "revoked"
            ? button("Revoke", () => revokeDevice(device), "btn btn-danger btn-sm")
            : null,
          button("Reset", () => resetDevice(device)),
        ]),
        "align-right"
      ),
    ])
  );

  root.append(
    panel(
      "Device registry",
      "Bindings exist only after a key with device policy authenticates. A device identifier alone is never authentication.",
      null,
      [
        dataTable(
          ["Device", "Project", "Key", "Platform", "Status", "Last seen", "Created", "Actions"],
          rows,
          "No device bindings"
        ),
      ]
    )
  );
}

async function revokeDevice(device) {
  const confirmed = await confirmAction({
    title: "Revoke device?",
    message: "This binding will be permanently blocked. Use Reset to allow the device to register again.",
    confirmLabel: "Revoke device",
    tone: "danger",
  });
  if (!confirmed.confirmed) return;
  try {
    await api.revokeUniversalDevice(device.id, "Revoked from Classic Operations");
    toast("Device revoked.", { type: "success" });
    await renderOperations();
  } catch (error) {
    handleError(error, "Device revocation failed.");
  }
}

async function resetDevice(device) {
  const confirmed = await confirmAction({
    title: "Reset device binding?",
    message: "The binding row is deleted so the device can register again on a future authentication.",
    confirmLabel: "Reset binding",
    tone: "warn",
  });
  if (!confirmed.confirmed) return;
  try {
    await api.resetUniversalDevice(device.id);
    toast("Device binding reset.", { type: "success" });
    await renderOperations();
  } catch (error) {
    handleError(error, "Device reset failed.");
  }
}

/* -------------------------------------------------------------- api usage */

async function renderApiUsage(root) {
  const data = await api.apiUsage({ page: 1, page_size: 50 });
  const summary = data.summary_24h || {};
  const rows = (data.items || []).map((item) =>
    el("tr", {}, [
      cell("Time", formatDate(item.created_at)),
      cell("Project", item.project_name || "Unattributed"),
      cell("Key", el("span", { class: "mono", text: item.key_hint || "—" })),
      cell("Type", item.key_type_id || "—"),
      cell("Endpoint", el("span", { class: "mono", text: item.endpoint })),
      cell("Method", item.method),
      cell("Status", pill(item.status_code, statusCodeTone(item.status_code))),
      cell("Latency", `${item.latency_ms} ms`),
    ])
  );

  root.append(
    panel("API usage", "Every access attempt is recorded with a hashed IP, endpoint, status, and latency. Key material is never recorded.", null, [
      statGrid([
        ["Requests / 24h", summary.requests || 0],
        ["Failed / 24h", summary.failed || 0, summary.failed ? "is-bad" : ""],
        ["Average latency", `${summary.avg_latency_ms || 0} ms`],
        ["Tracked calls", data.pagination?.total || 0],
      ]),
      dataTable(
        ["Time", "Project", "Key", "Type", "Endpoint", "Method", "Status", "Latency"],
        rows,
        "No API calls recorded"
      ),
    ])
  );
}

/* --------------------------------------------------------------- webhooks */

function webhookForm(events, projects) {
  const project = select(
    [["", "Choose a project"]].concat(projects.map((item) => [item.id, item.project_name])),
    { required: "" }
  );
  const name = input({ placeholder: "Order events", maxlength: "100", required: "" });
  const url = input({
    type: "url",
    placeholder: "https://example.com/hooks/nexora",
    maxlength: "2048",
    required: "",
  });
  const selectedEvents = new Set(
    events.includes("key.generated") ? ["key.generated"] : events.slice(0, 1)
  );
  const checks = el(
    "div",
    { class: "check-grid" },
    events.slice(0, 20).map((event) => {
      const box = el("input", {
        type: "checkbox",
        checked: selectedEvents.has(event),
      });
      box.addEventListener("change", () => {
        if (box.checked) selectedEvents.add(event);
        else selectedEvents.delete(event);
      });
      return el("label", { class: "check-option" }, [
        box,
        el("span", { text: event }),
      ]);
    })
  );
  const submit = button(
    "Create webhook",
    async () => {
      if (!project.value) {
        toast("Choose a project for the webhook.", { type: "error" });
        return;
      }
      if (!name.value.trim() || !url.value.trim()) {
        toast("Enter a webhook name and HTTPS endpoint.", { type: "error" });
        return;
      }
      if (!selectedEvents.size) {
        toast("Select at least one event.", { type: "error" });
        return;
      }
      setButtonBusy(submit, true, "Creating…");
      try {
        const result = await api.createWebhook({
          project_id: project.value,
          name: name.value.trim(),
          url: url.value.trim(),
          events: Array.from(selectedEvents),
        });
        oneTimeSecret = {
          title: "Webhook signing secret",
          value: result.secret,
          notice: result.secret_notice || "Copy this signing secret now. It will not be shown again.",
        };
        toast("Webhook created.", { type: "success" });
        await renderOperations();
      } catch (error) {
        handleError(error, "Webhook creation failed.");
      } finally {
        setButtonBusy(submit, false);
      }
    },
    "btn btn-primary btn-sm"
  );
  const form = el("form", { class: "form-grid" }, [
    field("Project", project),
    field("Webhook name", name),
    field("HTTPS endpoint", url, "Private-network targets are rejected in production."),
    el("div", { class: "span-2" }, [
      el("span", { class: "field-label", text: "Events" }),
      checks,
    ]),
    el("div", { class: "span-2 clean-actions-bar margin-top-md" }, [submit]),
  ]);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit.click();
  });
  return form;
}

async function testWebhook(hook) {
  try {
    const result = await api.testWebhook(hook.id);
    toast(result.message || "Test delivery queued.", { type: "success" });
  } catch (error) {
    handleError(error, "Webhook test failed.");
  }
}

async function deleteWebhook(hook) {
  const confirmed = await confirmAction({
    title: "Delete webhook?",
    message: "The webhook and its delivery history are deleted.",
    confirmLabel: "Delete webhook",
    tone: "danger",
  });
  if (!confirmed.confirmed) return;
  try {
    await api.deleteWebhook(hook.id);
    toast("Webhook deleted.", { type: "success" });
    await renderOperations();
  } catch (error) {
    handleError(error, "Webhook deletion failed.");
  }
}

async function rotateWebhook(hook) {
  const confirmed = await confirmAction({
    title: "Rotate signing secret?",
    message: "The previous secret stops signing immediately. Update the receiver after this rotation.",
    confirmLabel: "Rotate secret",
    tone: "warn",
  });
  if (!confirmed.confirmed) return;
  try {
    const result = await api.rotateWebhook(hook.id);
    oneTimeSecret = {
      title: "Rotated webhook secret",
      value: result.secret,
      notice: `Secret version ${result.secret_version || "new"}. Copy it now.`,
    };
    await renderOperations();
  } catch (error) {
    handleError(error, "Webhook rotation failed.");
  }
}

async function renderWebhooks(root) {
  const [data, projectData] = await Promise.all([
    api.webhooks(),
    api.universalProjects({ page_size: 100 }),
  ]);
  const hooks = data.webhooks || [];
  const projects = projectData.items || [];
  const rows = hooks.map((hook) =>
    el("tr", {}, [
      cell(
        "Webhook",
        el("div", {}, [
          el("strong", { text: hook.name }),
          el("div", { class: "mono", text: hook.id }),
        ])
      ),
      cell("Project", hook.project_name),
      cell("URL", el("span", { class: "mono ops-url", text: hook.url })),
      cell("Events", (hook.events || []).length ? `${hook.events.length} events` : "—"),
      cell("Status", statusBadge(hook.is_active ? "active" : "inactive")),
      cell("Deliveries", `${hook.delivered_count || 0} delivered · ${hook.failed_count || 0} failed`),
      cell(
        "Actions",
        el("div", { class: "row-actions" }, [
          button("Test", () => testWebhook(hook)),
          button("Rotate secret", () => rotateWebhook(hook)),
          button("Delete", () => deleteWebhook(hook), "btn btn-danger btn-sm"),
        ]),
        "align-right"
      ),
    ])
  );

  root.append(
    panel("Webhook delivery", "Events are queued without secrets and signed with a derived per-webhook secret. Retries are handled by automation.", null, [
      dataTable(
        ["Webhook", "Project", "URL", "Events", "Status", "Deliveries", "Actions"],
        rows,
        "No webhooks configured"
      ),
    ]),
    panel("Create webhook", "A newly created signing secret is shown once in the one-time secret panel.", null, [
      webhookForm(data.events || [], projects),
    ])
  );
}

/* ----------------------------------------------------------- notifications */

async function renderInbox(root) {
  const data = await api.notifications({ limit: "100" });
  const items = data.notifications || [];
  const list = el("div", { class: "ops-stack" });
  if (!items.length) {
    list.append(emptyState("No notifications", "Expiry and security notices will appear here.", "◔"));
  } else {
    items.forEach((item) => {
      const actions = [];
      if (!item.read_at) {
        actions.push(
          button(
            "Mark read",
            async () => {
              try {
                await api.markNotification(item.id);
                await renderOperations();
              } catch (error) {
                handleError(error, "Notification update failed.");
              }
            }
          )
        );
      }
      list.append(
        el("article", { class: `ops-notification ${item.read_at ? "is-read" : ""}`.trim() }, [
          el("div", {}, [
            el("strong", { text: item.title }),
            el("p", { class: "muted", text: item.body || "" }),
          ]),
          el("div", { class: "row-actions" }, [
            pill(item.severity || "info", item.severity === "critical" ? "danger" : item.severity === "warning" ? "warn" : "info"),
            ...actions,
          ]),
        ])
      );
    });
  }

  root.append(
    panel(
      "Notification center",
      `${data.unread || 0} unread notification(s). Server-generated expiry warnings, delivery failures, and security signals.`,
      [
        button(
          "Mark all read",
          async () => {
            try {
              await api.markAllNotifications();
              toast("Notifications marked read.", { type: "success" });
              await renderOperations();
            } catch (error) {
              handleError(error, "Notification update failed.");
            }
          },
          "btn btn-ghost btn-sm"
        ),
      ],
      [list]
    )
  );
}

/* ---------------------------------------------------------------- data UI */

function projectExportCard(projects) {
  const project = select(
    [["", "Choose a project"]].concat(projects.map((item) => [item.id, item.project_name]))
  );
  const download = (format) => {
    if (!project.value) {
      toast("Choose a project to export.", { type: "error" });
      return;
    }
    window.location.assign(api.universalProjectExportUrl(project.value, format));
  };
  return panel("Project backup", "Secret-free project bundles include custom types, key metadata, devices, and webhook configuration.", null, [
    el("div", { class: "form-grid" }, [
      field("Project", project),
      el("div", { class: "field clean-actions-bar" }, [
        button("Download JSON", () => download("json"), "btn btn-primary btn-sm"),
        button("Download ZIP", () => download("zip"), "btn btn-ghost btn-sm"),
      ]),
    ]),
  ]);
}

function projectRestoreCard() {
  const file = el("input", { type: "file", accept: ".json,.zip,application/json,application/zip" });
  const mode = select([
    ["keep_existing", "Keep existing / merge"],
    ["import_as_new", "Import as a new project"],
  ]);
  const newName = input({ placeholder: "Optional imported project name", maxlength: "200" });
  const preview = el("div", { class: "ops-stack" });
  let validated = false;
  const buildForm = () => {
    const form = new FormData();
    if (file.files && file.files[0]) form.append("file", file.files[0]);
    form.append("mode", mode.value);
    if (newName.value.trim()) form.append("project_name", newName.value.trim());
    return form;
  };
  const validateButton = button(
    "Validate bundle",
    async () => {
      clearNode(preview);
      validated = false;
      if (!file.files || !file.files[0]) {
        toast("Choose a .json or .zip bundle.", { type: "error" });
        return;
      }
      setButtonBusy(validateButton, true, "Validating…");
      try {
        const result = await api.universalProjectImportPreview(buildForm());
        validated = true;
        preview.append(
          el("p", { class: "muted", text: `${result.project.project_name} · schema ${result.bundle.version} · action ${result.action}` }),
          el("p", { class: "muted", text: `${result.counts.key_types} key types · ${result.counts.keys} keys · ${result.counts.devices} devices · ${result.counts.webhooks} webhooks` }),
          ...(result.warnings || []).map((warning) => el("p", { class: "muted", text: `• ${warning}` }))
        );
      } catch (error) {
        handleError(error, "Bundle validation failed.");
      } finally {
        setButtonBusy(validateButton, false);
      }
    },
    "btn btn-ghost btn-sm"
  );
  const importButton = button(
    "Import bundle",
    async () => {
      if (!validated) {
        toast("Validate the bundle first.", { type: "error" });
        return;
      }
      const confirmed = await confirmAction({
        title: "Import validated bundle?",
        message: "The validated project bundle will be imported in one transaction.",
        confirmLabel: "Import bundle",
        tone: "primary",
      });
      if (!confirmed.confirmed) return;
      setButtonBusy(importButton, true, "Importing…");
      try {
        const result = await api.universalProjectImport(buildForm());
        toast(`Imported '${result.project.project_name}'.`, { type: "success" });
        validated = false;
        await renderOperations();
      } catch (error) {
        handleError(error, "Project import failed.");
      } finally {
        setButtonBusy(importButton, false);
      }
    },
    "btn btn-primary btn-sm"
  );

  return panel("Project restore", "Upload a project bundle. Nothing is written until the server validates it and you confirm.", null, [
    el("div", { class: "form-grid" }, [
      field("Bundle file", file),
      field("Conflict policy", mode),
      field("New project name", newName, "Used when importing as a new project."),
      el("div", { class: "span-2 clean-actions-bar" }, [validateButton, importButton]),
      el("div", { class: "span-2" }, [preview]),
    ]),
  ]);
}

function keyImportCard(projects, keyTypes) {
  const project = select(
    [["", "Choose a project"]].concat(projects.map((item) => [item.id, item.project_name]))
  );
  const type = select(keyTypes.map((item) => [item.id, item.name]));
  const textarea = el("textarea", {
    rows: "6",
    placeholder: "Paste one key per line. Plaintext is sent once for hashing and is never returned.",
  });
  const preview = el("div", { class: "ops-stack" });
  const payload = () => ({
    project_id: project.value,
    key_type_id: type.value,
    keys: textarea.value
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean)
      .map((value) => ({ key: value })),
  });
  const previewButton = button(
    "Preview validation",
    async () => {
      clearNode(preview);
      if (!project.value || !type.value) {
        toast("Choose a project and key type.", { type: "error" });
        return;
      }
      setButtonBusy(previewButton, true, "Validating…");
      try {
        const result = await api.universalImportPreview(payload());
        preview.append(
          el("p", { class: "muted", text: `${result.valid_count} valid · ${result.invalid_count} invalid · secrets echoed: ${result.secrets_echoed}` }),
          ...(result.invalid || []).slice(0, 10).map((row) =>
            el("p", { class: "muted", text: `Row ${row.index + 1}: ${row.reason}` })
          )
        );
      } catch (error) {
        handleError(error, "Import preview failed.");
      } finally {
        setButtonBusy(previewButton, false);
      }
    },
    "btn btn-ghost btn-sm"
  );
  const importButton = button(
    "Import validated keys",
    async () => {
      if (!project.value || !type.value || !textarea.value.trim()) {
        toast("Choose a project and key type, then enter keys.", { type: "error" });
        return;
      }
      const confirmed = await confirmAction({
        title: "Import keys?",
        message: "All rows must be valid or the transaction rolls back. Plaintext is hashed immediately and not returned.",
        confirmLabel: "Import keys",
        tone: "warn",
      });
      if (!confirmed.confirmed) return;
      setButtonBusy(importButton, true, "Importing…");
      try {
        const result = await api.universalImport(payload());
        toast(`${result.imported} key(s) imported.`, { type: "success" });
        textarea.value = "";
        clearNode(preview);
      } catch (error) {
        handleError(error, "Key import failed.");
      } finally {
        setButtonBusy(importButton, false);
      }
    },
    "btn btn-primary btn-sm"
  );

  return panel("Import keys", "Validated bulk key import. Every row commits in one transaction; plaintext is never returned.", null, [
    el("div", { class: "form-grid" }, [
      field("Project", project),
      field("Key type", type),
      field("Keys (one per line)", textarea, "Up to the configured bulk limit."),
      el("div", { class: "span-2 clean-actions-bar" }, [previewButton, importButton]),
      el("div", { class: "span-2" }, [preview]),
    ]),
  ]);
}

function importedCard(rows) {
  const tableRows = rows.map((row) =>
    el("tr", {}, [
      cell(
        "Imported record",
        el("div", {}, [
          el("strong", { text: row.label || "Imported key" }),
          el("div", { class: "mono", text: `${row.key_hint || ""} · ${row.id}` }),
        ])
      ),
      cell("Project", row.project_name || "—"),
      cell("Status", pill(row.status || "imported", row.status === "replaced" ? "success" : "info")),
      cell("Replacements", String(row.replacement_count || 0)),
      cell("Imported", formatDate(row.imported_at)),
      cell(
        "Actions",
        el("div", { class: "row-actions" }, [
          button("Recover", () => recoverImportedKey(row), "btn btn-primary btn-sm"),
          button("History", () => showImportedHistory(row)),
        ]),
        "align-right"
      ),
    ])
  );
  const content = [
    dataTable(
      ["Imported record", "Project", "Status", "Replacements", "Imported", "Actions"],
      tableRows,
      "No imported key metadata"
    ),
  ];
  if (importedHistory) {
    content.unshift(
      panel(
        `Replacement history · ${importedHistory.label}`,
        "History contains key hints and metadata only. Plaintext is never stored.",
        [button("Close history", () => {
          importedHistory = null;
          renderOperations();
        })],
        [
          dataTable(
            ["Generated", "Key hint", "Status", "By", "Warnings"],
            (importedHistory.items || []).map((item) =>
              el("tr", {}, [
                cell("Generated", formatDate(item.created_at)),
                cell("Key hint", el("span", { class: "mono", text: item.key_hint || "—" })),
                cell("Status", pill(item.key_status || "unknown", "info")),
                cell("By", item.generated_by_email || "—"),
                cell("Warnings", (item.warnings || []).join(" ") || "—"),
              ])
            ),
            "No replacements yet"
          ),
        ]
      )
    );
  }
  return panel(
    "Imported key recovery",
    "Generate independent replacement credentials from imported metadata. Original plaintext and revoked credentials are never restored.",
    null,
    content
  );
}

async function recoverImportedKey(row) {
  try {
    const preview = await api.importedKeyReplacementPreview({
      record_ids: [row.id],
      allow_duplicate: false,
    });
    const batch = preview.batch || {};
    if (!batch.eligible) {
      const plan = (preview.records || [])[0] || {};
      toast(plan.reason || "This imported key is not eligible for replacement.", { type: "error" });
      return;
    }
    const confirmed = await confirmAction({
      title: "Generate replacement key?",
      message: "The replacement is a new credential. The original plaintext is never restored.",
      confirmLabel: "Generate replacement",
      tone: "primary",
    });
    if (!confirmed.confirmed) return;
    const result = await api.generateImportedKeyReplacements({
      record_ids: [row.id],
      allow_duplicate: false,
      confirm: true,
    });
    replacementReveal = {
      keys: result.keys || [],
      notice: `${result.generated_count || 0} replacement credential(s). Copy them now.`,
    };
    if (!replacementReveal.keys.length) {
      toast((result.failures || [])[0]?.message || "Replacement generation failed.", { type: "error" });
      replacementReveal = null;
    }
    await renderOperations();
  } catch (error) {
    handleError(error, "Replacement generation failed.");
  }
}

async function showImportedHistory(row) {
  try {
    const data = await api.importedKeyHistory(row.id);
    importedHistory = {
      label: row.label || row.key_hint || row.id,
      items: data.history || [],
    };
    await renderOperations();
  } catch (error) {
    handleError(error, "Replacement history failed to load.");
  }
}

async function renderData(root) {
  const [projectData, typeData, importedData] = await Promise.all([
    api.universalProjects({ page_size: 100 }),
    api.keyTypes({}),
    api.importedKeys({ page_size: 100 }),
  ]);
  const projects = projectData.items || [];
  const keyTypes = typeData.key_types || [];
  root.append(
    projectExportCard(projects),
    projectRestoreCard(),
    panel("Key metadata export", "Download key inventory for reconciliation. Plaintext and HMAC digests are excluded.", null, [
      el("div", { class: "row-actions" }, [
        link("JSON export", api.universalKeysExportUrl({ format: "json" }), "btn btn-primary btn-sm"),
        link("CSV export", api.universalKeysExportUrl({ format: "csv" }), "btn btn-ghost btn-sm"),
      ]),
    ]),
    keyImportCard(projects, keyTypes),
    importedCard(importedData.items || [])
  );
}

/* ------------------------------------------------------------- automation */

async function runAutomation(buttonNode) {
  setButtonBusy(buttonNode, true, "Running…");
  try {
    const result = await api.automationRun();
    toast(`Automation ${result.status}.`, { type: "success" });
    await renderOperations();
  } catch (error) {
    handleError(error, "Automation run failed.");
  } finally {
    setButtonBusy(buttonNode, false);
  }
}

async function renderAutomation(root) {
  const [runsData, scheduler] = await Promise.all([
    api.automationRuns(),
    api.schedulerStatus(),
  ]);
  const counts = scheduler.counts || {};
  const runs = runsData.runs || [];
  const runButton = button("Run automation now", () => runAutomation(runButton), "btn btn-primary btn-sm");

  root.append(
    panel(
      "Automation and scheduler",
      "One advisory-locked worker executes bounded, idempotent expiry, retention, notification, device, and webhook jobs.",
      [runButton],
      [
        statGrid([
          ["Worker", scheduler.worker_status || "unknown", scheduler.worker_status === "running" ? "is-good" : "is-warn"],
          ["Webhook queue", counts.webhook_queue || 0],
          ["Dead deliveries", counts.webhook_dead || 0],
          ["Projects due", counts.projects_due || 0],
        ]),
        recordGrid([
          ["Jobs", (scheduler.jobs || []).join(", ") || "—"],
          ["Interval", `${scheduler.interval_seconds || 0}s`],
          ["Batch size", scheduler.batch_size || 0],
          ["Advisory lock", scheduler.lock_held ? "held" : "available"],
          ["Last success", scheduler.last_success ? formatDate(scheduler.last_success) : "Never"],
          ["Last failure", scheduler.last_failure ? formatDate(scheduler.last_failure) : "None"],
        ]),
        scheduler.last_failure_reason
          ? el("p", { class: "form-message", "data-tone": "error", text: scheduler.last_failure_reason })
          : null,
      ]
    ),
    panel("Recent runs", "Recent job outcomes. Failed runs retain their error text.", null, [
      dataTable(
        ["Started", "Job", "Status", "Finished", "Details", "Error"],
        runs.slice(0, 25).map((run) =>
          el("tr", {}, [
            cell("Started", formatDate(run.started_at)),
            cell("Job", el("span", { class: "mono", text: run.job })),
            cell("Status", statusBadge(run.status)),
            cell("Finished", run.finished_at ? formatDate(run.finished_at) : "—"),
            cell("Details", el("span", { class: "mono ops-url", text: JSON.stringify(run.details || {}) })),
            cell("Error", run.error || "—"),
          ])
        ),
        "No automation runs"
      ),
    ])
  );
}

/* ----------------------------------------------------------- access control */

function roleCreateCard(permissions) {
  const name = input({ placeholder: "Role name", maxlength: "80", required: "" });
  const roleId = input({ placeholder: "lowercase_id", maxlength: "50" });
  const description = input({ placeholder: "What is this role for?", maxlength: "300" });
  const selected = new Set();
  const checks = el(
    "div",
    { class: "check-grid" },
    permissions.map((permission) => {
      const box = el("input", { type: "checkbox" });
      box.addEventListener("change", () => {
        if (box.checked) selected.add(permission.id);
        else selected.delete(permission.id);
      });
      return el("label", { class: "check-option" }, [
        box,
        el("span", { text: `${permission.id} — ${permission.description || ""}` }),
      ]);
    })
  );
  const submit = button(
    "Create role",
    async () => {
      if (!name.value.trim()) {
        toast("Enter a role name.", { type: "error" });
        return;
      }
      if (!selected.size) {
        toast("Select at least one permission.", { type: "error" });
        return;
      }
      setButtonBusy(submit, true, "Creating…");
      try {
        await api.createRole({
          name: name.value.trim(),
          id: roleId.value.trim() || undefined,
          description: description.value.trim(),
          permissions: Array.from(selected),
        });
        toast("Role created.", { type: "success" });
        await renderOperations();
      } catch (error) {
        handleError(error, "Role creation failed.");
      } finally {
        setButtonBusy(submit, false);
      }
    },
    "btn btn-primary btn-sm"
  );

  return panel("Create custom global role", "Project-scoped role assignment remains available from the project APIs; this form creates a global custom role.", null, [
    el("div", { class: "form-grid" }, [
      field("Role name", name),
      field("Role id", roleId, "Optional; 3–50 lowercase letters, digits, or underscores."),
      field("Description", description),
      el("div", { class: "span-2" }, [
        el("span", { class: "field-label", text: "Permissions" }),
        checks,
      ]),
      el("div", { class: "span-2 clean-actions-bar margin-top-md" }, [submit]),
    ]),
  ]);
}

async function applyAdminRole(admin, roleSelect, customRoleIds) {
  const body = customRoleIds.has(roleSelect.value)
    ? { platform_role: "custom", platform_role_id: roleSelect.value }
    : { platform_role: roleSelect.value };
  try {
    await api.setAdminRole(admin.id, body);
    toast("Platform role updated.", { type: "success" });
    await renderOperations();
  } catch (error) {
    handleError(error, "Role update failed.");
  }
}

async function deleteCustomRole(role) {
  const confirmed = await confirmAction({
    title: `Delete '${role.name}'?`,
    message: "A role that is still assigned to an administrator cannot be deleted.",
    confirmLabel: "Delete role",
    tone: "danger",
  });
  if (!confirmed.confirmed) return;
  try {
    await api.deleteRole(role.id);
    toast("Role deleted.", { type: "success" });
    await renderOperations();
  } catch (error) {
    handleError(error, "Role deletion failed.");
  }
}

async function renderAccessControl(root) {
  const [roleData, adminData] = await Promise.all([
    api.universalRoles(),
    api.universalAdmins(),
  ]);
  const roles = roleData.roles || [];
  const admins = adminData.admins || [];
  const builtins = roles.filter((role) => role.built_in && role.id !== "custom");
  const customs = roles.filter((role) => role.custom && !role.project_id);
  const customRoleIds = new Set(customs.map((role) => role.id));

  const roleRows = roles.map((role) =>
    el("tr", {}, [
      cell(
        "Role",
        el("div", {}, [
          el("strong", { text: role.name }),
          el("div", { class: "muted", text: role.description || "" }),
        ])
      ),
      cell("Id", el("span", { class: "mono", text: role.id })),
      cell("Scope", pill(role.built_in ? "built-in" : role.project_id ? "project" : "global", role.built_in ? "info" : "success")),
      cell("Permissions", String((role.permissions || []).length)),
      cell(
        "Actions",
        role.custom
          ? el("div", { class: "row-actions" }, [
              button("Delete", () => deleteCustomRole(role), "btn btn-danger btn-sm"),
            ])
          : el("span", { class: "muted", text: "Immutable" }),
        "align-right"
      ),
    ])
  );

  const adminRows = admins.map((admin) => {
    const roleSelect = select(
      [
        ...builtins.map((role) => [role.id, role.name]),
        ...customs.map((role) => [role.id, `${role.name} (custom)`]),
      ],
      { value: admin.platform_role_id || admin.platform_role || "read_only" }
    );
    return el("tr", {}, [
      cell(
        "Administrator",
        el("div", {}, [
          el("strong", { text: admin.full_name || admin.email }),
          el("div", { class: "mono", text: admin.email }),
        ])
      ),
      cell("Base role", admin.role),
      cell("Platform role", roleSelect),
      cell("Active sessions", String(admin.active_sessions || 0)),
      cell("Last login", admin.last_login_at ? formatDate(admin.last_login_at) : "Never"),
      cell("Status", statusBadge(admin.is_active ? "active" : "inactive")),
      cell(
        "Actions",
        el("div", { class: "row-actions" }, [
          button("Apply role", () => applyAdminRole(admin, roleSelect, customRoleIds), "btn btn-primary btn-sm"),
          button(
            "Revoke sessions",
            async () => {
              try {
                await api.revokeAdminSessions(admin.id);
                toast("Administrator sessions revoked.", { type: "success" });
                await renderOperations();
              } catch (error) {
                handleError(error, "Session revocation failed.");
              }
            },
            "btn btn-danger btn-sm"
          ),
        ]),
        "align-right"
      ),
    ]);
  });

  root.append(
    panel("Platform roles", "Built-in roles are immutable. Role authority is always enforced server-side.", null, [
      dataTable(
        ["Role", "Id", "Scope", "Permissions", "Actions"],
        roleRows,
        "No roles"
      ),
    ]),
    roleCreateCard(roleData.permissions || []),
    panel("Administrators", "Only an Owner can assign the Owner role. A non-owner cannot grant permissions beyond their own authority.", null, [
      dataTable(
        ["Administrator", "Base role", "Platform role", "Active sessions", "Last login", "Status", "Actions"],
        adminRows,
        "No administrators"
      ),
    ])
  );
}

/* ----------------------------------------------------------------- search */

async function renderSearch(root) {
  const query = input({
    placeholder: "Search projects, key hints, fingerprints, types, devices, audit…",
    maxlength: "100",
    "aria-label": "Global search",
  });
  const results = el("div", { class: "ops-stack" });
  const searchButton = button(
    "Search",
    async () => {
      clearNode(results);
      if (query.value.trim().length < 2) {
        toast("Enter at least two characters.", { type: "error" });
        return;
      }
      setButtonBusy(searchButton, true, "Searching…");
      try {
        const data = await api.universalSearch(query.value.trim());
        const groups = data.groups || {};
        const group = (title, items, mapper) => {
          const section = el("section", { class: "panel glass ops-panel" }, [
            el("header", { class: "panel-head" }, [
              el("h3", { text: `${title} · ${items.length}` }),
            ]),
          ]);
          if (!items.length) {
            section.append(emptyState(`No ${title.toLowerCase()} matched`, "Try a broader search.", "⌕"));
          } else {
            items.forEach((item) => section.append(mapper(item)));
          }
          return section;
        };
        results.append(
          group("Projects", groups.projects || [], (item) =>
            el("div", { class: "ops-result-row" }, [
              el("strong", { text: item.project_name }),
              el("span", { class: "mono", text: `${item.project_type} · ${item.status}` }),
              link("Open Projects", "#projects", "btn btn-ghost btn-sm"),
            ])
          ),
          group("Keys", groups.keys || [], (item) =>
            el("div", { class: "ops-result-row" }, [
              el("strong", { class: "mono", text: item.key_hint }),
              el("span", { class: "mono", text: `${item.project_name} · ${item.status}` }),
              link("Open Keys", "#keys", "btn btn-ghost btn-sm"),
            ])
          ),
          group("Key types", groups.key_types || [], (item) =>
            el("div", { class: "ops-result-row" }, [
              el("strong", { text: item.name }),
              el("span", { class: "mono", text: `${item.id} · ${item.is_builtin ? "built-in" : "custom"}` }),
              link("Open Key Types", "#key-types", "btn btn-ghost btn-sm"),
            ])
          ),
          group("Devices", groups.devices || [], (item) =>
            el("div", { class: "ops-result-row" }, [
              el("strong", { text: item.device_name || item.device_id }),
              el("span", { class: "mono", text: `${item.project_name} · ${item.status}` }),
              link("Open Operations", "#operations", "btn btn-ghost btn-sm"),
            ])
          ),
          group("Audit", groups.audit || [], (item) =>
            el("div", { class: "ops-result-row" }, [
              el("strong", { text: item.action }),
              el("span", { class: "mono", text: `${item.actor_email || "system"} · ${formatDate(item.created_at)}` }),
              pill(item.outcome, item.outcome === "success" ? "success" : "warn"),
            ])
          )
        );
      } catch (error) {
        clearNode(results);
        results.append(errorPanel("Search unavailable", error));
        handleError(error, "Search failed.");
      } finally {
        setButtonBusy(searchButton, false);
      }
    },
    "btn btn-primary btn-sm"
  );
  const form = el("form", { class: "toolbar" }, [
    el("label", { class: "search" }, [
      el("span", { class: "search-icon", text: "⌕" }),
      query,
    ]),
    searchButton,
  ]);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    searchButton.click();
  });
  root.append(
    panel("Global search", "Search is scoped to the authenticated inventory and never returns key material.", null, [
      form,
      results,
    ])
  );
}

/* ------------------------------------------------------------------- docs */

async function renderDocs(root) {
  const data = await api.universalOpenapi();
  const paths = Object.entries(data.paths || {});
  const rows = [];
  paths.forEach(([path, methods]) => {
    Object.entries(methods || {}).forEach(([method, operation]) => {
      rows.push(
        el("tr", {}, [
          cell("Method", pill(method.toUpperCase(), "info")),
          cell("Path", el("span", { class: "mono", text: path })),
          cell("Summary", operation.summary || operation.description || "—"),
          cell("Tags", (operation.tags || []).join(", ") || "—"),
        ])
      );
    });
  });
  root.append(
    panel(
      "Universal API documentation",
      `${data.info?.title || "NexOra Universal API"} · ${data.info?.version || "v1"} · ${rows.length} operations`,
      [
        link("Open raw OpenAPI JSON", "/api/v1/universal/openapi.json", "btn btn-ghost btn-sm", {
          target: "_blank",
          rel: "noopener",
        }),
      ],
      [
        dataTable(["Method", "Path", "Summary", "Tags"], rows, "No documented operations"),
      ]
    )
  );
}

/* --------------------------------------------------------------- renderer */

async function renderTab(root) {
  switch (activeTab) {
    case "devices":
      return renderDevices(root);
    case "api-usage":
      return renderApiUsage(root);
    case "webhooks":
      return renderWebhooks(root);
    case "inbox":
      return renderInbox(root);
    case "data":
      return renderData(root);
    case "automation":
      return renderAutomation(root);
    case "access-control":
      return renderAccessControl(root);
    case "search":
      return renderSearch(root);
    case "docs":
      return renderDocs(root);
    default:
      activeTab = "devices";
      return renderDevices(root);
  }
}

export async function renderOperations() {
  const root = $("#operations-content");
  if (!root) return;
  syncTabs();
  clearNode(root);
  root.append(loadingBlock());
  const staging = el("div", { class: "ops-stack" });
  try {
    await renderTab(staging);
    clearNode(root);
    const secret = secretCard();
    if (secret) root.append(secret);
    const replacements = replacementCard();
    if (replacements) root.append(replacements);
    root.append(staging);
  } catch (error) {
    clearNode(root);
    root.append(errorPanel("This operations section could not load", error));
    handleError(error, "Operations failed to load.");
  }
}
