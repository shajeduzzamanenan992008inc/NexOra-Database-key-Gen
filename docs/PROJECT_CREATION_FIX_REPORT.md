# Project Creation Failure — Fix Report

**Date:** 2026-11-16  
**Scope:** Code-level error handling and read-only migration readiness only  
**Hosted database migration:** Not applied; not approved

## A. Code Fix Completed

The application now distinguishes a missing migration from an unrelated internal failure. A missing required schema object returns a safe HTTP 503 instead of a generic HTTP 500.

## Files Changed

| File | Change |
|---|---|
| `backend/schema_readiness.py` | New classifier for PostgreSQL missing-schema SQLSTATEs and known application objects |
| `backend/errors.py` | Safe `DATABASE_MIGRATION_REQUIRED` handler, request-ID logging, schema classification ordering |
| `backend/migrations.py` | New read-only `inspect_migrations()` for migration checksums, pending files, and 007 object presence |
| `backend/cli.py` | New `python -m backend.cli migration-status` command; no migration execution |
| `tests/test_project_creation_schema_errors.py` | Missing-table, missing-column, unrelated-error, and generation-rollback tests |
| `tests/test_migration_readiness.py` | Classifier, Migration 007 content, and read-only readiness tests |
| `tests/test_frontend.py` | Canonical Generate Key endpoint/field integration assertion |
| `docs/PROJECT_CREATION_*.md` | Root-cause, fix, test, and readiness reports |

## Exact Code-Level Changes

### 1. Missing-schema classification

`backend/schema_readiness.py` recognizes:

- `42P01` — missing relation
- `42703` — missing column
- `42883` — missing function
- `3F000` — missing schema

It maps known objects to their creating migration. The tombstone table, `auto_delete_at`, and the 007 indexes map to:

```text
007_project_key_management.sql
```

Classification is SQLSTATE-first. A unique violation (`23505`), connection failure, or other unrelated error is not converted into a migration error merely because its message contains a table name.

### 2. Safe HTTP response

`backend/errors.py` now returns:

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json
X-Request-ID: <correlation-id>

{
  "error": {
    "code": "DATABASE_MIGRATION_REQUIRED",
    "message": "A required database migration is pending. Apply the approved migration, then retry.",
    "request_id": "<correlation-id>",
    "details": {
      "schema_ready": false,
      "migration": "007_project_key_management.sql"
    }
  }
}
```

The browser receives no SQL, database host, connection string, password, stack trace, or internal exception text.

The server log records safe diagnostics with the same request ID:

```text
database migration required request_id=<id> sqlstate=42P01 \
  object=nexora_deleted_project_names migration=007_project_key_management.sql
```

The previous generic-handler request-ID lookup used `request.request_id`, which was not where the application stored the ID. It now uses `g.request_id`, matching `X-Request-ID` and the response payload.

### 3. Preserved behavior

| Behavior | Status |
|---|---|
| Duplicate normalized project name | Unchanged: HTTP 409 `PROJECT_ALREADY_EXISTS` |
| Permanently deleted name | Unchanged: HTTP 409 `PROJECT_NAME_RETIRED` |
| Authentication | Unchanged |
| Owner/admin authorization | Unchanged |
| CSRF enforcement | Unchanged |
| Tombstone lookup | Retained; not bypassed |
| One project + one key transaction | Unchanged |
| One-key-per-project database enforcement | Unchanged |
| Plaintext key returned only after commit | Unchanged |
| Audit events | Unchanged |
| Legacy SMS Reader code | Unchanged |

No project is created and no key is returned when the schema check fails. The service's existing transaction context rolls back.

### 4. Read-only migration preflight

The new command:

```bash
python -m backend.cli migration-status
```

prints the configured host, port, database, and SSL mode without the username, password, or full connection URL. It queries:

- `schema_migrations` rows and local checksums
- pending migration files
- expected 007 objects and tombstone RLS

It does not create `schema_migrations`, execute migration SQL, or change any database object.

## B. Database Migration Still Pending

The read-only preflight against the configured target returned:

```text
Applied: 001 through 006
Pending: 007_project_key_management.sql
007 recorded: false
tombstone_table: MISSING
auto_delete_at: MISSING
one_key_index: MISSING
auto_delete_index: MISSING
tombstone_index: MISSING
tombstone_rls: MISSING
NOT READY
```

While 007 is pending, Generate Key now returns a clear 503 rather than an unexplained 500. It still cannot create a project or key on that target.

## C. Hosted Migration Approved or Not Approved

**Not approved.** No hosted migration was executed. Migration 007 still requires:

1. Verified backup/PITR or an approved restorable off-host backup.
2. A secure `KEY_PEPPER` recovery copy.
3. Confirmation that the running service uses the intended database target.
4. Separate explicit human authorization.
5. A controlled migration run and post-migration verification.

## Readiness Distinction

- **Code fix:** Complete.
- **Hosted schema:** Migration 007 still missing.
- **Hosted migration approval:** Not granted.

Project creation is ready for a controlled test only in an environment where 007 has been applied. It is not fully fixed on the hosted target until that approved migration is completed.
