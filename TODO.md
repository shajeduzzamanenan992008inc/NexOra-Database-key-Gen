# NexOra OMEGA — TODO

> Status snapshot: 2026-11-21
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

### Documentation
- [x] Added `docs/PROJECT_BACKUP.md`, `docs/SCHEDULER.md`, `docs/SECURITY_WEBHOOKS.md`, and `docs/CUSTOM_ROLES.md`.
- [x] Updated `README.md` and `docs/API.md` with transfer, roles, scheduler, and migration details.

### Verified results
- [x] Full test suite: **282 passed, 26 skipped, 0 failed** (`333.61s`, exit 0).
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
- [ ] Run the optional SMS Reader suite; its 26 tests remain skipped in the current suite.

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
- The 26 skipped tests belong to the optional SMS Reader suite.
- Production migration and live infrastructure verification remain outstanding.
