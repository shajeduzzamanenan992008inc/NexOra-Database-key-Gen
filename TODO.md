# NexOra OMEGA — TODO

> Status snapshot: 2026-11-21
> Source: verified completion report and repository checks.
> Scope: migration 010 was tested against the local test PostgreSQL database only; it has **not** been applied to production.
> Change control: no commit, push, or deployment was performed as part of this work.

## Completed

### Backend and data
- [x] Forward-only migration 010 added for custom roles, imported-key metadata, scheduler ledger fields, and indexes. Migrations 001–009 were not edited.
- [x] Project export: single/bulk JSON and ZIP, preview counts, manifest hashes, and filtered key-metadata export.
- [x] Project import: JSON/ZIP validation, preview, `keep_existing` / `import_as_new` / `replace_existing`, transactional rollback, and imported-code is never executed.
- [x] Permanent single-project delete and Owner-only delete-all with fresh password and exact phrase.
- [x] Owner-only pre-deletion totals endpoint and UI counts before delete-all.
- [x] Fixed replace-import bug where the old project was deleted but the replacement was not inserted.
- [x] Custom role CRUD, templates, clone, assignment, anti-escalation checks, and last-Owner protection.
- [x] Standalone scheduler with advisory locking, bounded batches, idempotent jobs, ledger records, CLI, dry-run, and status.
- [x] Webhook SSRF hardening: private/loopback/metadata blocking, all-answer DNS validation, pinned connections, redirect rejection, and bounded time/size limits.

### Frontend
- [x] Export/import dialogs, conflict selection, replacement confirmation, and backup preview.
- [x] Delete and delete-all dialogs with verified blast-radius counts.
- [x] Custom-role management and administrator role assignment.
- [x] Scheduler status in the Automation view.
- [x] Bounded shutdown animation that starts after session revocation and ends at login.
- [x] Fixed the 390px reveal-modal overflow; mobile navigation works.

### Documentation
- [x] Added `docs/PROJECT_BACKUP.md`, `docs/SCHEDULER.md`, `docs/SECURITY_WEBHOOKS.md`, and `docs/CUSTOM_ROLES.md`.
- [x] Updated `README.md` and `docs/API.md` with transfer, roles, scheduler, and migration details.

### Verified results
- [x] Full test suite: **277 passed, 26 skipped, 0 failed** (`245.89s`, exit 0).
- [x] Responsive Chrome audit: **282 checks passed** across 11 viewports.
- [x] Authenticated platform smoke: **33 checks passed** against the real local app and test database.
- [x] Python compilation and JavaScript module syntax checks passed.
- [x] Migration 010 passed local integration tests.
- **Production status:** migration 010 has **not** been applied to production.

## Remaining Development

- [ ] Add automatic bulk replacement-key generation for imported key metadata, if required by product policy. Imported records currently require explicit regeneration.
- [ ] Add project-scoped permissions to custom roles. Current custom roles are project-independent.
- [ ] Confirm and implement any additional scheduled maintenance jobs beyond `automation`, `project_purge`, and `sessions` (for example, inactive-device cleanup).

## Testing & Verification

- [x] Focused transfer/roles/scheduler/SSRF regression tests passed.
- [x] Full suite and browser checks above completed with no failures.
- [ ] Run migration 010 plus restore/rollback rehearsal on staging or a production-like database.
- [ ] Run a bounded load/performance benchmark for export, import, and scheduler batches.
- [ ] Verify Firefox/Safari and physical mobile devices.
- [ ] Test a live external webhook endpoint, including real DNS/TLS failure modes.
- [ ] Run the scheduler as a long-lived cron/systemd/Task Scheduler worker.
- [ ] Run the optional SMS Reader suite; its 26 tests remain skipped in the current suite.

## Production Deployment (Blocked/Not Authorized)

- [ ] Apply migration 010 to production. Blocked: requires an approved backup/PITR checkpoint and migration window.
- [ ] Deploy the application. Blocked: deployment was not authorized.
- [ ] Configure the production scheduler worker and its batch/interval environment variables.
- [ ] Configure production secrets, `COOKIE_SECURE=true`, HTTPS webhook egress, and the CA bundle.
- [ ] Add post-deploy monitoring for scheduler failures, webhook queues, and audit events.

**Status:** No commit, push, or deployment occurred. All changes remain uncommitted in the working tree.

## Known Limitations

- Imported Reader Keys cannot be restored from plaintext or hashes; replacement keys must be generated.
- Installation credentials are intentionally not exported or restored; only device metadata can be preserved.
- Imported webhooks are created inactive and receive new signing material.
- Webhook redirects are always rejected by design; endpoints that require redirects are unsupported.
- Browser verification covered Chrome only; no physical-device or cross-browser run was performed.
- No load/performance benchmark was run, so no speedup claim is made.
- The 26 skipped tests belong to the optional SMS Reader suite.
- Production migration and live infrastructure verification remain outstanding.
