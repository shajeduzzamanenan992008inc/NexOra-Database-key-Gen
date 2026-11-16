# Project Creation Failure — Root Cause Report

**Date:** 2026-11-16  
**Scope:** NexOra Admin / Database Key Generator only  
**Status:** Root cause confirmed. Code-level error handling fixed. Hosted Migration 007 is still pending.

## Executive Summary

Generate Key fails because the configured database does not have Migration 007 applied. The API is executing the required tombstone query:

```sql
SELECT project_name
FROM nexora_deleted_project_names
WHERE project_name_normalized = %s
```

That relation does not exist in the configured database, so PostgreSQL returns SQLSTATE `42P01`. The exception is not an `ApiError` or `DatabaseUnavailable`; it reached the generic Flask exception handler and became an unexplained HTTP 500.

This is not a missing route, a wrong frontend endpoint, or a wrong JSON field. The route and frontend contract already match.

## Confirmed Failure Chain

1. Admin Dashboard calls `POST /api/v1/projects/generate-key`.
2. `backend/api/v1/projects.py` parses `project_name` and `label`.
3. `ProjectService.generate_project_key()` generates a key and opens one transaction.
4. The service checks the retired-name tombstone before inserting the project.
5. `nexora_deleted_project_names` is absent because Migration 007 is not applied.
6. PostgreSQL raises SQLSTATE `42P01`.
7. The service re-raises the database exception.
8. The generic error handler returns HTTP 500 with `internal_error`.
9. No project or key is created, and no plaintext key is returned.

The transaction is rolled back by the database context manager. The tombstone lookup must not be removed or bypassed; doing so would allow permanently deleted names to be reused.

## Database Evidence

The configured `.env` target was inspected read-only. Credentials and the full connection string were not printed.

| Check | Result |
|---|---|
| Target | Supabase PostgreSQL pooler; database `postgres`; schema `public`; PostgreSQL `17.11` |
| `schema_migrations` rows | 001 through 006 only |
| `007_project_key_management.sql` recorded | **No** |
| `nexora_deleted_project_names` | **Missing** |
| `nexora_projects.auto_delete_at` | **Missing** |
| `ux_reader_keys_project_single` | **Missing** |
| `ix_nexora_projects_auto_delete` | **Missing** |
| `ix_nexora_deleted_project_names_deleted_at` | **Missing** |
| Tombstone RLS | **Missing** because the table is missing |
| `nexora_set_updated_at()` | Present |
| Live project / reader key / installation / device token counts | `0` / `0` / `0` / `0` |

The local repository contains `db/migrations/007_project_key_management.sql`; the failure is that the hosted database has not applied it.

## Code Path Findings

| Area | Finding |
|---|---|
| API route | Correct: `POST /api/v1/projects/generate-key` |
| Request fields | Correct: `project_name`, optional `label` |
| Frontend | Correct: `api.generateProjectKey()` calls the v1 route with the correct fields |
| Project service | Correctly performs the tombstone check inside the creation transaction |
| Transaction behavior | Project and key are committed together; database errors roll back |
| Duplicate behavior | Existing duplicate names return HTTP 409 and no second key |
| Retired behavior | Existing tombstones return HTTP 409 `PROJECT_NAME_RETIRED` |
| Error handling | Defect: raw missing-schema SQLSTATE became a generic HTTP 500 |
| Migration 007 | Present locally; not applied on the configured target |
| Migration 001–006 | Applied; no checksum mismatch was reported by the read-only preflight |

## Why Migration 007 Is Required

Migration 007 creates all objects the current project workflow expects:

- `nexora_projects.auto_delete_at`
- `ux_reader_keys_project_single`
- `ix_nexora_projects_auto_delete`
- `nexora_deleted_project_names`
- `ix_nexora_deleted_project_names_deleted_at`
- Tombstone checks, deny-by-default RLS, and role grants

Without the tombstone table, the current creation workflow cannot safely distinguish a new project from a retired project name.

## Root Cause Statement

**The hosted database is missing Migration 007. The application compounded that operational failure by translating the missing-relation error into a generic HTTP 500 instead of a clear migration-required response.**

## What Was Not Changed

- Migration 007 itself was not edited.
- Migrations 001–006 were not edited.
- No hosted migration, manual DDL, reset, project deletion, or data mutation was performed.
- The tombstone lookup was not removed.
- Project-name uniqueness and one-key-per-project enforcement were not weakened.
- Authentication, authorization, audit behavior, and legacy SMS Reader code were not changed.
