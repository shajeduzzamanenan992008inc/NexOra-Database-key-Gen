# NexOra-Database-key-Gen — Deployment Guide

Target: one small Linux host (or container) running Python + Waitress behind a TLS reverse proxy,
with PostgreSQL (Supabase or self-hosted). Everything here was run on the Windows test host except
the Linux service examples, which are written but were not executed.

---

## 1. Requirements

| Component | Minimum | Notes |
| --- | --- | --- |
| Python | 3.11 | tested on 3.15.2 |
| PostgreSQL | 14 | tested on 17.11; Supabase runs managed PostgreSQL |
| Web server | Waitress 3.0 (bundled) or gunicorn | development server is **not** for production |
| Reverse proxy | nginx, Caddy or equivalent | TLS termination + `X-Forwarded-For` |

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: ..\.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pins Flask 3.1, pg8000 1.31, python-dotenv 1.0, Waitress 3.0 and argon2-cffi by
compatible range. `requirements-dev.txt` adds pytest.

---

## 2. Database

### Supabase

1. Create a project; wait for provisioning.
2. **Project Settings → Database → Connection string → URI**. Use the **session pooler** (port
   `5432`) for this app: it holds long-lived connections and uses session-level advisory locks,
   which the transaction pooler (port `6543`) does not.
3. Percent-encode any special characters in the database password (`@` → `%40`, `#` → `%23`).
4. Append `?sslmode=require`. This application treats `require`, `verify-ca` and `verify-full` as
   *verified* TLS. Supabase's pooler serves a chain signed by its private
   `Supabase Root 2021 CA`, so also set `DATABASE_SSLROOTCERT=certs/prod-ca-2021.crt` (the bundle
   ships with the repository; the official copy is under **Database → SSL Configuration**).
5. Paste the URI into `DATABASE_URL`. It is server-side only — never ship it to a browser.

The migrations also revoke all privileges from Supabase's `anon` and `authenticated` roles and
enable RLS, so the public PostgREST surface cannot read or write these tables.

### Self-hosted PostgreSQL

```sql
CREATE DATABASE nexora_keygen;
CREATE ROLE nexora_keygen_app LOGIN PASSWORD '...';
ALTER DATABASE nexora_keygen OWNER TO nexora_keygen_app;
```

The role must own the schema to run migrations (it creates tables, indexes and a function). If your
policy separates DDL from runtime, run migrations as the owner once, then point the app at a role
with `SELECT/INSERT/UPDATE/DELETE` on the tables and `EXECUTE` on `rh_expire_overdue_keys()`.
Require TLS in `pg_hba.conf` if the database is not on the same host, and use `sslmode=require`.

---

## 3. Configuration

Copy `.env.example` to `.env` (or inject real environment variables — the process environment wins).
`.env` is git-ignored; never commit it.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `APP_ENV` | ✅ | `production` | `production`, `staging`, `development` |
| `DATABASE_URL` | ✅ | – | `postgresql://user:pass@host:5432/db?sslmode=require` |
| `DATABASE_SSLROOTCERT` | – | – | CA bundle (PEM) for a private CA such as Supabase's pooler; relative paths resolve from the project root |
| `KEY_PEPPER` | ✅ | – | ≥ 32 characters; placeholders (`change-me`, `example`, `<…>`) are rejected at boot |
| `SECRET_KEY` | ✅ | – | ≥ 32 random characters, never exposed to the browser |
| `ADMIN_EMAIL` | ✅ | – | Email of the initial administrator, created once on first start |
| `ADMIN_PASSWORD_HASH` | ✅ | – | Password hash from `python -m backend.cli hash-password` (Argon2id; legacy scrypt accepted); never the plaintext password |
| `ADMIN_ROLE` | – | `owner` | `owner` or `admin`; used only when the account is first created |
| `SESSION_TTL_HOURS` | – | `8` | 1–720 |
| `COOKIE_SECURE` | – | `true` in production | must be `false` for plain-HTTP local dev |
| `TRUST_PROXY` | – | `false` | `true` only behind a proxy that sets the forwarded headers |
| `RATE_LIMIT_ENABLED` | – | `true` | keep `true` in production |
| `MAX_BULK_KEYS` | – | `500` | 1–2000, per bulk request |
| `DB_POOL_MAX` | – | `10` | 1–50, per process |
| `LOG_LEVEL` | – | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

Generate the pepper and secret key once and store both in a secret manager:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Rotating the pepper makes every existing key unverifiable — back it up with the database backups.
Rotating `SECRET_KEY` invalidates any framework-signed data; this application's admin sessions are
stored server-side and are unaffected.

Generate the administrator hash interactively (the plaintext password is never echoed, written to
`.env`, or logged):

```bash
python -m backend.cli hash-password --email ops@yourdomain.com
# Paste the single Argon2id line into ADMIN_PASSWORD_HASH.
```

Invalid or missing configuration fails fast at start-up with a clear message (`ConfigError`) instead
of running insecurely. `python -m backend.cli` validates the full application environment;
`scripts/init_db.py` only needs `DATABASE_URL` (or `--database-url`) for migrations.

---

## 4. Migrations

```bash
python -m backend.cli migrate
# or
python scripts/init_db.py --database-url "postgresql://…"
```

- `001_initial_schema.sql` creates `admin_users`, `admin_sessions`, `license_keys`,
  `key_activations`, `generation_logs`, `admin_audit_logs`, constraints and the expiry function.
- `002_indexes_and_policies.sql` adds lookup/filter indexes and the RLS/privilege lockdown.
- `003_password_hash_comment.sql` updates the schema documentation for Argon2id; it changes no data.
- Applied files are recorded in `schema_migrations`; re-running prints `Database is up to date.`
  Never edit an applied migration — add a new numbered file.

Run migrations as a release step, before starting the new app version. They are safe to re-run.

---

## 5. First administrator

The initial account is provisioned from `.env` the first time the application starts (or explicitly
with `python -m backend.cli bootstrap-admin`):

```env
ADMIN_EMAIL=ops@yourdomain.com
ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
ADMIN_ROLE=owner
```

- Generate `ADMIN_PASSWORD_HASH` with `python -m backend.cli hash-password --email ops@yourdomain.com`.
  The command enforces the password policy (12–128 chars, ≥ 3 character classes, not common, no email
  local part) and prints only the hash.
- **The account is created once.** On every later start the existing row is preserved exactly,
  including its password hash and active/disabled state. Editing `ADMIN_PASSWORD_HASH` in `.env` does
  not reset an existing password.
- To change a password deliberately, use the explicit command. It verifies the policy, replaces the
  hash, clears the lockout, and revokes every session for that account:

  ```bash
  python -m backend.cli set-password --email ops@yourdomain.com
  ```

  After a change, regenerate `ADMIN_PASSWORD_HASH` if you keep the file as a disaster-recovery
  bootstrap credential.
- `--password-stdin` is available for both commands and keeps the password out of shell history and
  process arguments. Never put a plaintext password in `.env`, source code, or frontend files.
- `python -m backend.cli list-admins` shows accounts and roles. Additional administrators can be
  created with `create-admin` (roles `owner`, `admin`, `viewer`), but the environment-provisioned
  account remains the bootstrap account.

---

## 6. Run the application

### Waitress (cross-platform, recommended default)

```bash
waitress-serve --listen=127.0.0.1:8000 --threads=8 wsgi:app
```

`wsgi.py` loads `.env` and exposes `app` / `application`. Bind to loopback and let nginx terminate
TLS. Eight threads comfortably serve an admin console; raise `DB_POOL_MAX` if you raise threads
substantially (threads > pool is fine — they queue — but keep pool ≤ database connection limit).

The first start after migrations creates the initial administrator from `.env` (idempotently; see
section 5). If a required variable is missing or malformed, start-up aborts instead of serving with
an incomplete security configuration. Run migrations before the first application start.

### gunicorn (Linux)

```bash
gunicorn --workers 2 --threads 4 --bind 127.0.0.1:8000 --access-logfile - wsgi:app
```

Multiple workers are supported: sessions and lockouts live in PostgreSQL, and activation uses a row
lock, so no cross-worker coordination is needed. The one exception is the in-memory rate limiter —
each worker enforces its own budget (`docs/SECURITY.md`).

### systemd unit

```ini
[Unit]
Description=NexOra Database Key Generator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=keygen
Group=keygen
WorkingDirectory=/srv/nexora-keygen
EnvironmentFile=/etc/nexora/keygen.env
ExecStart=/srv/nexora-keygen/.venv/bin/waitress-serve --listen=127.0.0.1:8000 --threads=8 wsgi:app
Restart=on-failure
RestartSec=3

# hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/nexora-keygen/.tmp
UMask=0077

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now nexora-keygen
```

Put secrets in `/etc/nexora/keygen.env` owned by `root:keygen` with mode `0640`, not in the unit
file.

---

## 7. Reverse proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name keys.example.com;

    ssl_certificate     /etc/letsencrypt/live/keys.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/keys.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;

    client_max_body_size 1m;      # matches Flask MAX_CONTENT_LENGTH

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }
}

server {
    listen 80;
    server_name keys.example.com;
    return 301 https://$host$request_uri;
}
```

Set `TRUST_PROXY=true` **only** because this proxy overwrites `X-Forwarded-For`; otherwise a client
could spoof its IP and dodge rate limits. The app does not add HSTS itself unless
`APP_ENV=production` and `COOKIE_SECURE=true`; adding it at the proxy as above is fine (do not send
it twice with conflicting values).

If you terminate TLS elsewhere, make sure the app still sees HTTPS (so `Secure` cookies work) —
either via `X-Forwarded-Proto` + `TRUST_PROXY=true`, or by keeping `COOKIE_SECURE=true` while the
browser-facing URL is HTTPS end to end.

---

## 8. Scheduled jobs

Expiry is also enforced at read time, but the sweep keeps stored statuses accurate and prunes old
sessions:

```cron
# /etc/cron.d/nexora-keygen
17 * * * * keygen cd /srv/nexora-keygen && .venv/bin/python -m backend.cli expire >> /var/log/nexora-keygen-expire.log 2>&1
```

Output: `Marked N key(s) as expired.` and `Pruned M expired or revoked session(s).`
Run it once manually after the first deploy to confirm credentials and connectivity.

---

## 9. Health checks

`GET /api/health` (no auth):

```json
{ "status": "ok", "database": "connected", "version": "1.0.0", "service": "nexora-keygen" }
```

Returns `503` with `"status": "degraded"` when the database ping fails. Use it for readiness; the
proxy should return `502` when the process is down. It performs one pooled `SELECT 1`, so a 10-second
interval is cheap.

---

## 10. Backups and recovery

| Asset | Method | Frequency |
| --- | --- | --- |
| PostgreSQL | Supabase automated backups/PITR, or `pg_dump -Fc` off-host | daily + PITR |
| `KEY_PEPPER` | secret manager + offline copy | on creation; verify quarterly |

```bash
pg_dump -Fc "$DATABASE_URL" -f nexora-keygen-$(date +%F).dump
pg_restore -d "$DATABASE_URL" --clean --if-exists nexora-keygen-2026-11-15.dump
```

Restore drill: restore into a scratch database, run `python -m backend.cli migrate` (should report up
to date), and verify `SELECT count(*) FROM license_keys` plus one known key via `/api/keys/verify`.
A database backup without the pepper cannot verify a single key — treat them as one backup set.

---

## 11. Scaling and operations

- **Connection budget:** `instances × DB_POOL_MAX` must stay under the database limit (Supabase
  pooler tiers have their own cap). A single 1 vCPU host with `DB_POOL_MAX=10` is ample for the
  console workload.
- **Stateless app tier:** any instance can serve any request; sticky sessions are unnecessary.
- **Rate limiting:** per process. If you need a strict global quota, enforce it at the proxy/WAF or
  add a shared counter.
- **Logs:** stdout (systemd journal or container logs). Every response carries `X-Request-ID`, and
  that id appears in error responses, so a user report maps to a log line. Request bodies,
  passwords, tokens and plaintext keys are never logged.
- **Upgrades:** deploy code → run `migrate` → restart → check `/api/health`. Static assets are served
  with `no-cache` + ETag, so browsers pick up a new console immediately.
- **Key compromise:** revoke via the console (frees activations, keeps the audit trail) and issue a
  replacement from the same batch format. Deleting is for mistakes, not incidents — deletion removes
  the activations too.

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Boot: `KEY_PEPPER is required` / placeholder | missing or sample pepper | set a real 32+ char secret |
| Boot: `DATABASE_URL is not set` | env not loaded | check `EnvironmentFile` path/permissions |
| `503 database_unavailable` | wrong DSN, TLS rejected, pool exhausted, DB paused | check `dsn_summary` in logs, verify with `psql "$DATABASE_URL"`, raise `DB_POOL_MAX` |
| Boot: `Unsupported sslmode …` | DSN contains something other than the four accepted modes | use `disable`, `require`, `verify-ca` or `verify-full` (or set `DATABASE_SSLMODE`) |
| Boot: `CERTIFICATE_VERIFY_FAILED … self-signed certificate in certificate chain` | private CA not trusted (Supabase's pooler) | set `DATABASE_SSLROOTCERT=certs/prod-ca-2021.crt`; never fall back to `sslmode=disable` |
| Boot: `DATABASE_SSLROOTCERT is set but no certificate bundle exists` | relative path resolved somewhere unexpected, or the file was not copied | use an absolute path, or keep `certs/` next to `backend/` |
| Login returns 200 but the console bounces to `/login` | `Secure` cookie over plain HTTP | serve HTTPS, or set `COOKIE_SECURE=false` for local dev only |
| Every mutation returns `403 csrf_failed` | proxy strips `X-CSRF-Token`, or the page predates a session rotation | allow the header through; reload the page (`/api/auth/session` refreshes the token) |
| All clients share one rate-limit bucket | `TRUST_PROXY=false` behind a proxy | set `TRUST_PROXY=true` **and** make the proxy overwrite `X-Forwarded-For` |
| `500 internal_error` | unhandled exception | search logs for the response's `X-Request-ID`; details are intentionally not returned |
| Migration: `must be owner of table` | runtime role lacks DDL | run migrations as the owner, or grant ownership |
| `activate` returns `409 activation_conflict` | two devices raced for the last slot | retry once; the lock guarantees only one wins |
