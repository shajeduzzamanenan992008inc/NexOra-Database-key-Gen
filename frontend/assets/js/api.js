let csrfToken = "";

export function setCsrfToken(token) {
  csrfToken = typeof token === "string" ? token : "";
}

export function getCsrfToken() {
  return csrfToken;
}

export class ApiProblem extends Error {
  constructor(message, status, code = "request_failed", details = {}) {
    super(message);
    this.name = "ApiProblem";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/* Every authenticated call is bounded. A hung database or proxy must never
   leave the UI waiting forever behind a loading state. */
const DEFAULT_TIMEOUT_MS = 15000;

async function request(
  path,
  { method = "GET", body, headers = {}, timeoutMs = DEFAULT_TIMEOUT_MS, _csrfRetry = false } = {}
) {
  const options = {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...headers,
    },
  };

  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  const mutating = !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
  if (mutating && csrfToken) {
    options.headers["X-CSRF-Token"] = csrfToken;
  }

  const controller = new AbortController();
  options.signal = controller.signal;
  const timer = window.setTimeout(
    () => controller.abort(),
    Math.max(1000, Number(timeoutMs) || DEFAULT_TIMEOUT_MS)
  );

  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiProblem(
        "The server took too long to respond. Check your connection and try again.",
        408,
        "timeout"
      );
    }
    throw new ApiProblem(
      "Network unavailable. Check your connection and try again.",
      0,
      "network_error"
    );
  } finally {
    window.clearTimeout(timer);
  }

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  let payload = null;
  if (isJson) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }

  // Keep the cached CSRF token in sync whenever a session is read, since the
  // server rotates it on every /auth/session call.
  if (path === "/api/v1/auth/session" && payload && payload.csrf_token) {
    setCsrfToken(payload.csrf_token);
  }
  if (path === "/api/v1/auth/login" && payload && payload.csrf_token) {
    setCsrfToken(payload.csrf_token);
  }

  if (!response.ok) {
    const problem = payload && payload.error ? payload.error : {};
    const mutating = !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
    if (
      !_csrfRetry &&
      mutating &&
      response.status === 403 &&
      problem.code === "csrf_failed"
    ) {
      try {
        const session = await request("/api/v1/auth/session", {
          timeoutMs: 8000,
          _csrfRetry: true,
        });
        if (session && session.csrf_token) setCsrfToken(session.csrf_token);
      } catch {
        // Fall through and surface the original error.
      }
      if (csrfToken) {
        return request(path, {
          method,
          body,
          headers,
          timeoutMs,
          _csrfRetry: true,
        });
      }
    }
    throw new ApiProblem(
      problem.message || `Request failed with status ${response.status}.`,
      response.status,
      problem.code || "request_failed",
      problem.details || {}
    );
  }
  return payload;
}

function query(params) {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, value);
    }
  });
  return search.toString();
}

/* CSRF tokens are rotated whenever the session is read, so a second tab can
   hold a stale token. Sign-out must still terminate the real session instead
   of failing with 403 and bouncing the user back into the console. */
async function logoutWithCsrfRecovery() {
  let lastError = null;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      return await request("/api/v1/auth/logout", { method: "POST" });
    } catch (error) {
      lastError = error;
      const csrfProblem =
        error instanceof ApiProblem &&
        (error.code === "csrf_failed" ||
          (error.status === 403 && error.code !== "forbidden"));
      if (!csrfProblem) throw error;

      // Another tab (or the dashboard bootstrap) may rotate the token again
      // while this retry is in flight. Refresh, pause briefly, and retry.
      const session = await request("/api/v1/auth/session", {
        timeoutMs: 8000,
      });
      if (session && session.csrf_token) setCsrfToken(session.csrf_token);
      await new Promise((resolve) => window.setTimeout(resolve, 120));
    }
  }

  throw lastError || new ApiProblem("Sign-out failed.", 403, "csrf_failed");
}

async function authorizedFetch(path, options = {}, timeoutMs = 30000) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const controller = new AbortController();
  const timer = window.setTimeout(
    () => controller.abort(),
    Math.max(1000, Number(timeoutMs) || 30000)
  );
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin",
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiProblem(
        "The server took too long to respond. Try again.",
        408,
        "timeout"
      );
    }
    throw new ApiProblem("Network unavailable. Try again.", 0, "network_error");
  } finally {
    window.clearTimeout(timer);
  }
  if (!response.ok) {
    let problem = {};
    try {
      const payload = await response.json();
      problem = payload && payload.error ? payload.error : {};
    } catch {
      problem = {};
    }
    if (
      response.status === 403 &&
      problem.code === "csrf_failed"
    ) {
      try {
        const session = await request("/api/v1/auth/session", { timeoutMs: 8000, _csrfRetry: true });
        if (session && session.csrf_token) setCsrfToken(session.csrf_token);
      } catch {
        // fall through
      }
      if (csrfToken) {
        const retryHeaders = { ...(options.headers || {}), "X-CSRF-Token": csrfToken };
        const retryResponse = await fetch(path, {
          ...options,
          headers: retryHeaders,
          credentials: "same-origin",
        });
        if (retryResponse.ok) return retryResponse;
        // fall through to throw original
      }
    }
    throw new ApiProblem(
      problem.message || `Request failed with status ${response.status}.`,
      response.status,
      problem.code || "request_failed",
      problem.details || {}
    );
  }
  return response;
}

async function upload(path, formData, timeoutMs = 60000) {
  const response = await authorizedFetch(
    path,
    { method: "POST", body: formData },
    timeoutMs
  );
  return response.json();
}

async function postBlob(path, body, timeoutMs = 60000) {
  const response = await authorizedFetch(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    },
    timeoutMs
  );
  return response.blob();
}

export const api = {
  // Authentication
  session: (options = {}) =>
    request("/api/v1/auth/session", { timeoutMs: 8000, ...options }),
  login: (email, password) =>
    request("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
      timeoutMs: 25000,
    }),
  logout: () => request("/api/v1/auth/logout", { method: "POST" }),
  logoutSafe: logoutWithCsrfRecovery,

  // Dashboard Overview & Health
  stats: () => request("/api/v1/dashboard/stats"),
  health: () => request("/api/v1/health"),

  // Project Management
  projects: (params) => request(`/api/v1/projects?${query(params)}`),
  project: (id) => request(`/api/v1/projects/${encodeURIComponent(id)}`),
  createProject: (projectName, options = {}) =>
    request("/api/v1/projects", {
      method: "POST",
      body: { project_name: projectName, ...options },
    }),
  generateProjectKey: (body) =>
    request("/api/v1/projects/generate-key", { method: "POST", body }),
  generateKeyForProject: (projectId, body) =>
    request(`/api/v1/projects/${encodeURIComponent(projectId)}/keys`, {
      method: "POST",
      body,
    }),
  setProjectStatus: (id, status) =>
    request(`/api/v1/projects/${encodeURIComponent(id)}/status`, {
      method: "PATCH",
      body: { status },
    }),
  setProjectAutoDelete: (id, autoDeleteAt) =>
    request(`/api/v1/projects/${encodeURIComponent(id)}/auto-delete`, {
      method: "PATCH",
      body: { auto_delete_at: autoDeleteAt },
    }),
  deleteProject: (id, confirmProjectName) =>
    request(`/api/v1/projects/${encodeURIComponent(id)}`, {
      method: "DELETE",
      body: { confirm_project_name: confirmProjectName },
    }),
  deleteAllProjects: (confirmation, password) =>
    request("/api/v1/projects", {
      method: "DELETE",
      body: { confirmation, password },
    }),

  // Reader Key Management & Generation
  readerKeys: (params) => request(`/api/v1/keys?${query(params)}`),
  readerKey: (id) => request(`/api/v1/keys/${encodeURIComponent(id)}`),
  revokeReaderKey: (keyId, reason) =>
    request(`/api/v1/keys/${encodeURIComponent(keyId)}/revoke`, {
      method: "POST",
      body: { reason },
    }),
  exportReaderKeysUrl: (params) => `/api/v1/keys/export?${query(params)}`,

  // Installation Management
  installations: (params) => request(`/api/v1/installations?${query(params)}`),
  installation: (id) =>
    request(`/api/v1/installations/${encodeURIComponent(id)}`),
  revokeInstallation: (id, reason) =>
    request(`/api/v1/installations/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
      body: { reason },
    }),

  // Security Center
  securityStatus: () => request("/api/v1/security/status"),

  // Audit Logs
  auditLogs: (params) => request(`/api/v1/audit-logs?${query(params)}`),

  // System Settings
  settings: () => request("/api/v1/settings"),
  updateSettings: (body) =>
    request("/api/v1/settings", { method: "PATCH", body }),

  // Universal Platform
  universalOverview: () => request("/api/v1/universal/overview"),
  universalProjects: (params) =>
    request(`/api/v1/universal/projects?${query(params)}`),
  universalProjectStats: (id) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}/statistics`),
  universalProjectTimeline: (id) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}/timeline`),
  updateUniversalProject: (id, body) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body,
    }),
  archiveUniversalProject: (id) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}/archive`, {
      method: "POST",
    }),
  cloneUniversalProject: (id, projectName) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}/clone`, {
      method: "POST",
      body: { project_name: projectName },
    }),
  universalProjectExport: (id) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}/export`),
  universalProjectExportPreview: (id) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}/export/preview`),
  universalProjectExportUrl: (id, format = "json") =>
    `/api/v1/universal/projects/${encodeURIComponent(id)}/export?${query({ format })}`,
  exportUniversalProjectsBlob: (body) =>
    postBlob("/api/v1/universal/projects/export", body, 90000),
  universalProjectImportPreview: (formData) =>
    upload("/api/v1/universal/projects/import/preview", formData, 90000),
  universalProjectImport: (formData) =>
    upload("/api/v1/universal/projects/import", formData, 90000),
  deleteUniversalProject: (id, confirmProjectName, password) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(id)}`, {
      method: "DELETE",
      body: { confirm_project_name: confirmProjectName, password },
      timeoutMs: 30000,
    }),
  universalProjectDeletePreview: () =>
    request("/api/v1/universal/projects/delete-preview"),
  deleteAllUniversalProjects: (confirmation, password) =>
    request("/api/v1/universal/projects", {
      method: "DELETE",
      body: { confirmation, password },
      timeoutMs: 60000,
    }),
  universalKeys: (params) => request(`/api/v1/universal/keys?${query(params)}`),
  universalKey: (id) => request(`/api/v1/universal/keys/${encodeURIComponent(id)}`),
  generateUniversalKeys: (body) =>
    request("/api/v1/universal/keys", { method: "POST", body }),
  updateUniversalKey: (id, body) =>
    request(`/api/v1/universal/keys/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body,
    }),
  activateUniversalKey: (id) =>
    request(`/api/v1/universal/keys/${encodeURIComponent(id)}/activate`, {
      method: "POST",
    }),
  deactivateUniversalKey: (id, reason = "") =>
    request(`/api/v1/universal/keys/${encodeURIComponent(id)}/deactivate`, {
      method: "POST",
      body: { reason },
    }),
  revokeUniversalKey: (id, reason) =>
    request(`/api/v1/universal/keys/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
      body: { reason },
    }),
  regenerateUniversalKey: (id) =>
    request(`/api/v1/universal/keys/${encodeURIComponent(id)}/regenerate`, {
      method: "POST",
    }),
  keyTypes: (params) => request(`/api/v1/universal/key-types?${query(params)}`),
  createKeyType: (body) =>
    request("/api/v1/universal/key-types", { method: "POST", body }),
  updateKeyType: (id, body) =>
    request(`/api/v1/universal/key-types/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body,
    }),
  deleteKeyType: (id) =>
    request(`/api/v1/universal/key-types/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  universalDevices: (params) =>
    request(`/api/v1/universal/devices?${query(params)}`),
  revokeUniversalDevice: (id, reason = "") =>
    request(`/api/v1/universal/devices/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
      body: { reason },
    }),
  resetUniversalDevice: (id) =>
    request(`/api/v1/universal/devices/${encodeURIComponent(id)}/reset`, {
      method: "POST",
    }),
  apiUsage: (params) => request(`/api/v1/universal/api-usage?${query(params)}`),
  webhooks: (params) => request(`/api/v1/universal/webhooks?${query(params)}`),
  createWebhook: (body) =>
    request("/api/v1/universal/webhooks", { method: "POST", body }),
  updateWebhook: (id, body) =>
    request(`/api/v1/universal/webhooks/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body,
    }),
  deleteWebhook: (id) =>
    request(`/api/v1/universal/webhooks/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  rotateWebhook: (id) =>
    request(`/api/v1/universal/webhooks/${encodeURIComponent(id)}/rotate-secret`, {
      method: "POST",
    }),
  testWebhook: (id) =>
    request(`/api/v1/universal/webhooks/${encodeURIComponent(id)}/test`, {
      method: "POST",
    }),
  webhookDeliveries: (id) =>
    request(`/api/v1/universal/webhooks/${encodeURIComponent(id)}/deliveries`),
  notifications: (params) =>
    request(`/api/v1/universal/notifications?${query(params)}`),
  markNotification: (id) =>
    request(`/api/v1/universal/notifications/${encodeURIComponent(id)}/read`, {
      method: "POST",
    }),
  markAllNotifications: () =>
    request("/api/v1/universal/notifications/read-all", { method: "POST" }),
  notificationPreferences: () =>
    request("/api/v1/universal/notifications/preferences"),
  updateNotificationPreferences: (preferences) =>
    request("/api/v1/universal/notifications/preferences", {
      method: "PATCH",
      body: { preferences },
    }),
  universalSearch: (q) => request(`/api/v1/universal/search?${query({ q })}`),
  automationRun: () =>
    request("/api/v1/universal/automation/run", { method: "POST" }),
  automationRuns: () => request("/api/v1/universal/automation/runs"),
  schedulerStatus: () => request("/api/v1/universal/automation/scheduler"),
  universalRoles: () => request("/api/v1/universal/roles"),
  createRole: (body) =>
    request("/api/v1/universal/roles", { method: "POST", body }),
  updateRole: (id, body) =>
    request(`/api/v1/universal/roles/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body,
    }),
  cloneRole: (id, body) =>
    request(`/api/v1/universal/roles/${encodeURIComponent(id)}/clone`, {
      method: "POST",
      body,
    }),
  deleteRole: (id) =>
    request(`/api/v1/universal/roles/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  universalAdmins: () => request("/api/v1/universal/admins"),
  setAdminRole: (id, body) =>
    request(`/api/v1/universal/admins/${encodeURIComponent(id)}/role`, {
      method: "PATCH",
      body,
    }),
  revokeAdminSessions: (id) =>
    request(`/api/v1/universal/admins/${encodeURIComponent(id)}/sessions/revoke`, {
      method: "POST",
    }),
  universalImportPreview: (body) =>
    request("/api/v1/universal/keys/import/preview", { method: "POST", body }),
  universalImport: (body) =>
    request("/api/v1/universal/keys/import", { method: "POST", body }),
  universalKeysExportUrl: (params) =>
    `/api/v1/universal/keys/export?${query(params)}`,
  importedKeys: (params) =>
    request(`/api/v1/universal/imported-keys?${query(params)}`),
  importedKeyReplacementPreview: (body) =>
    request("/api/v1/universal/imported-keys/replacement-preview", {
      method: "POST",
      body,
    }),
  generateImportedKeyReplacements: (body) =>
    request("/api/v1/universal/imported-keys/replacements", {
      method: "POST",
      body,
      timeoutMs: 90000,
    }),
  importedKeyHistory: (id) =>
    request(
      `/api/v1/universal/imported-keys/${encodeURIComponent(id)}/history`
    ),
  projectRoles: (projectId) =>
    request(`/api/v1/universal/projects/${encodeURIComponent(projectId)}/roles`),
  projectRoleAssignments: (projectId) =>
    request(
      `/api/v1/universal/projects/${encodeURIComponent(projectId)}/role-assignments`
    ),
  assignProjectRole: (projectId, body) =>
    request(
      `/api/v1/universal/projects/${encodeURIComponent(projectId)}/role-assignments`,
      { method: "POST", body }
    ),
  removeProjectRoleAssignment: (projectId, assignmentId) =>
    request(
      `/api/v1/universal/projects/${encodeURIComponent(projectId)}/role-assignments/${encodeURIComponent(assignmentId)}`,
      { method: "DELETE" }
    ),
  universalOpenapi: () => request("/api/v1/universal/openapi.json"),

  // Legacy API mappings for backward compatibility
  keys: (params) => request(`/api/v1/keys?${query(params)}`),
  key: (id) => request(`/api/v1/keys/${encodeURIComponent(id)}`),
  generate: (body) => request("/api/keys/generate", { method: "POST", body }),
  bulkGenerate: (body) =>
    request("/api/keys/bulk-generate", { method: "POST", body }),
  revoke: (keyId, reason) =>
    request(`/api/v1/keys/${encodeURIComponent(keyId)}/revoke`, {
      method: "POST",
      body: { reason },
    }),
  logs: (params) => request(`/api/v1/audit-logs?${query(params)}`),
  exportUrl: (params) => `/api/v1/keys/export?${query(params)}`,
};
