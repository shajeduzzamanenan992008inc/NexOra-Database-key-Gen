# NexOra-Database-key-Gen

**NexOra Database Key Generator & License Management System** — *Secure Keys. Smarter Database Management.*

Production-oriented license key system for NexOra: issues cryptographically random keys,
stores only keyed hashes, activates them one-time or multi-use, enforces expiry and activation
limits server-side, and exposes an authenticated operations console with audit logging.

- **Backend:** Python 3.11+ · Flask 3.1 · pg8000 (PostgreSQL wire protocol, TLS) · Waitress for production
- **Database:** Supabase PostgreSQL (any PostgreSQL 14+ works) — tables, constraints, indexes, RLS deny-by-default, SQL expiry function
- **Frontend:** server-served HTML, one stylesheet, vanilla ES modules. No build step, no CDN, no inline script (CSP-enforced)

Everything runs on the server: the browser never receives the key pepper, the database DSN, or
any plaintext key other than the single response that created it.

---

## 1. What it does

| Capability | Behaviour |
| --- | --- |
| Key generation | `secrets.choice` over a 32-character alphabet (no `I`, `O`, `0`, `1`), default `NXR-XXXX-XXXX-XXXX-XXXX` ≈ 80 bits of entropy, configurable prefix/suffix/groups/group length/separator |
| Bulk generation | 2 – `MAX_BULK_KEYS` keys per request, unique per batch, single transaction + `generation_logs` row |
| Storage | `HMAC-SHA256(KEY_PEPPER, normalised_key)` unique index + masked hint (`NXR-****-…-8VL3`). Plaintext is returned **once**, never persisted or logged |
| Statuses | `active`, `inactive`, `pending`, `expired`, `revoked`, `used` |
| Activation | Atomic `SELECT … FOR UPDATE`, per-device binding, per-account binding, `max_activations`, expiry check, replay-safe (same device → idempotent 200) |
| Revocation | Reason required, recorded with actor, IP and user agent; revoked/deactivated keys refuse further activation |
| Console | Overview stats, key table with filters/sort/pagination, detail dialog with activation history, generation/activation/audit logs, masked CSV export |
| Audit | `admin_audit_logs` (who/what/when/outcome/IP/UA) + `generation_logs` + `key_activations` |
| Auth | Argon2id password hashes, initial administrator provisioned from `.env` once (never reset on restart), opaque server-side sessions (hashed tokens, HttpOnly `SameSite=Strict` cookie), CSRF token bound to the session, roles `owner` / `admin` / `viewer` |

---

## 2. Quick start (local PostgreSQL)

```bash
cd NexOra-Database-key-Gen
python -m venv ../.venv                     # or any virtualenv
../.venv/Scripts/pip install -r requirements-dev.txt      # Windows; use bin/pip on macOS/Linux

cp .env.example .env                        # then edit:
#   DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/nexora_keygen?sslmode=disable
#   KEY_PEPPER=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
#   SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
#   ADMIN_EMAIL=you@example.com
#   APP_ENV=development
#   COOKIE_SECURE=false                     # local HTTP only

# Hash the admin password (prompts; the plaintext never touches .env or logs):
python -m backend.cli hash-password --email you@example.com
# Paste the printed Argon2id hash into ADMIN_PASSWORD_HASH.

python -m backend.cli migrate               # applies db/migrations/*.sql in order
python run.py                               # creates the admin once, then serves http://127.0.0.1:5000
```

Open `/`, sign in, and the console redirects to `/dashboard`. `GET /dashboard` requires a session —
anonymous visitors are redirected to `/login`.

The initial administrator is created from `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH` on first start.
Restarting the application never changes an existing password; use
`python -m backend.cli set-password --email you@example.com` for an explicit change (it also revokes
that account's sessions). `create-admin` remains available for additional accounts.

### Supabase instead of a local server

1. Create the project, then copy **Project Settings → Database → Connection string → URI**
   (session pooler, port `5432`).
2. Put it in `DATABASE_URL` with `sslmode=require` and keep `APP_ENV=production`, `COOKIE_SECURE=true`.
3. Generate the admin hash, set the environment variables, and run the migrations from a trusted
   machine:

   ```bash
   python -m backend.cli hash-password --email ops@yourdomain.com
   python -m backend.cli migrate
   ```

Migrations are idempotent and tracked in `schema_migrations`; re-running is safe.

---

## 3. Layout

```
NexOra-Database-key-Gen/
├─ backend/
│  ├─ app.py              app factory, security headers/CSP, page + asset routes, request id
│  ├─ config.py           env parsing with fail-fast validation (never logs secrets)
│  ├─ db.py               pg8000 connection pool, transaction helper, TLS
│  ├─ errors.py           uniform JSON error envelope, 500/503 hardening
│  ├─ validation.py       typed request payload validation, client IP resolution
│  ├─ migrations.py       migration runner
│  ├─ cli.py              hash-password | bootstrap-admin | set-password | migrate | expire
│  ├─ api/                auth, keys, dashboard, logs blueprints + shared helpers
│  ├─ security/           keys (generation/format/hash), passwords (Argon2id + legacy scrypt),
│  │                      sessions, decorators (auth/RBAC/CSRF), rate_limit
│  └─ services/           license_service, auth_service, admin_bootstrap, audit
├─ db/migrations/         001 schema, 002 indexes/policies, 003 password-hash docs
├─ frontend/              index.html (login), dashboard.html, assets/{app.css,js/*,logo,favicon}
├─ tests/                 97 pytest tests (unit, API, activation, security, config, bootstrap, branding)
├─ docs/                  API, SECURITY, TEST_REPORT, DEPLOYMENT
├─ run.py / wsgi.py       dev server / WSGI entrypoint
└─ .env.example
```

---

## 4. Roles

| Role | Generate | List / detail / export / stats / logs | Update | Revoke / deactivate | Delete |
| --- | --- | --- | --- | --- | --- |
| `owner` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `admin` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `viewer` | ❌ | ✅ | ❌ | ❌ | ❌ |

Roles and limits are enforced in the API layer on every request; the UI only reflects them.

---

## 5. Key lifecycle

```
generate ──▶ active ──activate──▶ used ──(limit reached)
                │  ▲
      deactivate│  │activate
                ▼  │
             inactive
   any non-revoked state ──revoke──▶ revoked (terminal)
   past expires_at ─────────────────▶ expired  (checked on read/activate, or `cli expire`)
```

- Activation trusts nothing from the caller except the key, device id and optional account id.
  Status, expiry and activation counts are read inside the transaction.
- The same device activating twice returns `200` with `replayed: true` instead of consuming a slot.
- Activation writes `key_activations` with a hashed device hint and hashed account hint (raw device
  ids are never stored).

---

## 6. Verification

```bash
python -m pytest                          # 97 tests, needs a reachable DATABASE_URL
python -m backend.cli migrate             # tests expect the schema to exist
```

Measured on this machine (PostgreSQL 17.11, Windows, Python 3.15.2): **97 passed, 0 failed, 0 skipped
in 43.4 s**. See [docs/TEST_REPORT.md](docs/TEST_REPORT.md) for the full breakdown, plus the live
HTTP smoke (44/44), browser/responsive UI checks (19/19) and the NexOra branding audit (23/23).

---

## 7. Documentation

| Document | Contents |
| --- | --- |
| [docs/API.md](docs/API.md) | Every endpoint, payload, response, status code, error envelope, rate limits |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, hashing, sessions/CSRF, RBAC, rate limiting, hardening checklist, known limits |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | What was tested, how, measured results, gaps |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Supabase setup, production server, TLS proxy, backups, key rotation, monitoring |

---

## 8. Operational notes

- **Back up `KEY_PEPPER`.** Without it no key can be looked up; changing it invalidates every
  existing key. Keep it in a secret manager, never in the repository.
- **The initial administrator is created once.** `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH` are read at
  start-up, but an existing account is never modified. Change a password only with
  `python -m backend.cli set-password`; it revokes that account's sessions.
- `python -m backend.cli expire` marks overdue keys, so `/api/keys` and stats agree even for keys
  nobody activates, and prunes session rows that expired more than a day ago. Reads already treat
  past-due keys as expired, so the key sweep is a consistency job, not a correctness requirement.
- Sessions live in `admin_sessions`; deleting rows or the whole table signs everyone out.
- Delete removes the key row and cascades its activations; the audit log keeps the history.
# NexOra-Database-key-Gen
