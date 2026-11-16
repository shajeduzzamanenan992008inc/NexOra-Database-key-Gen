# Project Creation Failure — Test Report

**Date:** 2026-11-16  
**Test database:** Isolated local PostgreSQL test database configured by `tests/conftest.py`  
**Hosted database:** Not used for destructive tests

## Results

| Run | Result |
|---|---|
| Focused regression suite | **17 passed**, 0 failed, 0 skipped |
| Full suite | **208 passed**, 0 failed, **26 skipped** |
| Total collected for full suite | **234** |

Commands:

```bash
python -B -m pytest tests/test_project_creation_schema_errors.py \
  tests/test_migration_readiness.py tests/test_frontend.py -q

python -B -m pytest -q -rA -p no:warnings
```

## Skipped Tests

All 26 skips are in the legacy SMS Reader suite because the separate `sms_reader` module is not present in this repository environment:

| Area | Skipped |
|---|---:|
| `test_sms_auth.py` | 1 |
| `test_sms_frontend.py` | 6 |
| `test_sms_keys.py` | 9 |
| `test_sms_reader.py` | 10 |

These tests were **not executed** and are **not claimed as passing**. No SMS Reader source was changed. Historical SMS behavior therefore remains unverified by this run, while the relevant code paths were untouched.

## New/Updated Tests

### `tests/test_project_creation_schema_errors.py`

| Test | Purpose |
|---|---|
| `test_missing_tombstone_returns_safe_migration_503` | SQLSTATE `42P01` for the tombstone table returns 503 `DATABASE_MIGRATION_REQUIRED`, includes the 007 filename, and exposes no SQL, relation name, DSN, or traceback |
| `test_missing_007_column_is_also_classified_as_migration_required` | SQLSTATE `42703` for `auto_delete_at` maps to 007 rather than a generic 500 |
| `test_unrelated_database_error_is_not_misclassified` | SQLSTATE `08006` stays HTTP 500 `internal_error`, even when its message mentions the tombstone table |
| `test_generation_transaction_rolls_back_when_key_insert_fails` | A forced `reader_keys` insert failure leaves zero projects and zero keys and returns no plaintext key |

### `tests/test_migration_readiness.py`

| Test | Purpose |
|---|---|
| `test_missing_relation_maps_to_007` | Missing tombstone relation maps to Migration 007 |
| `test_missing_column_maps_to_007_before_its_relation` | Column mapping wins over the containing table mapping |
| `test_unrelated_error_is_not_a_schema_issue` | Unique violation is not classified as schema readiness |
| `test_migration_007_declares_all_application_requirements` | 007 contains the tombstone table, column, indexes, RLS, and grants |
| `test_inspect_migrations_reports_migrated_test_database_ready` | Read-only inspector reports the fully migrated local test database as ready |

### `tests/test_frontend.py`

`test_dashboard_generate_key_uses_canonical_endpoint_and_fields` verifies:

- `api.js` calls `/api/v1/projects/generate-key`
- the dashboard calls `api.generateProjectKey`
- the request body uses `project_name` and `label`

## Requirement Coverage

| # | Requirement | Coverage | Result |
|---:|---|---|---|
| 1 | Missing tombstone table → safe 503 | New missing-tombstone test | Passed |
| 2 | Missing migration → clear response | New 503 tests plus readiness inspector | Passed |
| 3 | Unrelated database error not misclassified | New `08006` test | Passed |
| 4 | Successful project + key creation | Existing generate-key tests | Passed |
| 5 | Duplicate normalized name → 409 | Existing duplicate and concurrent tests | Passed |
| 6 | Retired name → 409 | Existing single-delete and delete-all tests | Passed |
| 7 | Project/key rollback on failure | New forced key-insert failure test; existing delete-all rollback | Passed |
| 8 | Exactly one key per project | Existing API, concurrency, and database unique-index tests | Passed |
| 9 | No plaintext on failed transaction | New rollback test asserts no `plaintext_key` and no `NXR-` value | Passed |
| 10 | Dashboard Generate Key integration | New frontend endpoint/field test and existing API tests | Passed |
| 11 | Admin authentication and authorization | Existing CSRF, viewer-forbidden, and owner/admin tests | Passed |
| 12 | Historical SMS Reader unaffected | 26 SMS tests skipped because `sms_reader` is absent | **Not verified** |

## Notes

- The test fixture applies migrations only to the isolated local test database. It does not touch the hosted Supabase target.
- The new tests do not use real credentials, `DATABASE_URL`, or hosted data.
- The full suite produced pre-existing `ResourceWarning: unclosed file` warnings from Flask test-client file responses; they did not affect any result.
- No test failure was suppressed to obtain a passing result.
