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
  } catch {
    throw new ApiProblem(
      "Network unavailable. Check your connection and try again.",
      0,
      "network_error"
    );
  }

  const contentType = response.headers.get("content-type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
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
    if (value !== undefined && value !== null && value !== "" && value !== false) {
      search.set(key, value === true ? "1" : value);
    }
  });
  return search.toString();
}

export const api = {
  session: () => request("/api/admin/session"),
  login: (email, password) =>
    request("/api/admin/login", { method: "POST", body: { email, password } }),
  logout: () => request("/api/admin/logout", { method: "POST" }),

  stats: () => request("/api/dashboard/stats"),
  keys: (params) => request(`/api/keys?${query(params)}`),
  key: (id) => request(`/api/keys/${encodeURIComponent(id)}`),
  generate: (body) => request("/api/keys/generate", { method: "POST", body }),
  bulkGenerate: (body) =>
    request("/api/keys/bulk-generate", { method: "POST", body }),
  revoke: (id, reason) =>
    request(`/api/keys/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
      body: { reason },
    }),
  deleteKey: (id) =>
    request(`/api/keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
