let csrfToken = "";

export function setCsrfToken(token) {
  csrfToken = typeof token === "string" ? token : "";
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
  session: () => request("/api/auth/session"),
  login: (email, password) =>
    request("/api/auth/login", { method: "POST", body: { email, password } }),
  logout: () => request("/api/auth/logout", { method: "POST" }),

  stats: () => request("/api/dashboard/stats"),

  keys: (params) => request(`/api/keys?${query(params)}`),
  key: (id) => request(`/api/keys/${encodeURIComponent(id)}`),
  generate: (body) => request("/api/keys/generate", { method: "POST", body }),
  bulkGenerate: (body) =>
    request("/api/keys/bulk-generate", { method: "POST", body }),
  updateKey: (id, body) =>
    request(`/api/keys/${encodeURIComponent(id)}`, { method: "PATCH", body }),
  deleteKey: (id) =>
    request(`/api/keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
  revoke: (keyId, reason) =>
    request("/api/keys/revoke", {
      method: "POST",
      body: { key_id: keyId, reason },
    }),
  deactivate: (body) =>
    request("/api/keys/deactivate", { method: "POST", body }),
  logs: (params) => request(`/api/logs?${query(params)}`),
  exportUrl: (params) => `/api/keys/export?${query(params)}`,
};
