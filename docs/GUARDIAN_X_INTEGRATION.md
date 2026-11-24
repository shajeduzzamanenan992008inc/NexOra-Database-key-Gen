# GUARDIAN-X ↔ NexOra host integration

**Status:** configured and locally verified against the repository checkout. This is
not a production deployment and does not authorize production data access.

## 1. What is connected

| Component | Location | Purpose |
|---|---|---|
| Host project | repository root | NexOra Flask/PostgreSQL key-management platform |
| Shield runtime | `GUARDIAN-X/` | OMEGA GUARDIAN X `0.8.0a1` analysis, scanner, Guardian, policy and linker |
| Host profile | `.github/project-profile.json` | verified NexOra identity, criticality, protected paths, boundaries and CI checks |
| Agent policy | `AGENTS.md` | authoritative repository instructions for coding agents |
| Copilot policy | `.github/instructions/nexora-guardian.instructions.md` | path-aware repository guidance |
| Link manifest | `GUARDIAN-X/link-manifest.json` | SHA-256 ownership of files created by OGX's opt-in linker |
| External registry | OGX state outside the repository | persistent project shortcut used by `ogx ... --project nexora` |
| Reports and approvals | OGX external state | scans, Guardian reports and the local approval ledger |

The integration deliberately keeps the engine and reports outside the host code
path. The shield is the project control folder; it is not a second application.

## 2. Connection commands

From the repository root:

```bash
python GUARDIAN-X/init.py status
python GUARDIAN-X/init.py link status
python GUARDIAN-X/init.py ogx projects list
python scripts/guardian_x_status.py
python scripts/guardian_x_status.py --verify --local
```

`ogx projects list` is expected to contain a registration whose path resolves to
this checkout. The status command reports the registry entry, link manifest
integrity, last scan/audit/maintenance reports, finding counts, pending approvals,
shield policy, and capability limits. It is read-only and does not print secrets.

The link manifest stores the absolute project root. If the repository is moved, the
manifest hashes can remain intact while the host-path check reports a mismatch;
re-run a fresh `link plan`/`apply` after reviewing the target path. Do not edit the
manifest by hand.

## 3. Host profile

The host profile is `.github/project-profile.json`, not the generic template inside
the shield distribution. It records:

- Project name, purpose, Flask/PostgreSQL/static-console architecture, Python
  version, entry points and deployment targets.
- High criticality and a strict security baseline.
- Authentication, session, key, activation, device, webhook, audit, scheduler,
  migration, CI and secret-handling boundaries.
- Protected paths and critical components for reviewer/agent attention.
- A mandatory PostgreSQL test database (`TEST_DATABASE_URL`) and focused CI checks.
- The Android Reader App as a separate repository, not host code.

The profile is metadata and review guidance. It is **not** an OS sandbox or a
server-side write control. Nexus application authorization remains in the Flask
services and database constraints.

## 4. Protection model: what is real

Implemented and verifiable:

- OGX scanner, inventory, architecture, compatibility, SBOM, license inventory and
  Guardian audit run read-only and write reports outside the host.
- `shield.toml` is restrict-only: file writes, network and execution are disabled
  by default.
- Link-managed files are created create-exclusive and recorded with SHA-256 hashes.
  `link status` detects modified, missing and unsafe entries.
- `link remove` deletes only files whose current hash still matches; modified files
  are preserved for review.
- The scanner redacts secret values in findings/reports.
- The profile and agent instructions require explicit approval for protected paths,
  production actions, dependency changes, migrations, commits, pushes and deploys.

Not implemented by this integration:

- Per-path write blocking or an OS-level policy engine. `protectedPaths` is review
  and agent guidance, not an enforced firewall.
- Running project tests, builds, package managers, containers, browsers or live
  services during a normal scan.
- A WAF, runtime exploitation test, complete SAST/DAST, or penetration test.
- Automatic vulnerability fixes, automatic patches, or automatic merging/upgrades.
- A claim that the project is secure, compliant or production-ready.

Use code review, branch protection, CI, least-privilege credentials and the existing
NexOra security tests for enforcement. Guardian findings are evidence to investigate,
not proof of exploitability.

## 5. Governed bug-fix workflow

1. Detect a finding or reproduce a failure with evidence.
2. Identify the affected route/service/database/UI path and root cause.
3. Run `ogx impact` for a static dependant estimate when useful.
4. Build a remediation plan with security, data and regression risks.
5. Obtain explicit approval for protected-path, production or destructive changes.
6. Implement the smallest coherent fix; do not modify unrelated files.
7. Add regression tests, run focused checks, then the relevant full suite.
8. Run browser verification when the change is user-visible and an isolated test
   database/browser environment is available.
9. Review the diff for secrets and unrelated changes.
10. Report exact tests, results, unresolved risks and rollback steps.

Useful read-only commands:

```bash
python GUARDIAN-X/init.py audit
python GUARDIAN-X/init.py ogx guardian maintain .
python GUARDIAN-X/init.py ogx guardian repair-plan .
python GUARDIAN-X/init.py ogx debug . --logs <sanitized-log>
python GUARDIAN-X/init.py ogx test-plan .
python GUARDIAN-X/init.py ogx impact . --file backend/services/project_service.py
```

`guardian repair` is deliberately narrow: this alpha only applies allowlisted safe
fixes such as `.env` ignore patterns, with an external before-image. It does not
generate arbitrary source patches.

## 6. Evolution workflow

```bash
python GUARDIAN-X/init.py ogx guardian evolve .
```

The evolution output is a proposal, not a self-rewrite. Prioritize critical security
and data integrity first, then production reliability, bugs, test gaps, dependency
health, performance, UX and maintainability. Every proposal needs a stated problem,
benefit, risk, impact analysis, test plan and rollback. Changes to Guardian-X core,
approval behavior or protection policy require a separate reviewed proposal.

## 7. Automation

No scheduler or background service is installed by this integration. A read-only
audit can be run by an operator-controlled cron/Task Scheduler entry, for example:

```bash
python scripts/guardian_x_status.py >/dev/null
python GUARDIAN-X/init.py audit
```

Automation rules:

- Default to read-only; reports go to external OGX state.
- Use bounded intervals and avoid overlapping runs.
- Never auto-apply patches, delete files, change migrations/secrets, commit, push,
  publish or deploy.
- Alert on failures rather than silently retrying destructive actions.

The status command reports the latest available scan/audit/maintenance report but
does not prove that a scheduled job is currently running. Check the external report
timestamps and the operator scheduler separately.

## 8. Agent integration

`AGENTS.md` is authoritative for this repository. The Copilot instruction file
supplements it. Generic `.github/agents` and `.github/instructions` files remain
project policy and are not silently replaced by OGX. Existing root instruction
files are preserved by the linker; only missing files are created.

Guardian analysis and external AI output are untrusted inputs. They cannot override
NexOra's security rules, database rules, repository boundary or approval gates.

## 9. Removal and rollback

Preview first:

```bash
python GUARDIAN-X/init.py link status
python GUARDIAN-X/init.py link remove --confirm
```

Removal deletes only unchanged OGX-created link files and leaves modified files,
`AGENTS.md`, the host profile, and the shield folder intact. Removing the external
registration is a separate operator action in OGX state; do not edit repository
source to hide a registration. The shield folder itself is not removed by `link
remove`; remove it only under an approved repository decision.

Rollback of host-specific metadata is a normal reviewed revert of the profile,
agent instructions, status/verification script and documentation. Do not reset the
repository or discard unrelated user changes.

## 10. Known limitations and owner actions

- The current status/verification command has no web dashboard; it is JSON/CLI.
- The link manifest is checkout-path-specific by design.
- External OGX registration and reports are not portable in a fresh clone.
- The CLI email approval gate is not configured in this environment; future
  mutating OGX operations should use the owner's configured approval mailbox.
- `.github/workflows/config-validation.yml` references a missing
  `_dev/tools/validate_config.rb` path and is not a passing CI control until the
  owner repairs or removes it. This integration did not fabricate a replacement.
- No production database, migration, deployment or external webhook was exercised.
- Browser/UI verification for a future change remains the normal NexOra workflow.
