# OMEGA NEX-ORA — Project & Reader Key Management Audit

**Repository:** `NexOra-Database-key-Gen`
**Branch:** `main`
**Latest commit at audit start:** `e088f5d` (`Update files`)
**Audit date:** 2026-11-16
**Scope:** Existing Flask admin system, PostgreSQL schema, dashboard, tests, documentation.
**Explicitly out of scope:** the separate Android Reader App repository, legacy SMS Reader behavior, live Supabase migrations, live project deletion.

This audit was produced by reading the actual source and tests, not by trusting earlier reports. A
baseline test run was executed before changes.

---

## 1. Baseline verification

| Check | Result |
| --- | --- |
| Working tree | Uncommitted edits already present (no commits made) |
| Baseline test command | `python -m pytest -q -p no:warnings` |
| Baseline result | **170 passed, 26 skipped, exit 0** |
| Backend compile | `python -m compileall -q backend` passed |

The 26 skips are the optional SMS Reader test suite when the SMS module is unavailable.

---

## 2. Architecture as found

- Flask application factory in `backend/app.py`; blueprints under `backend/api/`.
- Single PostgreSQL access layer in `backend/db.py` (`pg8000`), one transaction per `cursor()` block.
- Admin authentication: Argon2id passwords, hashed session tokens, HttpOnly cookies, CSRF header on
  non-GET requests, role checks (`owner`, `admin`, `viewer`).
- Project/key domain in `backend/services/project_service.py`.
- NexOra schema introduced by migrations `005_nexora_projects.sql` and renamed by
  `006_rename_rh_function.sql`.
- Dashboard is a single static page with external JS/CSS and a strict CSP (no inline scripts/styles).
- Security audit in `admin_audit_logs`; `project_id` already referenced `nexora_projects` with
  `ON DELETE SET NULL` (migration 005, line 217).
- Legacy licensing API (`/api/keys/*`) and the SMS Reader tables are separate from the NexOra
  project/key workflow.

---

## 3. Conflicts and issues found

### 3.1 Project creation was split across conflicting workflows

Found:

- `POST /api/v1/projects` created an empty project.
- `POST /api/v1/projects/<id>/keys` generated a single key.
- `POST /api/v1/projects/<id>/keys/bulk` generated many keys.
- The dashboard exposed a **Create Project** modal plus separate key-generation flows.

This conflicts with the required single **Generate Key** workflow and makes it possible to create a
project with no key, or to attempt multiple keys.

### 3.2 One-key-per-project was not enforced by PostgreSQL

Migration 005 declared `reader_keys.project_id uuid NOT NULL REFERENCES nexora_projects(id)`, but no
unique constraint/index existed on `reader_keys.project_id`. Application checks alone cannot prevent
two concurrent requests from each inserting a key.

### 3.3 Deletion was a soft delete and allowed silent name reuse

`soft_delete_project()` only set `deleted_at`/status and revoked credentials:

- Project, key, installation, and device-token rows remained physically present.
- The normalized name could be reused by a later project, with no tombstone.
- The requirement is a genuine hard delete plus a name tombstone.

### 3.4 No scheduled deletion field or scheduler

No `auto_delete_at` column existed, no retention setting, and no server-side purge command. Browser
JavaScript cannot be used for scheduled deletion, and no external scheduling service was present.

### 3.5 Dashboard columns were incomplete

The project table did not show Reader Key Status, Created Date, Auto Delete Date, Remaining Days, or
Active Installation together. There was no per-project permanent delete, no delete-all action, and
no auto-delete policy control.

### 3.6 Activation URL contract was ambiguous and not fail-closed enough

`POST /api/v1/reader/activate` accepted a field named `database_url` that actually represents the
public Supabase project URL. Risks identified:

- If `SUPABASE_URL` was unset, activation could not consistently fail closed.
- Readers could submit a PostgreSQL DSN or an arbitrary URL under a misleading field name.
- Concurrent activation needed a row lock plus a database-level single-active-installation check.
- An expired-key status update had to commit even though the API returns an expiration error.

### 3.7 Audit survival on deletion was fragile

`admin_audit_logs.project_id` uses `ON DELETE SET NULL`, so an audit row can survive a project delete
only if it is written **before** the project row is removed and with `project_id = NULL`. A cascade or
a post-delete audit insert would lose the event.

### 3.8 Foreign keys require explicit ordered deletion

Verified in migration 005:

| Table | Relationship | Action |
| --- | --- | --- |
| `reader_keys.project_id` | → `nexora_projects(id)` | `ON DELETE RESTRICT` |
| `reader_installations.project_id` | → `nexora_projects(id)` | `ON DELETE RESTRICT` |
| `reader_installations.reader_key_id` | → `reader_keys(id)` | `ON DELETE RESTRICT` |
| `device_tokens.project_id` | → `nexora_projects(id)` | `ON DELETE RESTRICT` |
| `device_tokens.installation_id` | → `reader_installations(id)` | `ON DELETE CASCADE` |
| `admin_audit_logs.project_id` | → `nexora_projects(id)` | `ON DELETE SET NULL` |

Because project ownership uses `RESTRICT`, a project delete must explicitly remove
`device_tokens` → `reader_installations` → `reader_keys` → `nexora_projects` inside one transaction.
No FK rewrite is required.

### 3.9 Duplicate-name and multi-key risk in existing data

Before a unique index can be added, legacy rows must be checked for:

- two live projects sharing the same normalized name;
- one project owning more than one reader key.

Migration 007 therefore aborts rather than merging or deleting data automatically.

---

## 4. Required changes

### Database

1. Add `ux_reader_keys_project_single` unique index on `reader_keys(project_id)`.
2. Add `nexora_projects.auto_delete_at timestamptz` plus a partial index.
3. Add `nexora_deleted_project_names` tombstone table (name and deletion metadata only, no key
   material) with RLS deny-by-default.
4. Preflight duplicate-name and multi-key checks that stop the migration with a descriptive error.
5. Do not modify migrations 001–006 and do not touch legacy SMS tables.

### Backend

1. Replace project creation and key generation with one atomic `generate_project_key()` flow.
2. Remove the standalone create route, single-key route, and bulk route.
3. Add per-name PostgreSQL advisory locks around generation and deletion.
4. Add project status changes with audit, blocking activations, credential checks, and heartbeats.
5. Add auto-delete scheduling, retention setting (`nexora.auto_delete_days`, default disabled), and
   `purge-expired-projects` CLI command.
6. Replace soft delete with verified hard delete plus tombstone and surviving audit.
7. Add transactional delete-all with exact phrase, owner role, and password re-authentication.
8. Fail closed when `SUPABASE_URL` is missing; prefer `supabase_url` and retain `database_url` as a
   validated alias; commit the expired key status before returning the expiration error.
9. Add `AuthService.verify_admin_password()`.

### Frontend

1. Remove the Create Project modal and bulk-key UI.
2. Make Generate Key accept a Project Name directly.
3. Show the required project fields and auto-delete controls.
4. Add per-project permanent delete and a separate delete-all flow with typed confirmation.
5. Keep CSP-safe DOM APIs, CSRF, and one-time plaintext display.

### Tests / docs

1. Rewrite stale Phase 2 tests for the new routes.
2. Add a regression suite for generation, lifecycle, auto-delete, hard delete, delete-all rollback,
   roles, CSRF, and secret hygiene.
3. Update README and API documentation.
4. Publish audit, implementation, test, and migration reports.

---

## 5. Migration impact and rollback

**Impact:** one unique index, one nullable timestamp column, one partial index, one tombstone table,
one check constraint, and RLS grants. No existing column is dropped or rewritten. Legacy SMS and
licensing tables are untouched.

**Preflight:** aborts on duplicate live normalized project names or projects with multiple keys.
Reconciliation is manual; no automatic merge/delete.

**Rollback (isolated/manual only):**

```sql
DROP INDEX IF EXISTS ux_reader_keys_project_single;
DROP TABLE IF EXISTS nexora_deleted_project_names;
DROP INDEX IF EXISTS ix_nexora_projects_auto_delete;
ALTER TABLE nexora_projects DROP COLUMN IF EXISTS auto_delete_at;
```

Rollback discards tombstone protection and any scheduled dates. It must not be used after production
reliance without a separate decision and backup.

---

## 6. Safety conclusions

- All changes are code and a forward migration; no live database was touched during this work.
- The migration is prepared but **not executed against Supabase**.
- Migrations 005, 006, and 007 remain behind the existing backup/PITR approval gate.
- The Android Reader App, legacy SMS behavior, and legacy `/api/keys/*` licensing API were not
  modified.
- Residual live-data risk: the Supabase database must be checked with the preflight queries before
  migration approval.
