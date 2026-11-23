# NexOra OMEGA — TODO

> Status snapshot: 2026-11-22
> Source: verified completion report and repository checks.
> Scope: migration 011 was tested against the local test PostgreSQL database only; it has **not** been applied to production.
> Change control: no commit, push, or deployment was performed as part of this work.

## Completed

### Backend and data
- [x] Forward-only migration 010 added for custom roles, imported-key metadata, scheduler ledger fields, and indexes. Migrations 001–009 were not edited.
- [x] Forward-only migration 011 added for project-scoped roles, project role assignments, and imported-key replacement history. Migrations 001–010 were not edited.
- [x] Imported-key replacement generation implemented: preview, duplicate detection, partial-failure reporting, one-time plaintext reveal, transactional history, metadata preservation, warnings, and no reactivation of revoked/expired originals.
- [x] Project-scoped custom roles and assignments implemented: CRUD/filtering, project permission intersection, anti-escalation, last-Owner protection, assignment/removal, immutable built-ins, and server-side authorization.
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
- [x] Custom-role management, project scope selection, and project role assignment.
- [x] Imported-key replacement inventory, preview, generation, one-time reveal, and history.
- [x] Scheduler worker status, per-job health, and run history.
- [x] Bounded shutdown animation that starts after session revocation and ends at login.
- [x] Fixed the 390px reveal-modal overflow; mobile navigation works.
- [x] Dashboard and footer dividers now use the original `assets/omega-key-assets/svg/divider.svg`; animated WebP divider references were removed from the Classic shell.
- [x] Restored generator selection feedback: project, key-type and expiry selects replay `is-picked`; key-type changes replay `type-pick`, expiry presets flash, and reduced-motion/lite-mode remain respected.

### Documentation
- [x] Added `docs/PROJECT_BACKUP.md`, `docs/SCHEDULER.md`, `docs/SECURITY_WEBHOOKS.md`, and `docs/CUSTOM_ROLES.md`.
- [x] Updated `README.md` and `docs/API.md` with transfer, roles, scheduler, and migration details.

### Verified results
- [x] Full test suite: **343 collected, 318 passed, 25 skipped tests plus 1 module-level SMS console skip (26 skip reports), 0 failed** after Reader lifecycle and generator regression coverage. All 26 skip reports are the optional legacy SMS Reader console (25 test-level skips plus one module-level import skip); one SMS database-only constraint test executes and passes. No NexOra Reader API test is skipped.
- [x] Reader lifecycle: **17 new executable tests** covering activation validation, URL contract, replay/cross-project rejection, device binding, digest-only storage, project states, concurrent activation, one-installation rule, heartbeat/status, installation revocation, rate limiting, and parameterized queries.
- [x] Generator regression: exact Classic create-project → generate-key flow, bulk quantity, custom/never expiry, retry after failed type validation, and batch idempotency.
- [x] Imported replacement/project role focused suite: **5 passed, 0 failed**.
- [x] Responsive Chrome audit: **282 checks passed** across 11 viewports (pre-existing legacy dashboard audit; new platform views were smoke-tested at desktop and mobile navigation only).
- [x] Authenticated platform smoke: **35 checks passed** against the real local app and test database, covering all 22 views.
- [x] Python compilation and JavaScript module syntax checks passed.
- [x] Migration 010 passed local integration tests.
- [x] Migration 011 applied to the local test database; focused project-role/imported-replacement tests passed.
- **Production status:** migration 011 has **not** been applied to production.

## Testing & Verification

- [x] Focused transfer/roles/scheduler/SSRF/imported-replacement regression tests passed.
- [x] Full suite and browser checks above completed with no failures.
- [ ] Run migration 011 plus restore/rollback rehearsal on staging or a production-like database.
- [ ] Run a bounded load/performance benchmark for export, import, and scheduler batches.
- [ ] Verify Firefox/Safari and physical mobile devices.
- [ ] Test a live external webhook endpoint, including real DNS/TLS failure modes.
- [ ] Run the scheduler as a long-lived cron/systemd/Task Scheduler worker.
- [x] Audited the optional SMS Reader suite: all 26 tests skip because the repository does not contain or install the `sms_reader` console package. They cannot be enabled without implementing an out-of-scope legacy console; the real NexOra `/api/v1/reader` lifecycle is covered instead.

## Production Deployment (Blocked/Not Authorized)

- [ ] Apply migration 011 to production. Blocked: requires an approved backup/PITR checkpoint and migration window.
- [ ] Deploy the application. Blocked: deployment was not authorized.
- [ ] Configure the production scheduler worker and its batch/interval environment variables.
- [ ] Configure production secrets, `COOKIE_SECURE=true`, HTTPS webhook egress, and the CA bundle.
- [ ] Add post-deploy monitoring for scheduler failures, webhook queues, and audit events.

**Status:** No commit, push, or deployment occurred. All changes remain uncommitted in the working tree.

## Known Limitations

- Imported Reader Keys cannot be restored from plaintext or hashes; independent replacement keys must be generated.
- Installation credentials are intentionally not exported or restored; only device metadata can be preserved.
- Imported webhooks are created inactive and receive new signing material.
- Webhook redirects are always rejected by design; endpoints that require redirects are unsupported.
- Browser verification covered Chrome only; no physical-device or cross-browser run was performed.
- No load/performance benchmark was run, so no speedup claim is made.
- The SMS Reader console produces 26 skip reports (`sms_reader` package absent): 25 skipped test functions plus one module-level import skip. One SMS database-only constraint test does execute. Skips are reported as skips, never as passes. NexOra Reader API coverage is executable and does not depend on that package.
- Production migration and live infrastructure verification remain outstanding.
