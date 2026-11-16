# OMEGA NEX-ORA — Project & Key Management Test Report

**Date:** 2026-11-16
**Environment:** Windows, Python 3.15, PostgreSQL isolated test instance
**Database:** `postgresql://postgres@127.0.0.1:55432/nexora_keygen_test?sslmode=disable`
**Live Supabase:** **not contacted and not modified**
**Working tree:** uncommitted; no commit, push, or deployment performed

---

## 1. Commands and actual output

### 1.1 Baseline (before changes)

```text
$ python -m pytest -q -p no:warnings
170 passed, 26 skipped
exit 0
```

### 1.2 Targeted project/key management suites

```text
$ set -o pipefail; python -m pytest -q -p no:warnings \
    tests/test_phase2_dashboard.py tests/test_project_key_management.py

...........................................                              [100%]
EXIT_STATUS=0
```

43 tests passed. This covers 18 rewritten Phase 2 dashboard tests plus 25 new regression tests.

### 1.3 Full suite (after changes)

```text
$ set -o pipefail; python -m pytest -p no:warnings --tb=short

........................................................................ [ 32%]
........................................................................ [ 64%]
.................................sssssssssssssssssssssssss.............. [ 96%]
.......                                                                  [100%]
198 passed, 26 skipped in 174.52s (0:02:54)
EXIT_STATUS=0
```

### 1.4 Backend compile

```text
$ python -m compileall -q backend
exit 0
```

### 1.5 Migration 007 in the isolated test database

The pytest app fixture calls `apply_migrations(...)` with the repository's migration directory. On
the isolated test database, migration 007 was applied transactionally and its checksum recorded in
`schema_migrations`.

The first attempt exposed a real migration bug:

```text
function min(uuid) does not exist
```

The preflight query now uses `min(project_id::text)`. This was fixed before the final green run. No
live database was involved.

---

## 2. Coverage by requirement

### Generate Key (`TestGenerateKeyWorkflow`)

| Requirement | Test |
| --- | --- |
| New project automatically created | `test_generate_creates_project_and_exactly_one_key` |
| Exactly one key generated | `test_generate_creates_project_and_exactly_one_key` |
| Plaintext returned only once | `test_plaintext_is_returned_only_once`, `test_no_plaintext_in_duplicate_or_listing_responses` |
| Duplicate project name rejected | `test_case_insensitive_duplicate_rejected_without_second_key` |
| Case-insensitive duplicate rejected | same test (`"  case   PROJECT  "`) |
| Existing project never gets a second key | `test_existing_project_never_receives_second_key` (route absent + DB `23505`) |
| Concurrency cannot duplicate project/key | `test_concurrent_requests_create_one_project_and_one_key` (2 threads → 1 success, 1 × 409) |
| Bulk route cannot bypass the one-key rule | `test_bulk_and_project_id_generation_routes_are_removed` |

### Key lifecycle (`TestReaderKeyLifecycle`)

| Requirement | Test |
| --- | --- |
| First activation changes `GENERATED → USED` | `test_first_activation_marks_key_used_and_second_is_rejected` |
| Second activation rejected | same test (`409 ACTIVE_INSTALLATION_EXISTS`) |
| Used key remains used after deactivation/reactivation | `test_deactivation_blocks_credentials_and_preserves_used_key` |
| Deactivated project rejects new activation | `test_deactivated_project_rejects_new_activation` |
| Deactivated project rejects existing credentials/heartbeat | `test_deactivation_blocks_credentials_and_preserves_used_key` (`403 PROJECT_INACTIVE`) |
| Expired key cannot activate and status persists | `test_expired_key_persists_expired_status` |
| Revoked key cannot activate | `test_revoked_key_cannot_activate` |
| Used key cannot create a replacement installation | `test_used_key_cannot_create_replacement_installation` (`410 KEY_ALREADY_USED`) |
| Legacy `database_url` alias remains valid | `test_legacy_database_url_alias_still_works` |

### Auto-delete (`TestAutoDelete`)

| Requirement | Test |
| --- | --- |
| Date saved, returned, listed, and cleared | `test_schedule_is_saved_returned_and_cleared` |
| Past date rejected | `test_past_schedule_is_rejected` |
| Only due scheduled projects are purged | `test_purge_only_deletes_due_scheduled_projects` |
| No schedule means no deletion | `test_no_schedule_means_no_automatic_deletion` |

### Permanent delete (`TestPermanentDelete`)

| Requirement | Test |
| --- | --- |
| Single project physically removed | `test_single_delete_removes_owned_records_and_preserves_audit_and_sms` |
| Key/installations/device tokens removed | same test (all counts verified zero) |
| Device credential stops working | same test (`401`) |
| Audit survives with safe snapshot | same test (`project_id IS NULL`, snapshot name) |
| Unrelated project remains | same test |
| Legacy SMS row unchanged | same test |
| Exact-name, owner, and CSRF checks | `test_delete_requires_exact_name_owner_and_csrf` |
| Deleted name cannot be reused | tombstone assertions + `PROJECT_NAME_RETIRED` |
| Tombstone contains no key material | column inspection in the delete test |

### Delete All (`TestDeleteAllProjects`)

| Requirement | Test |
| --- | --- |
| All projects and owned records physically removed | `test_delete_all_removes_projects_and_preserves_system_data` |
| Admin accounts/settings preserved | same test (counts before/after) |
| Audit history and delete-all event preserved | same test (`project.delete_all`, count, names) |
| Legacy SMS tables unchanged | same test |
| Every name tombstoned and blocked | same test |
| Wrong phrase / wrong password / non-owner rejected | `test_delete_all_requires_phrase_password_and_owner` |
| Transaction rollback on database failure | `test_delete_all_rolls_back_on_database_failure` (trigger-induced failure → 500; project/key still present; no tombstone/audit) |

### Security (`TestProjectSecurity`)

| Requirement | Test |
| --- | --- |
| CSRF required | `test_generate_key_requires_csrf`, plus Phase 2 tests |
| Role checks | `test_generate_key_forbidden_for_viewer`, `test_delete_requires_exact_name_owner_and_csrf` |
| No plaintext in later responses | `test_no_plaintext_in_duplicate_or_listing_responses`, `test_plaintext_is_returned_only_once` |
| No key hashes in responses | same tests |
| SQL injection resistance | `test_project_name_sql_injection_is_treated_literally` |
| Safe error handling | delete-all wrong password/rollback tests |
| CSP/XSS | `tests/test_frontend.py` (existing suite, still green) |

### Frontend and regression coverage

`tests/test_frontend.py` and `tests/test_phase2_dashboard.py` verify:

- no inline styles or inline event handlers;
- CSP-safe DOM construction;
- no credential/secret leakage in HTML or JS;
- the removed Create Project/bulk UI is absent;
- settings include `auto_delete_days`;
- security center does not expose password hashes or service-role secrets.

---

## 3. Test count breakdown

| Suite | Result |
| --- | --- |
| Full suite | 198 passed, 26 skipped |
| Rewritten Phase 2 dashboard suite | 18 passed |
| New project/key management suite | 25 passed |
| SMS Reader suite | 26 skipped (optional module not installed) |
| Failures / errors | 0 |

---

## 4. What is not covered

- No live Supabase migration or live project deletion was executed.
- No production concurrency/load test was run; concurrency is covered by a two-thread transaction
  test plus the PostgreSQL unique index and advisory lock.
- No browser automation was run; frontend verification is static/DOM-focused.
- The optional SMS suite was skipped because the SMS module is not installed in this environment.
- Scheduler installation (cron/systemd/Task Scheduler) is an operational action, not tested by the
  Python suite.
- Migration 007's live-data preflight depends on the actual Supabase contents, which were not
  inspected.

---

## 5. Conclusion

The implemented behavior is verified against the isolated test database and the full existing suite:
Generate Key creates exactly one project and one key, one-key-per-project is enforced in PostgreSQL,
lifecycle and deactivation behavior are preserved, auto-delete only affects due scheduled projects,
single and bulk deletion are transactional/hard deletes with surviving audit and legacy-table
isolation. Live migration and live deletion remain pending explicit approval.
