# OMEGA NEX-ORA — Phase 3: Supabase Live Migration Plan & Verification Strategy

**প্রকল্প:** OMEGA NEX-ORA Database Key Gen  
**তারিখ:** নভেম্বর ১৬, ২০২৬  
**ফেজ:** Phase 3 — Database Migration Execution Plan  
**স্ট্যাটাস:** 🛑 AWAITING EXPLICIT USER APPROVAL BEFORE LIVE EXECUTION  

---

## ১. ওভারভিউ ও মাইগ্রেশন সিকোয়েন্স (Migration Sequence)

NexOra ব্যাকএন্ডের ডেটাবেস স্কিমা মোট ৬টি সুসংহত ও ক্রমানুসারে সাজানো SQL ফাইলের সমন্বয়ে গঠিত। এই ফাইলগুলো `backend/migrations.py`-এর `apply_migrations()` ইঞ্জিনের মাধ্যমে এক্সিকিউট হয় এবং `schema_migrations` টেবিলে SHA-256 চেকারসহ সংরক্ষিত থাকে।

### নির্ধারিত মাইগ্রেশন তালিকা ও এক্সিকিউশন অর্ডার:

| ক্রম | মাইগ্রেশন ফাইল | উদ্দেশ্য ও কার্যকারিতা | আইডেমপোটেন্ট? | ট্রানজ্যাকশন সেফ? |
|:---:|---|---|:---:|:---:|
| **১** | `db/migrations/001_initial_schema.sql` | কোর টেবিল (`admin_users`, `admin_sessions`, `license_keys`, `key_activations`, `generation_logs`, `admin_audit_logs`), `rh_set_updated_at()` ফাংশন ও ট্রিগার | হ্যাঁ (`IF NOT EXISTS`) | হ্যাঁ (`BEGIN ... COMMIT`) |
| **২** | `db/migrations/002_indexes_and_policies.sql` | পারফরম্যান্স ইনডেক্স, ইউনিক ইনডেক্স, RLS এনাবল ও পাবলিক প্রিভিলেজ রেভোকেশন, `rh_expire_overdue_keys()` হেল্পার | হ্যাঁ (`IF NOT EXISTS`, `DO` ব্লক) | হ্যাঁ (`BEGIN ... COMMIT`) |
| **৩** | `db/migrations/003_password_hash_comment.sql` | স্কিমা ডকুমেন্টেশন ও কমেন্ট আপডেট (Argon2id হ্যাশ স্ট্যান্ডার্ড নিশ্চিতকরণ) | হ্যাঁ (`COMMENT ON`) | হ্যাঁ (`BEGIN ... COMMIT`) |
| **৪** | `db/migrations/004_sms_reader_keys.sql` | সেশন অডিয়েন্স আইসোলেশন (`audience` কলাম), রিডার কি ও ইভেন্টস টেবিল (`sms_reader_keys`, `sms_reader_events`) | হ্যাঁ (`ADD COLUMN IF NOT EXISTS`) | হ্যাঁ (`BEGIN ... COMMIT`) |
| **৫** | `db/migrations/005_nexora_projects.sql` | **NexOra কোর প্রজেক্ট আর্কিটেকচার:** `nexora_projects`, `reader_keys`, `reader_installations`, `device_tokens`, `system_settings`, অডিট লগে `project_id`, এবং RLS সিকিউরিটি | হ্যাঁ (`IF NOT EXISTS`, `DO` ব্লক) | হ্যাঁ (`BEGIN ... COMMIT`) |
| **৬** | `db/migrations/006_rename_rh_function.sql` | **লেগ্যাসি ক্লিনআপ:** `rh_set_updated_at()` ট্রিগার ফাংশনটি নিরাপদভাবে `nexora_set_updated_at()`-এ রূপান্তর এবং সব ট্রিগার পুনঃসংযুক্তকরণ | হ্যাঁ (`CREATE OR REPLACE`, `DROP IF EXISTS`) | হ্যাঁ (`BEGIN ... COMMIT`) |

---

## ২. প্রত্যাশিত ডেটাবেস পরিবর্তনসমূহ (Expected Database Changes)

### ২.১ নতুন তৈরিকৃত টেবিলসমূহ (Tables Created)

1. **`admin_users`**: সুপার অ্যাডমিন অ্যাকাউন্ট সংরক্ষণ (Argon2id পাসওয়ার্ড হ্যাশ, ব্রুট-ফোর্স লকআউট স্টেট)।
2. **`admin_sessions`**: অপেক সার্ভার-সাইড সেশন (SHA-256 টোকেন ডাইজেস্ট ও সেশন-বাউন্ড CSRF টোকেন)।
3. **`license_keys`**: জেনারেল লাইসেন্স কি স্টোরেজ (ঐচ্ছিক/লেগ্যাসি কি সাপোর্ট)।
4. **`key_activations`**: লাইসেন্স কি এক্টিভেশন হিস্টোরি ও ডিভাইস বাইন্ডিং।
5. **`generation_logs`**: কি জেনারেশন হিস্টোরি ও ব্যাচ ট্র্যাকিং।
6. **`admin_audit_logs`**: অ্যাডমিন অ্যাক্টিভিটি ট্র্যাকিং অডিট ট্রেইল।
7. **`sms_reader_keys`**: রিডার ওয়ান-টাইম কি (আইসোলেটেড SMS মডিউল স্কিমা)।
8. **`sms_reader_events`**: রিডার ইভেন্ট ট্র্যাকিং।
9. **`nexora_projects`**: মাল্টি-প্রজেক্ট ম্যানেজমেন্ট (কেস-ইনসেনসিটিভ নাম, সফট-ডিলিট)।
10. **`reader_keys`**: প্রজেক্ট-বাউন্ড ওয়ান-টাইম রিডার কি (HMAC-SHA256 হ্যাশ, মাস্কড হিন্ট, পার্মানেন্ট/এক্সপায়ারেবল)।
11. **`reader_installations`**: সক্রিয় রিডার ইনস্টলেশন রেকর্ড (এক প্রজেক্টে একটি সক্রিয় ইনস্টলেশন নীতি)।
12. **`device_tokens`**: রিডার অ্যাপের রিকানেকশন ও হার্টবিট ক্রেডেনশিয়াল ডাইজেস্ট।
13. **`system_settings`**: সিস্টেম রানটাইম কনফিগারেশন কি-ভ্যালু পেয়ার।
14. **`schema_migrations`**: মাইগ্রেশন ট্র্যাকার টেবিল (ফাইলনেম ও চেকার)।

### ২.২ ইনডেক্স ও ইউনিক কনস্ট্রেইন্ট (Indexes & Uniqueness Guarantees)

- **`ux_nexora_projects_name_normalized`**: `nexora_projects(project_name_normalized) WHERE deleted_at IS NULL` — ডুপ্লিকেট প্রজেক্ট প্রতিরোধ (কেস-ইনসেনসিটিভ)।
- **`ux_reader_keys_key_hash`**: `reader_keys(key_hash)` — প্রতিটি কি হ্যাশ ইউনিক।
- **`ux_reader_installations_project_active`**: `reader_installations(project_id) WHERE status = 'active'` — **কঠোরভাবে প্রতি প্রজেক্টে একটি সক্রিয় রিডার ইনস্টলেশন সীমাবদ্ধতা**।
- **`ux_reader_installations_key`**: `reader_installations(reader_key_id)` — একটি কি দ্বিতীয়বার রিডিম হওয়া সম্পূর্ণ অসম্ভব।
- **`ux_reader_installations_token_active`**: `reader_installations(device_token_hash) WHERE status = 'active' AND device_token_hash != ''` — সক্রিয় ডিভাইস ক্রেডেনশিয়ালের ইউনিকনেস।
- **`ux_device_tokens_hash`**: `device_tokens(token_hash) WHERE is_active = true` — সক্রিয় হার্টবিট টোকেন ইউনিকনেস।
- **`ux_admin_users_email_lower`**: `admin_users(lower(email))` — ইউনিক অ্যাডমিন ইমেইল।

### ২.৩ ফাংশন এবং ট্রিগারসমূহ (Functions & Triggers)

- `nexora_set_updated_at()`: `updated_at` ফিল্ড স্বয়ংক্রিয়ভাবে বর্তমান টাইমস্ট্যাম্পে আপডেট করার ট্রিগার ফাংশন।
  - ট্রিগার: `trg_admin_users_updated_at` ON `admin_users`
  - ট্রিগার: `trg_license_keys_updated_at` ON `license_keys`
  - ট্রিগার: `trg_nexora_projects_updated_at` ON `nexora_projects`
- `rh_expire_overdue_keys()`: ডেটাবেস লেভেলে মেয়াদোত্তীর্ণ কিগুলোকে স্বয়ংক্রিয়ভাবে `expired` স্ট্যাটাসে পরিবর্তনকারী ফাংশন।

---

## ৩. কনফ্লিক্ট ও ডিপেনডেন্সি রিস্ক অ্যানালাইসিস (Conflict & Dependency Analysis)

### ৩.১ বিদ্যমান অবজেক্টের সাথে কনফ্লিক্ট ঝুঁকি
1. **পূর্ববর্তী টেবিল উপস্থিতি:** প্রতিটি টেবিলে `CREATE TABLE IF NOT EXISTS` ব্যবহৃত হয়েছে, তাই কোনো টেবিল ইতোমধ্যে থাকলে মাইগ্রেশন ফেইল করবে না।
2. **লেগ্যাসি ফাংশন নাম (`rh_set_updated_at`):** 
   - মাইগ্রেশন `001` এবং `005`-এ `rh_set_updated_at()` কল করা হয়।
   - মাইগ্রেশন `006`-এ এটি নিরাপদে `nexora_set_updated_at()` দ্বারা প্রতিস্থাপিত হয় এবং পূর্ববর্তী সব ট্রিগার নতুন ফাংশনে রি-পয়েন্ট করে পুরোনো ফাংশনটি ড্রপ করা হয়।
   - মাইগ্রেশন `001` থেকে `006` ক্রমানুসারে চললে কোনো ডিপেনডেন্সি এরর বা কনফ্লিক্ট হবে না।
3. **কলাম সংযোজন (`admin_sessions.audience`, `admin_audit_logs.project_id`):** 
   - `ADD COLUMN IF NOT EXISTS` ব্যবহৃত হওয়ায় নিরাপদ।

### ৩.২ রো লেভেল সিকিউরিটি ও পারমিশন (RLS Policies & Permissions)
- Supabase-এর ক্ষেত্রে ডিফল্ট `anon` এবং `authenticated` রোল ব্রাউজার API (PostgREST)-এর সাথে সরাসরি সংযুক্ত থাকে।
- মাইগ্রেশন `002` এবং `005`-এ প্রতিটি টেবিলের জন্য RLS এনাবল করা হয়েছে এবং `anon`, `authenticated`, ও `PUBLIC` রোল থেকে সব ধরনের প্রিভিলেজ (`REVOKE ALL`) বাতিল করা হয়েছে।
- কেবলমাত্র ব্যাকএন্ডের বিশ্বাসযোগ্য সার্ভিস রোল (`service_role` বা ডিরেক্ট সুপারইউজার কানেকশন) সম্পূর্ণ এক্সেস পাবে।
- ফলে বাহ্যিক কোনো ব্রাউজার রিকোয়েস্ট বা রিডার অ্যাপ সরাসরি Supabase PostgREST এন্ডপয়েন্ট দিয়ে ডেটাবেসের টেবিল পড়তে বা পরিবর্তন করতে পারবে না।

---

## ৪. ব্যাকআপ ও রোলব্যাক স্ট্র্যাটেজি (Backup Requirements & Rollback Plan)

### ৪.১ প্রি-মাইগ্রেশন ব্যাকআপ রিকয়ারমেন্ট
লাইভ সুপাবেস ডেটাবেসে মাইগ্রেশন চালানোর পূর্বে অবশ্যই নিম্নোক্ত ব্যাকআপ সম্পন্ন করতে হবে:
1. **Supabase Dashboard Backup:** Supabase Console -> Project Settings -> Database -> Database Backups থেকে সাম্প্রতিক ব্যাকআপ পয়েন্ট নিশ্চিত করা।
2. **Logical Dump (pg_dump):** 
   ```bash
   pg_dump "DATABASE_URL" -Fc -f "nexora_pre_phase3_backup.dump"
   ```

### ৪.২ রোলব্যাক প্ল্যান (Rollback Strategy)

যদি লাইভ মাইগ্রেশনের সময় কোনো আনপ্রত্যাশিত ত্রুটি ঘটে:

1. **স্বয়ংক্রিয় ট্রানজ্যাকশন সেফটি:**
   - প্রতিটি মাইগ্রেশন ফাইল একটি নিজস্ব ডেডিকেটেড ট্রানজ্যাকশনে (`BEGIN ... COMMIT`) চলে। মাঝপথে কোনো সিঙ্গেল কোয়েরি ফেইল করলে সেই মাইগ্রেশনের সমস্ত পরিবর্তন স্বয়ংক্রিয়ভাবে রোলব্যাক হবে।
2. **মাইগ্রেশন ০0৬ রোলব্যাক স্ক্রিপ্ট:**
   `db/migrations/006_rename_rh_function.sql`-এর রোলব্যাক অংশটি নিম্নরূপ:
   ```sql
   BEGIN;
   CREATE OR REPLACE FUNCTION rh_set_updated_at()
   RETURNS TRIGGER LANGUAGE plpgsql AS $$
   BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;

   DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;
   CREATE TRIGGER trg_admin_users_updated_at
     BEFORE UPDATE ON admin_users FOR EACH ROW
     EXECUTE FUNCTION rh_set_updated_at();

   DROP TRIGGER IF EXISTS trg_license_keys_updated_at ON license_keys;
   CREATE TRIGGER trg_license_keys_updated_at
     BEFORE UPDATE ON license_keys FOR EACH ROW
     EXECUTE FUNCTION rh_set_updated_at();

   DROP TRIGGER IF EXISTS trg_nexora_projects_updated_at ON nexora_projects;
   CREATE TRIGGER trg_nexora_projects_updated_at
     BEFORE UPDATE ON nexora_projects FOR EACH ROW
     EXECUTE FUNCTION rh_set_updated_at();

   DROP FUNCTION IF EXISTS nexora_set_updated_at();
   COMMIT;
   ```
3. **টেবিল লেভেল ড্রপ (জরুরি ডিসাস্টার রিকভারি):**
   যদি ক্লিন রিস্টার্ট প্রয়োজন হয়, তবে রিভার্স ক্রমানুসারে টেবিল ড্রপ করা সম্ভব:
   ```sql
   DROP TABLE IF EXISTS device_tokens CASCADE;
   DROP TABLE IF EXISTS reader_installations CASCADE;
   DROP TABLE IF EXISTS reader_keys CASCADE;
   DROP TABLE IF EXISTS nexora_projects CASCADE;
   DROP TABLE IF EXISTS system_settings CASCADE;
   DROP TABLE IF EXISTS sms_reader_events CASCADE;
   DROP TABLE IF EXISTS sms_reader_keys CASCADE;
   DROP TABLE IF EXISTS admin_audit_logs CASCADE;
   DROP TABLE IF EXISTS generation_logs CASCADE;
   DROP TABLE IF EXISTS key_activations CASCADE;
   DROP TABLE IF EXISTS license_keys CASCADE;
   DROP TABLE IF EXISTS admin_sessions CASCADE;
   DROP TABLE IF EXISTS admin_users CASCADE;
   DROP TABLE IF EXISTS schema_migrations CASCADE;
   ```

---

## ৫. পোস্ট-মাইগ্রেশন ভেরিফিকেশন কোয়েরিসমূহ (Verification Queries)

লাইভ মাইগ্রেশন সম্পন্ন হওয়ার পর ডেটাবেসের সুস্থতা যাচাই করার জন্য নিম্নোক্ত রিড-অনলি কোয়েরিগুলো চালাতে হবে:

### ১. মাইগ্রেশন সম্পন্ন হওয়ার স্ট্যাটাস চেক:
```sql
SELECT filename, applied_at, checksum 
FROM schema_migrations 
ORDER BY filename ASC;
```
*(প্রত্যাশিত ফলাফল: ৬টি মাইগ্রেশন ফাইলের রেকর্ড উপস্থিত থাকবে।)*

### ২. প্রয়োজনীয় সকল টেবিলের উপস্থিতি যাচাই:
```sql
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;
```
*(প্রত্যাশিত ফলাফল: admin_audit_logs, admin_sessions, admin_users, device_tokens, generation_logs, key_activations, license_keys, nexora_projects, reader_installations, reader_keys, schema_migrations, sms_reader_events, sms_reader_keys, system_settings।)*

### ৩. RLS সক্রিয়তা যাচাই:
```sql
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' 
ORDER BY tablename;
```
*(প্রত্যাশিত ফলাফল: প্রতিটি টেবিলের `rowsecurity = true`।)*

### ৪. রিডার ইনস্টলেশন ও প্রজেক্ট ইউনিক ইনডেক্স যাচাই:
```sql
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE schemaname = 'public' 
  AND indexname IN (
    'ux_nexora_projects_name_normalized',
    'ux_reader_keys_key_hash',
    'ux_reader_installations_project_active',
    'ux_reader_installations_key',
    'ux_device_tokens_hash'
  );
```

### ৫. ট্রিগার ফাংশন যাচাই:
```sql
SELECT routine_name 
FROM information_schema.routines 
WHERE routine_schema = 'public' 
  AND routine_name IN ('nexora_set_updated_at', 'rh_expire_overdue_keys');
```

---

## ৬. লাইভ মাইগ্রেশন এক্সিকিউশন গেট (IMPORTANT APPROVAL GATE)

> [!CAUTION]
> **কঠোর স্টপ নির্দেশনা (STRICT APPROVAL GATE):**
> - বর্তমান প্রিফ্লাইট অডিট এবং মাইগ্রেশন প্ল্যান সম্পন্ন হয়েছে।
> - ব্যবহারকারীর সুস্পষ্ট লিখিত নির্দেশ ও অনুমোদন ব্যতিরেকে কোনো লাইভ ডেটাবেস সংযোগ বা মাইগ্রেশন স্ক্রিপ্ট চালানো হবে না।
> - লাইভ ডেটাবেসে কোনো অবজেক্ট তৈরি বা মোডিফাই করার পূর্বে ব্যবহারকারীর অনুমতির অপেক্ষা করা হচ্ছে।
