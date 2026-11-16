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

async function request(path, { method = "GET", body, headers = {} } = {}) {
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

  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new ApiProblem(
      "Network unavailable. Check your connection and try again.",
      0,
      "network_error"
    );
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

  if (!response.ok) {
    const problem = payload && payload.error ? payload.error : {};
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

export const api = {
  // Authentication
  session: () => request("/api/v1/auth/session"),
  login: (email, password) =>
    request("/api/v1/auth/login", { method: "POST", body: { email, password } }),
  logout: () => request("/api/v1/auth/logout", { method: "POST" }),

  // Dashboard Overview & Health
  stats: () => request("/api/v1/dashboard/stats"),
  health: () => request("/api/v1/health"),

  // Project Management
  projects: (params) => request(`/api/v1/projects?${query(params)}`),
  project: (id) => request(`/api/v1/projects/${encodeURIComponent(id)}`),
  createProject: (body) => request("/api/v1/projects", { method: "POST", body }),
  setProjectStatus: (id, status) =>
    request(`/api/v1/projects/${encodeURIComponent(id)}/status`, {
      method: "PATCH",
      body: { status },
    }),
  deleteProject: (id) =>
    request(`/api/v1/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),

  // Reader Key Management & Generation
  generateReaderKey: (projectId, body) =>
    request(`/api/v1/projects/${encodeURIComponent(projectId)}/keys`, {
      method: "POST",
      body,
    }),
  generateBulkReaderKeys: (projectId, body) =>
    request(`/api/v1/projects/${encodeURIComponent(projectId)}/keys/bulk`, {
      method: "POST",
      body,
    }),
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
