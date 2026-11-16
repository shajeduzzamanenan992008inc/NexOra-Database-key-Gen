# OMEGA NEX-ORA — Project & Reader Key Management

**NexOra Database Key Generator & Project Management System** — *Secure Keys. Smarter Database Management.*

Production-oriented Admin-only Project and Reader Key Management Backend.

---

## ⚠️ Important Project Boundary

- **Admin System & Backend APIs Only**: This repository contains solely the NexOra Admin system and its backend REST APIs.
- **Android Reader App is Completely Separate**: The Android Reader App is a completely separate project and repository. No Android source code, Gradle files, or Android UI are included here.
- **Secure Integration Surface**: NexOra provides hardened backend REST APIs (`/api/v1/reader/`) that a separate Reader App uses for initial one-time key redemption and subsequent authenticated reconnections.

---

## 1. Target Architecture

```
Admin Dashboard (HTML5 / Vanilla JS / CSS)
                   |  (HTTPS, HttpOnly Strict Cookies, CSRF Protection)
                   v
Flask Backend Application
  ├── Environment Fail-Fast Validation (SUPABASE_URL, SECRET_KEY, KEY_PEPPER, etc.)
  ├── Single Super Admin Authentication (Argon2id, Hashed Sessions, Rate Limiting)
  ├── Project Management (Generate Key creates one project + one key; ACTIVE/DEACTIVE)
  ├── Reader Key Lifecycle (CSPRNG, HMAC-SHA256, One-Time Redemption, one key per project)
  ├── High-Entropy Device Credential Minting & Verification
  └── Versioned REST APIs (/api/v1/)
                   |  (TLS Connection Pool - pg8000)
                   v
Supabase PostgreSQL Database
  ├── Row Level Security (RLS Deny-by-Default)
  └── Migrations (001_initial_schema through 007_project_key_management)
```

- **Trusted Backend Layer**: The Flask backend is the only layer with direct database credentials.
- **Zero Credential Exposure**: The browser and the separate Reader App **never** receive the Supabase service-role key or direct PostgreSQL password.

---

## 2. Environment Configuration

Copy `.env.example` to `.env` and configure the required variables. **Never commit `.env`**.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | Direct PostgreSQL connection string to Supabase pooler (`sslmode=require`). |
| `SECRET_KEY` | Yes | Flask cryptographic secret (min 32 characters, CSPRNG). |
| `KEY_PEPPER` | Yes | Server-side HMAC pepper for key and token digests (min 32 characters). |
| `ADMIN_EMAIL` | Yes | Super administrator email address. |
| `ADMIN_PASSWORD_HASH` | Yes | Argon2id hash of the admin password. |
| `SUPABASE_URL` | Required for Reader activation | Supabase project URL (`https://<project-ref>.supabase.co`). Reader activation fails closed when it is missing. |
| `SUPABASE_SERVICE_ROLE_KEY` | Optional/Paired | Supabase server-side service-role JWT (never sent to client). |
| `APP_ENV` | Optional | `production` (default), `staging`, or `development`. |
| `COOKIE_SECURE` | Optional | `true` in production (requires HTTPS), `false` for local testing. |

---

## 3. Database Foundations & Migrations

The database schema is managed via PostgreSQL migrations in `db/migrations/`:

| Migration | Purpose |
|---|---|
| `001_initial_schema.sql` | `admin_users`, `admin_sessions`, `license_keys`, `key_activations`, `generation_logs`, `admin_audit_logs`. |
| `002_indexes_and_policies.sql` | Uniqueness constraints, query performance indexes, and RLS deny-by-default policies. |
| `003_password_hash_comment.sql` | Documentation of Argon2id password hashing standard. |
| `004_sms_reader_keys.sql` | Legacy SMS reader schema (retained for backward compatibility). |
| `005_nexora_projects.sql` | **Phase 1 Foundation**: `nexora_projects`, `reader_keys`, `reader_installations`, `device_tokens`, `system_settings`. |
| `006_rename_rh_function.sql` | **Phase 2 Cleanup**: Renames legacy `rh_set_updated_at()` trigger function to `nexora_set_updated_at()` and repoints all triggers. |
| `007_project_key_management.sql` | **Project/key management upgrade**: one-reader-key-per-project unique index, `nexora_projects.auto_delete_at`, `nexora_deleted_project_names` tombstone registry, supporting indexes and RLS. Preflight aborts on ambiguous legacy data. |

To apply migrations:
```bash
python -m backend.cli migrate
```

> **Live migration gate:** migrations 005, 006, and 007 remain subject to the Phase 3 backup/PITR approval gate. Do not run them against the production Supabase database until that gate is cleared. See `docs/PROJECT_KEY_MANAGEMENT_MIGRATION_REPORT.md`.

### Scheduled auto-delete

Automatic deletion is disabled until an administrator configures a retention period (`auto_delete_days > 0`) and/or an individual project schedule. A server-side scheduler must run:

```bash
python -m backend.cli purge-expired-projects --limit 100
```

This command only deletes projects whose `auto_delete_at` timestamp is due. It never runs from browser JavaScript and writes a `project.delete` audit event for each project.

---

## 4. Project & Reader Key Workflow

1. Open **Key Generator** in the dashboard.
2. Enter a Project Name and click **Generate Key**.
3. The backend normalizes the name (case-insensitive) and, in one transaction, creates the project plus exactly **one** Reader Key. Only the key's HMAC is stored; the plaintext key is returned once for copying.
4. If the name already exists — including a retired name from a permanently deleted project — the API returns `409` with safe metadata and never returns the original plaintext.
5. The Reader App redeems the key once against `POST /api/v1/reader/activate`, which permanently changes `GENERATED` → `USED`.
6. Deactivating a project blocks new activations and existing device credentials without changing the key's lifecycle status.
7. Deleting a project is a permanent hard delete of the project, its key, installations, device tokens, and owned records, inside one transaction. The audit event survives, and the normalized name is tombstoned so it cannot be silently reused.
8. **DELETE ALL PROJECTS** permanently removes every NexOra project in one transaction after the exact phrase and a fresh password check; admin accounts, settings, audit history, and legacy SMS data are preserved.

---

## 5. Versioned REST API (`/api/v1/`)

### Health
- `GET /api/v1/health` — Service and database connection health status.

### Authentication (Admin)
- `POST /api/v1/auth/login` — Admin login (rate-limited, sets HttpOnly session cookie).
- `POST /api/v1/auth/logout` — Destroy session.
- `GET /api/v1/auth/session` — Verify active session.

### Dashboard
- `GET /api/v1/dashboard/stats` — Aggregate counters: projects, keys by status, installations, DB health.

### Admin Project & Reader Key Management (Privileged)
- `POST /api/v1/projects/generate-key` — Create one project and exactly one Reader Key atomically. This is the only project/key creation path.
- `GET /api/v1/projects` — List managed projects with reader-key status, auto-delete date, remaining days, and active installation status.
- `GET /api/v1/projects/<id>` — Project details and installation overview.
- `PATCH /api/v1/projects/<id>/status` — Set `active` or `inactive` (deactivation blocks activations, heartbeats, and credential checks; key status is preserved).
- `PATCH /api/v1/projects/<id>/auto-delete` — Set or clear the timezone-aware scheduled deletion timestamp (`null` disables it).
- `DELETE /api/v1/projects/<id>` — Permanently hard-delete one project and all owned records; requires `confirm_project_name`.
- `DELETE /api/v1/projects` — Permanently delete all NexOra projects in one transaction; requires `confirmation: "DELETE ALL PROJECTS"` and the current admin password.

### Key Management (Admin)
- `GET /api/v1/keys` — List reader keys with search, project filter, status filter, pagination.
- `GET /api/v1/keys/<id>` — Key detail (no plaintext, hint only).
- `GET /api/v1/keys/export` — Download keys as CSV.
- `POST /api/v1/keys/<id>/revoke` — Revoke a reader key permanently.

### Installation Management (Admin)
- `GET /api/v1/installations` — List reader installations with filters and pagination.
- `GET /api/v1/installations/<id>` — Installation detail and related audit events.
- `POST /api/v1/installations/<id>/revoke` — Revoke an active Reader installation.

### Security Center (Admin)
- `GET /api/v1/security/status` — Auth config, session config, rate-limit info, recent security events.

### Audit Logs (Admin)
- `GET /api/v1/audit-logs` — Paginated audit log with search, date, and event type filters.

### Settings (Admin)
- `GET /api/v1/settings` — Current system settings.
- `PATCH /api/v1/settings` — Update allowed settings (key prefix, expiry, bulk limit, heartbeat interval, etc.).

### Reader App Connection & Authorization (External Reader App)
- `POST /api/v1/reader/activate` — One-time key redemption (submits `project_name`, `supabase_url` preferred, `reader_key`, `device_id`). The deprecated `database_url` field is still accepted as an alias for compatibility, but it must contain the Supabase Project URL, not a PostgreSQL connection string.
- `POST /api/v1/reader/heartbeat` — Periodic heartbeat using `Authorization: Bearer <device_credential>`.
- `GET /api/v1/reader/status` — Status verification using `Authorization: Bearer <device_credential>`.

---

## 6. Automated Testing

Run the full test suite:
```bash
python -m pytest -v
```

Latest verified run: **198 passed, 26 skipped, exit 0** (`174.52s`). The 26 skips are the optional SMS Reader suite (SMS module not installed in the test environment).

- Phase 1 Core Cryptography & Config Security: `test_nexora_config.py`, `test_nexora_project_normalization.py`, `test_nexora_reader_keys.py`, `test_nexora_api_v1.py`
- Phase 2 Admin Dashboard & API Integration: `test_phase2_dashboard.py` (unauthenticated rejection, CSRF, live DB views, settings, security center)
- Project/Key Management Regression Suite: `test_project_key_management.py` (generate-key, one-key constraint, lifecycle, activation, auto-delete, hard delete, delete-all rollback, security)
- Frontend CSP & XSS Hardening: `test_frontend.py`

See `docs/PROJECT_KEY_MANAGEMENT_TEST_REPORT.md` for exact commands and output.

---

## 7. Project/Key Management Deliverables

- [x] Technical audit: [`docs/PROJECT_KEY_MANAGEMENT_AUDIT.md`](docs/PROJECT_KEY_MANAGEMENT_AUDIT.md)
- [x] Implementation report: [`docs/PROJECT_KEY_MANAGEMENT_IMPLEMENTATION_REPORT.md`](docs/PROJECT_KEY_MANAGEMENT_IMPLEMENTATION_REPORT.md)
- [x] Test report: [`docs/PROJECT_KEY_MANAGEMENT_TEST_REPORT.md`](docs/PROJECT_KEY_MANAGEMENT_TEST_REPORT.md)
- [x] Migration report: [`docs/PROJECT_KEY_MANAGEMENT_MIGRATION_REPORT.md`](docs/PROJECT_KEY_MANAGEMENT_MIGRATION_REPORT.md)
- [x] Forward migration prepared (not executed live): `db/migrations/007_project_key_management.sql`
- [x] Generate-Key-only project creation with one key per project
- [x] ACTIVE/DEACTIVE project controls, permanent hard delete, and transactional delete-all
- [x] Auto-delete scheduling plus `purge-expired-projects` CLI command
- [x] Full dashboard, service, API, and regression-test updates

Historical Phase 2 reports remain available: [`docs/PHASE_2_IMPLEMENTATION_PLAN.md`](docs/PHASE_2_IMPLEMENTATION_PLAN.md), [`docs/PHASE_2_FINAL_REPORT.md`](docs/PHASE_2_FINAL_REPORT.md).
