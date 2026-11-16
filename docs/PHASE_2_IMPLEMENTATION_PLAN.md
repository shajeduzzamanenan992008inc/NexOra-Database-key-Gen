# OMEGA NEX-ORA — Phase 2: Implementation Plan
**তারিখ:** ২০২৬-১১-১৬  
**প্রজেক্ট:** OMEGA NEX-ORA Database Key Gen  
**উদ্দেশ্য:** Complete Admin Dashboard + Hardened Backend Integration  
**স্ট্যাটাস:** APPROVED & READY FOR IMPLEMENTATION

---

## ১. প্রজেক্ট বাউন্ডারি ও মূল লক্ষ্য (Project Boundary & Scope)

### কঠোর বাউন্ডারি (Strict Boundary)
- এই রিপোজিটরিটি কেবল **OMEGA NEX-ORA Admin System**, **Flask Backend**, **Versioned REST APIs (`/api/v1/`)** এবং **Supabase PostgreSQL** ইন্টিগ্রেশনের জন্য সীমাবদ্ধ।
- **Android SMS Reader App সম্পূর্ণ পৃথক একটি প্রজেক্ট ও রিপোজিটরি।** কোনো অ্যান্ড্রয়েড সোর্স কোড, Gradle স্ক্রিপ্ট বা অ্যান্ড্রয়েড UI এখানে যোগ করা হবে না।
- ব্যাকএন্ড কেবল সুরক্ষিত REST API প্রদান করবে যার মাধ্যমে এক্সটার্নাল রিডার অ্যাপ ওয়ান-টাইম রিডেম্পশন এবং পরবর্তীতে অ্যাথেনটিকেটেড হার্টবিট সম্পন্ন করতে পারে।

### Phase 2 মূল লক্ষ্য
- বিদ্যমান রিপোজিটরির আর্কিটেকচার অক্ষুণ্ণ রেখে একটি কমপ্লিট, রেসপনসিভ, প্রিমিয়াম ডার্ক-থিম OMEGA NEX-ORA Admin Dashboard তৈরি করা।
- ড্যাশবোর্ডের প্রতিটি পেজ, বাটন, ফর্ম, কাউন্টার, টেবিল এবং ম্যানেজমেন্ট অপারেশনকে রিয়েল ব্যাকএন্ড এন্ডপয়েন্টের সাথে যুক্ত করা (কোনো ফেইক বা ক্লায়েন্ট-অনলি ডেমো ফিচার থাকবে না)।

---

## ২. বিদ্যমান আর্কিটেকচার রিরিপোর্ট (Re-Audit Findings)

### ২.১ বিদ্যমান Admin UI আর্কিটেকচার
1. **Frontend Architecture:**
   - সার্ভার-সাইড রেন্ডারড শেল: Flask রুট `/dashboard` সরাসরি `frontend/dashboard.html` রিটার্ন করে (ভ্যালিড সেশন চেক সাপেক্ষে)।
   - লগইন রুট `/` এবং `/login` পরিবেশন করে `frontend/index.html`।
   - স্ট্যাটিক অ্যাসেটস: `frontend/assets/app.css`, `frontend/assets/logo.svg`, `frontend/assets/favicon.svg` এবং মডিউলার ভ্যানিলা জাভাস্ক্রিপ্ট (`api.js`, `ui.js`, `login.js`, `dashboard.js`)।
   - বর্তমানে ড্যাশবোর্ডে কেবল তিনটি ভিউ ছিল: `overview`, `keys` (পুরানো লাইসেন্স কি ভিত্তিক), `logs`।
   - বিদ্যমান `frontend/assets/js/api.js` এখনও পুরানো আনভার্সনড `/api/` এন্ডপয়েন্ট ব্যবহার করছিল।

### ২.২ বিদ্যমান লগইন ও অথেনটিকেশন বাস্তবায়ন
- **Super Admin:** সিঙ্গেল সুপার অ্যাডমিন মডেল (`ADMIN_EMAIL`, `ADMIN_PASSWORD_HASH`), `backend/services/admin_bootstrap.py` দিয়ে ফার্স্ট বুটে প্রোমিশন করা হয়। কোনো পাবলিক রেজিস্ট্রেশন নেই।
- **Password Hashing:** Argon2id ক্রিপ্টোগ্রাফিক হ্যাশ (`argon2-cffi`), মেমোরি খরচ 64 MiB, 3 আইটারেশন, 4 প্যারালাল থ্রেড।
- **Session Management:** সার্ভার-সাইড অপেক সেশন টোকেন (32-বাইট CSPRNG), SHA-256 ডাইজেস্ট `admin_sessions` টেবিলে সংরক্ষিত। সেশন অডিয়েন্স `keygen`।
- **Cookie Settings:** `nexora_session` কুকি `HttpOnly=True`, `SameSite=Strict`, প্রোডাকশনে `Secure=True`, `Path=/`।
- **CSRF Protection:** ডাবল-সাবমিট কুঁকি ও সেশন বাউন্ড টোকেন `X-CSRF-Token` হেডার দ্বারা নিয়ন্ত্রিত।
- **Brute-force Resistance:** `failed_login_attempts` ট্র্যাকিং, ৫ বার ব্যর্থ হলে ১৫ মিনিটের জন্য অ্যাকাউন্ট লক। টাইমিং অ্যাটাক প্রতিরোধে ডামি হ্যাশ ভেরিফিকেশন।

### ২.৩ বিদ্যমান API Endpoints ও ফরম্যাট
- `GET /api/health` — আনভার্সনড সিস্টেম ও ডিবি হেলথ চেক।
- `GET /api/v1/health` — ভার্সনড হেলথ চেক।
- `POST /api/auth/login` — সুপার অ্যাডমিন লগইন।
- `POST /api/auth/logout` — সেশন ইনভ্যালিডেশন।
- `GET /api/auth/session` — সেশন রি-ভ্যালিডেশন ও CSRF ফ্রেশ টোকেন প্রদান।
- `POST /api/v1/projects` — নতুন প্রজেক্ট তৈরি (কেস-ইনসেনসিটিভ নাম)।
- `GET /api/v1/projects` — প্রজেক্ট তালিকা ও স্ট্যাটাস।
- `GET /api/v1/projects/<id>` — প্রজেক্ট ডিটেইলস ও সক্রিয় ইনস্টলেশন সারাংশ।
- `DELETE /api/v1/projects/<id>` — সফট-ডিলিট প্রজেক্ট এবং সংশ্লিষ্ট সব কী ও রিডার ইনস্টলেশন বাতিল।
- `POST /api/v1/projects/<id>/keys` — প্রজেক্টের জন্য একটি রিডার কি তৈরি (প্লেইনটেক্সট একবারই রিটার্ন হয়)।
- `POST /api/v1/projects/<id>/keys/bulk` — বাল্ক রিডার কি তৈরি।
- `POST /api/v1/keys/<key_id>/revoke` — নির্দিষ্ট রিডার কি বাতিল।
- `POST /api/v1/admin/installations/<id>/revoke` — রিডার ইনস্টলেশন ও ক্রেডেনশিয়াল বাতিল।
- `POST /api/v1/reader/activate` — রিডার অ্যাপ এক্টিভেশন।
- `POST /api/v1/reader/heartbeat` — রিডার হার্টবিট।
- `GET /api/v1/reader/status` — রিডার অথোরাইজেশন ভেরিফিকেশন।

### ২.৪ ড্যাশবোর্ডের জন্য প্রয়োজনীয় নতুন/অনুপস্থিত API Endpoints
1. `GET /api/v1/dashboard/stats`: রিয়েল ওভারভিউ মেট্রিক্স (মোট প্রজেক্ট, অ্যাক্টিভ প্রজেক্ট, মোট কী, অব্যবহৃত কী, ব্যবহৃত কী, মেয়াদোত্তীর্ণ কী, বাতিলকৃত কী, অ্যাক্টিভ ইনস্টলেশন, সাম্প্রতিক অডিট ও ডিবি কানেক্টিভিটি)।
2. `PATCH /api/v1/projects/<id>/status`: প্রজেক্ট সক্রিয়করণ ও নিষ্ক্রিয়করণ (`active` <-> `inactive`)।
3. `GET /api/v1/keys`: পেজিনেটেড রিডার কী তালিকা (সার্চ, প্রজেক্ট ফিল্টার, স্ট্যাটাস ফিল্টার, এক্সপায়ারি ফিল্টার, সর্টিং)।
4. `GET /api/v1/keys/<id>`: একক রিডার কী-এর বিস্তারিত তথ্য (মাস্কড কী, প্রজেক্ট, হিস্টোরি)।
5. `GET /api/v1/installations`: পেজিনেটেড রিডার ইনস্টলেশন তালিকা (সার্চ, প্রজেক্ট ফিল্টার, স্ট্যাটাস ফিল্টার)।
6. `GET /api/v1/installations/<id>`: ইনস্টলেশন বিস্তারিত ও হার্টবিট হিস্টোরি।
7. `GET /api/v1/security/status`: সিকিউরিটি সেন্টার স্ট্যাটাস (অ্যাডমিন অথ স্ট্যাটাস, সেশন সিকিউরিটি কনফিগ, রেট লিমিটার, পাসওয়ার্ড হ্যাশিং মোড, ডিবি কানেকশন নিরাপত্তা, অডিট পরিসংখ্যান; কোনো সিক্রেট ছাড়া)।
8. `GET /api/v1/audit-logs`: ফিল্টারযোগ্য এবং পেজিনেটেড অডিট লগ (ইভেন্ট ফিল্টার, ডেট রেঞ্জ, প্রজেক্ট ফিল্টার)।
9. `GET /api/v1/settings` & `PATCH /api/v1/settings`: নিরাপদ রানটাইম সেটিংস দেখা ও আপডেট করা (কী প্রিফিক্স, বাল্ক লিমিট, হার্টবিট ইন্টারভাল, টাইমজোন)।
10. `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/session`: ভার্সনড রুটেও অথেনটিকেশন অ্যাভেইলেবল রাখা।

### ২.৫ পুনর্ব্যবহারযোগ্য UI উপাদান (Reusable Components)
- `frontend/assets/app.css` এর কাস্টম প্রোপার্টিজ, গ্লাসকার্ড (`.glass`), প্যানেল ও স্ট্যাট কার্ড ডিজাইন।
- নেটিভ HTML5 `<dialog>` ভিত্তিক মোডাল ডায়ালগ ও ব্যাকড্রপ ব্লার।
- টোস্ট নোটিফিকেশন সিস্টেম (`toast(message, opts)` in `ui.js`)।
- ডেটা টেবিল স্টাইলিং (`.data-table`, `.table-wrap`, সর্ট বাটন ও পেজার)।
- স্ট্যাটাস চিপস এবং পালস ডট ইন্ডিকেটরস।

### ২.৬ সিকিউরিটি সমস্যা ও ইন্টিগ্রেশন ঝুঁকি (Security & Integration Risks)
- **জিরো সিক্রেট এক্সপোজার:** কোনো অবস্থাতেই `SUPABASE_SERVICE_ROLE_KEY`, `KEY_PEPPER`, `SECRET_KEY` বা ডেটাবেস পাসওয়ার্ড কোনো API রেসপন্স বা ক্লায়েন্ট বান্ডলে যাবে না।
- **প্লেইনটেক্সট কী লিক প্রতিরোধ:** প্লেইনটেক্সট রিডার কী কেবলমাত্র তৈরির সময় ইন-মেমোরি রিটার্ন হবে, ডেটাবেসে কেবল HMAC-SHA256 সংরক্ষিত থাকবে। পরবর্তীতে রিট্রিভ করার কোনো এন্ডপয়েন্ট থাকবে না।
- **ব্যবহৃত কী-এর স্থায়ীত্ব (Used Key Permanence):** রিডিম হওয়া কী আজীবন `used` থাকবে; কখনো পুনরায় `generated` বা রিইউজেবল করা যাবে না।
- **প্রজেক্ট সফট-ডিলিট সুরক্ষা:** প্রজেক্ট সফট-ডিলিট হলে তাৎক্ষণিকভাবে তার সমস্ত সক্রিয় ইনস্টলেশন বাতিল হবে এবং ডিভাইস ক্রেডেনশিয়াল নিষ্ক্রিয় হবে।
- **লাইভ ডিবি সেফটি:** ব্যবহারকারীর স্পষ্ট অনুমোদন ছাড়া লাইভ সুপাবেস ডাটাবেসে কোনো পরিবর্তন বা মাইগ্রেশন চালানো যাবে না।

---

## ৩. ফাইল পরিবর্তনের পরিকল্পনা (Files Planned for Modification)

| ফাইল | অ্যাকশন | উদ্দেশ্য |
|---|---|---|
| `backend/services/project_service.py` | MODIFIED | ড্যাশবোর্ড পরিসংখ্যান, পেজিনেটেড কী কুয়েরি, ইনস্টলেশন কুয়েরি, প্রজেক্ট স্ট্যাটাস টগল |
| `backend/api/v1/dashboard.py` | CREATED | `GET /api/v1/dashboard/stats` রিয়েল পরিসংখ্যান এন্ডপয়েন্ট |
| `backend/api/v1/projects.py` | MODIFIED | সার্চ সাপোর্ট এবং `PATCH /api/v1/projects/<id>/status` |
| `backend/api/v1/keys.py` | CREATED | `GET /api/v1/keys`, `GET /api/v1/keys/<id>`, CSV এক্সপোর্ট |
| `backend/api/v1/installations.py` | MODIFIED | `GET /api/v1/installations`, `GET /api/v1/installations/<id>` |
| `backend/api/v1/security.py` | CREATED | `GET /api/v1/security/status` সিকিউরিটি সেন্টার অডিট ও মেট্রিক্স |
| `backend/api/v1/audit.py` | CREATED | `GET /api/v1/audit-logs` পেজিনেটেড অডিট লগ ফিল্টারিং |
| `backend/api/v1/settings.py` | CREATED | `GET /api/v1/settings`, `PATCH /api/v1/settings` সিস্টেম কনফিগ |
| `backend/api/v1/auth.py` | CREATED | ভার্সনড `/api/v1/auth/*` রাউটিং |
| `backend/api/v1/__init__.py` | MODIFIED | নতুন ব্লুপ্রিন্টসমূহ রেজিস্টার করা |
| `frontend/dashboard.html` | MODIFIED | পূর্ণাঙ্গ ড্যাশবোর্ড লেআউট (৮টি পেজ/ভিউ: Overview, Projects, Generator, Keys, Installations, Security, Audit, Settings) |
| `frontend/assets/js/api.js` | MODIFIED | সম্পূর্ণ ভার্সনড `/api/v1/` এন্ডপয়েন্ট ইন্টিগ্রেশন |
| `frontend/assets/js/dashboard.js` | MODIFIED | প্রতিটি ভিউয়ের রিয়েল ডেটা রেন্ডারিং, ফিল্টারিং, সার্চ ও ফর্ম অ্যাকশন |
| `frontend/assets/app.css` | MODIFIED | প্রিমিয়াম ডার্ক ফিউচারিস্টিক ডিজাইন সিস্টেম ফাইন-টিউনিং |
| `tests/test_phase2_dashboard.py` | CREATED | Phase 2 এর সমস্ত নতুন এন্ডপয়েন্ট ও সিকিউরিটি রুলসের স্বয়ংক্রিয় টেস্ট |
| `docs/ARCHITECTURE.md` | MODIFIED | Phase 2 আর্কিটেকচার আপডেট |
| `README.md` | MODIFIED | Phase 2 ড্যাশবোর্ড ডকুমেন্টেশন ও ব্যবহার নির্দেশিকা |
| `docs/PHASE_2_FINAL_REPORT.md` | CREATED | চূড়ান্ত অডিট ও টেস্ট ফলাফলসহ রিপোর্ট |

---

## ৪. বাস্তবায়ন ধাপসমূহ (Step-by-Step Execution Plan)

1. **Step 1: Re-Audit & Plan** (বর্তমান ধাপ — সম্পন্ন)।
2. **Step 2 & 13: Backend Service & API Enhancements (`/api/v1/`)**:
   - `project_service.py` তে `dashboard_stats()`, `list_keys()`, `get_key_detail()`, `list_installations()`, `get_installation_detail()`, `set_project_status()`, `get_security_status()`, `get_audit_logs()`, `get_system_settings()`, `update_system_settings()` যুক্ত করা।
   - API endpoints (`dashboard.py`, `keys.py`, `installations.py`, `security.py`, `audit.py`, `settings.py`, `auth.py`) তৈরি ও মাউন্ট করা।
3. **Step 3, 5-12: Premium Frontend Dashboard Development**:
   - `frontend/dashboard.html` এ ৮টি ডেডিকেটেড ভিউ তৈরি:
     1. Overview
     2. Projects
     3. Key Generator
     4. Key Management
     5. Installations
     6. Security Center
     7. Audit Logs
     8. Settings
   - `frontend/assets/app.css` এ প্রয়োজনীয় স্টাইলিং ও রেসপনসিভনেস আপডেট।
   - `frontend/assets/js/api.js` ও `frontend/assets/js/dashboard.js` এ পূর্ণাঙ্গ কন্ট্রোলার যুক্ত করা।
4. **Step 15: Automated Test Suite Expansion**:
   - সমস্ত নতুন এন্ডপয়েন্ট ও ড্যাশবোর্ড লজিকের টেস্ট লিখে টেস্ট স্যুইট রান করা (লক্ষ্য: 100% পাস এবং কোনো রিগ্রেশন নেই)।
5. **Step 16: Documentation & Final Report**:
   - `docs/ARCHITECTURE.md`, `README.md`, `docs/PHASE_2_FINAL_REPORT.md` আপডেট ও তৈরি করা।
