"""Hierarchical, secret-free project export and transactional import.

A bundle contains project configuration, project-owned key types, key
metadata, device metadata and webhook configuration. It deliberately never
contains:

* key hashes, fingerprints or plaintext key material;
* device credential hashes or tokens;
* webhook signing salts, secrets or query-string credentials;
* database URLs, service-role keys, peppers, admin passwords or sessions.

Imported keys and devices are retained as non-recoverable metadata in
``nexora_imported_key_records``. They are never written into ``reader_keys`` or
``nexora_devices``, so an imported revoked or expired credential can never
become active. Imported webhooks are created inactive with a fresh local salt
and must be reviewed and enabled explicitly.

ZIP parsing uses a strict allow-list, checks traversal, symlinks, entry count,
compressed size, uncompressed size, compression ratio and the manifest checksum
before any JSON is accepted.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import zipfile
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from ..db import Database, is_unique_violation
from ..errors import ApiError, ValidationError
from ..security.keys import normalize_project_name
from ..serialization import jsonable, utcnow
from .webhook_security import WebhookSecurityError, resolve_webhook_target
from .webhook_service import WEBHOOK_EVENTS, sanitize_payload

BUNDLE_FORMAT = "nexora-project-bundle"
BUNDLE_VERSION = 1
MODES = ("keep_existing", "import_as_new", "replace_existing")
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_ZIP_ENTRIES = 8
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_KEYS = 5000
MAX_DEVICES = 5000
MAX_KEY_TYPES = 50
MAX_WEBHOOKS = 100
ALLOWED_ZIP_NAMES = frozenset({"manifest.json", "project.json", "README.txt"})
TYPE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,63}$")
KEY_STATUSES = (
    "generated",
    "active",
    "used",
    "deactivated",
    "expired",
    "revoked",
    "deleted",
)
DEVICE_STATUSES = ("active", "deactivated", "revoked")


def _text(value: Any, field: str, *, maximum: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError({field: "Must be text."})
    value = value.strip()
    if required and not value:
        raise ValidationError({field: "This field is required."})
    if len(value) > maximum:
        raise ValidationError({field: f"Must be {maximum} characters or fewer."})
    return value


def _optional_text(value: Any, field: str, *, maximum: int) -> str:
    return _text(value, field, maximum=maximum)


def _timestamp(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValidationError({field: "Use an ISO-8601 timestamp."})
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ValidationError({field: "Use an ISO-8601 timestamp."}) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _dict(value: Any, field: str, *, maximum_bytes: int = 12000) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError({field: "Must be an object."})
    if len(json.dumps(jsonable(value), default=str)) > maximum_bytes:
        raise ValidationError({field: "Payload is too large."})
    return value


def _string_list(value: Any, field: str, *, maximum: int = 64) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError({field: "Must be an array."})
    if len(value) > maximum:
        raise ValidationError({field: f"At most {maximum} entries are allowed."})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError({field: "Every entry must be a non-empty string."})
        item = item.strip()
        if len(item) > 100:
            raise ValidationError({field: "Every entry must be 100 characters or fewer."})
        if item not in result:
            result.append(item)
    return result


def _uuid_text(value: Any, field: str, *, required: bool = True) -> str:
    if value in (None, "") and not required:
        return ""
    if not isinstance(value, str):
        raise ValidationError({field: "Must be a UUID string."})
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError):
        raise ValidationError({field: "Must be a valid UUID."}) from None


def _redact_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(raw)
        netloc = parts.netloc.rsplit("@", 1)[-1]
        path = parts.path or "/"
        return f"{parts.scheme}://{netloc}{path}"
    except ValueError:
        return ""


def _canonical(bundle: dict[str, Any]) -> bytes:
    return json.dumps(
        jsonable(bundle), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class ProjectTransferService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings

    # ---------------------------------------------------------------- export
    def _project(self, project_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """
            SELECT id, project_name, description, project_type, status,
                   expires_at, never_expires, settings, auto_delete_at,
                   archived_at, created_at, updated_at
              FROM nexora_projects
             WHERE id = %s AND deleted_at IS NULL
            """,
            (project_id,),
        )
        if not row:
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        return row

    def export_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        key_types = self.db.fetch_all(
            """
            SELECT id, name, description, key_prefix, key_format, permissions,
                   scopes, expiration_policy, device_policy, usage_limits,
                   lifecycle_rules, endpoint_permissions, validation_rules,
                   metadata_schema, is_active
              FROM nexora_key_types
             WHERE project_id = %s
             ORDER BY name ASC
            """,
            (project_id,),
        )
        keys = self.db.fetch_all(
            """
            SELECT id, key_type_id, label, key_hint, status, permissions, scopes,
                   tags, metadata, usage_count, max_usage, is_permanent,
                   expires_at, created_at, activated_at, deactivated_at, used_at,
                   revoked_at, revoked_reason
              FROM reader_keys
             WHERE project_id = %s
             ORDER BY created_at ASC
             LIMIT %s
            """,
            (project_id, MAX_KEYS),
        )
        devices = self.db.fetch_all(
            """
            SELECT id, device_id, device_name, device_model, platform, status,
                   metadata, created_at, last_seen_at, deactivated_at, revoked_at
              FROM nexora_devices
             WHERE project_id = %s
             ORDER BY created_at ASC
             LIMIT %s
            """,
            (project_id, MAX_DEVICES),
        )
        webhooks = self.db.fetch_all(
            """
            SELECT name, url, events, is_active
              FROM nexora_webhooks
             WHERE project_id = %s
             ORDER BY created_at ASC
            """,
            (project_id,),
        )
        imported = self.db.fetch_all(
            """
            SELECT source_id, record_type, key_type_id, label, key_hint, status,
                   requires_regeneration, data, imported_at
              FROM nexora_imported_key_records
             WHERE project_id = %s
             ORDER BY imported_at ASC
             LIMIT %s
            """,
            (project_id, MAX_KEYS),
        )
        stats = self.db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*)::int FROM reader_keys WHERE project_id = %s) AS keys,
              (SELECT COUNT(*)::int FROM nexora_devices WHERE project_id = %s) AS devices,
              (SELECT COUNT(*)::int FROM nexora_webhooks WHERE project_id = %s) AS webhooks,
              (SELECT COUNT(*)::int FROM nexora_imported_key_records WHERE project_id = %s) AS imported_records
            """,
            (project_id, project_id, project_id, project_id),
        ) or {}

        return {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "exported_at": utcnow().isoformat().replace("+00:00", "Z"),
            "generator": "NexOra OMEGA project transfer",
            "project": {
                "source_id": str(project["id"]),
                "project_name": project["project_name"],
                "description": project.get("description") or "",
                "project_type": project.get("project_type") or "database",
                "status": project.get("status") or "active",
                "expires_at": _timestamp_text(project.get("expires_at")),
                "never_expires": bool(project.get("never_expires", True)),
                "settings": sanitize_payload(project.get("settings") or {}),
                "auto_delete_at": _timestamp_text(project.get("auto_delete_at")),
                "archived_at": _timestamp_text(project.get("archived_at")),
                "created_at": _timestamp_text(project.get("created_at")),
                "updated_at": _timestamp_text(project.get("updated_at")),
            },
            "key_types": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description") or "",
                    "key_prefix": row.get("key_prefix") or "NX",
                    "key_format": row.get("key_format") or {},
                    "permissions": row.get("permissions") or [],
                    "scopes": row.get("scopes") or [],
                    "expiration_policy": row.get("expiration_policy") or {},
                    "device_policy": row.get("device_policy") or {},
                    "usage_limits": row.get("usage_limits") or {},
                    "lifecycle_rules": row.get("lifecycle_rules") or {},
                    "endpoint_permissions": row.get("endpoint_permissions") or [],
                    "validation_rules": row.get("validation_rules") or {},
                    "metadata_schema": row.get("metadata_schema") or {},
                    "is_active": bool(row.get("is_active", True)),
                }
                for row in key_types
            ],
            "keys": [
                {
                    "source_id": str(row["id"]),
                    "key_type_id": row["key_type_id"],
                    "label": row.get("label") or "",
                    "key_hint": row.get("key_hint") or "",
                    "status": row.get("status") or "generated",
                    "permissions": row.get("permissions") or [],
                    "scopes": row.get("scopes") or [],
                    "tags": row.get("tags") or [],
                    "metadata": sanitize_payload(row.get("metadata") or {}),
                    "usage_count": int(row.get("usage_count") or 0),
                    "max_usage": row.get("max_usage"),
                    "is_permanent": bool(row.get("is_permanent", False)),
                    "expires_at": _timestamp_text(row.get("expires_at")),
                    "created_at": _timestamp_text(row.get("created_at")),
                    "activated_at": _timestamp_text(row.get("activated_at")),
                    "deactivated_at": _timestamp_text(row.get("deactivated_at")),
                    "used_at": _timestamp_text(row.get("used_at")),
                    "revoked_at": _timestamp_text(row.get("revoked_at")),
                    "revoked_reason": row.get("revoked_reason") or "",
                    "requires_regeneration": True,
                    "notes_excluded": True,
                }
                for row in keys
            ],
            "devices": [
                {
                    "source_id": str(row["id"]),
                    "device_id": row["device_id"],
                    "device_name": row.get("device_name") or "",
                    "device_model": row.get("device_model") or "",
                    "platform": row.get("platform") or "",
                    "status": row.get("status") or "active",
                    "metadata": sanitize_payload(row.get("metadata") or {}),
                    "created_at": _timestamp_text(row.get("created_at")),
                    "last_seen_at": _timestamp_text(row.get("last_seen_at")),
                    "deactivated_at": _timestamp_text(row.get("deactivated_at")),
                    "revoked_at": _timestamp_text(row.get("revoked_at")),
                    "requires_regeneration": True,
                }
                for row in devices
            ],
            "webhooks": [
                {
                    "name": row["name"],
                    "url": _redact_url(row.get("url")),
                    "url_query_redacted": bool(
                        "?" in str(row.get("url") or "")
                    ),
                    "events": list(row.get("events") or []),
                    "is_active": bool(row.get("is_active", True)),
                }
                for row in webhooks
            ],
            "imported_records": [
                {
                    "source_id": row.get("source_id") or "",
                    "record_type": row.get("record_type") or "key",
                    "key_type_id": row.get("key_type_id") or "",
                    "label": row.get("label") or "",
                    "key_hint": row.get("key_hint") or "",
                    "status": row.get("status") or "imported",
                    "requires_regeneration": bool(
                        row.get("requires_regeneration", True)
                    ),
                    "data": sanitize_payload(row.get("data") or {}),
                    "imported_at": _timestamp_text(row.get("imported_at")),
                }
                for row in imported
            ],
            "statistics": {
                "keys": int(stats.get("keys") or 0),
                "devices": int(stats.get("devices") or 0),
                "webhooks": int(stats.get("webhooks") or 0),
                "imported_records": int(stats.get("imported_records") or 0),
                "key_types": len(key_types),
            },
            "security": {
                "secrets_exposed": False,
                "key_material_included": False,
                "key_hashes_included": False,
                "webhook_secrets_included": False,
                "database_credentials_included": False,
                "note": (
                    "Keys and devices are metadata only. They must be "
                    "regenerated after import. Webhook query strings and "
                    "signing secrets are never exported."
                ),
            },
        }

    def export_zip(self, project_id: str) -> tuple[bytes, str]:
        bundle = self.export_project(project_id)
        project_bytes = _canonical(bundle)
        manifest = {
            "format": BUNDLE_FORMAT,
            "version": BUNDLE_VERSION,
            "exported_at": bundle["exported_at"],
            "project_id": bundle["project"]["source_id"],
            "project_name": bundle["project"]["project_name"],
            "files": {
                "project.json": {
                    "sha256": hashlib.sha256(project_bytes).hexdigest(),
                    "bytes": len(project_bytes),
                }
            },
            "secrets_exposed": False,
        }
        readme = (
            "NexOra OMEGA project bundle\n"
            "===========================\n\n"
            "This archive contains project configuration and non-recoverable\n"
            "key/device metadata only. It contains no key material, key hashes,\n"
            "device credentials, webhook secrets, database URLs or platform\n"
            "credentials. Imported keys must be regenerated.\n"
        ).encode("utf-8")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in (
                ("manifest.json", json.dumps(manifest, indent=2).encode("utf-8")),
                ("project.json", project_bytes),
                ("README.txt", readme),
            ):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data)
        safe_name = re.sub(r"[^a-z0-9._-]+", "-", bundle["project"]["project_name"].lower())
        safe_name = safe_name.strip("-") or "nexora-project"
        return output.getvalue(), f"{safe_name}-bundle.zip"

    def export_preview(self, project_id: str) -> dict[str, Any]:
        bundle = self.export_project(project_id)
        return {
            "format": bundle["format"],
            "version": bundle["version"],
            "project": {
                "id": bundle["project"]["source_id"],
                "project_name": bundle["project"]["project_name"],
            },
            "counts": bundle["statistics"],
            "security": bundle["security"],
            "warnings": [
                "Key material is not exported and cannot be recovered from this bundle.",
                "Imported keys and devices will require regeneration.",
                "Imported webhooks are created inactive and must be reviewed.",
            ],
        }

    def export_many_zip(
        self, project_ids: list[str], *, maximum: int = 50
    ) -> tuple[bytes, str]:
        """Export several projects into one integrity-checked archive."""
        ids: list[str] = []
        for value in project_ids or []:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError({"project_ids": "Every project id must be text."})
            if value not in ids:
                ids.append(value)
        if not ids:
            raise ValidationError({"project_ids": "Select at least one project."})
        if len(ids) > maximum:
            raise ValidationError(
                {"project_ids": f"At most {maximum} projects per archive."}
            )
        output = io.BytesIO()
        files: dict[str, dict[str, Any]] = {}
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for project_id in ids:
                bundle = self.export_project(project_id)
                data = _canonical(bundle)
                safe = re.sub(
                    r"[^a-z0-9._-]+",
                    "-",
                    str(bundle["project"]["project_name"]).lower(),
                ).strip("-") or "project"
                name = f"projects/{safe}-{bundle['project']['source_id'][:8]}.json"
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data)
                files[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            manifest = {
                "format": "nexora-project-collection",
                "version": BUNDLE_VERSION,
                "exported_at": utcnow().isoformat().replace("+00:00", "Z"),
                "project_count": len(ids),
                "files": files,
                "secrets_exposed": False,
            }
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
            archive.writestr(
                "README.txt",
                (
                    "NexOra OMEGA multi-project bundle\n"
                    "=================================\n\n"
                    "Each projects/*.json file is a single-project bundle. Import\n"
                    "them one at a time so the preview and conflict policy apply\n"
                    "to each project. No key material, hashes, device credentials\n"
                    "or webhook secrets are included.\n"
                ).encode("utf-8"),
            )
        return output.getvalue(), "nexora-projects-bundle.zip"

    # ------------------------------------------------------------ deletion
    def _project_counts(self, project_id: str) -> dict[str, int]:
        row = self.db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*)::int FROM reader_keys WHERE project_id = %s) AS keys,
              (SELECT COUNT(*)::int FROM nexora_key_types WHERE project_id = %s) AS key_types,
              (SELECT COUNT(*)::int FROM nexora_devices WHERE project_id = %s) AS devices,
              (SELECT COUNT(*)::int FROM nexora_webhooks WHERE project_id = %s) AS webhooks,
              (SELECT COUNT(*)::int FROM nexora_imported_key_records WHERE project_id = %s) AS imported_records
            """,
            (project_id, project_id, project_id, project_id, project_id),
        ) or {}
        return {key: int(value or 0) for key, value in row.items()}

    def delete_project(
        self,
        project_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        """Permanently delete one universal project and its owned records.

        Reuses the existing transactional project deletion so foreign keys,
        the audit snapshot and verification remain in one implementation.
        The caller has already performed permission and password checks.
        """
        project = self._project(project_id)
        counts = self._project_counts(project_id)
        from .project_service import ProjectService

        result = ProjectService(self.db, self.settings).hard_delete_project(
            project_id,
            actor_id=actor.get("id"),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "project": {
                "id": str(project["id"]),
                "project_name": project["project_name"],
            },
            "deleted": bool(result.get("deleted")),
            "mode": "permanent",
            "counts": counts,
            "project_name_reusable": True,
        }

    def delete_preview(self) -> dict[str, Any]:
        """Return the exact blast radius before a delete-all confirmation."""
        counts = self.db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*)::int FROM nexora_projects) AS projects,
              (SELECT COUNT(*)::int FROM nexora_key_types WHERE project_id IS NOT NULL) AS key_types,
              (SELECT COUNT(*)::int FROM reader_keys) AS keys,
              (SELECT COUNT(*)::int FROM nexora_devices) AS devices,
              (SELECT COUNT(*)::int FROM nexora_webhooks) AS webhooks,
              (SELECT COUNT(*)::int FROM nexora_webhook_deliveries) AS deliveries,
              (SELECT COUNT(*)::int FROM nexora_imported_key_records) AS imported_records
            """
        ) or {}
        return {
            "mode": "permanent",
            "counts": {key: int(value or 0) for key, value in counts.items()},
            "preserved": [
                "administrator accounts",
                "administrator sessions",
                "administrator settings",
                "audit history",
                "legacy SMS Reader data",
            ],
        }

    def delete_all_projects(
        self,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        """Permanently delete every project after owner re-authentication."""
        counts = self.db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*)::int FROM nexora_projects) AS projects,
              (SELECT COUNT(*)::int FROM reader_keys) AS keys,
              (SELECT COUNT(*)::int FROM nexora_devices) AS devices,
              (SELECT COUNT(*)::int FROM nexora_webhooks) AS webhooks,
              (SELECT COUNT(*)::int FROM nexora_imported_key_records) AS imported_records
            """
        ) or {}
        counts = {key: int(value or 0) for key, value in counts.items()}
        from .project_service import ProjectService

        result = ProjectService(self.db, self.settings).hard_delete_all_projects(
            actor_id=actor.get("id"),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "deleted_count": int(result.get("deleted_count") or 0),
            "mode": "permanent",
            "counts": counts,
            "preserved": [
                "administrator accounts",
                "administrator sessions",
                "administrator settings",
                "audit history",
                "legacy SMS Reader data",
            ],
        }

    # ------------------------------------------------------------ zip parsing
    def parse_zip(self, data: bytes) -> dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)):
            raise ValidationError({"file": "The upload must be a binary ZIP archive."})
        if len(data) > MAX_ZIP_BYTES:
            raise ValidationError({"file": "The ZIP archive exceeds the size limit."})
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ValidationError({"file": "The upload is not a valid ZIP archive."})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ValidationError({"file": "The archive contains too many entries."})
            total = 0
            files: dict[str, bytes] = {}
            for info in infos:
                name = info.filename
                if (
                    name.startswith("/")
                    or "\\" in name
                    or ".." in name.split("/")
                    or name not in ALLOWED_ZIP_NAMES
                ):
                    raise ValidationError(
                        {"file": "The archive contains an unsafe or unexpected entry."}
                    )
                if info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValidationError({"file": "Symbolic links are not allowed."})
                if info.flag_bits & 0x1:
                    raise ValidationError({"file": "Encrypted archives are not supported."})
                total += int(info.file_size)
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValidationError({"file": "The archive expands beyond the safe limit."})
                if info.compress_size and info.file_size > info.compress_size * 200:
                    raise ValidationError({"file": "The archive compression ratio is unsafe."})
                files[name] = archive.read(info)
        if "project.json" not in files:
            raise ValidationError({"file": "The archive is missing project.json."})
        try:
            manifest = json.loads(files.get("manifest.json", b"{}").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError({"file": "The archive manifest is not valid JSON."}) from None
        if not isinstance(manifest, dict):
            raise ValidationError({"file": "The archive manifest is invalid."})
        if manifest.get("format") != BUNDLE_FORMAT:
            raise ValidationError({"file": "The archive format is not a NexOra bundle."})
        if manifest.get("version") != BUNDLE_VERSION:
            raise ValidationError({"file": "The archive version is not supported."})
        project_bytes = files["project.json"]
        if len(project_bytes) > MAX_JSON_BYTES:
            raise ValidationError({"file": "The bundle JSON exceeds the size limit."})
        expected = (
            (manifest.get("files") or {}).get("project.json", {}).get("sha256")
            if isinstance(manifest.get("files"), dict)
            else None
        )
        if expected and expected != hashlib.sha256(project_bytes).hexdigest():
            raise ValidationError({"file": "The archive checksum does not match."})
        try:
            bundle = json.loads(project_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError({"file": "The archive project JSON is invalid."}) from None
        if not isinstance(bundle, dict):
            raise ValidationError({"file": "The archive project JSON must be an object."})
        return bundle

    def parse_json(self, data: bytes) -> dict[str, Any]:
        if len(data) > MAX_JSON_BYTES:
            raise ValidationError({"bundle": "The bundle JSON exceeds the size limit."})
        try:
            bundle = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError({"bundle": "The bundle is not valid JSON."}) from None
        if not isinstance(bundle, dict):
            raise ValidationError({"bundle": "The bundle must be a JSON object."})
        return bundle

    def parse_upload(self, data: bytes, filename: str = "") -> dict[str, Any]:
        """Parse an uploaded JSON or ZIP bundle without executing anything."""
        if not data:
            raise ValidationError({"file": "The uploaded file is empty."})
        name = (filename or "").lower()
        if name.endswith(".zip") or data[:2] == b"PK":
            return self.parse_zip(data)
        if name.endswith(".json") or data[:1] in {b"{", b"["}:
            return self.parse_json(data)
        raise ValidationError({"file": "Use a NexOra .json or .zip project bundle."})

    # ------------------------------------------------------------ validation
    def _known_permissions(self, scope: str) -> set[str]:
        return {
            str(row["id"])
            for row in self.db.fetch_all(
                "SELECT id FROM nexora_permission_definitions WHERE scope = %s",
                (scope,),
            )
        }

    def _known_builtin_types(self) -> set[str]:
        return {
            str(row["id"])
            for row in self.db.fetch_all(
                "SELECT id FROM nexora_key_types WHERE project_id IS NULL AND is_builtin = true"
            )
        }

    def _validate_key_type(self, value: Any, index: int) -> dict[str, Any]:
        field = f"bundle.key_types[{index}]"
        if not isinstance(value, dict):
            raise ValidationError({field: "Must be an object."})
        type_id = _text(value.get("id"), f"{field}.id", maximum=50, required=True).lower()
        if not TYPE_ID_RE.match(type_id):
            raise ValidationError(
                {f"{field}.id": "Use 3-50 lowercase letters, digits or underscores."}
            )
        name = _text(value.get("name"), f"{field}.name", maximum=80, required=True)
        description = _text(
            value.get("description"), f"{field}.description", maximum=500
        )
        prefix = _text(
            value.get("key_prefix"), f"{field}.key_prefix", maximum=12
        ).upper() or "NX"
        if not re.match(r"^[A-Z0-9]{0,12}$", prefix):
            raise ValidationError({f"{field}.key_prefix": "Use uppercase letters or digits."})
        key_format = _dict(value.get("key_format"), f"{field}.key_format")
        groups = int(key_format.get("groups", 4) or 4)
        group_length = int(key_format.get("group_length", 4) or 4)
        if not 1 <= groups <= 20 or not 1 <= group_length <= 20:
            raise ValidationError({f"{field}.key_format": "Group sizes must be 1-20."})
        permissions = _string_list(
            value.get("permissions"), f"{field}.permissions", maximum=64
        )
        unknown = set(permissions) - self._known_permissions("key")
        if unknown:
            raise ValidationError(
                {f"{field}.permissions": f"Unknown key permissions: {sorted(unknown)}"}
            )
        scopes = _string_list(value.get("scopes"), f"{field}.scopes", maximum=64)
        for scope in scopes:
            if not SCOPE_RE.match(scope):
                raise ValidationError({f"{field}.scopes": f"Invalid scope: {scope}"})
        return {
            "id": type_id,
            "name": name,
            "description": description,
            "key_prefix": prefix,
            "key_format": {
                "groups": groups,
                "group_length": group_length,
                "separator": str(key_format.get("separator", "-"))[:4] or "-",
                "suffix": str(key_format.get("suffix") or "").upper()[:16],
            },
            "permissions": permissions or ["read"],
            "scopes": scopes,
            "expiration_policy": _dict(
                value.get("expiration_policy"), f"{field}.expiration_policy"
            ),
            "device_policy": _dict(value.get("device_policy"), f"{field}.device_policy"),
            "usage_limits": _dict(value.get("usage_limits"), f"{field}.usage_limits"),
            "lifecycle_rules": _dict(
                value.get("lifecycle_rules"), f"{field}.lifecycle_rules"
            ),
            "endpoint_permissions": _string_list(
                value.get("endpoint_permissions"),
                f"{field}.endpoint_permissions",
                maximum=64,
            ),
            "validation_rules": _dict(
                value.get("validation_rules"), f"{field}.validation_rules"
            ),
            "metadata_schema": _dict(
                value.get("metadata_schema"), f"{field}.metadata_schema"
            ),
            "is_active": bool(value.get("is_active", True)),
        }

    def validate_bundle(
        self,
        bundle: Any,
        *,
        mode: str,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(bundle, dict):
            raise ValidationError({"bundle": "Must be a JSON object."})
        mode = str(mode or "keep_existing").strip().lower().replace("-", "_")
        if mode not in MODES:
            raise ValidationError(
                {"mode": "Use keep_existing, import_as_new or replace_existing."}
            )
        if bundle.get("format") != BUNDLE_FORMAT:
            raise ValidationError({"bundle.format": "This is not a NexOra project bundle."})
        if bundle.get("version") != BUNDLE_VERSION:
            raise ValidationError({"bundle.version": "Unsupported bundle version."})
        raw_project = bundle.get("project")
        if not isinstance(raw_project, dict):
            raise ValidationError({"bundle.project": "Must be an object."})
        source_name = _text(
            raw_project.get("project_name"),
            "bundle.project.project_name",
            maximum=200,
            required=True,
        )
        requested_name = (
            _text(project_name, "project_name", maximum=200, required=True)
            if project_name
            else source_name
        )
        try:
            normalized = normalize_project_name(requested_name)
        except ValueError as exc:
            raise ValidationError({"project_name": str(exc)}) from None
        project_type = _text(
            raw_project.get("project_type"),
            "bundle.project.project_type",
            maximum=40,
        ).lower() or "database"
        status = _text(
            raw_project.get("status"), "bundle.project.status", maximum=20
        ).lower() or "active"
        if status not in {"active", "inactive", "expired"}:
            raise ValidationError(
                {"bundle.project.status": "Use active, inactive or expired."}
            )
        expires_at = _timestamp(
            raw_project.get("expires_at"), "bundle.project.expires_at"
        )
        if expires_at and expires_at <= utcnow() and status == "active":
            status = "expired"
        project = {
            "source_id": _uuid_text(
                raw_project.get("source_id"),
                "bundle.project.source_id",
                required=False,
            ),
            "project_name": requested_name,
            "source_project_name": source_name,
            "project_name_normalized": normalized,
            "description": _text(
                raw_project.get("description"),
                "bundle.project.description",
                maximum=1000,
            ),
            "project_type": project_type,
            "status": status,
            "expires_at": expires_at,
            "never_expires": bool(raw_project.get("never_expires", expires_at is None)),
            "settings": sanitize_payload(
                _dict(raw_project.get("settings"), "bundle.project.settings")
            ),
            "auto_delete_at": _timestamp(
                raw_project.get("auto_delete_at"), "bundle.project.auto_delete_at"
            ),
            "archived_at": _timestamp(
                raw_project.get("archived_at"), "bundle.project.archived_at"
            ),
        }
        if project["archived_at"]:
            project["status"] = "inactive"
        raw_types = bundle.get("key_types") or []
        if not isinstance(raw_types, list) or len(raw_types) > MAX_KEY_TYPES:
            raise ValidationError({"bundle.key_types": "Must be an array of at most 50."})
        key_types = [
            self._validate_key_type(item, index)
            for index, item in enumerate(raw_types)
        ]
        type_ids = [item["id"] for item in key_types]
        if len(type_ids) != len(set(type_ids)):
            raise ValidationError({"bundle.key_types": "Key type ids must be unique."})
        builtin = self._known_builtin_types()
        if set(type_ids) & builtin:
            raise ValidationError(
                {"bundle.key_types": "Built-in key types cannot be replaced by a bundle."}
            )
        all_type_ids = builtin | set(type_ids)
        raw_keys = bundle.get("keys") or []
        if not isinstance(raw_keys, list) or len(raw_keys) > MAX_KEYS:
            raise ValidationError({"bundle.keys": f"Must be an array of at most {MAX_KEYS}."})
        keys: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for index, item in enumerate(raw_keys):
            field = f"bundle.keys[{index}]"
            if not isinstance(item, dict):
                raise ValidationError({field: "Must be an object."})
            source_id = _uuid_text(item.get("source_id"), f"{field}.source_id")
            if source_id in seen_keys:
                raise ValidationError({f"{field}.source_id": "Duplicate key id."})
            seen_keys.add(source_id)
            key_type_id = _text(
                item.get("key_type_id"), f"{field}.key_type_id", maximum=50, required=True
            )
            if key_type_id not in all_type_ids:
                raise ValidationError({f"{field}.key_type_id": "Unknown key type."})
            status_value = _text(
                item.get("status"), f"{field}.status", maximum=30
            ).lower() or "generated"
            if status_value not in KEY_STATUSES:
                raise ValidationError({f"{field}.status": "Unknown key status."})
            max_usage = item.get("max_usage")
            if max_usage is not None:
                try:
                    max_usage = int(max_usage)
                except (TypeError, ValueError):
                    raise ValidationError({f"{field}.max_usage": "Must be an integer."}) from None
                if max_usage <= 0:
                    raise ValidationError({f"{field}.max_usage": "Must be positive."})
            try:
                usage_count = int(item.get("usage_count") or 0)
            except (TypeError, ValueError):
                raise ValidationError({f"{field}.usage_count": "Must be an integer."}) from None
            if usage_count < 0:
                raise ValidationError({f"{field}.usage_count": "Must not be negative."})
            keys.append(
                {
                    "source_id": source_id,
                    "key_type_id": key_type_id,
                    "label": _text(item.get("label"), f"{field}.label", maximum=100),
                    "key_hint": _text(
                        item.get("key_hint"), f"{field}.key_hint", maximum=100
                    ),
                    "status": status_value,
                    "permissions": _string_list(
                        item.get("permissions"), f"{field}.permissions", maximum=64
                    ),
                    "scopes": _string_list(
                        item.get("scopes"), f"{field}.scopes", maximum=64
                    ),
                    "tags": _string_list(item.get("tags"), f"{field}.tags", maximum=32),
                    "metadata": sanitize_payload(
                        _dict(item.get("metadata"), f"{field}.metadata", maximum_bytes=8000)
                    ),
                    "usage_count": usage_count,
                    "max_usage": max_usage,
                    "is_permanent": bool(item.get("is_permanent", False)),
                    "expires_at": _timestamp(item.get("expires_at"), f"{field}.expires_at"),
                    "created_at": _timestamp(item.get("created_at"), f"{field}.created_at"),
                    "activated_at": _timestamp(
                        item.get("activated_at"), f"{field}.activated_at"
                    ),
                    "deactivated_at": _timestamp(
                        item.get("deactivated_at"), f"{field}.deactivated_at"
                    ),
                    "used_at": _timestamp(item.get("used_at"), f"{field}.used_at"),
                    "revoked_at": _timestamp(item.get("revoked_at"), f"{field}.revoked_at"),
                    "revoked_reason": _text(
                        item.get("revoked_reason"),
                        f"{field}.revoked_reason",
                        maximum=500,
                    ),
                }
            )
        raw_devices = bundle.get("devices") or []
        if not isinstance(raw_devices, list) or len(raw_devices) > MAX_DEVICES:
            raise ValidationError(
                {"bundle.devices": f"Must be an array of at most {MAX_DEVICES}."}
            )
        devices: list[dict[str, Any]] = []
        seen_devices: set[str] = set()
        for index, item in enumerate(raw_devices):
            field = f"bundle.devices[{index}]"
            if not isinstance(item, dict):
                raise ValidationError({field: "Must be an object."})
            source_id = _uuid_text(item.get("source_id"), f"{field}.source_id")
            if source_id in seen_devices:
                raise ValidationError({f"{field}.source_id": "Duplicate device id."})
            seen_devices.add(source_id)
            status_value = _text(
                item.get("status"), f"{field}.status", maximum=20
            ).lower() or "deactivated"
            if status_value not in DEVICE_STATUSES:
                raise ValidationError({f"{field}.status": "Unknown device status."})
            devices.append(
                {
                    "source_id": source_id,
                    "device_id": _text(
                        item.get("device_id"), f"{field}.device_id", maximum=200, required=True
                    ),
                    "device_name": _text(
                        item.get("device_name"), f"{field}.device_name", maximum=120
                    ),
                    "device_model": _text(
                        item.get("device_model"), f"{field}.device_model", maximum=120
                    ),
                    "platform": _text(
                        item.get("platform"), f"{field}.platform", maximum=80
                    ),
                    "status": status_value,
                    "metadata": sanitize_payload(
                        _dict(item.get("metadata"), f"{field}.metadata", maximum_bytes=8000)
                    ),
                    "created_at": _timestamp(item.get("created_at"), f"{field}.created_at"),
                    "last_seen_at": _timestamp(
                        item.get("last_seen_at"), f"{field}.last_seen_at"
                    ),
                    "deactivated_at": _timestamp(
                        item.get("deactivated_at"), f"{field}.deactivated_at"
                    ),
                    "revoked_at": _timestamp(item.get("revoked_at"), f"{field}.revoked_at"),
                }
            )
        raw_webhooks = bundle.get("webhooks") or []
        if not isinstance(raw_webhooks, list) or len(raw_webhooks) > MAX_WEBHOOKS:
            raise ValidationError(
                {"bundle.webhooks": f"Must be an array of at most {MAX_WEBHOOKS}."}
            )
        webhooks: list[dict[str, Any]] = []
        for index, item in enumerate(raw_webhooks):
            field = f"bundle.webhooks[{index}]"
            if not isinstance(item, dict):
                raise ValidationError({field: "Must be an object."})
            name = _text(item.get("name"), f"{field}.name", maximum=100, required=True)
            url = _text(item.get("url"), f"{field}.url", maximum=2048, required=True)
            events = _string_list(item.get("events"), f"{field}.events", maximum=64)
            if not events:
                raise ValidationError({f"{field}.events": "Select at least one event."})
            unknown_events = set(events) - set(WEBHOOK_EVENTS)
            if unknown_events:
                raise ValidationError(
                    {f"{field}.events": f"Unknown events: {sorted(unknown_events)}"}
                )
            try:
                resolve_webhook_target(
                    url,
                    require_https=bool(getattr(self.settings, "is_production", False)),
                )
            except WebhookSecurityError as exc:
                raise ValidationError({f"{field}.url": exc.message}) from None
            webhooks.append(
                {
                    "name": name,
                    "url": url,
                    "events": events,
                    "is_active": bool(item.get("is_active", True)),
                }
            )
        existing = self.db.fetch_one(
            """
            SELECT id, project_name FROM nexora_projects
             WHERE project_name_normalized = %s AND deleted_at IS NULL
            """,
            (normalized,),
        )
        if mode == "replace_existing" and not existing:
            raise ValidationError(
                {"mode": "There is no existing project with that name to replace."}
            )
        action = "create"
        if existing and mode == "keep_existing":
            action = "merge"
        elif existing and mode == "import_as_new":
            action = "create_with_renamed_project"
        elif existing and mode == "replace_existing":
            action = "replace"
        warnings = [
            "Imported keys are metadata only and must be regenerated.",
            "Imported devices are metadata only and must be re-enrolled.",
            "Imported webhooks are created inactive.",
        ]
        if action == "merge":
            warnings.append(
                "Existing project settings and records are kept; matching imported "
                "records are skipped."
            )
        if action == "replace":
            warnings.append(
                "The existing project and all of its keys, devices, webhooks and "
                "usage history will be permanently deleted in the same transaction."
            )
        return {
            "mode": mode,
            "action": action,
            "project": project,
            "existing_project": (
                {"id": str(existing["id"]), "project_name": existing["project_name"]}
                if existing
                else None
            ),
            "key_types": key_types,
            "keys": keys,
            "devices": devices,
            "webhooks": webhooks,
            "warnings": warnings,
            "counts": {
                "key_types": len(key_types),
                "keys": len(keys),
                "devices": len(devices),
                "webhooks": len(webhooks),
            },
            "secrets_exposed": False,
        }

    # -------------------------------------------------------------- importing
    def _unique_name(self, cursor: Any, base: str) -> str:
        try:
            normalized = normalize_project_name(base)
        except ValueError:
            base = "Imported project"
            normalized = normalize_project_name(base)
        candidate = base[:190]
        suffix = 0
        while True:
            cursor.execute(
                "SELECT 1 FROM nexora_projects WHERE project_name_normalized = %s AND deleted_at IS NULL",
                (normalized,),
            )
            if not cursor.fetchone():
                return candidate
            suffix += 1
            suffix_text = f" (imported{' ' + str(suffix) if suffix > 1 else ''})"
            candidate = (base[: max(1, 200 - len(suffix_text))] + suffix_text).strip()
            normalized = normalize_project_name(candidate)

    def _unique_type_id(self, cursor: Any, base: str, project_id: str) -> str:
        cursor.execute(
            "SELECT project_id FROM nexora_key_types WHERE id = %s", (base,)
        )
        row = cursor.fetchone()
        if not row:
            return base
        if row[0] and str(row[0]) == project_id:
            return base
        suffix = 0
        while True:
            suffix += 1
            suffix_text = f"_{suffix}"
            candidate = (base[: 50 - len(suffix_text)] + suffix_text).lower()
            cursor.execute(
                "SELECT project_id FROM nexora_key_types WHERE id = %s", (candidate,)
            )
            found = cursor.fetchone()
            if not found:
                return candidate
            if found[0] and str(found[0]) == project_id:
                return candidate

    def _snapshot_and_delete(
        self,
        cursor: Any,
        project_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT id, project_name, status, created_at
              FROM nexora_projects WHERE id = %s AND deleted_at IS NULL
              FOR UPDATE
            """,
            (project_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        cursor.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM reader_keys WHERE project_id = %s) AS keys,
              (SELECT COUNT(*) FROM nexora_devices WHERE project_id = %s) AS devices,
              (SELECT COUNT(*) FROM nexora_webhooks WHERE project_id = %s) AS webhooks
            """,
            (project_id, project_id, project_id),
        )
        counts = cursor.fetchone() or (0, 0, 0)
        snapshot = {
            "project_id": str(row[0]),
            "project_name": row[1],
            "status": row[2],
            "created_at": _timestamp_text(row[3]),
            "key_count": int(counts[0] or 0),
            "device_count": int(counts[1] or 0),
            "webhook_count": int(counts[2] or 0),
        }
        cursor.execute(
            """
            INSERT INTO admin_audit_logs
                (admin_user_id, actor_email, action, target_type, target_id,
                 outcome, ip_address, user_agent, request_id, details)
            VALUES (%s, %s, 'project.delete', 'project', %s, 'success',
                    %s, %s, %s, %s::jsonb)
            """,
            (
                actor.get("id"),
                actor.get("email"),
                project_id,
                ip_address,
                user_agent,
                request_id,
                json.dumps(
                    {"mode": "replace_on_import", "snapshot": snapshot},
                    default=str,
                ),
            ),
        )
        cursor.execute("DELETE FROM device_tokens WHERE project_id = %s", (project_id,))
        cursor.execute(
            "DELETE FROM reader_installations WHERE project_id = %s", (project_id,)
        )
        cursor.execute("DELETE FROM reader_keys WHERE project_id = %s", (project_id,))
        cursor.execute("DELETE FROM nexora_projects WHERE id = %s", (project_id,))
        return snapshot

    def import_bundle(
        self,
        bundle: Any,
        *,
        mode: str,
        project_name: str | None,
        confirm: str | None,
        replace_confirmed: bool,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        validated = self.validate_bundle(
            bundle, mode=mode, project_name=project_name
        )
        action = validated["action"]
        existing = validated["existing_project"]
        if action == "replace":
            if not replace_confirmed:
                raise ValidationError(
                    {"replace_confirmed": "Explicit replacement confirmation is required."}
                )
            expected = (existing or {}).get("project_name", "")
            if (confirm or "").strip() != expected:
                raise ValidationError(
                    {
                        "confirm": (
                            "Type the exact existing project name to confirm replacement."
                        )
                    }
                )
        checksum = hashlib.sha256(_canonical(bundle)).hexdigest()
        result_counts = {
            "key_types_created": 0,
            "keys_imported": 0,
            "keys_skipped": 0,
            "devices_imported": 0,
            "devices_skipped": 0,
            "webhooks_created": 0,
            "webhooks_skipped": 0,
        }
        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (validated["project"]["project_name_normalized"],),
                )
                cursor.execute(
                    """
                    SELECT id, project_name FROM nexora_projects
                     WHERE project_name_normalized = %s AND deleted_at IS NULL
                     FOR UPDATE
                    """,
                    (validated["project"]["project_name_normalized"],),
                )
                current = cursor.fetchone()
                replaced_snapshot = None
                if action == "replace":
                    if not current:
                        raise ApiError(
                            404,
                            "PROJECT_NOT_FOUND",
                            "The project to replace no longer exists.",
                        )
                    replaced_snapshot = self._snapshot_and_delete(
                        cursor,
                        str(current[0]),
                        actor=actor,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        request_id=request_id,
                    )
                    current = None
                if action == "merge":
                    if not current:
                        action = "create"
                    else:
                        project_id = str(current[0])
                        final_name = str(current[1])
                if action in {"create", "create_with_renamed_project", "replace"}:
                    base_name = validated["project"]["project_name"]
                    # Replacement already deleted the conflicting row inside this
                    # transaction, so the requested name is available again. A
                    # plain create must never overwrite an existing project.
                    final_name = (
                        base_name
                        if action == "replace"
                        else self._unique_name(cursor, base_name)
                    )
                    normalized = normalize_project_name(final_name)
                    project_id = str(uuid4())
                    project = validated["project"]
                    cursor.execute(
                        """
                        INSERT INTO nexora_projects
                            (id, project_name, project_name_normalized, description,
                             project_type, status, expires_at, never_expires, settings,
                             auto_delete_at, archived_at, created_at, updated_at,
                             created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                                %s, %s, now(), now(), %s)
                        """,
                        (
                            project_id,
                            final_name,
                            normalized,
                            project["description"],
                            project["project_type"],
                            project["status"],
                            project["expires_at"],
                            project["never_expires"],
                            json.dumps(project["settings"], default=str),
                            project["auto_delete_at"],
                            project["archived_at"],
                            actor.get("id"),
                        ),
                    )
                elif action == "merge":
                    project_id = str(current[0])
                    final_name = str(current[1])
                else:  # pragma: no cover - defensive
                    raise ApiError(409, "IMPORT_CONFLICT", "Import conflict.")

                type_map: dict[str, str] = {}
                for key_type in validated["key_types"]:
                    target_id = self._unique_type_id(
                        cursor, key_type["id"], project_id
                    )
                    type_map[key_type["id"]] = target_id
                    if target_id != key_type["id"] or action != "merge":
                        cursor.execute(
                            """
                            INSERT INTO nexora_key_types
                                (id, name, description, project_id, key_prefix,
                                 key_format, permissions, scopes, expiration_policy,
                                 device_policy, usage_limits, lifecycle_rules,
                                 endpoint_permissions, validation_rules,
                                 metadata_schema, is_builtin, is_active, created_by)
                            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                                    false, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (
                                target_id,
                                key_type["name"],
                                key_type["description"],
                                project_id,
                                key_type["key_prefix"],
                                json.dumps(key_type["key_format"]),
                                json.dumps(key_type["permissions"]),
                                json.dumps(key_type["scopes"]),
                                json.dumps(key_type["expiration_policy"]),
                                json.dumps(key_type["device_policy"]),
                                json.dumps(key_type["usage_limits"]),
                                json.dumps(key_type["lifecycle_rules"]),
                                json.dumps(key_type["endpoint_permissions"]),
                                json.dumps(key_type["validation_rules"]),
                                json.dumps(key_type["metadata_schema"]),
                                key_type["is_active"],
                                actor.get("id"),
                            ),
                        )
                        result_counts["key_types_created"] += 1
                    else:
                        cursor.execute(
                            """
                            UPDATE nexora_key_types
                               SET name = %s, description = %s, key_prefix = %s,
                                   key_format = %s::jsonb, permissions = %s::jsonb,
                                   scopes = %s::jsonb, expiration_policy = %s::jsonb,
                                   device_policy = %s::jsonb, usage_limits = %s::jsonb,
                                   lifecycle_rules = %s::jsonb,
                                   endpoint_permissions = %s::jsonb,
                                   validation_rules = %s::jsonb,
                                   metadata_schema = %s::jsonb, is_active = %s,
                                   updated_at = now()
                             WHERE id = %s AND project_id = %s
                            """,
                            (
                                key_type["name"],
                                key_type["description"],
                                key_type["key_prefix"],
                                json.dumps(key_type["key_format"]),
                                json.dumps(key_type["permissions"]),
                                json.dumps(key_type["scopes"]),
                                json.dumps(key_type["expiration_policy"]),
                                json.dumps(key_type["device_policy"]),
                                json.dumps(key_type["usage_limits"]),
                                json.dumps(key_type["lifecycle_rules"]),
                                json.dumps(key_type["endpoint_permissions"]),
                                json.dumps(key_type["validation_rules"]),
                                json.dumps(key_type["metadata_schema"]),
                                key_type["is_active"],
                                target_id,
                                project_id,
                            ),
                        )
                for key in validated["keys"]:
                    payload = {
                        "original_status": key["status"],
                        "permissions": key["permissions"],
                        "scopes": key["scopes"],
                        "tags": key["tags"],
                        "metadata": key["metadata"],
                        "usage_count": key["usage_count"],
                        "max_usage": key["max_usage"],
                        "is_permanent": key["is_permanent"],
                        "expires_at": _timestamp_text(key["expires_at"]),
                        "created_at": _timestamp_text(key["created_at"]),
                        "activated_at": _timestamp_text(key["activated_at"]),
                        "deactivated_at": _timestamp_text(key["deactivated_at"]),
                        "used_at": _timestamp_text(key["used_at"]),
                        "revoked_at": _timestamp_text(key["revoked_at"]),
                        "revoked_reason": key["revoked_reason"],
                        "requires_regeneration": True,
                        "key_material_imported": False,
                    }
                    cursor.execute(
                        """
                        INSERT INTO nexora_imported_key_records
                            (project_id, record_type, source_id, key_type_id,
                             label, key_hint, status, requires_regeneration,
                             data, imported_by)
                        VALUES (%s, 'key', %s, %s, %s, %s, %s, true, %s::jsonb, %s)
                        ON CONFLICT (project_id, record_type, source_id)
                            WHERE source_id <> '' DO NOTHING
                        """,
                        (
                            project_id,
                            key["source_id"],
                            type_map.get(key["key_type_id"], key["key_type_id"]),
                            key["label"],
                            key["key_hint"],
                            key["status"],
                            json.dumps(payload, default=str),
                            actor.get("id"),
                        ),
                    )
                    if cursor.rowcount:
                        result_counts["keys_imported"] += 1
                    else:
                        result_counts["keys_skipped"] += 1
                for device in validated["devices"]:
                    payload = {
                        "device_id": device["device_id"],
                        "device_name": device["device_name"],
                        "device_model": device["device_model"],
                        "platform": device["platform"],
                        "original_status": device["status"],
                        "metadata": device["metadata"],
                        "created_at": _timestamp_text(device["created_at"]),
                        "last_seen_at": _timestamp_text(device["last_seen_at"]),
                        "deactivated_at": _timestamp_text(device["deactivated_at"]),
                        "revoked_at": _timestamp_text(device["revoked_at"]),
                        "requires_regeneration": True,
                        "credential_imported": False,
                    }
                    cursor.execute(
                        """
                        INSERT INTO nexora_imported_key_records
                            (project_id, record_type, source_id, label, status,
                             requires_regeneration, data, imported_by)
                        VALUES (%s, 'installation', %s, %s, %s, true, %s::jsonb, %s)
                        ON CONFLICT (project_id, record_type, source_id)
                            WHERE source_id <> '' DO NOTHING
                        """,
                        (
                            project_id,
                            device["source_id"],
                            device["device_name"] or device["device_id"],
                            device["status"],
                            json.dumps(payload, default=str),
                            actor.get("id"),
                        ),
                    )
                    if cursor.rowcount:
                        result_counts["devices_imported"] += 1
                    else:
                        result_counts["devices_skipped"] += 1
                for webhook in validated["webhooks"]:
                    cursor.execute(
                        """
                        SELECT 1 FROM nexora_webhooks
                         WHERE project_id = %s AND name = %s AND url = %s
                        """,
                        (project_id, webhook["name"], webhook["url"]),
                    )
                    if cursor.fetchone():
                        result_counts["webhooks_skipped"] += 1
                        continue
                    cursor.execute(
                        """
                        INSERT INTO nexora_webhooks
                            (id, project_id, name, url, events, secret_salt,
                             secret_version, is_active, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, 1, false, %s)
                        """,
                        (
                            str(uuid4()),
                            project_id,
                            webhook["name"],
                            webhook["url"],
                            webhook["events"],
                            _secrets.token_urlsafe(24),
                            actor.get("id"),
                        ),
                    )
                    result_counts["webhooks_created"] += 1
                cursor.execute(
                    """
                    INSERT INTO admin_audit_logs
                        (admin_user_id, actor_email, action, target_type, target_id,
                         project_id, outcome, ip_address, user_agent, request_id,
                         details)
                    VALUES (%s, %s, 'project.import', 'project', %s, %s,
                            'success', %s, %s, %s, %s::jsonb)
                    """,
                    (
                        actor.get("id"),
                        actor.get("email"),
                        project_id,
                        project_id,
                        ip_address,
                        user_agent,
                        request_id,
                        json.dumps(
                            {
                                "mode": validated["mode"],
                                "action": action,
                                "bundle_sha256": checksum,
                                "counts": result_counts,
                                "replaced": replaced_snapshot,
                                "secrets_imported": False,
                                "keys_activated": 0,
                            },
                            default=str,
                        ),
                    ),
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409,
                    "IMPORT_CONFLICT",
                    "The bundle conflicts with an existing record. Nothing was imported.",
                ) from None
            raise
        return {
            "project": {
                "id": project_id,
                "project_name": final_name,
                "action": action,
            },
            "mode": validated["mode"],
            "counts": result_counts,
            "warnings": validated["warnings"],
            "secrets_imported": False,
            "keys_activated": 0,
            "bundle_sha256": checksum,
        }
