# OMEGA NEX-ORA — Migration 007 Report

**Migration:** `db/migrations/007_project_key_management.sql`
**Date prepared:** 2026-11-16
**Status:** **Prepared and tested only in the isolated test database. NOT executed against Supabase.**
**Historical migrations:** 001–006 unchanged.

---

## 1. Purpose

Migration 007 introduces the database guarantees required by the project/key management upgrade:

1. At most one Reader Key per project.
2. A timezone-aware scheduled deletion timestamp.
3. A tombstone registry that prevents silent reuse of a permanently deleted project name.
4. Supporting indexes and deny-by-default RLS for the tombstone table.

It does not alter legacy SMS Reader tables, licensing tables, admin accounts, sessions, settings, or
audit history.

---

## 2. Contents

### 2.1 Preflight reconciliation

The migration starts with a `DO $preflight$` block. It counts:

- live projects sharing a normalized name (`deleted_at IS NULL`, grouped by
  `project_name_normalized`);
- reader keys grouped by `project_id` with `count(*) > 1`.

If either count is greater than zero, it raises an exception naming the count and an example. The
transaction aborts; nothing is merged, deleted, or rewritten. This is intentional: ambiguous legacy
data requires a human decision.

### 2.2 One-key-per-project

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_keys_project_single
    ON reader_keys (project_id);
```

The index is non-partial: a project may own at most one row in `reader_keys`, regardless of status
(`GENERATED`, `USED`, `EXPIRED`, `REVOKED`). A USED/REVOKED/EXPIRED key is never replaced
automatically; a future replacement requires a separate authorized workflow.

PostgreSQL reports `23505` on a second insert, and the service converts that to a safe
`KEY_ALREADY_EXISTS` response.

### 2.3 Auto-delete timestamp

```sql
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS auto_delete_at timestamptz;

CREATE INDEX IF NOT EXISTS ix_nexora_projects_auto_delete
    ON nexora_projects (auto_delete_at)
    WHERE auto_delete_at IS NOT NULL AND deleted_at IS NULL;
```

`NULL` means no automatic deletion is scheduled. There is no seeded retention period; automatic
deletion stays disabled until an administrator configures `nexora.auto_delete_days` or a per-project
date.

### 2.4 Name tombstones

```sql
CREATE TABLE IF NOT EXISTS nexora_deleted_project_names (
    project_name_normalized text PRIMARY KEY,
    project_name            text NOT NULL,
    deleted_at              timestamptz NOT NULL DEFAULT now(),
    deleted_by              uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    details                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    ...
);
```

The table stores only the name and deletion metadata. It contains no plaintext key, key HMAC, device
credential, or project-owned record. Generation checks it before inserting a project; a retired name
returns `409 PROJECT_NAME_RETIRED`.

RLS is enabled and access is revoked from `PUBLIC`, `anon`, and `authenticated`; `service_role`
receives table privileges when that role exists. The backend connects as the database owner.

---

## 3. Safety properties

| Property | Implementation |
| --- | --- |
| Transactional | The runner applies the file in one transaction; the file also has `BEGIN`/`COMMIT` for manual use |
| Idempotent | `IF NOT EXISTS` for the index, column, table, and indexes; preflight is read-only |
| Checksum-managed | `backend/migrations.py` records SHA-256 and refuses changed applied migrations |
| Legacy isolation | No `sms_reader_*`, `license_keys`, `key_activations`, or `generation_logs` changes |
| No silent data repair | Duplicate preflight aborts; no merge/delete |
| FK safety | Existing `RESTRICT` FKs require explicit ordered deletion; no FK changes are made |
| Rollback documented | See section 6 |

---

## 4. Preflight status

### 4.1 Isolated test database

- The pytest fixture created/used `nexora_keygen_test` at `127.0.0.1:55432`.
- Migration 007 applied successfully after the preflight fix.
- The first attempt failed with `function min(uuid) does not exist`; the query now casts
  `project_id::text`. This was a genuine migration bug caught before any live use.
- The test database contained no ambiguous data at application time.

### 4.2 Live Supabase

**Not inspected and not modified.** The following queries must be run by an authorized operator
before approval:

```sql
-- Duplicate live normalized names
SELECT project_name_normalized, count(*)
FROM nexora_projects
WHERE deleted_at IS NULL
GROUP BY project_name_normalized
HAVING count(*) > 1;

-- Projects with more than one reader key (including legacy soft-deleted projects)
SELECT project_id, count(*)
FROM reader_keys
GROUP BY project_id
HAVING count(*) > 1;
```

If either returns rows, **stop**. Reconcile manually under a separate approved plan; do not delete or
merge data to force the migration through.

### 4.3 Approval gate

Migrations 005, 006, and 007 remain behind the Phase 3 backup/PITR verification gate. This work does
**not** clear that gate and does not authorize a live migration.

---

## 5. Deployment runbook (after approval)

1. Confirm the backup/PITR gate is signed off and a rollback snapshot exists.
2. Run the preflight queries above against Supabase; reconcile any rows.
3. Apply migrations in an approved window:

   ```bash
   python -m backend.cli migrate
   ```

4. Verify:

   ```sql
   SELECT indexname FROM pg_indexes
   WHERE tablename = 'reader_keys'
     AND indexname = 'ux_reader_keys_project_single';

   SELECT column_name FROM information_schema.columns
   WHERE table_name = 'nexora_projects'
     AND column_name = 'auto_delete_at';
   ```

5. Deploy the matching backend and frontend together (the dashboard calls the new routes).
6. Configure `auto_delete_days` only if a retention policy has been approved.
7. Install the server-side scheduler for:

   ```bash
   python -m backend.cli purge-expired-projects --limit 100
   ```

8. Monitor the first runs for `project.delete` audit events and unexpected 409 responses.

---

## 6. Rollback

Only for an isolated environment or an explicitly approved emergency rollback:

```sql
BEGIN;

DROP INDEX IF EXISTS ux_reader_keys_project_single;
DROP TABLE IF EXISTS nexora_deleted_project_names;
DROP INDEX IF EXISTS ix_nexora_projects_auto_delete;
ALTER TABLE nexora_projects DROP COLUMN IF EXISTS auto_delete_at;

COMMIT;
```

**Caveats:**

- Dropping the unique index removes the database one-key guarantee.
- Dropping `nexora_deleted_project_names` removes tombstone protection, so deleted names could be
  reused.
- Dropping `auto_delete_at` loses all schedules.
- Roll back only with a backup and a new approval; do not run destructive SQL on production without
  authorization.
- The migration runner's `schema_migrations` row must be handled deliberately if a rollback is
  intended to allow re-application. By default, the runner would see the old checksum/record and
  refuse a changed file.

---

## 7. Result

- Migration file: complete, reviewed, and validated on the isolated test database.
- Live database: untouched.
- Blockers: Phase 3 gate sign-off and live preflight reconciliation.
- Manual actions: approve/apply migration, configure retention, install the scheduler, deploy the
  matching application version.
