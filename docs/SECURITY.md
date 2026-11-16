# NexOra-Database-key-Gen — Security Overview

This document describes what the system actually enforces, the reasoning behind each control, and
the residual risks an operator must own. Every claim here corresponds to code in `backend/` and is
covered by a test in `tests/` unless explicitly noted under *Known limitations*.

---

## 1. Threat model

| Attacker | Goal | Primary controls |
| --- | --- | --- |
| Network eavesdropper | Session cookie, credentials, plaintext keys | TLS (deployment), `Secure` + `HttpOnly` + `SameSite=Strict` cookies, `no-store` on all API responses, HSTS when production cookie is secure |
| Stolen database dump | Forge or look up license keys | Keys stored only as `HMAC-SHA256(KEY_PEPPER, key)`; pepper lives in the environment/secret manager, never in the DB |
| Stolen session cookie | Act as an administrator | Opaque 32-byte tokens, only SHA-256 digests stored, TTL (default 8 h), server-side revocation on logout, DB row deleted/expired |
| Cross-site request forgery | Mutate keys from a victim's browser | `SameSite=Strict` cookie **and** session-bound CSRF token on every non-`GET` request |
| Brute-force login | Guess admin passwords | Argon2id hashing, 10 logins/min per IP, 5 failed attempts → 15 min DB-backed lockout, constant-time compare, uniform error message |
| Key guessing / enumeration | Find a working key | 80-bit default entropy over a 32-symbol alphabet, per-IP rate limits, identical `valid:false` response for unknown keys |
| Malicious admin (least privilege failure) | Delete keys / escalate | Role separation (`viewer` < `admin` < `owner`), delete restricted to `owner`, every action audited |
| Injection | Read or destroy data | Every query uses bound parameters; sort/filter values come from allow-lists |
| XSS | Steal CSRF token or act in console | CSP `script-src 'self'`, no inline script/handlers, all DOM writes via `textContent` |

Out of scope: a compromised host or secret manager, a malicious database administrator, and
physical access to the server.

---

## 2. License keys

**Generation.** `secrets.choice` over `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` — 32 symbols, no `I`, `O`,
`0`, `1`, so keys survive being read aloud or typed. Default `NXR-XXXX-XXXX-XXXX-XXXX` = 16 random
characters = **80 bits**. The format validator enforces 12–64 random characters (60–320 bits) and a
24-character prefix+suffix budget, so a weak format cannot be requested through the API or UI.

**Storage.** Only `key_hash = HMAC-SHA256(KEY_PEPPER, "rh-esports:v1:license-key:" + normalized)`
plus a masked hint (`NXR-****-****-****-8VL3`) is stored. The `rh-esports:v1` domain prefix is a
frozen cryptographic constant: changing it would invalidate every key hashed before the NexOra
rename, so it is deliberately not rebranded. A deterministic keyed digest is the correct
construction here: keys must be looked up by value, a password hash cannot be indexed, and without
the pepper the digest cannot be reversed or precomputed. The unique index on `key_hash` makes
duplicates impossible; generation retries on collision.

**Plaintext exposure.** Returned exactly once, in the response that created it. It is never written
to `generation_logs`, `admin_audit_logs`, application logs, or the CSV export. Normalisation strips
spaces, `-` and `_` and upper-cases, so a key pasted without separators still resolves.

**Pepper.** Required, ≥ 32 characters, placeholders (`change-me`, `example`, `<…>`) rejected at
start-up — the app refuses to boot rather than run with a guessable pepper. Rotating it invalidates
lookup of **every** existing key (the hashes become unverifiable), so treat it as permanent and back
it up in a secret manager.

**Device and account identifiers.** Raw HWIDs/account ids are never stored: they are hashed with the
same pepper and a purpose prefix (`device`, `account`) and only a 4-character fingerprint is kept for
display. Activation IPs and user agents are recorded for audit (administrator's IP on admin
actions; the activating client's IP on activations).

---

## 3. Authentication and sessions

- **Password storage:** Argon2id (64 MiB, 3 iterations, 4 lanes, 16-byte random salt, 32-byte tag) for
  every new or changed password. Legacy `scrypt$…` hashes remain verifiable so an upgrade does not
  lock out an existing administrator, but new hashes are always Argon2id. Verification fails closed
  for malformed or unsupported hashes. Policy: 12–128 characters, ≥ 3 character classes, not a known
  weak password, must not contain the email local part.
- **Initial administrator:** provisioned once from `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH` in the
  server-side environment on first start. Only an Argon2id (or legacy scrypt) hash is accepted; a
  plaintext password or placeholder in that variable aborts start-up. If the account already exists,
  the row — password hash, role and active state — is left untouched, so restarting the application
  or editing `.env` cannot silently reset a password. Changing a password requires the explicit
  `python -m backend.cli set-password` operation, which revokes that account's sessions.
- **Unknown accounts** are compared against a fixed dummy hash so response timing does not reveal
  whether an email exists. Errors are identical for wrong password and unknown email.
- **Lockout:** 5 failed attempts within the window → `locked_until = now + 15 min`, persisted in
  `admin_users`, so it survives restarts and is shared by all workers. Locked accounts get
  `429 account_locked` with `Retry-After`.
- **Sessions:** 32 random bytes (`secrets.token_urlsafe(32)`) returned once; the DB stores only
  `SHA-256(token)`. Rows carry user, role snapshot, CSRF digest, IP, user agent, created/last-seen
  and expiry. `SESSION_TTL_HOURS` (default 8) bounds lifetime; expired rows are rejected by
  `find_session` and pruned by `python -m backend.cli expire`. Logout sets `revoked_at` (immediate
  revocation). `GET /api/auth/session` issues a fresh CSRF token
  for that session, so a long-open console recovers instead of failing.
- **Environment validation:** `DATABASE_URL`, `KEY_PEPPER`, `SECRET_KEY`, `ADMIN_EMAIL` and
  `ADMIN_PASSWORD_HASH` are required at start-up. Missing, malformed, or placeholder values raise
  `ConfigError` and prevent the process from serving traffic. Secret values are excluded from
  `repr()` and never included in API responses, audit rows, or application logs.
- **Environment protection:** `.env` is git-ignored and `.env.example` contains placeholders only.
  The frontend is served as static files with no templating or environment interpolation, so no
  server-side variable can reach browser JavaScript. `SECRET_KEY` is used only inside the server
  process.
- **Cookies:** `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` when `COOKIE_SECURE=true`
  (default in production). No token is ever exposed to JavaScript.
- **Bearer tokens:** the same opaque session token may be sent as `Authorization: Bearer …` for
  server-to-server use; CSRF is not required there because there is no ambient browser authority.

## 4. Authorization

Roles are checked on every request in the decorator, not in the UI:

| Capability | viewer | admin | owner |
| --- | --- | --- | --- |
| Read keys, stats, logs, export | ✅ | ✅ | ✅ |
| Generate / bulk-generate | ❌ | ✅ | ✅ |
| Update (status, limits, expiry, label) | ❌ | ✅ | ✅ |
| Revoke / deactivate | ❌ | ✅ | ✅ |
| Delete | ❌ | ❌ | ✅ |

`/dashboard` itself is session-gated: anonymous visitors get a `302` to `/login`, so the console
markup is not served publicly. The UI hides actions a role cannot perform, but the server rejects
them regardless.

## 5. Request integrity

- **CSRF:** cookie-authenticated non-`GET` requests must send `X-CSRF-Token`; the digest is compared
  with `hmac.compare_digest` against the session's stored digest. Failure → `403 csrf_failed`. The
  token is not a cookie, so a cross-site form post cannot supply it; `SameSite=Strict` blocks the
  cookie from being sent in the first place.
- **Validation:** a typed `Payload` validator checks presence, type, length, ranges, patterns, enums,
  ISO-8601 timestamps and unknown fields per endpoint; failures are `422` with per-field messages.
  Query parameters (`sort`, `order`, `status`, `kind`, `outcome`) are allow-listed.
- **SQL:** every value is a bound parameter. The only interpolated SQL fragments are column names
  taken from `SORT_COLUMNS` and table names from module constants.
- **Rate limiting:** sliding 60-second window per IP, per route class (see `docs/API.md`). Login is
  additionally protected by the DB lockout, which is the cross-worker defence.
- **Body size:** JSON bodies are capped by Flask (`MAX_CONTENT_LENGTH`), metadata is capped at 20 keys
  and 4 kB, labels at 80 characters, search at 200.

## 6. Response hardening

Every response carries:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
  font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self';
  frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
X-Request-ID: <hex>
```

`Cache-Control: no-store` on all `/api/*` responses and on the CSV export; static assets are
revalidated (`no-cache`) so a deploy cannot leave a stale console in a browser cache.
`Strict-Transport-Security` is added only when `APP_ENV=production` and `COOKIE_SECURE=true`.

Unhandled exceptions return a generic `500 internal_error` with a request id; stack traces, SQL and
secrets stay in server logs. `DatabaseUnavailable` returns `503` without exposing the DSN — logs use
`dsn_summary`, which omits the password, and `DatabaseSettings.password` is excluded from `repr`.

## 7. Audit trail

| Table | Contents | Retention |
| --- | --- | --- |
| `admin_audit_logs` | action (`auth.login`, `admin.bootstrap`, `admin.password_change`, `key.generate`, `key.activate`, `key.deactivate`, `key.revoke`, `key.delete`, `key.update`, `key.export`, …), actor email/role, target type/id, outcome, IP, user agent, JSON details, timestamp | indefinite |
| `generation_logs` | batch id, quantity, format, prefix/suffix, label, max activations, expiry, generator | indefinite |
| `key_activations` | key, hashed device/account hints, status, activate/deactivate timestamps and reasons, IP, user agent | until key deleted (cascade) |

Failed logins, failed activations and failed administrative actions are recorded with
`outcome = failure`. CSRF and role rejections happen before the handler runs, so they are not audit
rows; they are returned as `403` with a request id that correlates to the server log. Deleting a key removes the key and its activations; the audit row keeps the
masked hint, previous status and batch id, so the deletion itself remains accountable.

## 8. Database controls

- Migrations create the schema, foreign keys, `CHECK` constraints on status/enum columns, and indexes
  for lookup, filters and log queries (`001`, `002`).
- `002` enables RLS on every application table and **revokes all privileges from `PUBLIC`, `anon` and
  `authenticated`** (Supabase's PostgREST roles), denying by default.
- `rh_expire_overdue_keys()` is a plain SQL function with `EXECUTE` revoked from `PUBLIC`, `anon`
  and `authenticated`, so only the owner connection can sweep expired keys.
- Activation is a single transaction with `SELECT … FOR UPDATE` on the key row, so two concurrent
  activations cannot both consume the last slot; the loser gets `409 activation_conflict` and can
  retry.
- Connections use TLS whenever `sslmode != disable`; `require`, `verify-ca` and `verify-full` are all
  implemented as *verified* TLS with hostname checking. The trust store is the system roots plus the
  optional `DATABASE_SSLROOTCERT` bundle (required for Supabase's private pooler CA; see
  `certs/README.md`). Verification is never downgraded to make a connection work.

## 9. Known limitations

These are deliberate, documented trade-offs — not hidden gaps:

1. **Rate limiting is per process.** The sliding window lives in memory, so with `N` workers each
   enforces its own budget. Login lockout (DB) is global; API limits are not. Put a shared limiter
   (proxy/WAF/Redis) in front for strict global quotas. Documented in `backend/security/rate_limit.py`.
2. **Activation and verification are unauthenticated by design.** A game launcher cannot hold an
   admin credential. They are rate limited, return uniform responses, and store only hashes. For
   stronger guarantees, add a per-product shared secret or signed request from the client and verify
   it in `POST /api/keys/activate`.
3. **No MFA and no self-service password reset.** The bootstrap account comes from `.env`; additional
   accounts are provisioned by `create-admin` on a trusted machine. Passwords change only through
   `set-password` (or an authenticated administrator flow in a future release). Adding TOTP is the
   natural next step.
4. **RLS is not the enforcement point for this backend.** The app connects as the database owner,
   which bypasses RLS. RLS protects Supabase's `anon`/`authenticated` API surface; the API's own
   authorization is what protects this application.
5. **Decryption is impossible, not merely disabled.** A lost `KEY_PEPPER` means every key must be
   reissued; there is no recovery path.
6. **No key rotation for the pepper.** Because lookup is deterministic, rotation would require a
   re-hash migration (decrypt is impossible), so plan for a long-lived pepper.
7. **Dev server.** `run.py` uses Werkzeug; production must use `wsgi.py` behind Waitress/gunicorn +
   TLS (`docs/DEPLOYMENT.md`).
8. **Not penetration-tested.** Verification is the automated suite plus a live HTTP smoke and browser
   checks (`docs/TEST_REPORT.md`); an independent review is still recommended before public launch.
9. **Supabase-specific behaviour was not exercised here.** The schema is plain PostgreSQL and was
   validated on PostgreSQL 17.11 locally; pooler/SSL specifics follow documented behaviour but were
   not run against a live Supabase project in this environment.

## 10. Operator checklist

- [ ] `APP_ENV=production`, `COOKIE_SECURE=true`, `RATE_LIMIT_ENABLED=true`
- [ ] `KEY_PEPPER` and `SECRET_KEY` ≥ 32 random characters from a secret manager, backed up offline,
      never in git
- [ ] `ADMIN_EMAIL` set and `ADMIN_PASSWORD_HASH` generated with `cli hash-password` (never a
      plaintext password); `.env` mode `0640` or stricter
- [ ] `DATABASE_URL` uses `sslmode=require` (or stronger) and a least-privilege role where possible
- [ ] TLS terminated in front of the app; `TRUST_PROXY=true` only if the proxy overwrites `X-Forwarded-For`
- [ ] Migrations applied; `anon`/`authenticated` grants still revoked (`\dp` in psql)
- [ ] One `owner`, at most a few `admin`s, `viewer` for anyone who only needs reports
- [ ] `python -m backend.cli expire` scheduled (e.g. hourly) and backups verified
- [ ] Audit log shipped to durable storage / monitored for `outcome = failure` spikes
