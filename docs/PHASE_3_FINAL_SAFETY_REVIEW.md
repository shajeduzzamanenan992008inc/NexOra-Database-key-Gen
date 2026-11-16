# OMEGA NEX-ORA — Phase 3: Final Pre-Migration Safety Review

**প্রকল্প:** OMEGA NEX-ORA Database Key Gen  
**ডকুমেন্ট:** Pre-Migration Safety Audit & Read-Only Connection Specification  
**তারিখ:** নভেম্বর ১৬, ২০২৬  
**ফেজ:** Phase 3 — Supabase Live Integration Safety Gate  
**স্ট্যাটাস:** 🛡️ READ-ONLY SAFETY REVIEW COMPLETE — AWAITING EXPLICIT USER APPROVAL  

---

## ১. ভূমিকা ও পর্যালোচনা উদ্দেশ্য (Executive Summary & Objective)

লাইভ Supabase PostgreSQL ডেটাবেসের সাথে যেকোনো সংযোগ স্থাপন বা মাইগ্রেশন স্ক্রিপ্ট এক্সিকিউট করার পূর্বে এই চূড়ান্ত রিড-অনলি সেফটি রিভিউ পরিচালিত হয়েছে। কোনো অ্যাপ্লিকেশন কোড পরিবর্তন না করে এবং কোনো লাইভ কানেকশন বা মাইগ্রেশন না চালিয়ে সম্পূর্ণ সিস্টেমের ক্রেডেনশিয়াল আর্কিটেকচার, ডেটাবেস ড্রাইভারের কনফিগারেশন, মাইগ্রেশনের সামঞ্জস্যতা এবং Row Level Security (RLS) পুঙ্খানুপুঙ্খভাবে মূল্যায়ন করা হয়েছে।

### পর্যালোচনার মূল নীতিমালা:
- **জিরো মিউটেশন (Zero Mutation):** কোনো ডেটাবেস অবজেক্ট তৈরি, পরিবর্তন, ড্রপ বা ডেটা মোডিফিকেশন করা হয়নি।
- **ক্রেডেনশিয়াল আইসোলেশন:** কোনো সিক্রেট লগ বা কনসোলে প্রিন্ট হয় না এবং চ্যাটে পাসওয়ার্ড চাওয়া হয়নি।
- **প্রজেক্ট বাউন্ডারি রক্ষা:** কেবল NexOra Admin Backend ও Supabase PostgreSQL অন্তর্ভুক্ত; Android Reader App সম্পূর্ণ পৃথক।

---

## ২. ক্রেডেনশিয়াল আর্কিটেকচার অডিট (Credential Architecture Audit)

`backend/config.py`, `backend/db.py`, এবং `.env.example` পরীক্ষা করে ক্রেডেনশিয়াল প্রবাহের নিরাপত্তা যাচাই করা হয়েছে:

| অডিট আইটেম | যাচাইকৃত আচরণ ও বাস্তবায়ন | স্ট্যাটাস |
|---|---|:---:|
| **DATABASE_URL ফরম্যাট** | `DatabaseSettings.from_dsn()` শুধুমাত্র `postgres://` বা `postgresql://` স্কিম গ্রহণ করে। হোস্ট, পোর্ট, ইউজার, পাসওয়ার্ড, ডেটাবেস এবং কোয়েরি প্যারামিটার (`sslmode`, `sslrootcert`) নিরাপদে পার্স করে। | **PASS** |
| **SUPABASE_URL ফরম্যাট** | `_validate_supabase_url()` কঠোরভাবে নিশ্চিত করে যে এটি `https://` দিয়ে শুরু হয় এবং ডোমেইনে `.supabase.co` উপস্থিত থাকে। এটি কোনো ডেটাবেস পাসওয়ার্ড নয়, প্রজেক্ট API URL। | **PASS** |
| **SUPABASE_SERVICE_ROLE_KEY ব্যবহার** | `SUPABASE_SERVICE_ROLE_KEY` একটি Supabase REST API JWT ক্রেডেনশিয়াল। কোডে এটি কখনোই PostgreSQL কানেকশন বা `pg8000`-এ পাসওয়ার্ড হিসেবে পাঠানো হয় না। | **PASS** |
| **pg8000 ড্রাইভার ক্রেডেনশিয়াল** | `backend/db.py`-এর `_connect()` মেথডে `pgdb.connect()` শুধুমাত্র `DATABASE_URL` থেকে পার্স করা `user`, `password`, `host`, `port`, `database`, `timeout` এবং `ssl_context` গ্রহণ করে। | **PASS** |
| **সার্ভিস-রোল কি ও ডিবি পাসওয়ার্ড বিভাজন** | সার্ভারের ডেটাবেস পাসওয়ার্ড এবং সুপাবেস সার্ভিস-রোল JWT সম্পূর্ণ আলাদা ভ্যারিয়েবলে সংরক্ষিত। কোনো অবস্থাতেই সার্ভিস-রোল কি দিয়ে ডেটাবেস লগইন করার চেষ্টা করা হয় না। | **PASS** |
| **ডায়াগনস্টিক্সে সিক্রেট মাস্কিং** | `DatabaseSettings.password`, `Settings.secret_key`, `Settings.key_pepper`, `Settings.supabase_service_role_key`, এবং `Settings.admin_password_hash`-এ `repr=False` প্রয়োগ করা আছে। `DatabaseSettings.dsn_summary` শুধুমাত্র `postgresql://user@host:port/database?sslmode=...` রিটার্ন করে; পাসওয়ার্ড সম্পূর্ণ বাদ থাকে। | **PASS** |

### এনভায়রনমেন্ট ভ্যারিয়েবল স্পেসিফিকেশন (Required vs Optional)

```
+--------------------------------------------------------------------------------------------------+
| ভ্যারিয়েবল নাম                | ধরন       | ডিফল্ট মান              | প্রয়োজনীয়তা ও নিয়ম               |
+--------------------------------------------------------------------------------------------------+
| DATABASE_URL                   | সিক্রেট    | কোনো ডিফল্ট নেই         | REQUIRED (Supabase Postgres DSN)  |
| KEY_PEPPER                     | সিক্রেট    | কোনো ডিফল্ট নেই         | REQUIRED (কমপক্ষে ৩২ অক্ষর)        |
| SECRET_KEY                     | সিক্রেট    | কোনো ডিফল্ট নেই         | REQUIRED (কমপক্ষে ৩২ অক্ষর)        |
| ADMIN_EMAIL                    | স্ট্রিং    | কোনো ডিফল্ট নেই         | REQUIRED (ভ্যালিড ইমেইল)          |
| ADMIN_PASSWORD_HASH            | সিক্রেট    | কোনো ডিফল্ট নেই         | REQUIRED (Argon2id হ্যাশ)          |
+--------------------------------------------------------------------------------------------------+
| SUPABASE_URL                   | স্ট্রিং    | ""                      | OPTIONAL (যদি সেট হয়, KEY আবশ্যক) |
| SUPABASE_SERVICE_ROLE_KEY      | সিক্রেট    | ""                      | OPTIONAL (যদি URL সেট হয়, আবশ্যক)|
| APP_ENV / FLASK_ENV            | স্ট্রিং    | "production"            | OPTIONAL                          |
| ADMIN_ROLE                     | স্ট্রিং    | "owner"                 | OPTIONAL ("owner" বা "admin")     |
| DATABASE_SSLMODE               | স্ট্রিং    | "require"               | OPTIONAL (require, verify-full)   |
| DATABASE_SSLROOTCERT           | পাথ       | None                    | OPTIONAL (certs/prod-ca-2021.crt) |
| DB_POOL_MAX                    | ইন্টিজার   | 10                      | OPTIONAL (১ থেকে ৫০)              |
| DB_CONNECT_TIMEOUT             | ইন্টিজার   | 10                      | OPTIONAL (১ থেকে ৬০ সেকেন্ড)       |
| SESSION_TTL_HOURS              | ইন্টিজার   | 8                       | OPTIONAL (১ থেকে ৭২০ ঘন্টা)       |
| SESSION_COOKIE_NAME            | স্ট্রিং    | "nexora_session"        | OPTIONAL                          |
| COOKIE_SECURE                  | বুলিয়ান   | True (production-এ)     | OPTIONAL                          |
| RATE_LIMIT_ENABLED             | বুলিয়ান   | True                    | OPTIONAL                          |
| TRUST_PROXY                    | বুলিয়ান   | False                   | OPTIONAL                          |
| MAX_BULK_KEYS                  | ইন্টিজার   | 500                     | OPTIONAL (১ থেকে ২০০০)            |
+--------------------------------------------------------------------------------------------------+
```

---

## ৩. মাইগ্রেশন সামঞ্জস্যতা বিশ্লেষণ (Migrations 001–006 In-Depth Review)

প্রতিটি মাইগ্রেশন ফাইলের গঠন, টেবিল, ট্রিগার, পারমিশন এবং সম্ভাব্য ঝুঁকি পুঙ্খানুপুঙ্খভাবে বিশ্লেষণ করা হয়েছে:

### ৩.১ Migration 001 (`001_initial_schema.sql`)
- **প্রভাবিত বিদ্যমান অবজেক্ট:** কোনোটি নয় (বেস স্কিমা)।
- **তৈরিকৃত টেবিল:** `admin_users`, `admin_sessions`, `license_keys`, `key_activations`, `generation_logs`, `admin_audit_logs`।
- **ফাংশন ও ট্রিগার:** `public.rh_set_updated_at()` ফাংশন তৈরি; `trg_admin_users_updated_at` এবং `trg_license_keys_updated_at` ট্রিগার তৈরি।
- **RLS পলিসি:** 001 ফাইলে কোনো RLS নেই (002 ফাইলে সক্রিয় হয়)।
- **প্রয়োজনীয় পারমিশন:** `CREATE EXTENSION (pgcrypto)`, `CREATE TABLE`, `CREATE FUNCTION`, `CREATE TRIGGER`। Supabase-এর ডিফল্ট `postgres` ইউজারের এই সমস্ত পারমিশন রয়েছে।
- **`IF NOT EXISTS` ঝুঁকি:** সব টেবিলে `CREATE TABLE IF NOT EXISTS` ব্যবহৃত হয়েছে। কোনো টেবিলে পূর্বে ভিন্ন কলাম থাকলে এটি স্কিপ হবে, কিন্তু নতুন ডেটাবেসে কোনো অসঙ্গতি তৈরি করে না।
- **রোলব্যাক সীমাবদ্ধতা:** স্বয়ংক্রিয় ডাউন-মাইগ্রেশন নেই। রোলব্যাকের জন্য রিভার্স ক্রমানুসারে টেবিল ড্রপ করতে হবে।
- **সুপাবেসে নিরাপত্তা:** সম্পূর্ণ নিরাপদ।

### ৩.২ Migration 002 (`002_indexes_and_policies.sql`)
- **প্রভাবিত বিদ্যমান অবজেক্ট:** 001-এ তৈরি ৬টি টেবিল।
- **তৈরিকৃত ইনডেক্স:** `ux_admin_users_email_lower`, `ux_admin_sessions_token_hash`, `ux_license_keys_key_hash`, `ux_key_activations_key_device` এবং ১১টি কুয়েরি অপ্টিমাইজেশন ইনডেক্স।
- **ফাংশন:** `public.rh_expire_overdue_keys()` তৈরি।
- **RLS পলিসি ও পারমিশন:**
  - ডায়নামিক পিএল/পিজিএসকিউএল `DO` ব্লকের মাধ্যমে ৬টি টেবিলের প্রতিটিতে `ENABLE ROW LEVEL SECURITY` প্রয়োগ করা হয়।
  - `PUBLIC`, `anon` এবং `authenticated` রোল থেকে সমস্ত অধিকার (`REVOKE ALL`) কেড়ে নেওয়া হয়।
- **প্রয়োজনীয় পারমিশন:** `ALTER TABLE`, `REVOKE`, `CREATE INDEX`।
- **`IF NOT EXISTS` ঝুঁকি:** ইনডেক্সে `IF NOT EXISTS` ব্যবহৃত হয়েছে; ভিন্ন কাঠামোর ইনডেক্স থাকলে এড়িয়ে যাবে।
- **রোলব্যাক সীমাবদ্ধতা:** `DROP INDEX` ও `DISABLE ROW LEVEL SECURITY` ম্যানুয়ালি করতে হবে।
- **সুপাবেসে নিরাপত্তা:** অত্যন্ত নিরাপদ; Supabase API (`anon`/`authenticated`) দিয়ে সরাসরি ডেটাবেস অ্যাক্সেস রোধ করে।

### ৩.৩ Migration 003 (`003_password_hash_comment.sql`)
- **প্রভাবিত বিদ্যমান অবজেক্ট:** `admin_users` টেবিল ও `password_hash` কলাম।
- **পরিবর্তন:** মেটাডেটা কমেন্ট আপডেট (Argon2id হ্যাশের স্পেসিফিকেশন)। কোনো ডেটা বা স্কিমা পরিবর্তিত হয় না।
- **সুপাবেসে নিরাপত্তা:** সম্পূর্ণ নিরাপদ ও তাৎক্ষণিক।

### ৩.৪ Migration 004 (`004_sms_reader_keys.sql`) — বিশেষ পর্যবেক্ষণ: লেগ্যাসি SMS স্কিমা
- **প্রভাবিত বিদ্যমান অবজেক্ট:** `admin_sessions` টেবিল।
- **কলাম সংযোজন:** `ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT 'keygen';`।
  - *বিশ্লেষণ:* এই কলামটি সেশন অডিয়েন্স আইসোলেশন নিশ্চিত করে (`backend/config.py`-তে `session_audience = "keygen"` ব্যবহৃত হয়)। এটি বিদ্যমান অ্যাডমিন সেশনে কোনো সমস্যা তৈরি করে না।
- **তৈরিকৃত টেবিল:** `sms_reader_keys`, `sms_reader_events`।
  - *বিশ্লেষণ:* এই টেবিলগুলো আইসোলেটেড লেগ্যাসি SMS মডিউলের জন্য সংরক্ষিত। যদিও Android Reader App একটি পৃথক রিপোজিটরি, এই টেবিলগুলোর উপস্থিতি NexOra-এর মূল টেবিল (`reader_keys`, `nexora_projects`) এর কার্যকারিতায় কোনো হস্তক্ষেপ করে না।
- **RLS:** উভয় টেবিলে RLS সক্রিয় এবং `PUBLIC`, `anon`, `authenticated` থেকে পারমিশন রিভোকড।
- **সুপাবেসে নিরাপত্তা:** সম্পূর্ণ নিরাপদ।

### ৩.৫ Migration 005 (`005_nexora_projects.sql`) — কোর NexOra আর্কিটেকচার
- **প্রভাবিত বিদ্যমান অবজেক্ট:** `admin_audit_logs` টেবিলে `project_id` কলাম যুক্ত হয়।
- **তৈরিকৃত টেবিল:** `nexora_projects`, `reader_keys`, `reader_installations`, `device_tokens`, `system_settings`।
- **ইউনিক কনস্ট্রেইন্টসমূহ:**
  - `ux_nexora_projects_name_normalized`: কেস-ইনসেনসিটিভ নাম ইউনিকনেস (`deleted_at IS NULL` ফিল্টারে)।
  - `ux_reader_installations_project_active`: **প্রতি প্রজেক্টে একটি মাত্র সক্রিয় রিডার ইনস্টলেশন সীমাবদ্ধতা**।
  - `ux_reader_installations_key`: একটি কি শুধুমাত্র একবারই রিডিম হতে পারবে।
  - `ux_device_tokens_hash`: সক্রিয় ডিভাইস টোকেনের ইউনিকনেস।
- **ফাংশন ও ট্রিগার:** `nexora_projects`-এ `trg_nexora_projects_updated_at` ট্রিগার তৈরি (যা প্রাথমিকভাবে `rh_set_updated_at` কল করে)।
- **RLS ও পারমিশন:**
  - সমস্ত টেবিলে RLS সক্রিয় করা হয়।
  - `PUBLIC`, `anon`, `authenticated` থেকে সমস্ত প্রিভিলেজ প্রত্যাহার করা হয়।
  - `service_role`-কে স্পষ্ট `GRANT SELECT, INSERT, UPDATE, DELETE` দেওয়া হয়।
- **সুপাবেসে নিরাপত্তা:** সম্পূর্ণ সামঞ্জস্যপূর্ণ ও নিরাপদ।

### ৩.৬ Migration 006 (`006_rename_rh_function.sql`) — বিশেষ পর্যবেক্ষণ: ফাংশন রিপ্লেসমেন্ট
- **উদ্দেশ্য:** লেগ্যাসি "rh" ব্র্যান্ডিংযুক্ত ফাংশনটি অপসারণ এবং আধুনিক `nexora_set_updated_at()` প্রয়োগ।
- **ধাপসমূহ:**
  1. `CREATE OR REPLACE FUNCTION nexora_set_updated_at() RETURNS TRIGGER ...`
  2. `admin_users`, `license_keys`, এবং `nexora_projects` টেবিলের ট্রিগারগুলো ড্রপ করে নতুন `nexora_set_updated_at()`-এ পুনঃসংযুক্ত করা।
  3. পুরোনো `rh_set_updated_at()` ড্রপ করা (`DROP FUNCTION IF EXISTS rh_set_updated_at()`)।
  - *বিশ্লেষণ:* `CASCADE` ইচ্ছাকৃতভাবে ব্যবহার করা হয়নি, যাতে কোনো অপ্রত্যাশিত ডিপেনডেন্সি থাকলে ড্রপ ব্যর্থ হয়ে সতর্কবার্তা দেয়। যেহেতু মাইগ্রেশনে সব ট্রিগার আগেই রি-পয়েন্ট করা হয়েছে, তাই কোনো ডিপেনডেন্সি ত্রুটি হবে না।
- **সুপাবেসে নিরাপত্তা:** অত্যন্ত নিরাপদ ও সম্পূর্ণ আইডেমপোটেন্ট।

---

## ৪. RLS ও ডেটাবেস পারমিশন মূল্যায়ন (RLS & Permissions Assessment)

> [!IMPORTANT]
> **কানেকশন রোলের সাথে RLS-এর মিথস্ক্রিয়া:**
> - Flask ব্যাকএন্ড `DATABASE_URL`-এর মাধ্যমে ডেটাবেসের সাথে সংযুক্ত হয়। এই সংযোগে ব্যবহৃত ব্যবহারকারী সাধারণত Supabase-এর ডিফল্ট **`postgres`** ইউজার।
> - `001` থেকে `006` পর্যন্ত সমস্ত টেবিল তৈরি হয় এই `postgres` ইউজারের মাধ্যমে। ফলে PostgreSQL নিয়মানুযায়ী `postgres` ইউজার হলো এই সমস্ত টেবিলের **টেবিল ওনার (Table Owner)**।
> - PostgreSQL আর্কিটেকচার অনুযায়ী, ডিফল্টভাবে **টেবিল ওনার কোনো Row Level Security (RLS) পলিসির অধীন থাকে না**। শুধুমাত্র `ALTER TABLE ... FORCE ROW LEVEL SECURITY` চালালে ওনারের ওপর RLS প্রযোজ্য হয়। আমাদের কোনো মাইগ্রেশনে `FORCE ROW LEVEL SECURITY` ব্যবহৃত হয়নি (যা `002`-এর কমেন্টে সুস্পষ্টভাবে উল্লেখ আছে)।
> - উপরন্তু, Supabase-এ `postgres` রোলটির `BYPASSRLS` সুবিধা রয়েছে।
> - ফলস্বরূপ: ব্যাকএন্ড কোনো বাধা ছাড়াই সমস্ত SELECT, INSERT, UPDATE, DELETE এবং রো-লকিং (`SELECT ... FOR UPDATE`) সম্পন্ন করতে পারে। কোনো পারমিশন বা পলিসির ঘাটতি নেই।

### বাহ্যিক ব্রাউজার ও PostgREST-এর সাথে মিথস্ক্রিয়া:
- Supabase-এর ডিফল্ট ব্রাউজার ক্লায়েন্ট রোলসমূহ (`anon` এবং `authenticated`)-এর জন্য কোনো SELECT/INSERT পলিসি নেই এবং তাদের সমস্ত প্রিভিলেজ `REVOKE ALL` করা আছে।
- এর ফলে ব্রাউজার থেকে বা বাইরের কোনো ব্যক্তি সরাসরি Supabase URL ব্যবহার করে ডাটাবেসের টেবিল দেখতে বা হ্যাক করতে পারবে না। সম্পূর্ণ অ্যাক্সেস শুধুমাত্র Flask সার্ভার ব্যাকএন্ডের নিয়ন্ত্রণে থাকে।

---

## ৫. রিড-অনলি কানেকশন ভেরিফিকেশন প্ল্যান (Read-Only Connection Verification Procedure)

ব্যবহারকারীর অনুমোদনের পর লাইভ ডেটাবেসের সুস্থতা যাচাই করার জন্য নিম্নোক্ত **সম্পূর্ণ রিড-অনলি** টেস্ট স্ক্রিপ্ট প্রস্তুত রাখা হয়েছে। এটি কোনো টেবিল বা ডেটা পরিবর্তন করবে না:

```python
"""Read-only Supabase Connection Verification Script (Dry-Run / Pre-Check).
This script executes zero write/DDL statements. It verifies TLS, database identity,
permissions, and schema state, then rolls back and exits.
"""
import ssl
import sys
import pg8000.dbapi as pgdb
from backend.config import DatabaseSettings

def verify_readonly_connection(dsn: str, ca_cert: str | None = None) -> dict:
    settings = DatabaseSettings.from_dsn(dsn)
    ssl_ctx = ssl.create_default_context(cafile=ca_cert) if settings.uses_ssl else None
    
    conn = pgdb.connect(
        user=settings.user,
        password=settings.password,
        host=settings.host,
        port=settings.port,
        database=settings.database,
        timeout=settings.connect_timeout,
        ssl_context=ssl_ctx,
    )
    conn.autocommit = False
    cur = conn.cursor()
    
    results = {}
    try:
        # 1. Database & User Identity
        cur.execute("SELECT version(), current_database(), current_user, session_user;")
        row = cur.fetchone()
        results["version"] = row[0]
        results["database"] = row[1]
        results["user"] = row[2]
        
        # 2. Connection TLS Verification
        cur.execute("SHOW ssl;")
        results["ssl_enabled"] = cur.fetchone()[0]
        
        # 3. Role Privileges Check
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;")
        privs = cur.fetchone()
        results["is_superuser"] = privs[0] if privs else False
        results["bypass_rls"] = privs[1] if privs else False
        
        # 4. Check Existing Migrations State
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'schema_migrations'
            );
        """)
        has_migrations_tbl = cur.fetchone()[0]
        results["schema_migrations_exists"] = has_migrations_tbl
        
        if has_migrations_tbl:
            cur.execute("SELECT filename, applied_at FROM schema_migrations ORDER BY filename;")
            results["applied_migrations"] = cur.fetchall()
        else:
            results["applied_migrations"] = []
            
        # 5. List Existing Public Tables
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' ORDER BY table_name;
        """)
        results["existing_tables"] = [r[0] for r in cur.fetchall()]
        
    finally:
        conn.rollback()  # GUARANTEE: Zero writes
        conn.close()
        
    return results
```

---

## ৬. ফাইন্ডিংস ক্লাসিফিকেশন ম্যাট্রিক্স (Classification Matrix)

| নম্বর | অডিট আইটেম | ক্যাটাগরি | স্ট্যাটাস | ব্যাখ্যা |
|:---:|---|---|:---:|---|
| ১ | `DATABASE_URL` হ্যান্ডলিং | ক্রেডেনশিয়াল | **PASS** | `pg8000` ড্রাইভারের সাথে সম্পূর্ণ নিরাপদ ও পার্সড সংযোগ। |
| ২ | `SUPABASE_URL` হ্যান্ডলিং | ক্রেডেনশিয়াল | **PASS** | প্রজেক্ট URL ভ্যালিডেশন কঠোর; কোনো সিক্রেট পাসওয়ার্ড নয়। |
| ৩ | `SUPABASE_SERVICE_ROLE_KEY` আইসোলেশন | ক্রেডেনশিয়াল | **PASS** | ডেটাবেস পাসওয়ার্ড হিসেবে ব্যবহার হয় না; ডায়াগনস্টিক্সে মাস্কড। |
| ৪ | সিক্রেট মাস্কিং ও লগিং প্রোটেকশন | সিকিউরিটি | **PASS** | `repr=False` এবং নিরাপদ `dsn_summary` নিশ্চিত করা হয়েছে। |
| ৫ | এনভায়রনমেন্ট ভ্যারিয়েবল স্পেসিফিকেশন | কনফিগারেশন | **PASS** | ৫টি আবশ্যক ও ১২টি ঐচ্ছিক ভ্যারিয়েবল সুনির্দিষ্টভাবে নির্ধারিত। |
| ৬ | মাইগ্রেশন ক্রম ও ডিপেনডেন্সি | স্কিমা | **PASS** | 001 থেকে 006 ক্রমানুসারে সম্পূর্ণ সুসংহত। |
| ৭ | মাইগ্রেশন 004 লেগ্যাসি SMS সামঞ্জস্যতা | স্কিমা | **PASS** | অডিয়েন্স কলাম সামঞ্জস্যপূর্ণ; মূল প্রজেক্টে কোনো বাধা সৃষ্টি করে না। |
| ৮ | মাইগ্রেশন 006 ট্রিগার ও ফাংশন প্রতিস্থাপন | স্কিমা | **PASS** | `rh_set_updated_at` নিরাপদে `nexora_set_updated_at`-এ রূপান্তরিত। |
| ৯ | `IF NOT EXISTS` সেফটি মূল্যায়ন | স্কিমা | **PASS** | ফ্রেশ ডেটাবেসে নির্ভরযোগ্য; কোনো টেবিল কনফ্লিক্ট নেই। |
| ১০ | কানেকশন রোলের RLS প্রিভিলেজ | পারমিশন | **PASS** | `postgres` ইউজার টেবিল ওনার হওয়ায় ব্যাকএন্ড অপারেশনে বাধা নেই। |
| ১১ | PostgREST / ব্রাউজার ডিনায়াল পলিসি | সিকিউরিটি | **PASS** | `anon` এবং `authenticated` সম্পূর্ণ ডিনাইড (Deny-by-Default)। |
| ১২ | রিড-অনলি কানেকশন ভেরিফিকেশন প্ল্যান | অপারেশনাল | **PASS** | জিরো-মিউটেশন ভেরিফিকেশন টেস্ট তৈরি। |

---

## ৭. চূড়ান্ত সুপারিশ (Final Recommendation)

উপরি-উক্ত পুঙ্খানুপুঙ্খ অডিটের ফলাফলের ভিত্তিতে সিস্টেমের অবস্থা নিম্নরূপ:

```
+------------------------------------------------------------------------------------+
|  1. লাইভ কানেকশন টেস্ট সুপারিশ:    SAFE TO TEST CONNECTION                         |
|  2. লাইভ মাইগ্রেশন সুপারিশ:        SAFE TO MIGRATE AFTER APPROVAL                   |
+------------------------------------------------------------------------------------+
```

> [!CAUTION]
> **পরবর্তী নির্দেশনার জন্য অপেক্ষা (APPROVAL GATE):**
> - বর্তমান পর্যায়ে কোনো লাইভ Supabase সংযোগ বা মাইগ্রেশন সম্পন্ন করা হয়নি।
> - লাইভ Supabase কানেকশন টেস্ট এবং মাইগ্রেশন চালানোর পূর্বে ব্যবহারকারীর সুস্পষ্ট ও লিখিত অনুমোদনের প্রয়োজন।
