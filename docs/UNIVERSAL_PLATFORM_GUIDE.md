# OMEGA NEX-ORA Universal Key Platform

This guide describes the universal key-management layer added on top of the
existing Reader Key system. It is implemented in this repository and is backed
by migration `009_universal_key_platform.sql`.

## Entry points

| Surface | URL | Access |
|---|---|---|
| Existing administrator console | `/dashboard` | Human administrator session |
| Universal platform console | `/platform` | Human administrator session |
| Universal management API | `/api/v1/universal/...` | Human administrator session + CSRF |
| Universal authentication API | `POST /api/v1/access/authenticate` | Project key, no browser session |
| Animation assets | `/omega-assets/...` | Read-only static files |

The `/platform` console has 20 sections: Overview, Analytics, Global Search,
Projects, Universal Keys, Generate Keys, Key Types, Type Builder, Devices,
API Usage, Webhooks, Notifications, Audit Trail, Import/Export, Automation,
Roles, Administrators, Health, Preferences, and API Docs.

## Data model

Migration 009 extends the existing schema rather than creating a second key
store:

- `nexora_projects` gains project type, description, expiration, archival, and
  universal metadata.
- `reader_keys` remains the single key table and gains key type, permissions,
  scopes, tags, usage limits, metadata, fingerprint, and lifecycle fields.
- `nexora_permission_definitions` stores the permission catalogue.
- `nexora_key_types` stores 20 built-in key types plus administrator-defined
  types. A custom type's permissions and scopes are a ceiling, not a grant.
- `nexora_key_events` is an append-only key lifecycle history.
- `nexora_devices` stores device bindings and revocation state.
- `nexora_api_usage` records access telemetry without key plaintext.
- `nexora_webhooks` and `nexora_webhook_deliveries` store signed delivery
  configuration, attempts, and retry state.
- `nexora_notifications` and `nexora_notification_preferences` store
  administrative alerts and per-administrator preferences.
- `nexora_automation_runs` records expiry, cleanup, and retry jobs.
- `admin_users` gains a platform role, and `admin_audit_logs` gains a request ID
  for correlation.

All migrations are forward-only.

## Key model

A key is generated for one project and one key type. The server derives the
effective permissions and scopes from the type and rejects any requested
permission or scope outside that type's ceiling. A client-supplied type is never
trusted as an authorization decision.

Only an HMAC-SHA256 digest and a non-secret fingerprint/hint are stored. The
plaintext key appears once in the generation response. Regeneration creates a
new secret and invalidates the previous live key row.

Hard revocation removes the live key row and active device bindings while
retaining immutable key-event and audit history. The existing Reader
soft-revocation behavior is unchanged.

Reader activation now rejects expired projects and archived projects before
redeeming a key, including a re-check under the project row lock.

## Authentication and authorization

Human administrators continue to use the existing HttpOnly session cookie and
CSRF header. Universal management routes require a human session and a platform
permission such as `key:read`, `key:write`, `project:write`, `admin:read`, or
`admin:write`.

`POST /api/v1/access/authenticate` is separate. It accepts a project identifier
and a universal key, validates the key type and project state, optionally binds
a device, records usage, and returns only the key's effective permissions and
scopes. An administrative key is not accepted as a browser session and cannot
bypass the human-session checks.

## Webhooks

Webhook endpoints must be HTTP(S), resolve publicly, and pass private/link-local
address checks. Production delivery is signed with a per-webhook secret derived
from the server pepper. Only a digest is stored; the secret is revealed once at
creation or rotation. Failed deliveries are queued for retry and can leave a
dead-letter notification after the retry budget is exhausted.

## Environment

The universal layer uses the existing `DATABASE_URL`, `SECRET_KEY`, and
`KEY_PEPPER` settings. It does not require a new production secret. Keep
`KEY_PEPPER` stable: changing it invalidates existing key digests.

For local work, point `TEST_DATABASE_URL` at a disposable PostgreSQL database.
Never apply migration 009 to production without a current backup and an approved
migration window.

## Applying migration 009

```bash
# Review first
python -m backend.cli migration-status

# Apply to the configured database
python -m backend.cli migrate

# Confirm
python -m backend.cli migration-status
```

The migration seeds the permission catalogue and 20 built-in key types. It does
not modify or delete existing Reader projects or keys.

## Verification

```bash
python -m pytest
```

The full suite covers the existing application plus universal key generation,
type isolation, lifecycle, project expiry, RBAC, webhooks, import/export,
search, notifications, and custom-type permission ceilings.

Browser verification was performed against a local Flask server with Chrome:
all 20 platform views loaded, project creation succeeded, one-time key reveal
worked, the key appeared in inventory, and the mobile navigation opened.
