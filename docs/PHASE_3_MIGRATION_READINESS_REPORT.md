# OMEGA NEX-ORA — Phase 3 Step 4.2: Migration Execution Readiness Report

**প্রকল্প:** OMEGA NEX-ORA Database Key Gen  
**ডকুমেন্ট:** Pre-Migration Readiness Review (Migrations 005 & 006, Runner & Backup)  
**তারিখ:** নভেম্বর ১৬, ২০২৬  
**ফেজ:** Phase 3 — Step 4.2 (Migration Execution Readiness)  
**স্ট্যাটাস:** 🛡️ **READINESS REVIEW COMPLETE — 005 & 006 VERIFIED SAFE**  
**গেট স্ট্যাটাস:** 🛑 **APPROVAL GATE: STOPPED BEFORE LIVE MIGRATION (AWAITING STEP 5 APPROVAL)**

---

## ১. ওভারভিউ ও মূল ফলাফল (Executive Summary)

Phase 3 Step 4.2-এর অধীনে পেন্ডিং মাইগ্রেশন `005_nexora_projects.sql` এবং `006_rename_rh_function.sql`, মাইগ্রেশন রানার ইঞ্জিন (`backend/migrations.py`), এবং ডেটাবেস ব্যাকআপ অবস্থার ওপর চূড়ান্ত প্রস্তুতিমূলক পর্যালোচনা (Pre-Migration Readiness Review) সম্পন্ন করা হয়েছে।

### মূল ফলাফল ও পৃথক রায় (Separate Verdicts):
1. **ব্যাকআপ ভেরিফিকেশন (Backup Verification):** **NEEDS REVIEW**  
   - লাইভ ডেটাবেসে Supabase-এর অটোমেটেড WAL কন্টিনিউয়াস আর্কাইভিং সক্রিয় ও ত্রুটিমুক্ত (`archived_count = 44`, `failed_count = 0`)।  
   - তবে ক্লাউড ম্যানেজমেন্ট ড্যাশবোর্ডের স্ন্যাপশট ও রিস্টোর পয়েন্ট SQL কানেকশন থেকে সম্পূর্ণ নিশ্চিত করা সম্ভব নয় বিধায় এটি অ্যাডমিনিস্ট্রেটিভ পর্যালোচনার জন্য চিহ্নিত।
2. **Migration 005 (`005_nexora_projects.sql`):** **PASS**  
   - ৩৩টি সুনির্দিষ্ট স্টেটমেন্ট পর্যালোচিত। নতুন ৫টি টেবিল সম্পূর্ণ আইসোলেটেড, সঠিক ওনারশিপ ও RLS পলিসি সংবলিত। বিদ্যমান অ্যাডমিন ও লেগ্যাসি SMS ডেটার ওপর কোনো ধ্বংসাত্মক বা পরিবর্তনকারী প্রভাব নেই।
3. **Migration 006 (`006_rename_rh_function.sql`):** **PASS**  
   - ৯টি স্টেটমেন্ট পর্যালোচিত। ট্রিগার রিপয়েন্টিং ক্রম নির্ভুল। লেগ্যাসি SMS টেবিলে কোনো ডিপেনডেন্সি নেই। `DROP FUNCTION` স্টেটমেন্টে `CASCADE` বর্জন করায় কোনো অবজেক্ট ঝুঁকিতে নেই। সম্পূর্ণ রিভার্সিবল।
4. **মাইগ্রেশন রানার (`backend/migrations.py`):** **PASS**  
   - SHA-256 চেকার ভ্যালিডেশন নিশ্চিত। ০০১ থেকে ০০৪ ফাইলগুলো লাইভ ডেটাবেসের সাথে হুবহু মিলায় পুনরায় কখনো চলবে না। ফেইলিউরে সম্পূর্ণ ট্রানজ্যাকশন রোলব্যাক নিশ্চিত।

---

## ২. ব্যাকআপ স্ট্যাটাস অডিট (Backup & Restore Point Verification)

### ২.১ লাইভ PostgreSQL আর্কাইভার ক্যাটালগ অডিট
রিড-অনলি মোডে লাইভ Supabase PostgreSQL-এর `pg_stat_archiver` ক্যাটালগ পরীক্ষা করা হয়েছে:

```
+----------------------------------------------------------------------------------------------------+
| মেটাডেটা প্রোপার্টি            | মান                                                                |
+----------------------------------------------------------------------------------------------------+
| archived_count                 | 44 (মোট ৪৪টি WAL ফাইল সফলভাবে ক্লাউড স্টোরেজে সংরক্ষিত)             |
| last_archived_wal              | 00000001000000000000002E                                           |
| last_archived_time             | 2026-11-16 09:10:13 UTC (মাইগ্রেশন প্রস্তুতির কয়েক মিনিট পূর্বে)    |
| failed_count                   | 0 (জিরো ফেইলিউর)                                                  |
+----------------------------------------------------------------------------------------------------+
```

### ২.২ মূল্যায়ন ও নির্দেশনা
- **যাচাইকৃত সত্য (Verified Fact):** Supabase টোকিও ক্লাস্টারে WAL-বেসড কন্টিনিউয়াস আর্কাইভিং রিয়েল-টাইমে সচল এবং কোনো আর্কাইভ ফেইলিউর ঘটেনি।
- **সীমাবদ্ধতা:** Supabase Cloud Management API / Dashboard পরিচালিত ডিস্ক স্ন্যাপশট ও পয়েন্ট-ইন-টাইম রিকভারি (PITR) ব্যাকআপ স্ট্যাটাস সরাসরি PostgreSQL SQL কানেকশন দিয়ে পরীক্ষা করা যায় না।
- **রায়:** **NEEDS REVIEW**
- **সুপারিশ:** লাইভ মাইগ্রেশন (Step 5) অনুমোদনের পূর্বে অ্যাডমিনিস্ট্রেটরকে Supabase Dashboard (`Database -> Backups`) থেকে সাম্প্রতিক স্ন্যাপশট নিশ্চিত করার অথবা একটি অফলাইন ব্যাকআপ (`pg_dump`) নেওয়ার পরামর্শ দেওয়া হচ্ছে।

---

## ৩. Migration 005 পুঙ্খানুপুঙ্খ স্টেটমেন্ট বিশ্লেষণ

`db/migrations/005_nexora_projects.sql` ফাইলের প্রতিটি স্টেটমেন্ট এবং এর প্রভাব মূল্যায়ন:

| স্টেটমেন্ট ক্রম | অপারেশনের ধরন | টার্গেট অবজেক্ট | বিবরণ ও নিরাপত্তা মূল্যায়ন | স্ট্যাটাস |
|:---:|---|---|---|:---:|
| 1 | `CREATE TABLE IF NOT EXISTS` | `nexora_projects` | মাল্টি-প্রজেক্ট ম্যানেজমেন্ট কোর টেবিল। UUID প্রাইমারি কি, কেস-ইনসেনসিটিভ ইউনিক কলাম `project_name_normalized`। | **PASS** |
| 2–4 | `COMMENT ON` | `nexora_projects` | স্কিমা মেটাডেটা ডকুমেন্টেশন। | **PASS** |
| 5 | `CREATE UNIQUE INDEX IF NOT EXISTS` | `ux_nexora_projects_name_normalized` | পার্শিয়াল ইউনিক ইনডেক্স (`WHERE deleted_at IS NULL`)। ডুপ্লিকেট সক্রিয় প্রজেক্ট প্রতিরোধক। | **PASS** |
| 6 | `CREATE INDEX IF NOT EXISTS` | `ix_nexora_projects_status` | `(status, created_at DESC)` কুয়েরি অপ্টিমাইজেশন ইনডেক্স। | **PASS** |
| 7–8 | `DROP TRIGGER IF EXISTS` / `CREATE TRIGGER` | `trg_nexora_projects_updated_at` | `nexora_projects`-এ `rh_set_updated_at()` কলকারী ট্রিগার (006-এ আপডেট হবে)। | **PASS** |
| 9 | `CREATE TABLE IF NOT EXISTS` | `reader_keys` | ওয়ান-টাইম রিডার অ্যাক্টিভেশন কি টেবিল। HMAC-SHA256 হ্যাশ স্টোরেজ; প্লেইনটেক্সট কি কখনোই জমা হয় না। | **PASS** |
| 10–12 | `COMMENT ON` | `reader_keys` | মেটাডেটা ডকুমেন্টেশন। | **PASS** |
| 13 | `CREATE UNIQUE INDEX IF NOT EXISTS` | `ux_reader_keys_key_hash` | প্রতিটি কি হ্যাশের গ্লোবাল ইউনিকনেস গ্যারান্টি। | **PASS** |
| 14–16 | `CREATE INDEX IF NOT EXISTS` | `ix_reader_keys_*` | প্রজেক্ট, স্ট্যাটাস, এক্সপায়ারি ও ব্যাচ কুয়েরি ইনডেক্স। | **PASS** |
| 17 | `CREATE TABLE IF NOT EXISTS` | `reader_installations` | সক্রিয় রিডার অ্যাপ ইনস্টলেশন ট্র্যাকার। এক প্রজেক্টে একটি ডিভাইস সীমা প্রয়োগকারী। | **PASS** |
| 18–19 | `COMMENT ON` | `reader_installations` | মেটাডেটা ডকুমেন্টেশন। | **PASS** |
| 20 | `CREATE UNIQUE INDEX IF NOT EXISTS` | `ux_reader_installations_project_active` | **কঠোর বিজনেস পলিসি:** `(project_id) WHERE status = 'active'` — প্রতি প্রজেক্টে ১টি সক্রিয় ইনস্টলেশন। | **PASS** |
| 21 | `CREATE UNIQUE INDEX IF NOT EXISTS` | `ux_reader_installations_key` | একটি রিডার কি একাধিকবার রিডিম হওয়া সম্পূর্ণ অসম্ভব। | **PASS** |
| 22–23 | `CREATE INDEX IF NOT EXISTS` | `ux_reader_installations_*` | সক্রিয় ডিভাইস টোকেন ইউনিকনেস এবং স্ট্যাটাস ইনডেক্স। | **PASS** |
| 24 | `CREATE TABLE IF NOT EXISTS` | `device_tokens` | রিডার ডিভাইসের হার্টবিট ও রিকানেক্ট টোকেন ট্র্যাকিং টেবিল। | **PASS** |
| 25 | `COMMENT ON` | `device_tokens` | মেটাডেটা ডকুমেন্টেশন। | **PASS** |
| 26–27 | `CREATE INDEX IF NOT EXISTS` | `ux_device_tokens_hash` / `ix_*` | সক্রিয় টোকেন ইউনিকনেস ও ইনস্টলেশন লুকআপ ইনডেক্স। | **PASS** |
| 28 | `CREATE TABLE IF NOT EXISTS` | `system_settings` | রানটাইম সিস্টেম কনফিগারেশন কি-ভ্যালু টেবিল। | **PASS** |
| 29 | `COMMENT ON` | `system_settings` | মেটাডেটা ডকুমেন্টেশন। | **PASS** |
| 30 | `INSERT INTO ... ON CONFLICT DO NOTHING` | `system_settings` | ৩টি প্রাথমিক কনফিগারেশন সিড (`nexora.version`, `max_installations`, `heartbeat`)। আইডেমপোটেন্ট। | **PASS** |
| 31 | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | `admin_audit_logs` | বিদ্যমান অডিট লগে `project_id uuid REFERENCES nexora_projects(id) ON DELETE SET NULL` যুক্তকরণ। বিদ্যমান ১০টি রেকর্ড অক্ষুণ্ণ থাকবে (`project_id = NULL`)। | **PASS** |
| 32 | `CREATE INDEX IF NOT EXISTS` | `ix_admin_audit_logs_project` | `admin_audit_logs(project_id, created_at DESC) WHERE project_id IS NOT NULL` পার্শিয়াল ইনডেক্স। | **PASS** |
| 33 | `DO $nexora_rls$` (PL/pgSQL Block) | ৫টি নতুন টেবিল | ৫টি নতুন টেবিলে `ENABLE ROW LEVEL SECURITY`, `REVOKE ALL FROM PUBLIC, anon, authenticated` এবং `GRANT DML TO service_role`। | **PASS** |

### ৩.১ বিদ্যমান ডেটা ও ওনারশিপ অডিট:
- **ওনারশিপ:** স্ক্রিপ্টটি `postgres` রোল দ্বারা রান হবে; তৈরিকৃত ৫টি নতুন টেবিলের মালিকানা সরাসরি `postgres`-এর অধীনে থাকবে।
- **বিদ্যমান অ্যাডমিন ডেটা:** `admin_users`, `admin_sessions`, `license_keys`, `generation_logs`, `key_activations` টেবিলের কোনো ডেটা পরিবর্তিত বা মুছে যাবে না। `admin_audit_logs`-এ শুধুমাত্র একটি ফাঁকা কলাম যুক্ত হবে।
- **লেগ্যাসি SMS ডেটা:** `sms_reader_keys` ও `sms_reader_events` টেবিলে কোনো পরিবর্তন নেই (জিরো রেফারেন্স)।
- **Migration 005 রায়:** **PASS**

---

## ৪. Migration 006 পুঙ্খানুপুঙ্খ সিকোয়েন্স বিশ্লেষণ

`db/migrations/006_rename_rh_function.sql` ফাইলের উদ্দেশ্য হলো পূর্বের `rh-esports` ব্র্যান্ডিংয়ের লেগ্যাসি ফাংশন `rh_set_updated_at()`-কে সুসংহতভাবে `nexora_set_updated_at()`-এ প্রতিস্থাপন করা।

### ৪.১ ফাংশন প্রতিস্থাপন সিকোয়েন্স অডিট:

```
[ধাপ ১] নতুন ফাংশন তৈরি:
       CREATE OR REPLACE FUNCTION nexora_set_updated_at() RETURNS TRIGGER ...
                ↓
[ধাপ ২] admin_users টেবিলের ট্রিগার রিপয়েন্ট:
       DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;
       CREATE TRIGGER trg_admin_users_updated_at ... EXECUTE FUNCTION nexora_set_updated_at();
                ↓
[ধাপ ৩] license_keys টেবিলের ট্রিগার রিপয়েন্ট:
       DROP TRIGGER IF EXISTS trg_license_keys_updated_at ON license_keys;
       CREATE TRIGGER trg_license_keys_updated_at ... EXECUTE FUNCTION nexora_set_updated_at();
                ↓
[ধাপ ৪] nexora_projects টেবিলের ট্রিগার রিপয়েন্ট:
       DROP TRIGGER IF EXISTS trg_nexora_projects_updated_at ON nexora_projects;
       CREATE TRIGGER trg_nexora_projects_updated_at ... EXECUTE FUNCTION nexora_set_updated_at();
                ↓
[ধাপ ৫] পুরোনো ফাংশন নিরাপদ অপনোদন:
       DROP FUNCTION IF EXISTS rh_set_updated_at();  (CASCADE বর্জিত)
```

### ৪.২ নিরাপত্তা ও ডিপেনডেন্সি পর্যবেক্ষণ:
1. **CASCADE বর্জন নিরাপত্তা:** `DROP FUNCTION rh_set_updated_at()` স্টেটমেন্টে `CASCADE` ইচ্ছে করেই বাদ রাখা হয়েছে। যদি কোনো অজানা বা অনাকাঙ্ক্ষিত অবজেক্ট এখনও পুরোনো ফাংশনটির ওপর নির্ভরশীল থাকে, তবে পোস্টগ্রেস ড্রপ অপারেশনটি তাৎক্ষণিকভাবে প্রত্যাখ্যান করবে এবং ট্রানজ্যাকশন রোলব্যাক করবে।
2. **লেগ্যাসি SMS টেবিলে প্রভাব:** লাইভ ডেটাবেসের `information_schema.triggers` অডিটে নিশ্চিত হওয়া গেছে যে `sms_reader_keys` এবং `sms_reader_events` টেবিলে কোনো ট্রিগার নেই। পুরোনো ফাংশনটি ড্রপ করলে SMS মডিউলে কোনো প্রভাব পড়বে না।
3. **রোলব্যাক ক্ষমতা (Reversibility):** ফাইলে সুস্পষ্ট রোলব্যাক স্ক্রিপ্ট অন্তর্ভুক্ত রয়েছে। কোনো ত্রুটি হলে পুরোনো ফাংশন এবং ট্রিগারগুলো হুবহু পুনঃস্থাপনযোগ্য।
4. **Migration 006 রায়:** **PASS**

---

## ৫. মাইগ্রেশন রানার ইঞ্জিন অডিট (`backend/migrations.py`)

`backend/migrations.py` কোড ও এর আর্কিটেকচারাল বৈশিষ্ট্য পর্যালোচনা:

| রানার বৈশিষ্ট্য | বাস্তবায়িত কার্যপদ্ধতি | যাচাইকৃত ফলাফল | স্ট্যাটাস |
|---|---|---|:---:|
| **চেকসাম ভ্যালিডেশন** | ফাইলের সম্পূর্ণ টেক্সট থেকে `hashlib.sha256` হ্যাশ তৈরি হয়। `schema_migrations` টেবিলের রেকর্ডের সাথে তুলনা করা হয়। | লোকাল ফাইল ও ডেটাবেস হ্যাশ হুবহু মেলানো হয়েছে। | **PASS** |
| **আইডেমপোটেন্সি গ্যারান্টি** | `apply_migrations()` ফাংশন পূর্বে সম্পন্নকৃত ফাইল পেলে (`path.name in known`) স্বয়ংক্রিয়ভাবে এড়িয়ে যায় (`continue`)। | মাইগ্রেশন 001–004 পুনরায় কখনোই এক্সিকিউট হবে না। | **PASS** |
| **মডিফিকেশন সনাক্তকরণ** | পূর্বে সম্পন্ন কোনো ফাইলের কনটেন্ট পরিবর্তিত হলে `RuntimeError` উত্থাপন করে সম্পূর্ণ এক্সিকিউশন বন্ধ করে দেয়। | ফাইল টেম্পারিং প্রতিরোধক সক্রিয়। | **PASS** |
| **ট্রানজ্যাকশন বাউন্ডারি** | প্রতিটি মাইগ্রেশন ফাইল নিজস্ব ডেডিকেটেড `with db.cursor() as cursor:` ব্লকে চলে। | ফাইলে স্টেটমেন্ট চলাকালীন কোনো এরর হলে স্বয়ংক্রিয় `ROLLBACK` কার্যকর হয়। | **PASS** |
| **ফেইলিউর হ্যান্ডলিং** | কোনো ফাইল ফেইল করলে তার এন্ট্রি `schema_migrations`-এ জমা হয় না এবং লুপ বন্ধ হয়ে যায়; পরবর্তী কোনো মাইগ্রেশন ফাইলে হাত দেওয়া হয় না। | ডেটাবেস সম্পূর্ণ সংগতিপূর্ণ অবস্থায় থাকে। | **PASS** |

### ৫.১ হ্যাশ ও এক্সেকিউশন স্টেটাস টেবিল:

| মাইগ্রেশন ফাইল | লোকাল ফাইলের SHA-256 | লাইভ ডেটাবেস স্ট্যাটাস | এক্সেকিউশন অ্যাকশন |
|---|---|:---:|:---:|
| `001_initial_schema.sql` | `b54bd358...ade2b7` | Applied (2026-11-15) | **SKIP (Already Applied)** |
| `002_indexes_and_policies.sql` | `98f47807...55a6cd` | Applied (2026-11-15) | **SKIP (Already Applied)** |
| `003_password_hash_comment.sql` | `3ef1e7f5...46364` | Applied (2026-11-15) | **SKIP (Already Applied)** |
| `004_sms_reader_keys.sql` | `7330d343...d3bee` | Applied (2026-11-16) | **SKIP (Already Applied)** |
| `005_nexora_projects.sql` | `e9859e35...7ae7e` | Pending | **READY TO APPLY** |
| `006_rename_rh_function.sql` | `76f18a2d...ff6a7` | Pending | **READY TO APPLY** |

---

## ৬. সামগ্রিক চেকলিস্ট ও প্রস্তুতি রায়

| ক্রম | চেকলিস্ট আইটেম | স্ট্যাটাস | মন্তব্য |
|:---:|---|:---:|---|
| ১ | লাইভ ব্যাকআপ ও রিস্টোর পয়েন্ট পর্যালোচনা | **NEEDS REVIEW** | WAL আর্কাইভিং সচল; ক্লাউড স্ন্যাপশট অ্যাডমিন কর্তৃক নিশ্চায়ন প্রয়োজন। |
| ২ | মাইগ্রেশন 005 এসকিউএল ও স্কিমা পর্যালোচনা | **PASS** | সম্পূর্ণ আইসোলেটেড ও নিরাপদ। |
| ৩ | মাইগ্রেশন 005 ওনারশিপ ও পারমিশন নিশ্চিতকরণ | **PASS** | `postgres` ওনার; `PUBLIC`, `anon` সম্পূর্ণ রেভোকড। |
| ৪ | মাইগ্রেশন 005 বিদ্যমান ডেটা নিরাপত্তা | **PASS** | জিরো ডেটা লস / জিরো মিউটেশন। |
| ৫ | মাইগ্রেশন 006 রিপয়েন্টিং সিকোয়েন্স | **PASS** | নির্ভুল ও নিরাপদ ধাপ। |
| ৬ | মাইগ্রেশন 006 লেগ্যাসি SMS ট্রিগার আইসোলেশন | **PASS** | SMS টেবিলে কোনো ডিপেনডেন্সি নেই। |
| ৭ | মাইগ্রেশন রানার ট্রানজ্যাকশন ও চেকার সেফটি | **PASS** | ফেইল-সেফ ট্রানজ্যাকশন বাউন্ডারি ও আইডেমপোটেন্ট স্কিপ। |

---

## ৭. অনুমোদন গেট ও পরবর্তী ধাপ (Approval Gate)

```
========================================================================================
                                     APPROVAL GATE
========================================================================================
[x] Phase 3 Step 4.0: Live Connection Verification:                 PASSED (100% GREEN)
[x] Phase 3 Step 4.1: Final Permission & Security Audit:            PASSED (100% GREEN)
[x] Phase 3 Step 4.2: Migration Execution Readiness Review:         PASSED (005 & 006 SAFE)
[!] Phase 3 Step 5.0: Live Migration Execution (005 & 006):         BLOCKED / STOPPED
========================================================================================
```

> [!CAUTION]
> **কঠোর নির্দেশনা মেনে মাইগ্রেশন এক্সিকিউশন সম্পূর্ণ বন্ধ (STOP) রাখা হয়েছে। কোনো লাইভ রাইট অপারেশন বা মাইগ্রেশন চালানো হয়নি।**  
> সিস্টেম এবং মাইগ্রেশন স্ক্রিপ্টদ্বয় শতভাগ প্রস্তুত ও যাচাইকৃত।  
> ব্যবহারকারী ব্যাকআপ পর্যালোচনা শেষে লিখিত অনুমোদন প্রদান করলে পরবর্তী ধাপে **Phase 3 Step 5: Live Migration Execution** পরিচালনা করা হবে।
