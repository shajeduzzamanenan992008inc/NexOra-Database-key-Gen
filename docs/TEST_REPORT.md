# NexOra-Database-key-Gen — Test Report

Everything below was executed on the machine that built this project. Numbers are measured, not
estimated. Where something could not be exercised, it is listed under *Not covered*.

## Environment

| Item | Value |
| --- | --- |
| OS | Windows 10 (10.0.19045) |
| Python | 3.15.2 (venv at repository root) |
| Flask / pg8000 / pytest | 3.1.3 / 1.31.6 / 9.2.0 |
| Database | PostgreSQL 17.11 (portable local instance, database `nexora_keygen_test`) |
| Server under test | Werkzeug development server on `127.0.0.1:5055` |
| Browser | Chrome headless 1440×900 and 390×844, driven over CDP |

The automated suite ran against a local PostgreSQL 17.11 server, which is the same engine Supabase
runs. The hosted Supabase project was separately verified: TLS connectivity with the bundled CA,
migration application and admin bootstrap all succeeded.

---

## 1. Automated suite — 97 passed, 0 failed, 0 skipped (43.4 s)

```bash
python -m backend.cli migrate
python -m pytest -p no:cacheprovider --junitxml=.tmp/junit.xml
# 97 passed in 43.40s   (exit code 0; counts read from the JUnit XML)
```

The suite runs against a real database — no mocks, no in-memory substitute. `tests/conftest.py`
creates the schema once, truncates the application tables before each test, and provisions
`owner@`, `admin@` and `viewer@nexora.test` with an Argon2id-hashed password.

| File | Tests | Covers |
| --- | --- | --- |
| `test_unit_keys.py` | 20 | Alphabet/CSPRNG generation, entropy, formatting, prefix/suffix/separator, `KeySpec` limits, normalisation with/without separators, HMAC determinism and pepper sensitivity, masked hints, purpose-separated identifier hashing, Argon2id round trip, legacy scrypt compatibility + password policy, migration SQL splitter (dollar quotes/comments/transactions) |
| `test_auth_security.py` | 19 | Login/logout/session, wrong password, lockout, session expiry, CSRF rejection, role enforcement per endpoint, 401/403/404/405 envelopes, credential-material exposure checks, no secrets in the served bundle, SQL-injection-shaped search strings, security headers, cookie flags |
| `test_config_security.py` | 11 | Required environment variables, fail-closed startup, plaintext/placeholder hash rejection, secret redaction in `repr`, private-CA bundle validation, `.env`/`.env.example` hygiene |
| `test_admin_bootstrap.py` | 7 | First-run provisioning from the environment, Argon2id storage, idempotent restarts, preservation of an existing password/disabled state, startup integration, production guard, explicit password change with session revocation |
| `test_keys_api.py` | 13 | Plaintext revealed once then stored as a 64-char hash, custom formats, bulk limit, payload validation, masked listing/pagination/search by label/full key/status, detail with activations/events/batch, PATCH transition rules, expired-key reactivation guard, export masking + CSV formula escaping + export audit row, delete cascade, metadata preservation |
| `test_activation.py` | 12 | One-time activation, replays, multi-use limits, `409 activation_limit_reached`, device binding, account binding, expiry refusal, revoked/inactive refusal, deactivation releases slots, `used` → `active` recovery, concurrent activation conflict |
| `test_dashboard_logs.py` | 8 | Stats after generation/activation/revocation, `expiring_soon`, 24 h counters, three log kinds, filters/validation, `/api/health` DB probe, audit actor/target, delete keeps audit but removes the license, `/dashboard` requires authentication |
| `test_frontend.py` | 7 | Login and dashboard pages served (dashboard gated to sessions), every local asset resolves, CSP compatibility (no inline script/style/handlers), responsive breakpoints present, safe DOM APIs only, no secrets in HTML, NexOra branding present everywhere and old brand absent from pages and assets |

Assertions are behavioural: HTTP status, error codes, database state after the call, and
"the plaintext key does not appear anywhere else" checks, not implementation snapshots.

---

## 2. Live HTTP end-to-end smoke — 44/44 checks

A separate script (`.tmp/smoke_e2e.py`) drives the running server over HTTP with a real cookie jar
and a non-redirecting opener, against the same PostgreSQL instance. Flow, in order:

1. Public pages: `/`, `/login` (200 + HTML), `/assets/*` (200), strict CSP header present.
2. `/dashboard` **302 → /login** for anonymous visitors.
3. Unauthenticated API calls → `401`; bad password → `401 invalid_credentials`.
4. Login → `200`, `HttpOnly`/`SameSite=Strict` cookie, CSRF token issued.
5. State-changing request without `X-CSRF-Token` → `403 csrf_failed`.
6. Bulk generate 5 keys → `201`, all unique, prefix/suffix and format honoured.
7. List → masked hints only, no plaintext, correct total/pagination.
8. Detail, verify (`valid:true`, remaining activations).
9. Activate once → `200`; second device → `409 activation_limit_reached`; count persisted in DB.
10. Stats reflect the batch; generation/activation/audit logs each return rows.
11. CSV export → masked, no plaintext.
12. Deactivate → activations released; revoke → `409 key_revoked` on further activate/deactivate.
13. Owner delete → `200`; subsequent detail → `404`.
14. Logout → `200`; the old cookie → `401 session_expired`.

Result: **44/44 passed** (exit 0). Two initial failures were stale-fixture counts from earlier UI
runs, not defects: the smoke expects a truncated database, and the documented reset
(`TRUNCATE … RESTART IDENTITY CASCADE`) makes it deterministic.

---

## 3. Browser and responsive UI checks — 19/19

Chrome headless was driven over the DevTools protocol to use the real login form, the real buttons
and the real network stack (no stubbed `fetch`):

| Check | Result |
| --- | --- |
| Login page renders, design system applied (`rgb(4, 6, 13)` body) | ✅ |
| Submitting the form reaches `/dashboard` | ✅ |
| Document title, identity ("Smoke Owner / Owner"), stat values from API | ✅ |
| Busy overlay cleared, no global error banner | ✅ |
| Desktop 1440 px: no horizontal page overflow | ✅ |
| Keys table renders with pagination summary | ✅ |
| Generating 2 keys through the modal returns valid-format keys | ✅ |
| Detail dialog renders metadata and action buttons | ✅ |
| Logs view renders rows, header rebuilt per tab (6 columns) | ✅ |
| Mobile 390×844: no horizontal page overflow | ✅ |
| No console errors or uncaught exceptions | ✅ |

Screenshots were also inspected visually at both widths. That review found and fixed real defects:

- **Mobile navigation collapsed to a sliver** (`.side-nav` shrank inside a flex row, leaving a
  clipped glyph) — replaced with a 3-column grid, icons above labels, identity + sign-out on one row.
- **Logs panel header squeezed the title into a 6-line column** — header and tabs now stack, tabs
  share full width.
- **Log table columns were unreachable** — tables now have a minimum width and scroll inside their
  card instead of being clipped.

These were layout bugs only; no API behaviour changed.

---

## 4. Defects found during testing and fixed

| Defect | Fix | Verified by |
| --- | --- | --- |
| `/dashboard` was served to anonymous visitors (console markup public) | Session check + `302` redirect to `/login` | `test_frontend.py`, smoke step 2 |
| Two guard tests asserted the old public-dashboard behaviour | Rewritten to assert the redirect and to fetch `/dashboard` with an authenticated client | suite green again |
| Static assets cached for 1 h, so a deploy could serve a stale console | `max_age=0` (ETag revalidation) | `Cache-Control: no-cache, max-age=0` header check + browser run with cache cleared |
| UI status filter wrote to the wrong element (`select.dataset`) | Corrected to `statusSelect.dataset.current` | browser checks |
| Log table kept rows from the previous tab | Body cleared before each load | browser check (6-column header rebuilt) |
| `normalize_key` accepted keys shorter than the 12-character minimum | Regex `{12,96}` | `test_unit_keys.py` |
| Over-strict password assertion in a security test rejected a valid password | Assertion corrected against the documented policy | suite green |
| Expired/revoked session rows accumulated forever | `cli expire` prunes rows older than a day | `python -m backend.cli expire` run |

---

## 5. Manual/DB checks

- `python -m backend.cli migrate` on an empty database, then re-run → "Database is up to date."
- `hash-password` → single Argon2id line (the password itself is never printed); `bootstrap-admin` →
  row with Argon2id hash; a second run reports the account exists and preserves the hash;
  `list-admins` shows role and state; a weak password is rejected with policy reasons.
- SQL inspection confirmed: `key_hash` is 64 hex chars for every row, no plaintext key appears in
  `license_keys`, `generation_logs` or `admin_audit_logs`.
- Duplicate generation of the same format is unique per batch; deleting a key cascades
  `key_activations` while the audit log keeps the masked hint and previous status.
- `cli expire` marked overdue keys and printed the pruned-session count.

---

## 6. Not covered (known gaps)

- **Hosted Supabase RLS behaviour:** TLS connectivity, pooler connection and migrations 001–003 were
  verified against the hosted project, and the admin account persisted across restarts. RLS policy
  behaviour under the `anon`/`authenticated` PostgREST roles was not exercised, because the
  application connects as the database owner.
- **No load/performance testing.** No throughput or latency numbers are claimed. The pg8000 pool is
  bounded (`DB_POOL_MAX`, default 10) and `/api/health` is the readiness probe, but capacity is
  unmeasured.
- **Single browser engine.** Chrome was used; Firefox/Safari were not driven. The CSS is
  standards-based (`:focus-visible`, `prefers-reduced-motion`, CSS grid), but that is reasoning, not
  a test result.
- **Rate limiter is per process.** Tests exercise the limiter in one process; multi-worker global
  quotas are not verified (documented in `docs/SECURITY.md`).
- **No independent penetration test.** Security assurance here comes from the automated suite, the
  live smoke and code review, not third-party assessment.
- **Email delivery, MFA, password reset** are not implemented, so they are not tested.

---

## 7. Branding audit — NexOra

Executed after the rebrand on 2026-11-16.

**Scan.** All text files (`.md`, `.py`, `.html`, `.css`, `.js`, `.svg`, `.sql`, `.ini`, `.txt`,
`.toml`, `.json`, `.cfg`, `.yaml`, `.yml`, `.example`, `.bat`, `.ps1`) were searched
case-insensitively for `RH E-SPORTS`, `rhesports`, `rh-esports`, `rh_keygen`, `rh-keygen`,
`rh_session`, `RHX`, `rh_` and `rh-`, excluding `.tmp`, caches and the virtualenv.

**Result.** No old branding remains in user-facing pages, stylesheets, JavaScript, logos,
configuration, README or docs. The only remaining matches are intentional:

| Location | Reference | Why it stays |
| --- | --- | --- |
| `backend/security/keys.py` | `_DOMAIN_PREFIX = "rh-esports:v1"` | Frozen HMAC domain separator. Changing it changes every stored key/identifier hash and would invalidate all existing licenses. |
| `db/migrations/001–003` | `rh_set_updated_at()`, `rh_expire_overdue_keys()` | PostgreSQL schema objects referenced by application code; renaming requires a schema migration and has no user-facing branding effect. |
| `db/migrations/001–003` | `-- RH E-SPORTS …` header comments | Applied migrations are checksum-locked by `backend/migrations.py`; editing them makes the runner refuse startup. |
| `docs/SECURITY.md`, `docs/DEPLOYMENT.md` | mentions of the constant/function names | Document why the cryptographic constant is frozen and which DB functions exist. |
| `tests/test_frontend.py` | `OLD_BRAND` regex | The branding regression check itself. |

**Regression guard.** `test_branding_is_nexora_everywhere` loads both pages and every local asset
and asserts the old-brand regex never matches, while `NexOra`, `NexOra-Database-key-Gen` and
`NexOra Database Key Generator` are present.

**Live check.** 23/23 checks passed against the running app and hosted database: health
`ok` / `connected` / `service=nexora-keygen`, login identity/title/tagline, no old brand in served
HTML/JS/CSS/SVG, anonymous `/dashboard` → `302 /login`, CSP present.

**Compatibility.** Newly generated keys default to `NXR`; previously issued keys keep working
because verification normalises and hashes the supplied key and the frozen domain prefix is
unchanged. The session cookie is now `nexora_session`, so existing browser sessions require one
re-login; authentication, roles and CSRF enforcement are unchanged.

**Counts.** 97 pytest tests passing, including the new branding regression test.
