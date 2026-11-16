# OMEGA NEX-ORA — Project & Key Management Implementation Report

**Date:** 2026-11-16
**Repository:** `NexOra-Database-key-Gen` (branch `main`, based on `e088f5d`)
**Status:** Implementation complete; full test suite green; live migration **not** executed.
**Working tree:** Modified, uncommitted. Nothing was committed, pushed, or deployed.

---

## 1. What already existed

- Flask app factory, configuration fail-fast validation, PostgreSQL pool/transaction layer.
- Argon2id admin authentication, database-backed sessions, CSRF, rate limiting, role checks.
- `nexora_projects`, `reader_keys`, `reader_installations`, `device_tokens`, `system_settings`
  from migrations 005/006.
- Reader key generation, HMAC storage, one-time activation, device credentials, installation
  revocation, heartbeat, dashboard statistics, audit log, security center.
- Legacy `/api/keys/*` licensing API and the SMS Reader subsystem.
- 170 passing tests and 26 skipped SMS tests at baseline.

---

## 2. What changed

### 2.1 Database migration

New forward migration: `db/migrations/007_project_key_management.sql`.

- Preflight aborts on duplicate live normalized names or projects with multiple reader keys.
- `CREATE UNIQUE INDEX ux_reader_keys_project_single ON reader_keys(project_id)`.
- `nexora_projects.auto_delete_at timestamptz` (NULL = disabled) + partial due index.
- `nexora_deleted_project_names` tombstone table (normalized name primary key, display name,
  deletion metadata; no plaintext key or HMAC) + RLS deny-by-default.
- Transaction-safe and checksum-managed by the existing runner.
- Historical migrations 001–006 are unchanged.

### 2.2 Backend

`backend/services/project_service.py`

- `generate_project_key()` replaces creation + key generation. It normalizes the name, takes a
  PostgreSQL advisory lock, checks tombstones and existing/soft-deleted rows, inserts the project and
  exactly one key in one transaction, and writes `project.create` + `reader_key.generate` audits.
- Duplicate names return `409 PROJECT_ALREADY_EXISTS`; retired names return
  `409 PROJECT_NAME_RETIRED`. Both return safe metadata only.
- Unique-index violations are converted to `KEY_ALREADY_EXISTS`; concurrent requests cannot create
  two projects or two keys.
- `get_project()`, `list_projects()` now expose `auto_delete_at`, `days_remaining`,
  `auto_delete_due`, reader-key status/hint/timestamps, and active installation data.
- `set_project_auto_delete()`, `get_auto_delete_days()`, and
  `purge_due_projects()` implement scheduling. The retention setting is
  `nexora.auto_delete_days`; `0`/unset means disabled.
- `hard_delete_project()` writes a strict `project.delete` audit with `project_id = NULL` and a safe
  snapshot before deleting device tokens, installations, reader keys, and the project; verifies all
  rows are absent; writes a name tombstone.
- `hard_delete_all_projects()` runs one transaction, writes `project.delete_all` with the count and
  project names, deletes only NexOra-owned rows, verifies the project table is empty, and rolls back
  on any failure.
- `activate_reader()` fails closed with `503 SERVER_CONFIGURATION_ERROR` when `SUPABASE_URL` is
  missing; prefers `supabase_url` and accepts `database_url` as a validated alias; rejects DSNs and
  placeholders; commits the `EXPIRED` status before returning the expiration error.
- `verify_reader_credential()` rejects credentials for inactive projects with `403 PROJECT_INACTIVE`.
- Removed `create_project`, `soft_delete_project`, `generate_key`, and `generate_bulk_keys`.

`backend/api/v1/projects.py`

- Added `POST /api/v1/projects/generate-key`.
- Added `PATCH /api/v1/projects/<id>/auto-delete`.
- Replaced soft delete with owner-only `DELETE /api/v1/projects/<id>` requiring the exact name.
- Added owner-only `DELETE /api/v1/projects` requiring `DELETE ALL PROJECTS` + current password.
- Kept list/detail/status and key revocation.

`backend/api/v1/reader.py`

- `supabase_url` preferred; `database_url` accepted as a backward-compatible alias.

`backend/services/auth_service.py`

- Added `verify_admin_password()` for delete-all re-authentication.

`backend/cli.py`

- Added `python -m backend.cli purge-expired-projects [--limit N]`.

### 2.3 Frontend

- `frontend/dashboard.html`: new project table columns (Project Name, Status, Reader Key Status,
  Created, Auto Delete Date, Remaining Days, Installation, Actions); name-based Generate Key form;
  per-project auto-delete and permanent delete; separate delete-all modal; auto-delete policy
  setting; removed Create Project and bulk-key modals.
- `frontend/assets/js/api.js`: `generateProjectKey`, `setProjectAutoDelete`, permanent delete,
  delete-all.
- `frontend/assets/js/ui.js`: confirmation dialog supports typed text and password requirements.
- `frontend/assets/js/dashboard.js`: new table rendering, duplicate-project metadata display,
  hard-delete/delete-all flows, auto-delete controls, double-submit guards.
- `frontend/assets/app.css`: supporting styles. No inline scripts/styles or new CSP violations.

### 2.4 Tests and fixtures

- `tests/conftest.py`: truncate list includes NexOra tables, tombstones, and `system_settings`.
- `tests/test_phase2_dashboard.py`: rewritten for generate-key, auto-delete, permanent delete,
  settings, and removed-route assertions.
- `tests/test_project_key_management.py`: new regression suite.
- `tests/test_nexora_api_v1.py`, `tests/test_frontend.py`: updated for the new route/UI surface.
- Migration 007 preflight bug found by the isolated test database (`min(uuid)` is not a PostgreSQL
  aggregate) and fixed to `min(project_id::text)`.

### 2.5 Documentation

- `README.md` updated for the new workflow, migration 007, scheduler, API, and **198 passed / 26
  skipped** test result.
- `docs/API.md` updated with the current `/api/v1` project/reader contract while retaining the legacy
  licensing reference.
- Added this report plus the audit, test, and migration reports.

---

## 3. New files

| File | Purpose |
| --- | --- |
| `db/migrations/007_project_key_management.sql` | Forward migration for uniqueness, auto-delete, tombstones |
| `tests/test_project_key_management.py` | 25-test regression suite |
| `docs/PROJECT_KEY_MANAGEMENT_AUDIT.md` | Pre-implementation technical audit |
| `docs/PROJECT_KEY_MANAGEMENT_IMPLEMENTATION_REPORT.md` | This report |
| `docs/PROJECT_KEY_MANAGEMENT_TEST_REPORT.md` | Actual test output and coverage |
| `docs/PROJECT_KEY_MANAGEMENT_MIGRATION_REPORT.md` | Migration contents, preflight, rollback, approval gate |

No Android/Gradle files, new repositories, or external services were added.

---

## 4. Behavior removed or replaced

| Removed / replaced | Replacement |
| --- | --- |
| `POST /api/v1/projects` (empty project) | `POST /api/v1/projects/generate-key` |
| `POST /api/v1/projects/<id>/keys` | Project creation and key generation are one operation |
| `POST /api/v1/projects/<id>/keys/bulk` | No bulk keys; one key per project |
| `POST /api/v1/projects/<id>/keys/bulk` UI | Removed |
| Create Project modal | Removed |
| `soft_delete_project()` | Verified hard delete + tombstone |
| Revoke-only project delete | Permanent delete of project/key/installations/tokens |
| Browser-side deletion assumptions | Server-side `purge-expired-projects` command |

---

## 5. Preserved functionality

- Admin login/logout/session, CSRF, roles, rate limiting, account lockout.
- Dashboard statistics, key listing/detail/revocation, installation listing/revocation, audit log,
  security center, settings.
- Reader activation/heartbeat/status endpoints.
- Legacy `/api/keys/*` licensing API and all legacy tables.
- SMS Reader tables and behavior.
- CSP-safe frontend construction.
- Migrations 001–006 byte-for-byte unchanged.

---

## 6. Verification performed

| Check | Result |
| --- | --- |
| `python -m compileall -q backend` | exit 0 |
| Targeted suites (`test_phase2_dashboard.py`, `test_project_key_management.py`) | **43 passed, exit 0** |
| Full suite (`python -m pytest -p no:warnings`) | **198 passed, 26 skipped, exit 0** |
| Test duration | 174.52 s |
| Migration 007 in isolated test DB | Applied by the pytest fixture; checksum recorded there |
| Live Supabase | **Not touched** |

The 26 skips are the optional SMS Reader suite (module not installed). No test was skipped or
weakened to make the new behavior pass.

See `docs/PROJECT_KEY_MANAGEMENT_TEST_REPORT.md` for exact commands and output.

---

## 7. Migration status

- Migration 007 is **prepared**, reviewed, and validated against the isolated local test database.
- It has **not** been executed against the live Supabase database.
- Migrations 005, 006, and 007 remain subject to the existing Phase 3 backup/PITR approval gate.
- The migration runner verifies checksums and will refuse to run if an already-applied migration file
  changes.

---

## 8. Remaining blockers and manual actions

1. **Live migration approval:** obtain the Phase 3 backup/PITR sign-off, then run
   `python -m backend.cli migrate` in an approved window. Do not run it before that gate.
2. **Live-data reconciliation:** run the preflight queries in the migration report against Supabase.
   If duplicates exist, reconcile them manually; migration 007 intentionally aborts instead of
   merging/deleting.
3. **Configure retention:** automatic deletion is disabled until an administrator sets
   `auto_delete_days > 0` (settings) or sets an individual `auto_delete_at`. No retention period was
   invented.
4. **Install the scheduler:** configure cron/systemd/Task Scheduler (or an existing platform
   scheduler) to run `python -m backend.cli purge-expired-projects` at the desired cadence. It was
   not installed by this code change.
5. **Deploy the updated backend/frontend together:** the new dashboard expects
   `/api/v1/projects/generate-key` and the new delete routes.
6. **Rotate any credentials** if the pre-migration review finds secrets in legacy project rows; this
   work did not inspect or print live secrets.

No commit, push, deployment, live migration, or live deletion was performed.
