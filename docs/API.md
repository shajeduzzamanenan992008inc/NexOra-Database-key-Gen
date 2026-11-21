# NexOra-Database-key-Gen — API Reference

Base URL: same origin as the console (e.g. `https://keys.example.com`). All endpoints are JSON
unless stated (`/api/keys/export` returns CSV). Responses carry `X-Request-ID`; every `/api/*`
response is sent with `Cache-Control: no-store`.

## Conventions

### Authentication

Two ways to authenticate, handled by the same middleware:

| Client | Mechanism | CSRF |
| --- | --- | --- |
| Console (browser) | `POST /api/auth/login` sets the `nexora_session` cookie: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` when `COOKIE_SECURE=true` | Required on every non-`GET` request via `X-CSRF-Token` |
| Server-to-server | `Authorization: Bearer <session token>` | Not required (no ambient browser authority) |

The session token is stored only as a SHA-256 digest server-side. The CSRF token is returned by
login and by `GET /api/auth/session`; it is bound to one session and compared with
`hmac.compare_digest`.

```http
POST /api/auth/login
Content-Type: application/json

{ "email": "ops@yourdomain.com", "password": "…" }
```

```json
200 OK
{
  "user": { "id": "…", "email": "ops@yourdomain.com", "role": "owner", "full_name": "Ops Owner" },
  "csrf_token": "…",
  "expires_at": "2026-11-15T23:10:04Z"
}
```

Failure: `401 invalid_credentials` (same message for unknown email and wrong password),
`429 account_locked` while a DB-backed lockout is in effect, `429 rate_limited` for the IP limit.

| Endpoint | Auth | Roles |
| --- | --- | --- |
| `POST /api/auth/login` | – | – |
| `POST /api/auth/logout` | ✅ | any |
| `GET /api/auth/session` | ✅ | any |
| `GET /api/health` | – | – |

`GET /api/auth/session` returns the current user, a **fresh** CSRF token and the expiry — the console
calls it on load so a page left open past a token rotation still works.

### Error envelope

Every failure is JSON with a stable machine code and a safe message:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields need attention.",
    "request_id": "9f1c…",
    "details": { "quantity": "Must be between 1 and 500." }
  }
}
```

| HTTP | `code` | Meaning |
| --- | --- | --- |
| 400 | `invalid_body` | Body missing/not JSON/too large |
| 401 | `not_authenticated`, `invalid_credentials`, `session_expired` | No or invalid session |
| 403 | `forbidden`, `csrf_failed` | Role not permitted, CSRF token missing/stale |
| 404 | `key_not_found`, `not_found` | Unknown key id or route |
| 405 | `method_not_allowed` | Wrong verb |
| 409 | `key_revoked`, `key_expired`, `key_inactive`, `key_pending`, `key_not_active`, `activation_limit_reached`, `activation_already_deactivated`, `activation_conflict`, `already_revoked`, `no_active_activations` | State conflict |
| 422 | `validation_error` | Field validation (`details` maps field → message) |
| 429 | `rate_limited` | Per-IP limit; `details.retry_after_seconds` + `Retry-After` header |
| 500 | `internal_error` | Unhandled; details are never leaked |
| 503 | `database_unavailable` | Pool exhausted / DB unreachable |

### Pagination

`page` (default `1`) and `page_size` (default `25`, max `100`) are validated; invalid values return
`422`. List responses include:

```json
"pagination": { "page": 1, "page_size": 25, "total": 137, "total_pages": 6,
                "has_next": true, "has_previous": false }
```

### Key objects

Plaintext keys appear **only** in the creation response. Everywhere else a key is identified by
`id` (UUID) and `key_hint` (e.g. `NXR-****-****-****-8VL3`).

```json
{
  "id": "0b1f…",
  "key_hint": "NXR-****-****-****-8VL3",
  "key_format": "NXR-****-****-****-****",
  "key_prefix": "NXR",
  "key_suffix": "",
  "label": "Season 12 — Team Liquid",
  "status": "active",
  "max_activations": 1,
  "activation_count": 0,
  "expires_at": "2027-01-01T00:00:00Z",
  "metadata": { "tier": "pro" },
  "batch_id": "9c2e…",
  "created_at": "2026-11-15T20:00:00Z",
  "updated_at": "2026-11-15T20:00:00Z",
  "revoked_at": null,
  "revoked_reason": null,
  "created_by": "…",
  "created_by_email": "ops@yourdomain.com"
}
```

---

## Keys

## NexOra v1 Project & Reader Key API (`/api/v1`)

> This section documents the current Admin project/key management API. The `/api/keys/*` sections
> below document the legacy licensing API and remain unchanged for backward compatibility.

All `/api/v1` endpoints use the same session + CSRF rules described above. Roles are enforced per
route (`owner`, `admin`, or `viewer`); destructive operations require `owner`.

### Project creation is Generate Key

There is no standalone create-project endpoint and no bulk Reader Key endpoint. `POST
/api/v1/projects/generate-key` creates the project and exactly one key in one transaction.

```http
POST /api/v1/projects/generate-key
X-CSRF-Token: …
Content-Type: application/json

{ "project_name": "MyDatabase", "label": "Optional label" }
```

```json
201 Created
{
  "created": true,
  "project": { "id": "…", "project_name": "MyDatabase", "status": "active",
               "created_at": "2026-11-16T10:00:00Z", "auto_delete_at": null },
  "reader_key": { "id": "…", "project_id": "…", "project_name": "MyDatabase",
                  "plaintext_key": "NXR-…", "key_hint": "NXR-****-****-****-8VL3",
                  "status": "generated", "is_permanent": true, "expires_at": null }
}
```

The plaintext key is returned **only** in this 201 response. A duplicate normalized name (including
case/spacing variants and names retired by a permanent deletion) returns `409` with
`PROJECT_ALREADY_EXISTS` or `PROJECT_NAME_RETIRED` and safe metadata — never plaintext.

### Project endpoints

| Method | Path | Roles | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/projects/generate-key` | owner, admin | Create project + one key |
| `GET` | `/api/v1/projects` | owner, admin, viewer | List projects with key/installation/auto-delete data |
| `GET` | `/api/v1/projects/{id}` | owner, admin, viewer | Project detail |
| `PATCH` | `/api/v1/projects/{id}/status` | owner, admin | `{"status":"active"}` or `{"status":"inactive"}` |
| `PATCH` | `/api/v1/projects/{id}/auto-delete` | owner, admin | `{"auto_delete_at":"2026-12-16T23:59:59Z"}` or `null` |
| `DELETE` | `/api/v1/projects/{id}` | owner | `{"confirm_project_name":"Exact Name"}` — hard delete |
| `DELETE` | `/api/v1/projects` | owner | `{"confirmation":"DELETE ALL PROJECTS","password":"…"}` |
| `POST` | `/api/v1/keys/{id}/revoke` | owner, admin | Permanently revoke a key |

Project responses include `reader_key_status`, `reader_key_hint`, `auto_delete_at`, `days_remaining`,
`auto_delete_due`, `active_installation`, and the legacy lifecycle timestamps. `days_remaining` is
`null` when no date is scheduled; the dashboard shows *No Auto Delete Scheduled*.

Deactivation blocks new activations, device credential checks, and heartbeats (`403
PROJECT_INACTIVE`) but never changes `GENERATED`/`USED`/`REVOKED`/`EXPIRED`.

Single delete writes a `project.delete` audit event with `project_id = NULL` and a safe snapshot
before removing device tokens, installations, reader keys, and the project. A tombstone in
`nexora_deleted_project_names` prevents silent name reuse. Delete-all writes `project.delete_all`
with the deleted count and preserves admin accounts, sessions, settings, audit history, and legacy
SMS tables; any error rolls back the whole transaction.

### Reader endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/reader/activate` | – | One-time activation |
| `POST` | `/api/v1/reader/heartbeat` | Bearer device credential | Heartbeat |
| `GET` | `/api/v1/reader/status` | Bearer device credential | Credential/status check |

The activation body is `project_name`, `supabase_url`, `reader_key`, and `device_id`.
`supabase_url` is the public project URL (`https://<ref>.supabase.co`), never a PostgreSQL DSN or
service-role password. The deprecated `database_url` field is accepted as an alias for older Reader
builds, but is validated the same way. Activation fails closed with `503
SERVER_CONFIGURATION_ERROR` when the server has no `SUPABASE_URL`. A key transitions `GENERATED →
USED` exactly once; later attempts return `409 ACTIVE_INSTALLATION_EXISTS` or `410
KEY_ALREADY_USED`, and `EXPIRED`/`REVOKED` keys cannot activate.

---

### `POST /api/keys/generate` — owner, admin → `201`

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `quantity` | int | `1` | 1 … `MAX_BULK_KEYS` |
| `prefix` | string | `"NXR"` | 0–12 `A–Z0–9`, uppercased |
| `suffix` | string | `""` | same rules |
| `groups` | int | `4` | 1–8 |
| `group_length` | int | `4` | 3–10 |
| `separator` | string | `"-"` | `-`, `_` or `""` |
| `max_activations` | int | `1` | 1–10000 |
| `expires_at` | ISO-8601 | `null` | must be in the future |
| `label` | string | `""` | ≤ 80 chars |
| `metadata` | object | `{}` | ≤ 20 keys, ≤ 4 kB |

```json
201 Created
{
  "batch_id": "9c2e…",
  "quantity": 2,
  "key_format": "NXR-****-****-****-****",
  "keys": [
    { "id": "…", "key": "NXR-DANT-P97S-S4LQ-8VL3", "key_hint": "NXR-****-****-****-8VL3",
      "key_format": "NXR-****-****-****-****", "status": "active",
      "max_activations": 1, "expires_at": null, "created_at": "…" }
  ],
  "warning": "Store these keys now. Plaintext keys are never shown again."
}
```

Generation runs in one transaction: collision-retried inserts on a unique `key_hash`, one
`generation_logs` row for the batch, one `key.generate` audit event.

### `POST /api/keys/bulk-generate` — owner, admin → `201`

Same body; `quantity` is **required** and must be ≥ 2. Response identical to `generate`.

### `GET /api/keys` — owner, admin, viewer → `200`

| Query | Default | Notes |
| --- | --- | --- |
| `page`, `page_size` | 1, 25 | see pagination |
| `status` | all | one of the six statuses |
| `search` | – | ≤ 200 chars; matches hint, label, batch id, creator email |
| `sort` | `created_at` | `created_at`, `updated_at`, `expires_at`, `activation_count`, `status`, `label` |
| `order` | `desc` | `asc` / `desc` |
| `created_after`, `created_before`, `expires_before` | – | ISO-8601 |

```json
{ "items": [ { …key… } ], "pagination": { … },
  "filters": { "status": "active", "search": null, "sort": "created_at", "order": "desc" } }
```

Search is parameterised (`ILIKE`/`=` with bound values) — no string concatenation reaches SQL.

### `GET /api/keys/{id}` — owner, admin, viewer → `200`

Full key object plus `activations` (hashed device/account hints, timestamps, status), `events`
(last 50 audit entries for the key, newest first) and `batch` (quantity, format, label, generator).

### `PATCH /api/keys/{id}` — owner, admin → `200`

Exactly one or more of:

| Field | Values |
| --- | --- |
| `status` | `active`, `inactive`, `pending` only |
| `max_activations` | 1–10000 (cannot drop below the current `activation_count`) |
| `expires_at` | ISO-8601 or `null` (clears) |
| `label` | string ≤ 80 |

Unknown fields → `422`. Returns the refreshed detail object.

Allowed status transitions (anything else → `422`):

| From | To |
| --- | --- |
| `active` | `inactive`, `pending` |
| `inactive` | `active`, `pending` |
| `pending` | `active`, `inactive` |
| `expired` | `active`, `inactive` (future `expires_at` required for `active`) |
| `used` | `inactive` |
| `revoked` | – (terminal) |

Setting `active` is refused while `activation_count >= max_activations` or while `expires_at` is in
the past; raise `max_activations` / extend `expires_at` in the same request to satisfy both.
Lowering `max_activations` to the current count flips an `active` key to `used`.

### `DELETE /api/keys/{id}` — owner → `200`

```json
{ "id": "…", "deleted": true }
```

Deletes the key and cascades its activations. The audit log keeps the history. `404 key_not_found`
when already gone.

### `POST /api/keys/revoke` — owner, admin → `200`

```json
{ "key_id": "…", "reason": "Chargeback — order 1183" }
```

```json
{ "id": "…", "status": "revoked", "deactivated_activations": 2 }
```

Reason 3–500 chars, mandatory. Revocation is terminal and releases all active activations in the
same transaction. Already revoked → `409 already_revoked`.

### `POST /api/keys/deactivate` — owner, admin → `200`

```json
{ "key_id": "…", "activation_id": "optional-uuid", "reason": "Device replaced" }
```

Omit `activation_id` to release every active activation for the key. The freed slots become
reusable, and a `used` key returns to `active` as soon as its count drops below `max_activations`.

```json
{ "id": "…", "status": "active", "activation_count": 0,
  "max_activations": 1, "deactivated_activations": 1 }
```

Revoked key → `409 key_not_active`. Nothing active → `409 no_active_activations`.

### `GET /api/keys/export` — owner, admin, viewer → `200 text/csv`

Honours `status`, `search`, `created_after`, `created_before`, `expires_before`. Up to 10 000 rows.

Columns: `key_hint, key_format, label, status, max_activations, activation_count, expires_at,
created_at, revoked_at, revoked_reason, batch_id, created_by_email`.

Never contains plaintext keys. Cells beginning with `= + - @ tab CR` are prefixed with `'` to
neutralise CSV/formula injection, the file is UTF-8 with BOM, and the export itself is audited
(`key.export`, row count, filters).

---

## Licensing (called by launchers / games)

These two endpoints are intentionally **unauthenticated** so a distributed client can check and
activate a key. They are rate limited per IP, do not reveal whether a *guessed* key ever existed
(identical `valid:false` shape), and never return another player's device or account identifiers.

### `POST /api/keys/activate` → `200`

```json
{ "key": "NXR-DANT-P97S-S4LQ-8VL3", "device_id": "HWID-9f2…", "account_id": "player-42" }
```

`key` 8–96 chars, `device_id` 4–200 (required), `account_id` optional 0–200.

```json
200 OK
{
  "replayed": false,
  "activation": { "id": "…", "device_hint": "dev_7c…", "account_hint": "acc_1a…",
                  "activated_at": "2026-11-15T20:04:11Z", "status": "active" },
  "key": { "id": "…", "key_hint": "NXR-****-****-****-8VL3", "status": "used",
           "max_activations": 1, "activation_count": 1, "remaining_activations": 0,
           "expires_at": null }
}
```

- Same `device_id` activating the same key again → `200` with `"replayed": true`; no slot consumed.
- A different device on an exhausted key → `409 activation_limit_reached`.
- `409 key_revoked`, `409 key_expired`, `409 key_inactive`, `409 key_pending` as appropriate.
- `409 activation_conflict` if a concurrent activation wins the row lock; retry once.
- Device and account ids are hashed (`HMAC-SHA256` with a purpose prefix) before storage.

### `POST /api/keys/verify` → `200`

```json
{ "key": "NXR-DANT-P97S-S4LQ-8VL3" }
```

```json
{ "valid": true, "key_hint": "NXR-****-****-****-8VL3", "status": "active",
  "max_activations": 1, "activation_count": 0, "remaining_activations": 1,
  "expires_at": null }
```

Unknown/malformed key → `{ "valid": false, "status": "unknown", "remaining_activations": 0 }`
with `200`, so callers can branch on `valid` without parsing errors. Expired keys report
`status: "expired"` even before the `expire` job runs.

---

## Dashboard and logs

### `GET /api/dashboard/stats` — any role → `200`

```json
{
  "totals": { "total": 137, "active": 84, "inactive": 3, "pending": 1,
              "used": 41, "expired": 6, "revoked": 2 },
  "activity": { "generated_24h": 12, "activations_total": 41, "activations_24h": 5,
                "expiring_soon": 3, "activation_rate": 29.9 },
  "recent_generations": [ … ],
  "recent_audit": [ … ]
}
```

Computed with aggregate SQL, never from client-supplied numbers.

### `GET /api/logs` — any role → `200`

| Query | Default | Values |
| --- | --- | --- |
| `kind` | `generation` | `generation`, `activations`, `audit` |
| `page`, `page_size` | 1, 25 | pagination |
| `action` | – | audit only, e.g. `key.revoke` |
| `outcome` | – | audit only, `success` / `failure` |

```json
{ "kind": "audit", "items": [ … ], "pagination": { … } }
```

`generation` rows contain batch id, quantity, format, label, generator email, timestamp;
`activations` rows contain key hint, device/account hints, status, timestamps; `audit` rows contain
action, actor email, target, outcome, IP, user agent and details.

---

## Rate limits (per IP, sliding 60 s window)

| Endpoint | Limit / min |
| --- | --- |
| `POST /api/auth/login` | 10 (plus DB-backed account lockout) |
| `POST /api/keys/generate` | 30 |
| `POST /api/keys/bulk-generate` | 10 |
| `POST /api/keys/activate` | 30 |
| `POST /api/keys/verify` | 60 |
| `POST /api/keys/revoke`, `/deactivate` | 30 each |
| `DELETE /api/keys/{id}` | 30 |
| `GET /api/keys/export` | 10 |
| everything else under `/api/` | 240 |

Exceeding a limit returns `429` with `details.retry_after_seconds` and a `Retry-After` header.
`RATE_LIMIT_ENABLED=false` disables it (not recommended in production).

---

## Universal project transfer, roles, and scheduler

All routes below require an authenticated administrator session, CSRF protection on
mutations, and the permission shown. Authorization is server-side.

### Project export

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/universal/projects/{id}/export/preview` | `project:export` | Counts, excluded-secret flags, warnings |
| `GET` | `/api/v1/universal/projects/{id}/export` | `project:export` | JSON bundle; add `?format=zip` for ZIP |
| `POST` | `/api/v1/universal/projects/export` | `project:export` | `{"project_ids":[...],"format":"zip"}` or `{"all":true}` |
| `GET` | `/api/v1/universal/keys/export` | `export:read` | Filtered key metadata; `format=json` or `csv` |

Bundles are version `1`, format `nexora-project-bundle`. They contain project
configuration, declarative key types, key/device metadata, redacted webhook
destinations, and imported metadata. Plaintext keys, key hashes, device
credentials, webhook signing secrets, database URLs, and platform secrets are
excluded. ZIP entries are `manifest.json`, `project.json`, and `README.txt`; the
manifest contains the SHA-256 of `project.json`.

### Project import

`POST /api/v1/universal/projects/import/preview` and
`POST /api/v1/universal/projects/import` accept `multipart/form-data`:

| Field | Required | Values |
| --- | --- | --- |
| `file` | yes | JSON or ZIP bundle (maximum 8 MiB) |
| `mode` | no | `keep_existing` (default), `import_as_new`, `replace_existing` |
| `project_name` | no | New name for the imported project |
| `replace_confirmed` | replace only | `true` |
| `confirm` | replace only | Exact existing project name |

Preview returns the bundle format/version, source and resolved project identity,
conflict action, existing-project metadata, counts, key types, warnings, and
`secrets_exposed: false`. Import is one transaction; failure rolls back every
write. Imported keys/devices become regeneration-required metadata records,
never active credentials. Imported webhooks are created inactive.

### Permanent deletion

| Method | Path | Authorization |
| --- | --- | --- |
| `DELETE` | `/api/v1/universal/projects/{id}` | `project:delete`; body `{"confirm_project_name":"...","password":"..."}` |
| `GET` | `/api/v1/universal/projects/delete-preview` | Owner only; counts before delete-all |
| `DELETE` | `/api/v1/universal/projects` | Owner only; `{"confirmation":"DELETE ALL PROJECTS","password":"..."}` |

Single deletion removes owned children transactionally, writes a safe audit
snapshot, preserves audit history and administrators, and makes the name
reusable. Delete-all preserves administrator accounts, sessions, settings,
audit, and legacy SMS data. The preview returns projects, key types, keys,
devices, webhooks, deliveries, and imported-record counts.

### Custom roles and assignment

| Method | Path | Permission |
| --- | --- | --- |
| `GET` | `/api/v1/universal/roles` | `admin:read` |
| `POST` | `/api/v1/universal/roles` | `admin:roles` |
| `PATCH` | `/api/v1/universal/roles/{id}` | `admin:roles` |
| `POST` | `/api/v1/universal/roles/{id}/clone` | `admin:roles` |
| `DELETE` | `/api/v1/universal/roles/{id}` | `admin:roles` |
| `PATCH` | `/api/v1/universal/admins/{id}/role` | `admin:roles` |
| `POST` | `/api/v1/universal/admins/{id}/sessions/revoke` | `admin:sessions` |

Create body: `{"id":"ops_reader","name":"Ops Reader","description":"...","permissions":["project:read"]}`.
Custom ids match `^[a-z][a-z0-9_]{2,49}$`. Non-owners cannot grant permissions
they do not hold or any critical permission; only an Owner assigns Owner; the
last Owner cannot be demoted; assigned roles cannot be deleted. Every mutation
is audited.

### Scheduler

`GET /api/v1/universal/automation/scheduler` (`automation:read`) returns the job
list, advisory lock key, interval, batch size, queue/due counts, recent runs, and
the last successful run. The CLI commands `universal-scheduler-run`,
`universal-scheduler-status`, and `universal-automation-run` are documented in
[`SCHEDULER.md`](SCHEDULER.md).
