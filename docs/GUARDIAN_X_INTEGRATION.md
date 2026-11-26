# Optional GUARDIAN-X <-> NexOra integration

**Public repository status:** the GUARDIAN-X engine is optional and separately
distributed. It is not present in this public NexOra repository, and a clean clone
must not claim a local engine, manifest, registry entry, report set, or approval
ledger. This document describes the supported boundary; it is not evidence of a
production deployment or a live private-engine connection.

## 1. What ships in this repository

| Component | Publicly present | Purpose |
|---|---:|---|
| NexOra host application | Yes | Flask/PostgreSQL key-management platform |
| Host profile | Yes | Project identity, protected-path review guidance, CI and verification metadata |
| Agent policy | Yes | Repository scope, security invariants and approval boundaries |
| Status probe | Yes | Read-only detection and portable/local verification |
| GUARDIAN-X engine | No | Separately provisioned local analysis and governance tool |
| Link manifest and OGX state | No | Created only by an operator's local, opt-in integration |

The public repository does not vendor, download, or automatically execute the
separately distributed engine. Do not add private engine source, runtime .env,
credentials, local reports, approvals, registry files, or a machine-specific
link-manifest.json to a public commit.

## 2. Public-clone verification

From a clean public checkout:

    python scripts/guardian_x_status.py
    python scripts/guardian_x_status.py --verify

Expected behavior when GUARDIAN-X/ is absent:

- Status JSON reports guardian.status = not_installed and available = false.
- Guardian reports, approvals and registry are reported unavailable, not fabricated.
- Portable verification passes the repository-owned profile and instruction checks.
- Runtime-specific checks are marked not_required, not pass.
- No private runtime code is imported, no external registry is fabricated, and no
  local installation is claimed.

A partial or unsafe GUARDIAN-X/ directory is not treated as absent: portable
verification reports a failed runtime check. This prevents a broken or symlinked
installation from being silently accepted.

## 3. Optional local installation

An operator may separately provision a complete, trusted GUARDIAN-X drop-in at
GUARDIAN-X/. This repository does not provide or pin that private distribution.
Before running the integration, verify the source and version through its own
maintainer/release process. Do not download untrusted archives or copy private
configuration into NexOra.

The status probe requires these local files to recognize a complete drop-in:

- GUARDIAN-X/init.py
- GUARDIAN-X/core/ogx/__init__.py
- GUARDIAN-X/shield.toml

Then run the local verification commands from the repository root:

    python scripts/guardian_x_status.py
    python scripts/guardian_x_status.py --verify --local
    python GUARDIAN-X/init.py link status
    python GUARDIAN-X/init.py ogx projects list
    python GUARDIAN-X/init.py audit

The local --verify --local mode requires a valid SHA-256 link manifest, a
manifest root matching this checkout, and an external OGX registration resolving
to this same checkout. Missing runtime, manifest or registration is a failure in
local mode. Portable CI does not bypass or weaken this local gate; it tests the
public-clone contract separately.

The link manifest is checkout-path-specific. Moving a linked checkout requires a
new reviewed link plan/apply. Never hand-edit the manifest to force a path match.

## 4. Verification and integrity model

The public tests cover:

- Required profile paths actually exist in this checkout.
- Missing optional runtime is reported without an import attempt.
- Incomplete runtime directories are detected as incomplete.
- Symlinked runtime roots are rejected.
- Manifest hash checks correctly distinguish intact from modified files using a
  temporary fixture; the test never edits live managed files.
- A missing manifest is not represented as an active connection.
- Portable verification passes without private/local state.
- Local verification fails closed when the runtime is absent.
- CI skip reporting permits only the explicitly documented optional SMS Reader
  skips and rejects unrelated or incorrectly labeled skips.

A status probe is not a security certification. The profile's protectedPaths are
reviewer/agent guidance and do not implement OS-level write blocking.
GUARDIAN-X analysis, when separately installed, remains advisory until an approved
workflow explicitly authorizes a change.

## 5. Operational boundaries

- NexOra CI uses a disposable TEST_DATABASE_URL; this integration does not access
  production data.
- The status probe does not run project tests, build scripts, package managers,
  containers, browser actions, live services, or production migrations.
- No production database, migration, deployment, external webhook, scheduled task,
  or real approval email is exercised by the public-clone verification.
- No background service or scheduler is installed by this integration.
- External OGX reports and approval records remain in operator-controlled local
  state and are not portable repository artifacts.
- The Android Reader App is a separate project; this integration does not alter it.

## 6. Removal and rollback

This public NexOra repository does not include GUARDIAN-X files to remove. If an
operator has separately installed and linked a local runtime, use that runtime's
own documented link status and safe-removal workflow. Do not manually delete
managed files, edit an ownership manifest, reset the repository, or discard
unrelated local changes.

Repository-owned metadata changes are reverted through a normal reviewed Git
revert. Local OGX registration, reports, approvals and environment configuration
are separate operator state and are not removed by reverting this repository.