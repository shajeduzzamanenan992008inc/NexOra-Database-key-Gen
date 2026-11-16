# OMEGA NEX-ORA — Reader App API Integration Guide & Contract Specification

**প্রকল্প:** OMEGA NEX-ORA Database Key Gen  
**ডকুমেন্ট:** External Reader App Integration Contract  
**সংস্করণ:** 1.0.0 (API Version: `v1`)  
**প্রযোজ্য ক্লায়েন্ট:** Android SMS Reader App & External Automated Readers  

---

## ১. প্রজেক্ট বাউন্ডারি ও সিকিউরিটি ওভারভিউ (Project Boundary & Security)

> [!IMPORTANT]
> **কঠোর সিকিউরিটি নীতি:**
> - Android SMS Reader App একটি সম্পূর্ণ পৃথক অ্যাপ্লিকেশন।
> - Reader App-এর কাছে কখনোই Supabase Service Role Key (`SUPABASE_SERVICE_ROLE_KEY`), ডেটাবেস পাসওয়ার্ড বা কোনো অ্যাডমিন ক্রেডেনশিয়াল থাকবে না।
> - Reader App শুধুমাত্র NexOra Flask Backend-এর পাবলিক সিকিউর REST API (`/api/v1/reader/`) কল করবে।
> - ডেটাবেস কানেকশন, রো-লকিং, কি হ্যাশিং এবং স্ট্যাটাস চেকিং সম্পূর্ণভাবে NexOra সার্ভার ব্যাকএন্ডের নিয়ন্ত্রণে থাকে।

---

## ২. রিডার লাইফসাইকেল ফ্লো (Reader Lifecycle Architecture)

```
[Android Reader App]                                    [NexOra Server API]
        |                                                        |
        | 1. Initial Setup: POST /api/v1/reader/activate         |
        |    { project_name, database_url, reader_key, device_id}|
        |------------------------------------------------------->| (Validates URL, project,
        |                                                        |  HMAC key hash, row lock,
        |                                                        |  sets key='used', issues token)
        | 2. Receives 200 OK                                     |
        |    { device_credential, installation_id, status }      |
        |<-------------------------------------------------------|
        |                                                        |
        | [Device securely stores device_credential in Keystore] |
        |                                                        |
        | 3. Periodic Heartbeat: POST /api/v1/reader/heartbeat   |
        |    Header: Authorization: Bearer <device_credential>   |
        |------------------------------------------------------->| (Updates last_seen_at)
        |    { status: "ok", last_seen_at: "..." }               |
        |<-------------------------------------------------------|
        |                                                        |
        | 4. Health / Status Check: GET /api/v1/reader/status    |
        |    Header: Authorization: Bearer <device_credential>   |
        |------------------------------------------------------->| (Verifies valid active state)
        |    { status: "authorized", project_id: "..." }         |
        |<-------------------------------------------------------|
```

---

## ৩. এপিআই এন্ডপয়েন্ট স্পেসিফিকেশন (API Endpoints Specification)

### ৩.১ ওয়ান-টাইম রিডার কি অ্যাক্টিভেশন (First Activation Request)

নতুন একটি রিডার ডিভাইসে প্রজেক্ট কনফিগার করার সময় এই এন্ডপয়েন্ট একবার কল করতে হবে।

- **HTTP Method:** `POST`
- **URL Path:** `/api/v1/reader/activate`
- **Content-Type:** `application/json`
- **Authentication:** কোনো প্রিভিয়াস টোকেন প্রয়োজন নেই (Public Endpoint)।

#### Request Payload:
```json
{
  "project_name": "RH-ESPORTS-PROD",
  "database_url": "https://xyzcompany.supabase.co",
  "reader_key": "NXR-9K4M-2P7X-8VL3-5T9Q",
  "device_id": "samsung-sm-g998b-android14-uuid"
}
```

#### ফিল্ডের বিবরণ:
| ফিল্ড | টাইপ | আবশ্যক? | বিবরণ |
|---|---|:---:|---|
| `project_name` | string | **হ্যাঁ** | অ্যাডমিন ড্যাশবোর্ডে নিবন্ধিত প্রজেক্টের নাম (কেস-ইনসেনসিটিভ)। |
| `database_url` | string | **হ্যাঁ** | সার্ভারে কনফিগার করা Supabase প্রজেক্ট URL (`https://<ref>.supabase.co`)। ব্যাকএন্ডের রেফারেন্সের সাথে মিলতে হবে। |
| `reader_key` | string | **হ্যাঁ** | অ্যাডমিন কর্তৃক প্রদত্ত প্লেইনটেক্সট ওয়ান-টাইম রিডার কি (যেমন: `NXR-XXXX-XXXX-XXXX-XXXX`)। |
| `device_id` | string | না (ঐচ্ছিক) | রিডার ফোনের ইউনিক হার্ডওয়্যার বা অ্যাপ ইনস্ট্যান্স আইডেন্টিফায়ার (ডায়াগনস্টিক ও অডিটের জন্য)। |

#### সফল রেসপন্স (HTTP 200 OK):
```json
{
  "status": "activated",
  "project_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "project_name": "RH-ESPORTS-PROD",
  "installation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "device_credential": "nxr_dev_sample_c7f9e8a1b2c3d4e5f67890abcdef1234567890abcdef1234567890abcdef",
  "activated_at": "2026-11-16T14:30:00.000000Z"
}
```

> [!CAUTION]
> **গুরুত্বপূর্ণ:** `device_credential` হলো একটি হাই-এনট্রপি বেয়ারার ক্রেডেনশিয়াল যা **শুধুমাত্র একবার** অ্যাক্টিভেশনের জবাবে পাঠানো হয়। রিডার অ্যাপকে অবশ্যই এটি অ্যান্ড্রয়েডের `EncryptedSharedPreferences` অথবা `Android Keystore`-এ নিরাপদে সংরক্ষণ করতে হবে।

---

### ৩.২ পিরিওডিক হার্টবিট (Periodic Heartbeat)

অ্যাপ ব্যাকগ্রাউন্ডে বা ফোরগ্রাউন্ডে সচল থাকা অবস্থায় প্রতি ৫ মিনিট (অথবা কনফিগার করা সময়) পর পর হার্টবিট পাঠাবে।

- **HTTP Method:** `POST`
- **URL Path:** `/api/v1/reader/heartbeat`
- **Headers:**
  - `Content-Type: application/json`
  - `Authorization: Bearer <device_credential>`

#### Request Payload:
```json
{}
```
*(বডি ফাঁকা রাখা যাবে।)*

#### সফল রেসপন্স (HTTP 200 OK):
```json
{
  "status": "ok",
  "last_seen_at": "2026-11-16T14:35:00.000000Z"
}
```

---

### ৩.৩ রিডার অথোরাইজেশন স্ট্যাটাস যাচাই (Status Verification)

অ্যাপ শুরু হওয়ার সময় বা কোনো অপারেশন পরিচালনার পূর্বে ডিভাইস ক্রেডেনশিয়াল এখনও সচল এবং অনুমোদিত কিনা তা যাচাই করতে ব্যবহৃত হয়।

- **HTTP Method:** `GET`
- **URL Path:** `/api/v1/reader/status`
- **Headers:**
  - `Authorization: Bearer <device_credential>`

#### সফল রেসপন্স (HTTP 200 OK):
```json
{
  "status": "authorized",
  "project_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "project_name": "RH-ESPORTS-PROD",
  "installation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

---

## ৪. ত্রুটি ও রেসপন্স কোডসমূহ (Error Handling & HTTP Status Codes)

নেক্সওরা ব্যাকএন্ড সমস্ত ত্রুটির জন্য একটি স্ট্যান্ডার্ড ও সুসংহত JSON ফরম্যাট প্রদান করে:
```json
{
  "error": {
    "code": "ERROR_CODE_STRING",
    "message": "Human readable explanation.",
    "details": {}
  }
}
```

### সাধারণ ত্রুটি কোডের তালিকা:

| HTTP কোড | ত্রুটি কোড (`code`) | কারণ ও সমাধান |
|:---:|---|---|
| **400** | `DATABASE_URL_MISMATCH` | প্রেরিত `database_url` সার্ভারের কনফিগার করা Supabase URL-এর সাথে মিলেনি। |
| **400** | `KEY_INVALID_STATE` | কি-টি এমন কোনো অবস্থায় আছে যা রিডিম করা সম্ভব নয়। |
| **401** | `UNAUTHORIZED` | `Authorization` হেডার অনুপস্থিত, বেয়ারার ফরম্যাট ভুল, অথবা ডিভাইস ক্রেডেনশিয়ালটি ইনভ্যালিড/বাতিল। |
| **403** | `KEY_PROJECT_MISMATCH` | প্রদত্ত রিডার কি এই প্রজেক্টের জন্য জেনারেট করা হয়নি। |
| **404** | `PROJECT_NOT_FOUND` | নির্দিষ্ট নামের প্রজেক্টটি ডাটাবেসে নেই বা নিষ্ক্রিয় (`inactive`) রয়েছে। |
| **404** | `KEY_NOT_FOUND` | রিডার কি ডাটাবেসে পাওয়া যায়নি (ভুল কি ইনপুট)। |
| **409** | `ACTIVE_INSTALLATION_EXISTS` | **এক প্রজেক্ট - এক ইনস্টলেশন পলিসি:** এই প্রজেক্টে ইতোমধ্যে একটি রিডার ডিভাইস সক্রিয় আছে। পূর্বের ডিভাইস বাতিল না করে নতুন ডিভাইস যোগ করা যাবে না। |
| **410** | `KEY_ALREADY_USED` | এই কি-টি ইতোমধ্যে পূর্বে ব্যবহৃত হয়েছে (ওয়ান-টাইম কি পলিসি)। |
| **410** | `KEY_EXPIRED` | কি-এর মেয়াদের তারিখ পার হয়ে গেছে। |
| **410** | `KEY_REVOKED` | অ্যাডমিন ড্যাশবোর্ড থেকে এই কি-টি আগেই বাতিল (Revoke) করা হয়েছে। |
| **422** | `VALIDATION_ERROR` | প্রয়োজনীয় ফিল্ড অনুপস্থিত বা ইনপুট ফরম্যাট ভুল। |
| **429** | `RATE_LIMITED` | অতিরিক্ত রিকোয়েস্ট পাঠানোর ফলে রেট লিমিট ব্লক হয়েছে। রেসপন্সের `Retry-After` হেডার অনুসরণ করুন। |

---

## ৫. রিভোকেশন আচরণ (Revocation Behavior)

যদি অ্যাডমিন ড্যাশবোর্ড থেকে সংশ্লিষ্ট প্রজেক্ট, রিডার কি, অথবা রিডার ইনস্টলেশন বাতিল (`Revoke`) বা সফট-ডিলিট করা হয়:
1. সার্ভার-সাইডে সংশ্লিষ্ট `device_tokens`-এর `is_active` স্টেট তাৎক্ষণিকভাবে `false` করে দেওয়া হয় এবং `reader_installations`-এর টোকেন হ্যাশ মুছে দেওয়া হয়।
2. রিডার অ্যাপের পরবর্তী যেকোনো রিকোয়েস্টে (`/heartbeat` বা `/status`) ব্যাকএন্ড **HTTP 401 Unauthorized** রেসপন্স প্রদান করবে:
   ```json
   {
     "error": {
       "code": "UNAUTHORIZED",
       "message": "Invalid or revoked device credential."
     }
   }
   ```
3. এই রেসপন্স পেলে Android Reader App-কে অবশ্যই লোকাল ডিভাইস ক্রেডেনশিয়াল মুছে ফেলে লগইন/কনফিগারেশন স্ক্রিনে ফিরে যেতে হবে।
