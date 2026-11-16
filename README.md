# OMEGA NEX-ORA — Phase 2: Complete Admin Dashboard + Backend Integration

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
  ├── Project Management (Case-Insensitive Name Normalization, Soft-Delete)
  ├── Reader Key Lifecycle (CSPRNG, HMAC-SHA256, One-Time Redemption)
  ├── High-Entropy Device Credential Minting & Verification
  └── Versioned REST APIs (/api/v1/)
                   |  (TLS Connection Pool - pg8000)
                   v
Supabase PostgreSQL Database
  ├── Row Level Security (RLS Deny-by-Default)
  └── Migrations (001_initial_schema through 005_nexora_projects)
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
| `SUPABASE_URL` | Optional/Paired | Supabase project URL (`https://<project-ref>.supabase.co`). |
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

To apply migrations:
```bash
python -m backend.cli migrate
```

---

## 4. Versioned REST API (`/api/v1/`)

### Health
- `GET /api/v1/health` — Service and database connection health status.

### Authentication (Admin)
- `POST /api/v1/auth/login` — Admin login (rate-limited, sets HttpOnly session cookie).
- `POST /api/v1/auth/logout` — Destroy session.
- `GET /api/v1/auth/session` — Verify active session.

### Dashboard
- `GET /api/v1/dashboard/stats` — Aggregate counters: projects, keys by status, installations, DB health.

### Admin Project & Reader Key Management (Privileged)
- `POST /api/v1/projects` — Create a project with case-insensitive collision check.
- `GET /api/v1/projects` — List managed projects with active installation status.
- `GET /api/v1/projects/<id>` — Project details and installation overview.
- `PATCH /api/v1/projects/<id>/status` — Activate or deactivate a project.
- `DELETE /api/v1/projects/<id>` — Soft-delete project and revoke all associated authorizations.
- `POST /api/v1/projects/<id>/keys` — Generate single reader key (plaintext returned once).
- `POST /api/v1/projects/<id>/keys/bulk` — Generate bulk reader keys for a project.

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
- `POST /api/v1/reader/activate` — One-time key redemption (submits `project_name`, `database_url`, `reader_key`).
- `POST /api/v1/reader/heartbeat` — Periodic heartbeat using `Authorization: Bearer <device_credential>`.
- `GET /api/v1/reader/status` — Status verification using `Authorization: Bearer <device_credential>`.

---

## 5. Automated Testing

Run the full test suite:
```bash
python -m pytest -v
```

Currently, **151 tests pass** (with 26 skipped SMS tests):
- Phase 1 Core Cryptography & Config Security: `test_nexora_config.py`, `test_nexora_project_normalization.py`, `test_nexora_reader_keys.py`, `test_nexora_api_v1.py`
- Phase 2 Admin Dashboard & API Integration: `test_phase2_dashboard.py` (Unauthenticated access rejection, CSRF handling, live DB integration for all 8 views, Argon2id security center)
- Frontend CSP & XSS Hardening: `test_frontend.py` (No inline styles, no inline event handlers, safe DOM APIs, zero credential leakage)

---

## 6. Phase 2 Deliverables Summary

- [x] Phase 2 Implementation Plan: [`docs/PHASE_2_IMPLEMENTATION_PLAN.md`](docs/PHASE_2_IMPLEMENTATION_PLAN.md)
- [x] Phase 2 Final Report: [`docs/PHASE_2_FINAL_REPORT.md`](docs/PHASE_2_FINAL_REPORT.md)
- [x] Full 8-View Admin Dashboard: `frontend/dashboard.html`
- [x] Complete REST API Suite: `backend/api/v1/` (Dashboard, Projects, Keys, Installations, Security, Audit, Settings, Auth)
- [x] Secure Service Layer: `backend/services/project_service.py`
- [x] Legacy DB Function Cleanup: `db/migrations/006_rename_rh_function.sql`
- [x] CSP & XSS Compliance: 100% External CSS/JS, zero inline code, safe DOM APIs
- [x] Comprehensive Automated Test Suite: 151 passing tests with live PostgreSQL validation
