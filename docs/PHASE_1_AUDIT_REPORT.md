# NexOra Phase 1 Audit Report

**তারিখ:** ২০২৬-১১-১৬  
**অডিটর:** Automated Audit Agent  
**Repository:** `d:\FTP\NexOra-Database-key-Gen`  
**Commits Inspected:** `902cf16` (first commit) → `2e0cd24` (Update files)  
**উদ্দেশ্য:** Phase 1 implementation gap বিশ্লেষণ এবং NexOra রিব্র্যান্ডিং মূল্যায়ন

---

## সারসংক্ষেপ (Executive Summary)

| বিভাগ | অবস্থা |
|---|---|
| Core Flask Architecture | ✅ DONE |
| Security Primitives (Argon2id, HMAC, Sessions) | ✅ DONE |
| License Key Generation & Management | ✅ DONE |
| Admin Dashboard API | ✅ DONE |
| Audit Logging | ✅ DONE |
| Brand Rename (RH E-SPORTS → NexOra) | ⚠️ PARTIAL |
| SMS Reader Module | ❌ BROKEN (scope violation + missing module) |
| Phase 1 NexOra Tables (projects, reader_keys, etc.) | ❌ NOT DONE |
| `/api/v1/` Versioning | ❌ NOT DONE |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` Config | ⚠️ PARTIAL |
| Test Suite | ❌ BLOCKED (sms_reader import ব্যর্থ) |

---

## ১. বিদ্যমান আর্কিটেকচার (Existing Architecture)

### স্ট্যাক
- **Framework:** Flask 3.1+ (application factory pattern)
- **Database Driver:** pg8000 ≥ 1.31 (pure-Python PostgreSQL driver)
- **Database:** Supabase PostgreSQL (connection via `DATABASE_URL`)
- **Password Hashing:** Argon2id (argon2-cffi ≥ 23)
- **Production Server:** Waitress 3.0+
- **Config:** python-dotenv

### ফাইল স্ট্রাকচার (পরীক্ষিত)

```
backend/
├── app.py           — Flask factory, security headers, CSP, blueprint registration
├── config.py        — Settings dataclass, fail-fast env validation
├── db.py            — Connection pool (pg8000), cursor context manager
├── migrations.py    — SQL file runner (db/migrations/ ফোল্ডার থেকে)
├── errors.py        — ApiError, ValidationError, error handlers
├── serialization.py — JSON serialization helpers
├── validation.py    — Request payload validation
├── cli.py           — hash-password, set-password CLI commands
├── security/
│   ├── keys.py      — HMAC-SHA256 key generation/hashing (NXR- prefix)
│   ├── passwords.py — Argon2id hash/verify
│   ├── sessions.py  — Opaque token sessions (server-side)
│   ├── decorators.py— @require_auth, role guards
│   └── rate_limit.py— Sliding window rate limiter
├── services/
│   ├── license_service.py — LicenseService (generate, activate, revoke, list)
│   ├── auth_service.py    — AuthService (login, logout, session management)
│   ├── admin_bootstrap.py — Initial admin account provisioning
│   └── audit.py           — write_audit() helper
└── api/
    ├── auth.py      — POST /api/auth/login, POST /api/auth/logout, GET /api/auth/session
    ├── keys.py      — POST /api/keys/generate, GET /api/keys/, GET /api/keys/<id>, etc.
    ├── dashboard.py — GET /api/dashboard/stats
    └── logs.py      — GET /api/logs/
```

### Database Migrations (বিদ্যমান)

| ফাইল | বিষয়বস্তু |
|---|---|
| `001_initial_schema.sql` | admin_users, admin_sessions, license_keys, key_activations, generation_logs, admin_audit_logs |
| `002_indexes_and_policies.sql` | Performance indexes, RLS policies |
| `003_password_hash_comment.sql` | Comment update (Argon2id নোট) |
| `004_sms_reader_keys.sql` | sms_reader_keys, sms_reader_events টেবিল (scope-বাইরে) |

---

## ২. বিদ্যমান ফিচারসমূহ (Existing Features)

### ✅ DONE — সম্পূর্ণ কাজ করছে

#### ২.১ Authentication System
- `POST /api/auth/login` — email/password login, Argon2id verification
- `POST /api/auth/logout` — session revocation
- `GET /api/auth/session` — session validation, CSRF refresh
- Cookie-based session (HttpOnly, SameSite, Secure in production)
- CSRF double-submit cookie protection
- Account lockout (failed_login_attempts tracking)

#### ২.২ License Key Management
- `POST /api/keys/generate` — bulk key generation (1-500 keys per request)
- Key format: `NXR-XXXX-XXXX-XXXX-XXXX` (80-bit entropy, OS CSPRNG)
- HMAC-SHA256 keyed digest stored (plaintext কখনো store হয় না)
- `GET /api/keys/` — paginated list with filtering, sorting, search
- `GET /api/keys/<id>` — key detail + activations + audit events
- `POST /api/keys/<id>/revoke` — key revocation
- `POST /api/keys/activate` — device activation (row-level locking)
- `POST /api/keys/verify` — key status check (unauthenticated)
- `GET /api/keys/export` — CSV export (up to 10,000 rows)
- `DELETE /api/keys/<id>` — key deletion

#### ২.৩ Security Headers
- Content-Security-Policy (strict, no unsafe-inline)
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- Referrer-Policy: no-referrer
- Permissions-Policy (camera, mic, geo, payment disabled)
- HSTS (production only)
- Cross-Origin-Opener-Policy / Cross-Origin-Resource-Policy

#### ২.৪ Rate Limiting
- Sliding window limiter (per-IP)
- Trust-proxy aware (X-Forwarded-For)

#### ২.৫ Admin Dashboard
- `GET /api/dashboard/stats` — key counts by status, recent activity
- `GET /api/logs/` — audit log pagination

#### ২.৬ Frontend
- `frontend/index.html` — Login page
- `frontend/dashboard.html` — Admin dashboard SPA
- `frontend/assets/js/` — api.js, dashboard.js, login.js, ui.js
- Server-side session guard on `/dashboard` route

---

## ৩. সমস্যা সমূহ (Issues Found)

### 🚨 সমস্যা ১: Brand Conflict — "RH E-SPORTS" নাম

**অবস্থা:** ⚠️ PARTIAL

#### পাওয়া গেছে:

**`db/migrations/001_initial_schema.sql` (line 2):**
```sql
-- RH E-SPORTS — Secure Key Generator & License Management System
```

**`db/migrations/001_initial_schema.sql` (line 178):**
```sql
CREATE OR REPLACE FUNCTION public.rh_set_updated_at()
```
*(ফাংশনের নাম `rh_` prefix — RH E-SPORTS এর ব্র্যান্ড)*

**`backend/security/keys.py` (line 129):**
```python
_DOMAIN_PREFIX = "rh-esports:v1"
```
> ⚠️ **গুরুত্বপূর্ণ সতর্কতা:** এই string পরিবর্তন করলে **সব বিদ্যমান license key hash invalidate হবে**। ফাইলেই comment আছে: *"Changing this string changes every stored HMAC"*. Database-এ যদি real data থাকে তাহলে এটি পরিবর্তন করা যাবে না।

**বিদ্যমান NexOra নাম (ইতিমধ্যে সঠিক):**
- `backend/app.py` line 37: `logging.getLogger("nexora.access")`
- `backend/app.py` line 149: `"service": "nexora-keygen"`
- `backend/config.py` line 265: `session_cookie_name: str = "nexora_session"`
- `backend/config.py` line 274: `brand_name: str = "NexOra-Database-key-Gen"`
- `backend/security/keys.py` line 45: `prefix: str = "NXR"` (NexOra prefix)
- `.env.example` line 2: `NexOra-Database-key-Gen`

**করণীয়:**
1. `001_initial_schema.sql` এর header comment আপডেট করুন
2. `rh_set_updated_at()` ফাংশন rename করুন → `nexora_set_updated_at()`
3. `_DOMAIN_PREFIX` — **পরিবর্তন করবেন না** (existing data breaks)

---

### 🚨 সমস্যা ২: SMS Reader — Scope Violation

**অবস্থা:** ❌ SCOPE VIOLATION

Phase 1-এ SMS Reader সিস্টেম থাকার কথা নয়। কিন্তু রিপোজিটরিতে নিম্নলিখিত SMS-সম্পর্কিত artifacts আছে:

| আইটেম | অবস্থান | সমস্যা |
|---|---|---|
| `run_sms.py` | root | SMS Flask app entry point |
| `wsgi_sms.py` | root | SMS WSGI entry point |
| `frontend/sms/` | frontend | SMS dashboard HTML/JS/CSS |
| `db/migrations/004_sms_reader_keys.sql` | db/migrations | sms_reader_keys + sms_reader_events টেবিল |
| `tests/test_sms_auth.py` | tests | SMS auth tests |
| `tests/test_sms_frontend.py` | tests | SMS frontend tests |
| `tests/test_sms_keys.py` | tests | SMS key tests |
| `tests/test_sms_reader.py` | tests | SMS reader tests |
| `conftest.py` lines 20-21 | tests | `from sms_reader import create_sms_app` |

**করণীয়:** এই ফাইলগুলো পরবর্তী phase-এ সরাতে হবে অথবা একটি `sms/` branch-এ রাখতে হবে।

---

### 🚨 সমস্যা ৩: Missing Module — `sms_reader` Package

**অবস্থা:** ❌ BROKEN / BLOCKED

**`tests/conftest.py` lines 20-21:**
```python
from sms_reader import create_sms_app
from sms_reader.config import SmsConfig
```

কিন্তু `sms_reader/` package **রিপোজিটরিতে নেই**। ফলে:
- `pytest` চালালে import error হবে
- **সমস্ত 13টি test file collect করা যাবে না**
- এমনকি SMS-সম্পর্কিত নয় এমন tests-ও fail করবে (conftest.py সব test-এর জন্য load হয়)

**চেক করা ফাইল তালিকায় কোনো `sms_reader/` directory নেই।**

---

### 🚨 সমস্যা ৪: Phase 1-এ চাওয়া NexOra Tables নেই

**অবস্থা:** ❌ NOT DONE

Phase 1 specification অনুযায়ী প্রয়োজনীয় tables:

| Table | উদ্দেশ্য | বিদ্যমান? |
|---|---|---|
| `projects` | Reader project management | ❌ নেই |
| `reader_keys` | Project-based reader keys | ❌ নেই |
| `reader_installations` | Device installation tracking | ❌ নেই |
| `device_tokens` | Device authentication tokens | ❌ নেই |

**বিদ্যমান tables (Phase 1-এর বাইরের):**

| Table | উদ্দেশ্য | Phase 1 সম্পর্কিত? |
|---|---|---|
| `admin_users` | Admin accounts | ✅ হ্যাঁ |
| `admin_sessions` | Server-side sessions | ✅ হ্যাঁ |
| `license_keys` | License key storage | ✅ হ্যাঁ |
| `key_activations` | Device activations | ✅ হ্যাঁ |
| `generation_logs` | Key generation history | ✅ হ্যাঁ |
| `admin_audit_logs` | Audit trail | ✅ হ্যাঁ |
| `sms_reader_keys` | SMS activation keys | ❌ SMS scope |
| `sms_reader_events` | SMS event log | ❌ SMS scope |

---

### 🚨 সমস্যা ৫: Config Mismatch — Supabase Keys

**অবস্থা:** ⚠️ PARTIAL

**`.env.example` তে আছে (lines 115-116):**
```env
SUPABASE_URL=https://REPLACE_PROJECT_REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=REPLACE_WITH_SUPABASE_SERVICE_ROLE_KEY
```

**কিন্তু `backend/config.py`-তে নেই:**
- `Settings.from_env()` এ `SUPABASE_URL` বা `SUPABASE_SERVICE_ROLE_KEY` read বা validate করা হয় না
- Application এই values load করে না
- শুধু `DATABASE_URL` ব্যবহার হয় (Supabase connection pooler URL হিসেবে)

**অর্থাৎ:** `.env.example`-এ placeholder হিসেবে রাখা হয়েছে, কিন্তু backend এগুলো ব্যবহার করে না। Phase 1-এ যদি Supabase REST API বা RLS ব্যবহার করতে হয়, তাহলে config-এ যোগ করতে হবে।

---

### 🚨 সমস্যা ৬: API Versioning — `/api/v1/` prefix নেই

**অবস্থা:** ❌ NOT DONE

**বিদ্যমান API prefix:**
```python
bp = Blueprint("auth", __name__, url_prefix="/api/auth")
bp = Blueprint("keys", __name__, url_prefix="/api/keys")
bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")
bp = Blueprint("logs", __name__, url_prefix="/api/logs")
```

**Phase 1-এ প্রয়োজন:** `/api/v1/auth/`, `/api/v1/keys/`, ইত্যাদি।

**বর্তমান endpoints:**
- `POST /api/auth/login`
- `GET /api/keys/`
- `GET /api/dashboard/stats`
- `GET /api/health` (health check, unversioned — ঠিক আছে)

**পরিবর্তনের প্রভাব:** সব client/frontend কোডেও URL আপডেট করতে হবে।

---

### 🚨 সমস্যা ৭: Reader Authentication API Architecture Mismatch

**অবস্থা:** ❌ NOT DONE

**বিদ্যমান `sms_reader_keys` টেবিল:**
- Project-based নয় — `project_id` foreign key নেই
- `reader_name` field আছে, কিন্তু project hierarchy নেই
- `device_token_hash` সরাসরি key-তে stored (Phase 1-এর separate `device_tokens` table নয়)

**Phase 1-এর প্রয়োজনীয় architecture:**
```
projects → reader_keys → reader_installations → device_tokens
```

---

## ৪. Security Analysis

### ✅ ভালো দিক

| বিষয় | মূল্যায়ন |
|---|---|
| Password hashing | Argon2id ✅ (industry best practice) |
| Key storage | HMAC-SHA256 keyed digest only, plaintext কখনো store হয় না ✅ |
| Session management | Server-side opaque tokens, SHA-256 stored ✅ |
| CSRF protection | Double-submit cookie + X-CSRF-Token header ✅ |
| SQL injection | Parameterized queries throughout ✅ |
| Rate limiting | Sliding window, per-IP ✅ |
| Security headers | Comprehensive (CSP, HSTS, X-Frame-Options, etc.) ✅ |
| Config validation | Fail-fast on startup, placeholder detection ✅ |
| SSL | sslmode=require by default, CA cert support ✅ |
| Audit trail | All key operations logged ✅ |
| Row-level locking | Activation uses `SELECT FOR UPDATE` ✅ |

### ⚠️ মনোযোগ দেওয়া প্রয়োজন

| বিষয় | ব্যাখ্যা |
|---|---|
| `_DOMAIN_PREFIX = "rh-esports:v1"` | পুরানো brand name কিন্তু **পরিবর্তন করা যাবে না** (data integrity) |
| `rh_set_updated_at()` DB function | Rename করলে migration দরকার |
| `conftest.py` import error | Test infrastructure ভাঙা |
| SMS key table in schema | Phase 1 scope-এর বাইরের table production DB-তে আছে |

---

## ৫. Database Status

### বিদ্যমান Schema

```sql
-- Migration 001 (RH E-SPORTS branded header)
admin_users           ✅ সম্পূর্ণ
admin_sessions        ✅ সম্পূর্ণ (audience column Migration 004-এ added)
license_keys          ✅ সম্পূর্ণ
key_activations       ✅ সম্পূর্ণ
generation_logs       ✅ সম্পূর্ণ
admin_audit_logs      ✅ সম্পূর্ণ

-- Migration 004 (SMS scope violation)
sms_reader_keys       ⚠️ scope বাইরে
sms_reader_events     ⚠️ scope বাইরে
```

### Phase 1-এ যা চাই কিন্তু নেই

```sql
-- Migration 005 (তৈরি করতে হবে)
projects              ❌ নেই
reader_keys           ❌ নেই
reader_installations  ❌ নেই
device_tokens         ❌ নেই
```

---

## ৬. Test Status

**অবস্থা:** ❌ BLOCKED

### Test Files (13টি)

| ফাইল | বিষয় | চলবে? |
|---|---|---|
| `test_activation.py` | Key activation | ❌ BLOCKED (conftest import fails) |
| `test_admin_bootstrap.py` | Admin setup | ❌ BLOCKED |
| `test_auth_security.py` | Auth security | ❌ BLOCKED |
| `test_config_security.py` | Config validation | ❌ BLOCKED |
| `test_dashboard_logs.py` | Dashboard/logs | ❌ BLOCKED |
| `test_frontend.py` | Frontend serving | ❌ BLOCKED |
| `test_keys_api.py` | Keys API | ❌ BLOCKED |
| `test_unit_keys.py` | Key unit tests | ❌ BLOCKED |
| `test_sms_auth.py` | SMS auth | ❌ BLOCKED + scope |
| `test_sms_frontend.py` | SMS frontend | ❌ BLOCKED + scope |
| `test_sms_keys.py` | SMS keys | ❌ BLOCKED + scope |
| `test_sms_reader.py` | SMS reader | ❌ BLOCKED + scope |

### Root Cause

```python
# tests/conftest.py:20-21
from sms_reader import create_sms_app   # ModuleNotFoundError
from sms_reader.config import SmsConfig  # ModuleNotFoundError
```

`sms_reader` package repository-তে নেই। pytest collect করার সময়ই ব্যর্থ হবে।

### সমাধান

**Option A (দ্রুত):** conftest.py থেকে sms_reader import conditional করুন:
```python
try:
    from sms_reader import create_sms_app
    from sms_reader.config import SmsConfig
    SMS_AVAILABLE = True
except ImportError:
    SMS_AVAILABLE = False
```

**Option B (সঠিক):** SMS-related conftest fixtures এবং test files আলাদা pytest marker দিয়ে skip করুন।

**Option C (clean):** SMS test files সরিয়ে ফেলুন এবং conftest.py থেকে sms import সরিয়ে ফেলুন।

---

## ৭. Recommended Implementation Plan

### Phase 1 শেষ করতে যা করতে হবে

#### Step 1: Test Infrastructure Fix (জরুরি — ৩০ মিনিট)

```python
# tests/conftest.py এ পরিবর্তন:
# from sms_reader import create_sms_app  ← সরিয়ে ফেলুন
# from sms_reader.config import SmsConfig  ← সরিয়ে ফেলুন
```

SMS fixtures (`sms_app`, `sms_owner`, `sms_admin`, etc.) সরিয়ে ফেলুন বা guard করুন।

---

#### Step 2: Brand Cleanup (১ ঘণ্টা)

**`db/migrations/001_initial_schema.sql`:**
```sql
-- NexOra — Secure Key Generator & License Management System  ← header আপডেট
```

**নতুন Migration `005_rename_rh_function.sql`:**
```sql
BEGIN;
CREATE OR REPLACE FUNCTION public.nexora_set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;
CREATE TRIGGER trg_admin_users_updated_at
    BEFORE UPDATE ON admin_users
    FOR EACH ROW EXECUTE FUNCTION public.nexora_set_updated_at();

DROP TRIGGER IF EXISTS trg_license_keys_updated_at ON license_keys;
CREATE TRIGGER trg_license_keys_updated_at
    BEFORE UPDATE ON license_keys
    FOR EACH ROW EXECUTE FUNCTION public.nexora_set_updated_at();

DROP FUNCTION IF EXISTS public.rh_set_updated_at();
COMMIT;
```

> ⚠️ `_DOMAIN_PREFIX = "rh-esports:v1"` **পরিবর্তন করবেন না।** এটি cryptographic domain separator — পরিবর্তন করলে সব existing key hash invalidate হবে।

---

#### Step 3: API Versioning (২ ঘণ্টা)

```python
# backend/api/auth.py
bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")

# backend/api/keys.py
bp = Blueprint("keys", __name__, url_prefix="/api/v1/keys")

# backend/api/dashboard.py
bp = Blueprint("dashboard", __name__, url_prefix="/api/v1/dashboard")

# backend/api/logs.py
bp = Blueprint("logs", __name__, url_prefix="/api/v1/logs")
```

Frontend JS (api.js) এবং tests-এও URL আপডেট করুন।

---

#### Step 4: Phase 1 Database Tables (৩ ঘণ্টা)

**নতুন Migration `006_nexora_phase1_tables.sql`:**
```sql
BEGIN;

CREATE TABLE IF NOT EXISTS projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    slug        text NOT NULL UNIQUE,
    owner_id    uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reader_keys (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key_hash    text NOT NULL UNIQUE,
    key_hint    text NOT NULL DEFAULT '',
    status      text NOT NULL DEFAULT 'active',
    created_by  uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz,
    CONSTRAINT reader_keys_status_check
        CHECK (status IN ('active', 'revoked', 'expired'))
);

CREATE TABLE IF NOT EXISTS reader_installations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reader_key_id uuid NOT NULL REFERENCES reader_keys(id) ON DELETE CASCADE,
    device_id   text NOT NULL,
    name        text NOT NULL DEFAULT '',
    installed_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz
);

CREATE TABLE IF NOT EXISTS device_tokens (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id uuid NOT NULL REFERENCES reader_installations(id) ON DELETE CASCADE,
    token_hash      text NOT NULL UNIQUE,
    issued_at       timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,
    revoked_at      timestamptz
);

COMMIT;
```

---

#### Step 5: Config — Supabase Keys (১ ঘণ্টা)

```python
# backend/config.py Settings dataclass-এ যোগ করুন:
supabase_url: str | None = None
supabase_service_role_key: str = field(default="", repr=False)

# from_env()-এ:
supabase_url=_string_env("SUPABASE_URL"),
supabase_service_role_key=_string_env("SUPABASE_SERVICE_ROLE_KEY") or "",
```

---

#### Step 6: SMS Scope Isolation (পরবর্তী Phase)

নিম্নলিখিত items **পরবর্তী phase-এ** সরানো বা refactor করা উচিত:
- `run_sms.py`, `wsgi_sms.py`
- `frontend/sms/`
- `db/migrations/004_sms_reader_keys.sql`
- `tests/test_sms_*.py`

---

## ৮. Priority Summary

| Priority | কাজ | সময় | ব্লকার? |
|---|---|---|---|
| 🔴 P0 | `conftest.py` থেকে sms_reader import সরানো | 30 min | Tests সব বন্ধ |
| 🔴 P0 | SMS test files skip/remove | 1 hr | Test suite ভাঙা |
| 🟠 P1 | `001_initial_schema.sql` header brand fix | 15 min | — |
| 🟠 P1 | Migration 005: `rh_set_updated_at` → `nexora_set_updated_at` | 1 hr | — |
| 🟠 P1 | API versioning `/api/` → `/api/v1/` | 2 hr | — |
| 🟡 P2 | Migration 006: projects, reader_keys, reader_installations, device_tokens | 3 hr | — |
| 🟡 P2 | `config.py` SUPABASE_URL / SERVICE_ROLE_KEY load | 1 hr | — |
| 🟢 P3 | SMS scope files সরানো | 2 hr | Phase 2 পরিকল্পনা |

---

## পরিশিষ্ট: গুরুত্বপূর্ণ নোট

> [!CAUTION]
> `backend/security/keys.py` এর `_DOMAIN_PREFIX = "rh-esports:v1"` **কখনো পরিবর্তন করবেন না** যদি database-এ production data থাকে। এটি পরিবর্তন করলে সমস্ত existing license key-এর HMAC digest invalidate হবে এবং কোনো key আর activate করা যাবে না।

> [!WARNING]
> `tests/conftest.py`-তে `from sms_reader import create_sms_app` import আছে যা `ModuleNotFoundError` দেবে। এটি ঠিক না করলে **কোনো test-ই চলবে না**, এমনকি SMS-সম্পর্কিত নয় এমন tests-ও।

> [!NOTE]
> Repository-র working directory সম্প্রতি `git checkout 2e0cd24 -- .` দিয়ে restore করা হয়েছে। `.tmp/` ফোল্ডারে local PostgreSQL binary এবং browser screenshots আছে — এগুলো `.gitignore`-এ আছে কিনা নিশ্চিত করুন।

---

*Report generated: 2026-11-16 | Auditor: automated-audit-agent | Status: COMPLETE*
