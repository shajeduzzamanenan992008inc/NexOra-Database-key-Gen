# Project Backup, Restore, and Permanent Deletion

Status: implemented in the local repository. Migration 010 has been exercised only
on the local test database; it has not been applied to production.

## Data model

A project is the backup root. A bundle contains:

- project identity, description, type, status, expiry, archive state, and settings;
- project-owned declarative key types, including permissions, scopes, expiration,
  device, usage, lifecycle, endpoint, validation, and metadata-schema policy;
- key metadata only: type, label, hint, status, permissions, scopes, tags,
  metadata, usage counters, and lifecycle timestamps;
- device/installation metadata: device identifiers, names, model, platform,
  status, metadata, and timestamps;
- webhook destination path, event subscriptions, and active flag. Query strings
  and credentials are removed from exported URLs;
- previously imported metadata records.

A bundle never contains plaintext keys, key hashes, device credentials, webhook
signing secrets, database URLs, service-role keys, `KEY_PEPPER`, `SECRET_KEY`,
administrator passwords, or session tokens. Reader Keys are stored as HMAC
digests, so their original plaintext cannot be reconstructed. Exported keys and
devices are explicitly marked `requires_regeneration`.

Classic Reader installations are represented by the exported device metadata.
They cannot be restored as authenticated installations because the device
credential is not recoverable; a device must authenticate again after restore.

## Formats

Single-project JSON is `nexora-project-bundle`, version `1`.

ZIP bundles contain exactly:

| Entry | Purpose |
|---|---|
| `manifest.json` | Format, version, project identity, SHA-256 and size of `project.json`. |
| `project.json` | Complete single-project bundle. |
| `README.txt` | Human-readable security and regeneration notice. |

The ZIP parser rejects absolute paths, backslashes, `..`, unexpected filenames,
symlinks, encrypted entries, more than 8 entries, more than 8 MiB compressed,
more than 32 MiB expanded, and unsafe compression ratios.

Bulk ZIP export writes one JSON bundle per project under `projects/`, plus a
collection `manifest.json` with per-file SHA-256 values. Bulk JSON export is
limited to 50 projects per request. A single bundle is limited to 5,000 keys and
5,000 devices.

## Export API

| Method | Endpoint | Permission | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/universal/projects/<id>/export/preview` | `project:export` | Counts, security flags, and warnings before download. |
| `GET` | `/api/v1/universal/projects/<id>/export` | `project:export` | JSON bundle. |
| `GET` | `/api/v1/universal/projects/<id>/export?format=zip` | `project:export` | ZIP backup. |
| `POST` | `/api/v1/universal/projects/export` | `project:export` | Selected or all projects; `{"project_ids": [...], "format": "zip"}` or `{"all": true}`. |
| `GET` | `/api/v1/universal/keys/export` | `export:read` | Key metadata JSON/CSV with `project_id`, `key_type_id`, and `status` filters. |

Every export response uses `Cache-Control: no-store`. The preview reports
`secrets_exposed: false`, key/device/webhook/type counts, and regeneration
warnings. Webhook query strings are stripped and the bundle records
`url_query_redacted: true` so an operator knows to reconfigure the destination.

## Import API

Imports are multipart uploads using the field name `file`:

| Method | Endpoint | Permission | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/universal/projects/import/preview` | `project:import` | Validate and show conflicts; no writes. |
| `POST` | `/api/v1/universal/projects/import` | `project:import` | Transactional restore. |

Fields:

| Field | Values |
|---|---|
| `file` | JSON bundle or ZIP bundle. |
| `mode` | `keep_existing`, `import_as_new`, or `replace_existing`. |
| `project_name` | Optional new name for this import. |
| `replace_confirmed` | Must be `true` for replacement. |
| `confirm` | Must exactly match the existing project name for replacement. |

Conflict behavior:

- **Keep existing** merges into the project with the same normalized name.
  Source IDs make key/device metadata idempotent; duplicate webhooks are skipped.
- **Import as new** creates a separate project. A conflicting name is renamed
  deterministically rather than overwriting anything.
- **Replace existing** deletes the selected existing project inside the same
  transaction and recreates it under the confirmed name. It requires
  `replace_confirmed=true` and the exact existing name. Replacement is never
  silent.

Validation covers the bundle format/version, project identity and normalized
name, UUIDs, statuses, expiration timestamps, key types, key-type references,
permissions, scopes, metadata, device records, webhook URLs/events, duplicate
records, and declared limits. Built-in key types cannot be replaced by a bundle.
Imported code is never executed; custom key types remain declarative JSON.

Imported keys are written to `nexora_imported_key_records`, not `reader_keys`.
Their original status is retained only as metadata, with
`requires_regeneration: true` and `key_material_imported: false`. Revoked or
expired keys are therefore never restored as active. Imported devices are
metadata records and require re-authentication. Imported webhooks are created
inactive with a new signing salt and must be reviewed and enabled deliberately.

The import runs in one database transaction. Any validation or SQL failure rolls
back the project and every child write. A successful import writes a
`project.import` audit event.

## Permanent deletion

Archive and delete are separate operations. Archive is reversible; delete is
permanent.

| Method | Endpoint | Authorization |
|---|---|---|
| `DELETE` | `/api/v1/universal/projects/<id>` | `project:delete`, exact `confirm_project_name`, current password. |
| `GET` | `/api/v1/universal/projects/delete-preview` | Owner only; returns exact pre-deletion counts. |
| `DELETE` | `/api/v1/universal/projects` | Owner only, exact phrase `DELETE ALL PROJECTS`, current password. |

The single-project delete uses the existing transactional hard-delete service.
It removes owned keys, devices, devices' tokens, installations, key types,
webhooks, deliveries, imported records, notifications, and usage rows through
the schema's foreign keys. Administrator accounts and audit history are
preserved. The audit event stores a safe snapshot before deletion, and the name
becomes reusable.

Delete All is Owner-only server-side and requires the exact phrase plus a fresh
password check. The preview endpoint reports projects, key types, keys, devices,
webhooks, deliveries, and imported records so the confirmation dialog can show
the blast radius first. The operation preserves administrator accounts,
administrator sessions, settings, audit history, and legacy SMS Reader data. A
failure rolls back the complete transaction.

## Manual verification

```bash
python -m backend.cli migrate                 # local/test database only
python -m pytest tests/test_project_transfer_roles_scheduler.py -q
```

Then, as an Owner:

1. Create a project and generate a key.
2. Open Projects → Export and confirm the preview counts and security notice.
3. Download both JSON and ZIP; verify the plaintext key and its hash are absent.
4. Import with each mode; confirm replacement requires the exact name and flag.
5. Attempt delete with the wrong password, then the correct password.
6. Open Delete All and verify the pre-execution counts appear before the phrase
   and password are accepted.

## Production gate

Apply `db/migrations/010_project_transfer_roles_scheduler.sql` only after an
approved backup/PITR checkpoint and migration window. No production migration or
deployment was performed during this work.
