# OMEGA NEX-ORA — Phase 3: Supabase Live Integration Preflight Audit Report

**প্রকল্প:** OMEGA NEX-ORA Database Key Gen  
**তারিখ:** নভেম্বর ১৬, ২০২৬  
**ফেজ:** Phase 3 — Supabase Live Integration + End-to-End Verification (Preflight)  
**স্ট্যাটাস:** 🟡 PREFLIGHT COMPLETE — LIVE MIGRATION APPROVAL GATEWAY REACHED  

---

## ১. ভূমিকা ও কঠোর প্রজেক্ট বাউন্ডারি (Scope & Strict Project Boundary)

Phase 3-এর প্রাথমিক উদ্দেশ্য হলো বিদ্যমান NexOra Admin Backend-কে কনফিগার করা Supabase PostgreSQL ডেটাবেসের সাথে সংযুক্ত করার পূর্বে একটি সম্পূর্ণ, স্বাধীন এবং পুঙ্খানুপুঙ্খ অডিট সম্পন্ন করা। পূর্ববর্তী রিপোর্টের তথ্যের ওপর অন্ধভাবে নির্ভর না করে সরাসরি রিপোজিটরির মূল সোর্স কোড পরীক্ষা করে এই রিপোর্ট প্রস্তুত করা হয়েছে।

> [!IMPORTANT]
> **কঠোর প্রজেক্ট বাউন্ডারি (Strict Project Boundary):**
> - এই রিপোজিটরিটি কেবলমাত্র **OMEGA NEX-ORA Admin System**, **Flask Backend**, **Secure REST APIs (/api/v1/)**, এবং **Supabase PostgreSQL** ডাটাবেস লেয়ারের জন্য।
> - **Android Reader App** সম্পূর্ণ পৃথক রিপোজিটরি ও প্রজেক্ট। এই রিপোজিটরিতে কোনো অ্যান্ড্রয়েড সোর্স কোড, গ্রেডল ফাইল বা মোবাইল ইমপ্লিমেন্টেশন যুক্ত করা হবে না।
> - এই রিপোজিটরি থেকে কোনো সিক্রেট বা প্রিভিলেজড ক্রেডেনশিয়াল (`SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, `KEY_PEPPER`) এক্সটার্নাল রিডার অ্যাপ বা ফ্রন্টএন্ড ব্রাউজারে উন্মুক্ত করা সম্পূর্ণ নিষিদ্ধ।

---

## ২. সোর্স কোড রি-অডিট ফলাফল (Source Code Re-Audit Findings)

পূর্ববর্তী অডিট রিপোর্ট (`docs/PHASE_1_AUDIT_REPORT.md`, `docs/PHASE_2_FINAL_REPORT.md`, `docs/PHASE_2_VERIFICATION_REPORT.md`) এবং সিস্টেম আর্কিটেকচার (`docs/ARCHITECTURE.md`)-এর সাথে বাস্তব সোর্স কোডের সামঞ্জস্য পরীক্ষা করা হয়েছে:

| মডিউল / কম্পোনেন্ট | সোর্স কোড অবস্থান | বাস্তব কোডের অবস্থা | অডিট ফলাফল |
|---|---|---|:---:|
| **Config Engine** | `backend/config.py` | `DatabaseSettings`, `Settings.from_env()`, কঠোর ভ্যালিডেশন, `_PLACEHOLDER_MARKERS` প্রতিরোধ | ✅ VERIFIED |
| **Database Pool** | `backend/db.py` | `pg8000.dbapi`, পিওর পাইথন ড্রাইভার, থ্রেড-সেফ `LifoQueue`, কানেকশন পিং চেক, অটো-ডিসকার্ড | ✅ VERIFIED |
| **Migrations Runner** | `backend/migrations.py` | ডলার-কোট হ্যান্ডলিং (`_DOLLAR_TAG`), SHA-256 চেকার, `schema_migrations` ট্র্যাকিং | ✅ VERIFIED |
| **Project Service** | `backend/services/project_service.py` | কেস-ইনসেনসিটিভ ইউনিকনেস, রো-লেভেল ট্রানজ্যাকশন লক (`FOR UPDATE`), HMAC হ্যাশিং, অডিট ট্র্যাকিং | ✅ VERIFIED |
| **Auth Service** | `backend/services/auth_service.py` | Argon2id মেমরি-হার্ড পাসওয়ার্ড ভ্যালিডেশন, ব্রুট-ফোর্স লকআউট, অপেক সেশন ম্যানেজমেন্ট | ✅ VERIFIED |
| **Bootstrap Admin** | `backend/services/admin_bootstrap.py` | পরিবেশ ভ্যারিয়েবল থেকে সেফ অ্যাডমিন প্রভিশনিং (বিদ্যমান থাকলে ওভাররাইট করে না) | ✅ VERIFIED |
| **Audit Service** | `backend/services/audit.py` | স্ট্যান্ডার্ড অডিট ট্রেইল রাইটার | ✅ VERIFIED |
| **REST APIs v1** | `backend/api/v1/` | ১০টি ব্লুপ্রিন্ট (`health`, `projects`, `reader`, `installations`, `dashboard`, `keys`, `security`, `audit`, `settings`, `auth`) | ✅ VERIFIED |
| **Security Layer** | `backend/security/` | HMAC-SHA256 কি হ্যাশিং (`rh-esports:v1` ডোমেন প্রিফিক্স অক্ষত), Argon2id, স্লাইডিং উইন্ডো রেট লিমিট | ✅ VERIFIED |
| **Database Migrations** | `db/migrations/001-006` | ৬টি সুসংহত মাইগ্রেশন ফাইল, সম্পূর্ণ আইডেমপোটেন্ট DDL এবং RLS পলিসি | ✅ VERIFIED |
| **Automated Tests** | `tests/` | ১৭৭টি টেস্ট (১৫১ Passed, ০ Failed, ২৬ Skipped [Legacy SMS Scope-out]) | ✅ VERIFIED |

---

## ৩. সুপাবেস কানেকশন প্রিফ্লাইট (Supabase Connection Preflight)

### ৩.১ সার্ভার-সাইড এনভায়রনমেন্ট ভ্যারিয়েবল স্পেসিফিকেশন

সুপাবেস লাইভ ইন্টিগ্রেশনের জন্য নিম্নোক্ত এনভায়রনমেন্ট ভ্যারিয়েবলগুলো প্রয়োজন:

| ভ্যারিয়েবল নেম | সংবেদনশীলতা | উদ্দেশ্য ও ভ্যালিডেশন নিয়ম |
|---|---|---|
| `DATABASE_URL` | 🔴 **CRITICAL SECRET** | Supabase PostgreSQL ডিরেক্ট বা পুলার সংযোগ স্ট্রিং (`postgresql://postgres:[PASSWORD]@[HOST]:[PORT]/postgres?sslmode=require`)। `postgres://` অথবা `postgresql://` স্কিম বাধ্যতামূলক। |
| `SUPABASE_URL` | 🟡 **RESTRICTED** | Supabase প্রজেক্ট API URL (`https://<project-ref>.supabase.co`)। রিডার অ্যাপ ভ্যালিডেশনের রেফারেন্স হিসেবে ব্যবহৃত। |
| `SUPABASE_SERVICE_ROLE_KEY` | 🔴 **CRITICAL SECRET** | সার্ভার-সাইড অ্যাডমিন JWT কি। কোনো ক্লায়েন্ট বা ব্রাউজারে কখনো পাঠানো হয় না। |
| `KEY_PEPPER` | 🔴 **CRITICAL SECRET** | রিডার কি ও ডিভাইস টোকেনের HMAC-SHA256 ডাইজেস্ট তৈরির সার্ভার সিক্রেট (কমপক্ষে ৩২ অক্ষর, ক্রিপ্টোগ্রাফিক র্যান্ডম)। |
| `SECRET_KEY` | 🔴 **CRITICAL SECRET** | Flask সেশন ও CSRF টোকেন এনক্রিপশন কি (কমপক্ষে ৩২ অক্ষর)। |
| `ADMIN_EMAIL` | 🟢 **CONFIG** | সুপার অ্যাডমিনের প্রাথমিক ইমেইল ঠিকানা। |
| `ADMIN_PASSWORD_HASH` | 🔴 **CRITICAL SECRET** | সুপার অ্যাডমিনের Argon2id পাসওয়ার্ড হ্যাশ (`backend.cli hash-password` দিয়ে প্রস্তুতকৃত)। |

### ৩.২ বর্তমান এনভায়রনমেন্ট স্ট্যাটাস

1. **লোকাল `.env` ফাইল:** রিপোজিটরিতে কোনো `.env` ফাইল কমিট করা নেই (এটি সিকিউরিটি বেস্ট প্র্যাকটিস)।
2. **প্রসেস এনভায়রনমেন্ট:** বর্তমান রানিং প্রসেস এনভায়রনমেন্টে প্রোডাকশন Supabase সিক্রেট অনুপস্থিত (টেস্টিং এনভায়রনমেন্টে লোকাল টেস্ট ডেটাবেস `127.0.0.1:55432` ব্যবহৃত হচ্ছে)।
3. **সিক্রেট প্রটেকশন নীতি:**
   - কোনো ক্রেডেনশিয়াল কোডে হার্ডকোড করা নেই।
   - লগ বা কনসোলে কোনো পাসওয়ার্ড বা সিক্রেট প্রিন্ট হয় না (`DatabaseSettings.password` এবং `Settings.secret_key`, `key_pepper`, `supabase_service_role_key`-এ `repr=False` প্রয়োগ করা আছে)।
   - ব্রাউজার বান্ডেল বা রিডার অ্যাপে কোনো প্রিভিলেজড ডেটাবেস পাসওয়ার্ড বা সার্ভিস রোল কি এক্সপোজ করা হয় না।

### ৩.৩ ফেল-ফাস্ট ভ্যালিডেশন মেকানিজম (Fail-Fast Validation)

`backend/config.py`-এ `Settings.from_env()` বাস্তবায়ন পরীক্ষা করে দেখা গেছে:
- প্রয়োজনীয় কোনো সিক্রেট অনুপস্থিত থাকলে অ্যাপ্লিকেশন স্টার্টআপেই বন্ধ হয়ে যাবে এবং স্পষ্ট `ConfigError` থ্রো করবে।
- কোনো প্লেসহোল্ডার টেক্সট (যেমন: `change-me`, `replace-me`, `your-`, `<placeholder>`) থাকলে তৎক্ষণাৎ স্টার্টআপ ব্যর্থ হবে।
- `KEY_PEPPER` এবং `SECRET_KEY`-এর দৈর্ঘ্য ৩২ অক্ষরের কম হলে স্টার্টআপ ব্লক করা হবে।
- `SUPABASE_URL` কনফিগার করা থাকলে `SUPABASE_SERVICE_ROLE_KEY` থাকাও বাধ্যতামূলক করা হয়েছে।

### ৩.৪ ট্রান্সপোর্ট লেয়ার সিকিউরিটি (TLS Requirements)

- `backend/db.py`-এর `_connect()` মেথড কঠোর TLS নিয়ম মেনে চলে:
  - ডিফল্ট `sslmode=require`।
  - যদি `sslmode != "disable"` হয়, তবে পাইথনের `ssl.create_default_context()` ব্যবহার করে এনক্রিপ্টেড ও ভেরিফায়েড সকেট তৈরি হয়।
  - Supabase-এর কাস্টম CA ভেরিফিকেশনের জন্য `DATABASE_SSLROOTCERT` সাপোর্ট রয়েছে (রিপোজিটরির `certs/prod-ca-2021.crt` ফাইলটি বিদ্যমান এবং ভ্যালিড)।
  - কোনো প্লেইনটেক্সট বা আনএনক্রিপ্টেড সংযোগ প্রোডাকশনে অনুমোদিত নয়।

### ৩.৫ কানেকশন পুলিং আচরণ (Connection Pooling Behavior)

- `Database` ক্লাস (`backend/db.py`):
  - থ্রেড-সেফ `threading.BoundedSemaphore` এবং `queue.LifoQueue` ব্যবহার করে কনকারেন্ট রিকোয়েস্ট পরিচালনা করে।
  - ডিফল্ট পুল সাইজ: ১০টি সংযোগ (`DB_POOL_MAX` দিয়ে ১-৫০ পর্যন্ত কনফিগারেবল)।
  - কানেকশন চেকআউটের সময় লাইভ `SELECT 1` পিং টেস্ট করে সকেট সুস্থতা নিশ্চিত করে; ত্রুটিযুক্ত সংযোগ সঙ্গে সঙ্গে ডিসকার্ড করে নতুন সংযোগ তৈরি করে।
  - প্রতিটি ট্রানজ্যাকশন শেষে রোলব্যাক বা কমিট নিশ্চিত করে আইসোলেশন বজায় রাখে।

---

## ৪. টেস্ট সুইট ভেরিফিকেশন ফলাফল (Automated Test Verification)

রিপোজিটরির বর্তমান টেস্ট সুইট সম্পূর্ণভাবে রান করা হয়েছে:
```
============================= 151 passed, 26 skipped, 29 warnings in 80.45s =============================
```

- **Passed:** ১৫১টি টেস্ট সম্পূর্ণ পাস করেছে।
- **Failed:** ০টি ফেইল করেছে।
- **Skipped:** ২৬টি লেগ্যাসি SMS টেস্ট স্কিপ করা হয়েছে (যেহেতু SMS Reader মডিউল বাউন্ডারির বাইরে)।
- **Warnings:** ২৯টি পাইথন ৩.১৫ ফাইল রিডিং সংক্রান্ত রিসোর্স ওয়ার্নিং (কোনো বাগ নয়)।

---

## ৫. লাইভ ডেটাবেস মোডিফিকেশন নিষেধাজ্ঞা (Approval Gate)

> [!CAUTION]
> **লাইভ মাইগ্রেশন ও ডাটাবেস পরিবর্তন স্থগিত:**
> - এই মুহূর্তে লাইভ Supabase ডেটাবেসে কোনো DDL, DML, `CREATE`, `ALTER`, `DROP`, বা `TRUNCATE` চালানো হয়নি।
> - কোনো প্রোডাকশন ডেটা পরিবর্তিত হয়নি।
> - `docs/PHASE_3_MIGRATION_PLAN.md` তৈরি সম্পন্ন করার পর ব্যবহারকারীর সুস্পষ্ট ও লিখিত সম্মতি না পাওয়া পর্যন্ত কোনো লাইভ কানেকশন বা মাইগ্রেশন এক্সেকিউট করা হবে না।
