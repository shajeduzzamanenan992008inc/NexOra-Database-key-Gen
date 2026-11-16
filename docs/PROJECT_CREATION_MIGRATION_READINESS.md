# Project Creation — Migration Readiness

**Date:** 2026-11-16  
**Migration:** `007_project_key_management.sql`  
**Hosted target:** Not ready; migration not applied  
**Hosted migration approval:** Not granted

## Current Status

| Layer | Status |
|---|---|
| Application code | Ready for controlled testing |
| Migration 007 file | Present locally |
| Local isolated test database | Ready; 007 applied by test fixture |
| Configured hosted target | **NOT READY**; 001–006 applied, 007 pending |
| Hosted migration approval | **NOT APPROVED** |
| Backup/PITR gate | Not verified in this task; separate approval required |

Generate Key now returns a clear HTTP 503 while 007 is pending. It still cannot create projects or keys on the hosted target until the migration is applied.

## Migration 007 File

| Property | Value |
|---|---|
| Path | `db/migrations/007_project_key_management.sql` |
| Git status | Tracked |
| SHA-256 | `12dba36dbb58b568350109106726c57b83be0fce30c50eabb8b5f05998e47944` |
| Runner checksum prefix | `12dba36dbb58` |
| Historical migrations 001–006 | Unchanged |

Migration 007 creates or adds:

- `nexora_projects.auto_delete_at`
- `ux_reader_keys_project_single`
- `ix_nexora_projects_auto_delete`
- `nexora_deleted_project_names`
- `ix_nexora_deleted_project_names_deleted_at`
- Tombstone constraints, RLS, grants, and comments
- Preflight checks that abort on duplicate live names or multiple keys per project

No historical migration was modified. No alternative table or bypass was added.

## Read-Only Preflight Command

```bash
python -m backend.cli migration-status
```

The command is read-only. It does not run `apply_migrations`, create `schema_migrations`, execute DDL, or change data.

It prints the target host, port, database, and SSL mode without the username, password, or full connection URL.

### Current configured-target result

```text
Target: Supabase pooler host (region redacted):5432/postgres sslmode=require
Applied: 001_initial_schema.sql, 002_indexes_and_policies.sql,
         003_password_hash_comment.sql, 004_sms_reader_keys.sql,
         005_nexora_projects.sql, 006_rename_rh_function.sql
Pending: 007_project_key_management.sql
007: present=True recorded=False checksum=12dba36dbb58

tombstone_table:   MISSING
auto_delete_at:    MISSING
one_key_index:     MISSING
auto_delete_index: MISSING
tombstone_index:   MISSING
tombstone_rls:     MISSING

NOT READY
```

`nexora_set_updated_at()` is present.

Live project, reader key, installation, and device-token counts are all zero. Migration 007's data preflight would have no duplicate-name or multiple-key conflicts at this time.

## Database Target Verification

### Application resolution order

- `Settings.from_env()` reads `DATABASE_URL` from the process environment.
- `run.py` and `wsgi.py` call `load_dotenv()` before creating the app.
- Python-dotenv's default is `override=False`, so an existing process-level `DATABASE_URL` wins over `.env`.
- `backend.cli` explicitly loads the repository-root `.env`, then uses the same `Settings.from_env()`.

### Verification status

| Item | Status |
|---|---|
| `.env` target used by this read-only inspection | Confirmed: Supabase PostgreSQL pooler, `postgres`, schema `public` |
| Server version / timezone | `17.11` / `UTC` |
| Full connection string, username, password | Not printed |
| Running service process environment | **NOT VERIFIED** |
| Running service `DATABASE_URL` override | Possible by code; not observed |
| Service manager definition in repository | None found |

The running service may use a different target if its process environment overrides `.env`. Confirm the effective target in the service manager before approving migration. Do not print the connection string.

## Readiness Gate

Migration 007 may be considered ready for an authorized controlled run only when all of the following are true:

1. The migration file remains at checksum `12dba36dbb58b568350109106726c57b83be0fce30c50eabb8b5f05998e47944`.
2. The running service's effective target is confirmed to be the intended database.
3. `migration-status` reports no pending migration and no checksum mismatch.
4. Backup/PITR or an approved restorable off-host backup is verified.
5. `KEY_PEPPER` recovery material is securely accounted for.
6. A human administrator gives separate explicit migration approval.
7. A maintenance window is approved for the unique-index creation.

## Manual Steps After Separate Authorization

These steps were **not** performed in this task:

1. Verify the effective service target.
2. Verify backup/PITR and recovery material.
3. Confirm `migration-status` and the 007 checksum.
4. Run `python -m backend.cli migrate` only under separate explicit authorization.
5. Re-run `python -m backend.cli migration-status`; require `READY`.
6. Perform one controlled Generate Key operation and verify:
   - one project
   - one reader key
   - no plaintext stored at rest
   - audit events recorded
   - duplicate name returns 409
   - retired name remains blocked

## Final Distinction

**A. Code fix completed.** Missing-schema failures now produce safe 503 responses, and the project/key transaction remains atomic.

**B. Database migration still pending.** The configured hosted target is missing 007 and all of its objects.

**C. Hosted database migration approved or not approved.** **Not approved.** No hosted DDL, manual SQL, migration command, reset, or project deletion was performed.
