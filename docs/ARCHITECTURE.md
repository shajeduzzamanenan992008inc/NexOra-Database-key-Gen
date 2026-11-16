# NexOra Target Architecture & System Design
## Phase 1 — Secure Admin Backend Foundation

### ১. ওভারভিউ ও প্রজেক্ট বাউন্ডারি (Project Scope & Boundary)

**OMEGA NEX-ORA Database Key Gen** হলো একটি প্রোডাকশন-গ্রেড, সিকিউর অ্যাডমিন ও রিডার কি ম্যানেজমেন্ট ব্যাকএন্ড।

> [!IMPORTANT]
> **কঠোর বাউন্ডারি (Project Boundary):**
> - এই রিপোজিটরিটি কেবলমাত্র **NexOra Admin সিস্টেম** এবং এর **সার্ভার-সাইড REST API**-এর জন্য।
> - **Android Reader App** সম্পূর্ণ আলাদা একটি প্রজেক্ট ও রিপোজিটরি। এই রিপোজিটরিতে কোনো অ্যান্ড্রয়েড সোর্স কোড, গ্রেডল ফাইল বা অ্যান্ড্রয়েড UI থাকবে না।
> - NexOra ব্যাকএন্ড কেবল সুরক্ষিত REST API প্রদান করবে যা রিডার অ্যাপ নিরাপদে কল করতে পারে।

---

### ২. টার্গেট আর্কিটেকচার (Target Architecture)

```
+-------------------------------------------------------------+
|                  NexOra Admin Dashboard                     |
|           (HTML5 / CSS / Vanilla JS - No privileged keys)   |
+-------------------------------------------------------------+
                               |
                               | HTTPS / SameSite Strict Cookies + CSRF
                               v
+-------------------------------------------------------------+
|                     Flask Backend Layer                     |
|  - Request Validation & Sliding-Window Rate Limiting        |
|  - Argon2id Password Verification & Session Management      |
|  - Project Management & Case-Insensitive Name Normalization |
|  - Cryptographic Key Generator (CSPRNG, 80+ bits entropy)   |
|  - HMAC-SHA256 Keyed Hashing (KEY_PEPPER)                   |
|  - Atomic Key Redemption & Device Token Generation          |
|  - Versioned REST API (/api/v1/)                            |
+-------------------------------------------------------------+
                               |
                               | TLS Connection Pool (pg8000)
                               v
+-------------------------------------------------------------+
|                  Supabase PostgreSQL Layer                  |
|  - Managed PostgreSQL Database                              |
|  - Row-Level Security (RLS) Deny-by-Default                 |
|  - Strict Foreign Keys, Unique Indexes, Cascades & Triggers |
+-------------------------------------------------------------+
                               ^
                               | HTTPS /api/v1/reader/
+-------------------------------------------------------------+
|              Separate Android Reader App (External)         |
|  - Submits: Project Name, Database URL, Plaintext Reader Key|
|  - Receives: High-Entropy Bearer Device Credential (Once)   |
|  - NEVER receives Supabase Service Role Key or DB Password  |
+-------------------------------------------------------------+
```

---

### ৩. সিকিউরিটি মডেল ও ক্রেডেনশিয়াল আইসোলেশন (Security Model)

1. **বিশ্বাসযোগ্য সার্ভার-সাইড লেয়ার (Trusted Backend):**
   - একমাত্র Flask ব্যাকএন্ডের কাছে ডেটাবেসের সংযোগ স্ট্রিং (`DATABASE_URL`) এবং `SUPABASE_SERVICE_ROLE_KEY` থাকে।
   - ব্রাউজার অ্যাডমিন কনসোল অথবা এক্সটার্নাল রিডার অ্যাপ কখনোই প্রিভিলেজড ডেটাবেস পাসওয়ার্ড বা সার্ভিস রোল কি দেখতে পারে না।
2. **কি হ্যাশিং (HMAC-SHA256 Storage):**
   - কোনো প্লেইনটেক্সট লাইসেন্স কি বা রিডার কি ডেটাবেসে সংরক্ষণ করা হয় না।
   - রিডার কি-এর ক্ষেত্রে ডোমেইন সেপারেশন `nexora:v1:reader-key:<normalized_key>` সহ `KEY_PEPPER` ব্যবহার করে HMAC-SHA256 হ্যাশ সংরক্ষণ করা হয়।
   - প্লেইনটেক্সট কি শুধুমাত্র জেনারেশনের সময় একবার অ্যাডমিনকে দেখানো হয়।
3. **এক প্রজেক্ট - এক রিডার ইনস্টলেশন নীতি (Single Installation Enforced):**
   - প্রতিটি প্রজেক্টে একই সময়ে সর্বোচ্চ **একটি** সক্রিয় রিডার ইনস্টলেশন থাকতে পারে।
   - এটি ডেটাবেসে পার্সিয়াল ইউনিক ইনডেক্স (`ux_reader_installations_project_active`) দ্বারা গ্যারান্টিযুক্ত।

---

### ৪. ডেটাবেস স্কিমা ডিজাইন (Database Entities)

Phase 1 এর ডেটাবেস ফাউন্ডেশনে নিম্নোক্ত টেবিলসমূহ অন্তর্ভুক্ত:

1. **`admin_users`**:
   - সিঙ্গেল সুপার অ্যাডমিন অ্যাকাউন্ট।
   - `password_hash`: Argon2id ক্রিপ্টোগ্রাফিক হ্যাশ।
   - `failed_login_attempts` এবং `locked_until` দিয়ে ব্রুট-ফোর্স প্রতিরোধ।
2. **`admin_sessions`**:
   - সার্ভার-সাইড অপেক সেশন। টোকেনের SHA-256 ডাইজেস্ট ও সেশন-বাউন্ড CSRF টোকেন সংরক্ষিত।
3. **`nexora_projects`**:
   - প্রতিটি প্রজেক্টের `project_name` এবং কেস-ইনসেনসিটিভ ইউনিকনেসের জন্য `project_name_normalized`।
   - `status`: `active`, `inactive`, `deleted`।
   - অডিট হিস্টোরি ঠিক রাখার জন্য সেফ সফট-ডিলিট (`deleted_at`)।
4. **`reader_keys`**:
   - প্রজেক্ট-বাউন্ড ওয়ান-টাইম রিডার কি।
   - স্টেট লাইফসাইকেল: `GENERATED` -> `USED` (অথবা `EXPIRED`, `REVOKED`)।
   - `key_hash` (HMAC-SHA256, ইউনিক ইনডেক্স), `key_hint` (মাস্কড রিডার কি), `is_permanent`, `expires_at`।
5. **`reader_installations`**:
   - রিডার কি সফলভাবে এক্টিভেশনের পর ইনস্টলেশন রেকর্ড তৈরি হয়।
   - `device_token_hash`: ইস্যু করা হাই-এনট্রপি ক্রেডেনশিয়ালের হ্যাশ।
   - `status`: `active`, `revoked`।
6. **`device_tokens`**:
   - রিডার অ্যাপের সাথে সাবসিকুয়েন্ট রিকানেকশন ও হার্টবিটের জন্য টোকেন ট্র্যাকিং।
7. **`system_settings`**:
   - রানটাইম সিস্টেম কনফিগারেশন কি-ভ্যালু স্টোর।
8. **`admin_audit_logs`**:
   - অ্যাডমিন অ্যাকশন, রিডার এক্টিভেশন এবং রেভোকেশন ট্রেইল।

---

### ৫. রিডার কানেকশন আর্কিটেকচার (Reader Activation Flow)

```
[Reader App]                                        [NexOra Flask API]                      [PostgreSQL]
      |                                                      |                                   |
      | 1. POST /api/v1/reader/activate                     |                                   |
      |    {project_name, database_url, reader_key}         |                                   |
      |----------------------------------------------------->|                                   |
      |                                                      | 2. Verify database_url == configured
      |                                                      | 3. Normalize project_name         |
      |                                                      | 4. Check active project exists    |
      |                                                      | 5. Verify NO active installation  |
      |                                                      | 6. SELECT key FOR UPDATE -------->| (Row lock)
      |                                                      |    - Verify key belongs to project|
      |                                                      |    - Verify key status == GENERATED
      |                                                      |    - Verify not expired           |
      |                                                      | 7. UPDATE key SET status='used' ->|
      |                                                      | 8. INSERT reader_installation --->|
      |                                                      | 9. INSERT device_token ---------->|
      |                                                      |                                   |
      | 10. HTTP 200 OK                                      |                                   |
      |     {status: "activated", device_credential: "..."}  |<----------------------------------+
      |<-----------------------------------------------------|
      |
      | (Subsequent reconnect / heartbeat)
      | 11. POST /api/v1/reader/heartbeat
      |     Header: Authorization: Bearer <device_credential>
      |----------------------------------------------------->|
      |                                                      | 12. Verify HMAC(device_credential)
      |                                                      | 13. Update last_seen_at / last_used
      |<-----------------------------------------------------|
```

---

### ৬. এপিআই আর্কিটেকচার (`/api/v1/`)

| মেথড | এন্ডপয়েন্ট | পারমিশন | বর্ণনা |
|---|---|---|---|
| `GET` | `/api/v1/health` | পাবলিক | সিস্টেম ও ডেটাবেস হেলথ চেক |
| `POST` | `/api/v1/projects` | Admin/Owner | নতুন প্রজেক্ট তৈরি (কেস-ইনসেনসিটিভ নাম) |
| `GET` | `/api/v1/projects` | Admin/Viewer | প্রজেক্ট লিস্ট ও স্ট্যাটাস |
| `GET` | `/api/v1/projects/<id>` | Admin/Viewer | সিঙ্গেল প্রজেক্ট ডিটেইলস |
| `DELETE` | `/api/v1/projects/<id>` | Owner Only | প্রজেক্ট সফট-ডিলিট ও অথোরাইজেশন রেভোকেশন |
| `POST` | `/api/v1/projects/<id>/keys` | Admin/Owner | সিঙ্গেল রিডার কি জেনারেট (একবার প্লেইনটেক্সট প্রদান) |
| `POST` | `/api/v1/projects/<id>/keys/bulk` | Admin/Owner | বাল্ক রিডার কি জেনারেশন |
| `POST` | `/api/v1/keys/<id>/revoke` | Admin/Owner | রিডার কি বাতিলকরণ (Revocation) |
| `POST` | `/api/v1/reader/activate` | পাবলিক (রিডার অ্যাপ) | রিডার কি ভ্যালিডেশন ও এক্টিভেশন |
| `GET` | `/api/v1/reader/status` | রিডার ক্রেডেনশিয়াল | রিডার অথোরাইজেশন স্ট্যাটাস যাচাই |
| `POST` | `/api/v1/reader/heartbeat` | রিডার ক্রেডেনশিয়াল | রিডার অ্যাপের পর্যায়ক্রমিক হার্টবিট |
| `POST` | `/api/v1/admin/installations/<id>/revoke` | Admin/Owner | নির্দিষ্ট রিডার ইনস্টলেশন ও ক্রেডেনশিয়াল বাতিল |

---

### ৭. ফেজ ২ ড্যাশবোর্ড UI আর্কিটেকচার (Phase 2 Admin Dashboard UI Architecture)

ফেজ ২-এ সম্পূর্ণ প্রোডাকশন-গ্রেড ও রেসপন্সিভ ড্যাশবোর্ড তৈরি করা হয়েছে যা ৮টি প্রধান ভিউ এবং ৭টি ইন্টারঅ্যাক্টিভ মোডাল ধারণ করে:

1. **Overview (সিস্টেম ওভারভিউ):**
   - লাইভ মেট্রিক্স কার্ড: মোট প্রজেক্ট, অ্যাক্টিভ প্রজেক্ট, মোট কি, অব্যবহৃত (Unused) কি, ব্যবহৃত (Used) কি, মেয়াদোত্তীর্ণ (Expired) কি, বাতিল (Revoked) কি, এবং অ্যাক্টিভ ইনস্টলেশন।
   - সিস্টেম হেলথ মনিটর: ব্যাকএন্ড স্ট্যাটাস, ডেটাবেস সংযোগ (Supabase PostgreSQL), এবং রিসেন্ট অডিট হিস্টোরি।

2. **Project Management (প্রজেক্ট ব্যবস্থাপনা):**
   - প্রজেক্ট তৈরি, অনুসন্ধান, বিবরণী প্রদর্শন, স্ট্যাটাস পরিবর্তন (`active`/`inactive`), এবং নিরাপদ সফট-ডিলিট (`soft-delete`)।
   - ডিলিট করলে তৎক্ষণাৎ সংশ্লিষ্ট সমস্ত ইনস্টলেশন ও রিডার অ্যাক্সেস বাতিল হয়।

3. **Key Generator (রিডার কি জেনারেটর):**
   - একক কি বা বাল্ক কি (১০, ২৫, ৫০, ১০০ পর্যন্ত) নিরাপদ জেনারেশন।
   - পার্মানেন্ট কি অথবা কাস্টম মেয়াদের অপশন।
   - CSPRNG ভিত্তিক কি তৈরি; শুধুমাত্র একবার জেনারেশন মোডালে প্লেইনটেক্সট প্রদর্শিত হয় এবং কপি বা CSV ডাউনলোডের সুযোগ থাকে। ব্যাকএন্ডে শুধুমাত্র HMAC-SHA256 সেভ হয়।

4. **Key Management (কি ব্যবস্থাপনা):**
   - সম্পূর্ণ সার্চ, প্রজেক্ট ফিল্টার, স্ট্যাটাস ফিল্টার (`generated`, `used`, `expired`, `revoked`), পেজিনেশন এবং কি ডিটেইলস।
   - আনইউজড কি বাতিল (Revoke) করার সুবিধা; ব্যবহৃত (Used) কি স্থায়ীভাবে অপরিবর্তনীয়।
   - CSV এক্সপোর্ট ফিচার।

5. **Installation Management (ইনস্টলেশন ব্যবস্থাপনা):**
   - প্রতি প্রজেক্টে একটি অ্যাক্টিভ ইনস্টলেশন রুল।
   - ডিভাইস ইনস্টলেশন আইডি, প্রজেক্ট, রেজিস্ট্রেশন সময়, সর্বশেষ হার্টবিট ও স্ট্যাটাস প্রদর্শন।
   - ইনস্টলেশন রেভোকেশন (Revocation) অ্যাকশন যা তাত্ক্ষণিকভাবে ডিভাইস ক্রেডেনশিয়াল অকার্যকর করে।

6. **Security Center (সিকিউরিটি সেন্টার):**
   - অ্যাডমিন অথেন্টিকেশন স্ট্যাটাস, সেশন সিকিউরিটি কনফিগারেশন, রেট-লিমিট প্যারামিটারস, এবং সাম্প্রতিক সিকিউরিটি ইভেন্টস।
   - কোনো সিক্রেট কি, পেপার বা ডেটাবেস পাসওয়ার্ড ক্লায়েন্টে পাঠানো হয় না।

7. **Audit Logs (অডিট লগ):**
   - ফিল্টারেবল এবং পেজিনেটেড অডিট ট্রেইল (লগইন, ফেইল্ড লগইন, প্রজেক্ট ক্রিয়েশন, কি জেনারেশন, এক্টিভেশন, রেভোকেশন)।

8. **Settings (সিস্টেম সেটিংস):**
   - কি প্রিফিক্স, ডিফল্ট এক্সপায়ারি, বাল্ক লিমিট, হার্টবিট ইন্টারভাল, রেট লিমিট এবং মেইন্টেনেন্স মোড কনফিগারেশন।

---

### ৮. ফেজ ২ এপিআই রুটসমূহ (`/api/v1/`)

| মেথড | এন্ডপয়েন্ট | পারমিশন | বর্ণনা |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | পাবলিক (রেট লিমিটেড) | সুপার অ্যাডমিন লগইন ও সুরক্ষিত কুকি সেশন প্রদান |
| `POST` | `/api/v1/auth/logout` | অথেন্টিকেটেড | বর্তমান সেশন বাতিল ও কুকি ক্লিয়ার |
| `GET` | `/api/v1/auth/session` | পাবলিক/অথেন্টিকেটেড | সেশন ভ্যালিডিটি ও CSRF টোকেন চেক |
| `GET` | `/api/v1/dashboard/stats` | অথেন্টিকেটেড | ড্যাশবোর্ডের সামগ্রিক রিয়েলটাইম কাউন্টার ও হেলথ মেট্রিক্স |
| `PATCH` | `/api/v1/projects/<id>/status` | Admin/Owner | প্রজেক্ট একটিভ বা ইনঅ্যাক্টিভ করা |
| `GET` | `/api/v1/keys` | অথেন্টিকেটেড | ফিল্টার এবং পেজিনেশন সহ রিডার কি তালিকা |
| `GET` | `/api/v1/keys/<id>` | অথেন্টিকেটেড | নির্দিষ্ট কি-এর বিস্তারিত (শুধুমাত্র মাস্কড হিন্ট) |
| `GET` | `/api/v1/keys/export` | অথেন্টিকেটেড | কি তালিকার নিরাপদ CSV এক্সপোর্ট |
| `GET` | `/api/v1/installations` | অথেন্টিকেটেড | ইনস্টলেশন তালিকা ও হার্টবিট ট্র্যাকিং |
| `GET` | `/api/v1/installations/<id>` | অথেন্টিকেটেড | ইনস্টলেশন বিস্তারিত ও সম্পর্কিত অডিট লগ |
| `POST` | `/api/v1/installations/<id>/revoke` | Admin/Owner | ইনস্টলেশন ও রিডার কানেকশন বাতিলকরণ |
| `GET` | `/api/v1/security/status` | অথেন্টিকেটেড | সিস্টেম সিকিউরিটি প্যারামিটারস ও ইভেন্টস |
| `GET` | `/api/v1/audit-logs` | অথেন্টিকেটেড | পেজিনেটেড ও সার্চেবল অডিট ট্রেইল |
| `GET` | `/api/v1/settings` | অথেন্টিকেটেড | সিস্টেম সেটিংস কনফিগারেশন রিড |
| `PATCH` | `/api/v1/settings` | Admin/Owner | নিরাপদ সিস্টেম সেটিংস আপডেট |

---

### ৯. সিকিউরিটি এবং CSP কমপ্লায়েন্স গ্যারান্টি (Security & CSP Guarantees)

- **Content Security Policy (CSP):** কোনো ইনলাইন স্টাইল (`style="..."`) বা ইনলাইন ইভেন্ট হ্যান্ডলার (`onclick="..."`) নেই। সমস্ত স্ক্রিপ্ট ও স্টাইলশীট এক্সটার্নাল ফাইলে আইসোলেটেড।
- **XSS প্রতিরোধ:** কোনো `innerHTML` ব্যবহার নিষিদ্ধ; DOM ম্যানিপুলেশনের জন্য নিরাপদ `document.createElement`, `textContent` এবং `setAttribute` ব্যবহার করা হয়েছে।
- **পাসওয়ার্ড হ্যাশিং:** সুপার অ্যাডমিনের জন্য মেমরি-হার্ড **Argon2id** অ্যালগরিদম ব্যবহৃত।
- **ক্রেডেনশিয়াল আইসোলেশন:** `SUPABASE_SERVICE_ROLE_KEY`, `KEY_PEPPER`, `DATABASE_URL` বা প্লেইনটেক্সট কি কখনোই ব্রাউজারে রিটার্ন হয় না।

