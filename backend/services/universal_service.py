"""Universal key management engine.

This module extends the existing Reader Key architecture rather than creating a
second secret store. Every universal key is a ``reader_keys`` row with a
``key_type_id``; only an HMAC digest is stored. Reader activation continues to
work for ``reader`` type keys, while other types authenticate only through the
type-aware access endpoint.

Security boundaries enforced here:
- project and key status/expiry are authoritative on every authentication;
- key-type permissions are a ceiling and per-key permissions are a subset;
- type isolation is checked from the stored row, never a client claim;
- admin key types still require an authenticated human session for admin APIs;
- plaintext exists only in the creation/regeneration/import request and the
  one-time response, never in logs, audit records, search or exports.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence
from uuid import UUID, uuid4

from ..db import Database, is_unique_violation
from ..errors import ApiError, ValidationError
from ..security.keys import (
    KeyFormatError,
    KeySpec,
    generate_plaintext_key,
    hash_reader_key,
    masked_hint,
    normalize_key,
    normalize_project_name,
)
from ..serialization import utcnow

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
PROJECT_STATUSES = ("active", "inactive", "expired", "deleted")
WEBHOOK_EVENTS = (
    "project.created",
    "project.updated",
    "project.expired",
    "project.archived",
    "key.generated",
    "key.activated",
    "key.deactivated",
    "key.expired",
    "key.revoked",
    "key.usage_limit",
    "device.activated",
    "device.deactivated",
    "security.alert",
    "usage.limit_reached",
)
EXPIRATION_DELTAS = {
    "1_day": timedelta(days=1),
    "7_days": timedelta(days=7),
    "30_days": timedelta(days=30),
    "90_days": timedelta(days=90),
    "1_year": timedelta(days=365),
}
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({"*"}),
    "administrator": frozenset(
        {
            "analytics:read",
            "project:read",
            "project:write",
            "key:read",
            "key:write",
            "device:read",
            "device:write",
            "webhook:read",
            "webhook:write",
            "notification:read",
            "notification:write",
            "automation:read",
            "automation:run",
            "admin:read",
            "admin:sessions",
            "import:write",
            "export:read",
        }
    ),
    "project_manager": frozenset(
        {
            "analytics:read",
            "project:read",
            "project:write",
            "key:read",
            "key:write",
            "device:read",
            "notification:read",
            "export:read",
        }
    ),
    "key_manager": frozenset(
        {
            "analytics:read",
            "project:read",
            "key:read",
            "key:write",
            "device:read",
            "device:write",
            "notification:read",
            "export:read",
        }
    ),
    "read_only": frozenset(
        {
            "analytics:read",
            "project:read",
            "key:read",
            "device:read",
            "webhook:read",
            "notification:read",
            "automation:read",
            "admin:read",
            "export:read",
        }
    ),
    "custom": frozenset(),
}
PLATFORM_ROLES = tuple(ROLE_PERMISSIONS)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_obj(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _string_list(value: Any, field: str, *, max_items: int = 50) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError({field: "Must be an array of strings."})
    if len(value) > max_items:
        raise ValidationError({field: f"At most {max_items} entries are allowed."})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError({field: "Every entry must be a non-empty string."})
        item = item.strip()
        if len(item) > 80:
            raise ValidationError({field: "Every entry must be 80 characters or fewer."})
        if item not in result:
            result.append(item)
    return result


def _like_term(value: str) -> str:
    return f"%{value.strip().lower()}%"


def pagination(page: int, page_size: int, total: int) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    return {
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "total_pages": math.ceil(total / page_size) if total else 1,
    }


class UniversalService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self.pepper = getattr(settings, "key_pepper", "")

    # ------------------------------------------------------------------ audit
    def _audit(
        self,
        cursor: Any,
        *,
        actor: dict[str, Any] | None,
        action: str,
        target_type: str,
        target_id: str | None,
        outcome: str = "success",
        project_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
        strict: bool = False,
    ) -> None:
        sql = """
            INSERT INTO admin_audit_logs
                (admin_user_id, actor_email, action, target_type, target_id,
                 outcome, ip_address, user_agent, details, project_id, request_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        """
        params = (
            str(actor.get("id")) if actor and actor.get("id") else None,
            (actor or {}).get("email"),
            action,
            target_type,
            target_id,
            outcome,
            ip_address,
            user_agent,
            json.dumps(details or {}, default=str),
            project_id,
            request_id,
        )
        try:
            cursor.execute(sql, params)
        except Exception:
            if strict:
                raise

    def _event(
        self,
        cursor: Any,
        *,
        key_id: str | None,
        project_id: str | None,
        fingerprint: str,
        event: str,
        actor: dict[str, Any] | None,
        result: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO nexora_key_events
                (project_id, key_id, fingerprint, event, actor_id, actor_email,
                 result, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                project_id,
                key_id,
                fingerprint or "",
                event,
                str(actor.get("id")) if actor and actor.get("id") else None,
                (actor or {}).get("email", ""),
                result,
                json.dumps(metadata or {}, default=str),
            ),
        )

    def _notify(
        self,
        cursor: Any,
        *,
        type_: str,
        severity: str,
        title: str,
        body: str,
        project_id: str | None = None,
        key_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str = "",
        admin_user_id: str | None = None,
    ) -> bool:
        if dedupe_key:
            exists = cursor.execute(
                """
                SELECT 1 FROM nexora_notifications
                 WHERE metadata->>'dedupe_key' = %s
                 LIMIT 1
                """,
                (dedupe_key,),
            )
            if cursor.fetchone():
                return False
        payload = dict(metadata or {})
        if dedupe_key:
            payload["dedupe_key"] = dedupe_key
        cursor.execute(
            """
            INSERT INTO nexora_notifications
                (admin_user_id, project_id, key_id, type, severity, title, body, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                admin_user_id,
                project_id,
                key_id,
                type_,
                severity,
                title[:200],
                body[:1000],
                json.dumps(payload, default=str),
            ),
        )
        return True

    # --------------------------------------------------------------- helpers
    def _permissions_from_rules(self, rules: Any) -> list[str]:
        return [str(item) for item in _json_list(rules)]

    def _type_row(self, key_type_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT * FROM nexora_key_types WHERE id = %s", (key_type_id,)
        )
        if not row:
            raise ApiError(404, "KEY_TYPE_NOT_FOUND", "Key type not found.")
        return row

    def _project_row(self, project_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """
            SELECT id, project_name, project_name_normalized, description,
                   project_type, status, expires_at, never_expires, archived_at,
                   settings, created_at, updated_at, deleted_at
              FROM nexora_projects WHERE id = %s
            """,
            (project_id,),
        )
        if not row:
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        return row

    def _assert_project_live(self, project: dict[str, Any], *, cursor: Any = None) -> None:
        if project.get("deleted_at"):
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        if project.get("archived_at"):
            raise ApiError(403, "PROJECT_ARCHIVED", "Project is archived.")
        if project.get("status") != "active":
            raise ApiError(403, "PROJECT_INACTIVE", "Project is not active.")
        expires = _utc(project.get("expires_at"))
        if expires and expires <= utcnow():
            if cursor is not None:
                cursor.execute(
                    "UPDATE nexora_projects SET status = 'expired', updated_at = now() WHERE id = %s",
                    (str(project["id"]),),
                )
            raise ApiError(403, "PROJECT_EXPIRED", "Project has expired.")

    def _key_spec(self, type_row: dict[str, Any]) -> KeySpec:
        fmt = _json_obj(type_row.get("key_format"))
        try:
            return KeySpec(
                prefix=str(type_row.get("key_prefix") or fmt.get("prefix") or ""),
                suffix=str(fmt.get("suffix") or ""),
                groups=int(fmt.get("groups", 4)),
                group_length=int(fmt.get("group_length", 4)),
                separator=str(fmt.get("separator", "-")),
            )
        except (KeyFormatError, TypeError, ValueError) as exc:
            raise ApiError(
                500,
                "KEY_TYPE_FORMAT_INVALID",
                f"The stored key format is invalid: {exc}",
            ) from None

    def _fingerprint(self, plaintext: str, key_type_id: str) -> str:
        digest = hmac.new(
            self.pepper.encode("utf-8"),
            f"nexora:v1:universal-fingerprint:{key_type_id}:{plaintext}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest[:20]

    def _resolve_expiration(
        self,
        preset: str | None,
        expires_at: datetime | None,
        now: datetime,
        *,
        project: bool = False,
    ) -> tuple[str, datetime | None, bool]:
        preset = (preset or "never").strip().lower()
        expires = _utc(expires_at)
        if preset == "never":
            return "never", None, True
        if preset == "custom":
            if expires is None:
                raise ValidationError(
                    {"expires_at": "A custom expiration timestamp is required."}
                )
            if expires <= now:
                raise ValidationError(
                    {"expires_at": "The expiration must be in the future."}
                )
            return "custom", expires, False
        delta = EXPIRATION_DELTAS.get(preset)
        if delta is None:
            raise ValidationError(
                {"expiration_preset": "Use 1_day, 7_days, 30_days, 90_days, 1_year, custom or never."}
            )
        return preset, now + delta, False

    def _validate_permissions(
        self, requested: Any, type_row: dict[str, Any]
    ) -> list[str]:
        values = _string_list(requested, "permissions")
        known = {
            row["id"]
            for row in self.db.fetch_all(
                "SELECT id FROM nexora_permission_definitions"
            )
        }
        allowed = set(self._permissions_from_rules(type_row.get("permissions")))
        for value in values:
            if value not in known:
                raise ValidationError(
                    {"permissions": f"Unknown permission: {value}"}
                )
            if value not in allowed:
                raise ValidationError(
                    {
                        "permissions": (
                            f"Permission '{value}' is not enabled for key type "
                            f"'{type_row['id']}'."
                        )
                    }
                )
        return values if values else sorted(allowed)

    def _validate_scopes(self, requested: Any, type_row: dict[str, Any]) -> list[str]:
        values = _string_list(requested, "scopes")
        for value in values:
            if not SCOPE_RE.match(value):
                raise ValidationError(
                    {"scopes": f"Scope '{value}' must use lowercase letters, digits, . _ : -."}
                )
        allowed = set(str(item) for item in _json_list(type_row.get("scopes")))
        if allowed:
            for value in values:
                if value not in allowed:
                    raise ValidationError(
                        {"scopes": f"Scope '{value}' is not enabled for this key type."}
                    )
            return values if values else sorted(allowed)
        return values

    def _validate_metadata(
        self, metadata: Any, type_row: dict[str, Any]
    ) -> dict[str, Any]:
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValidationError({"metadata": "Must be an object."})
        if len(json.dumps(metadata, default=str)) > 8000:
            raise ValidationError({"metadata": "Metadata is too large."})
        schema = _json_obj(type_row.get("metadata_schema"))
        fields = schema.get("fields") if isinstance(schema.get("fields"), dict) else {}
        for name, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            required = bool(spec.get("required"))
            if required and name not in metadata:
                raise ValidationError(
                    {"metadata": f"Required metadata field '{name}' is missing."}
                )
            if name not in metadata:
                continue
            kind = spec.get("type", "string")
            value = metadata[name]
            valid = (
                (kind == "string" and isinstance(value, str))
                or (kind == "number" and isinstance(value, (int, float)))
                or (kind == "boolean" and isinstance(value, bool))
                or (kind == "array" and isinstance(value, list))
                or (kind == "object" and isinstance(value, dict))
            )
            if not valid:
                raise ValidationError(
                    {"metadata": f"Metadata field '{name}' must be {kind}."}
                )
            if kind == "string" and spec.get("max_length"):
                if len(value) > int(spec["max_length"]):
                    raise ValidationError(
                        {"metadata": f"Metadata field '{name}' is too long."}
                    )
        rules = _json_obj(type_row.get("validation_rules"))
        for name in rules.get("required_metadata", []) or []:
            if name not in metadata:
                raise ValidationError(
                    {"metadata": f"Required metadata field '{name}' is missing."}
                )
        return metadata

    def _public_key(self, row: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        expires = _utc(row.get("expires_at"))
        project_expires = _utc(row.get("project_expires_at"))
        status = str(row.get("status") or "")
        if status in {"generated", "active", "deactivated"} and expires and expires <= now:
            status = "expired"
        project_status = str(row.get("project_status") or "")
        if row.get("project_archived_at"):
            project_status = "archived"
        elif project_expires and project_expires <= now:
            project_status = "expired"
        return {
            "id": str(row.get("id")),
            "project_id": str(row.get("project_id")),
            "project_name": row.get("project_name", ""),
            "project_status": project_status,
            "label": row.get("label", ""),
            "key_hint": row.get("key_hint", ""),
            "fingerprint": row.get("fingerprint", ""),
            "status": status,
            "key_type_id": row.get("key_type_id", ""),
            "key_type_name": row.get("key_type_name", ""),
            "permissions": _json_list(row.get("permissions")),
            "scopes": _json_list(row.get("scopes")),
            "tags": list(row.get("tags") or []),
            "notes": row.get("notes", ""),
            "metadata": _json_obj(row.get("metadata")),
            "usage_count": int(row.get("usage_count") or 0),
            "max_usage": row.get("max_usage"),
            "is_permanent": bool(row.get("is_permanent")),
            "expiration_preset": row.get("expiration_preset", "never"),
            "expires_at": expires.isoformat() if expires else None,
            "created_at": row.get("created_at"),
            "used_at": row.get("used_at"),
            "activated_at": row.get("activated_at"),
            "deactivated_at": row.get("deactivated_at"),
            "revoked_at": row.get("revoked_at"),
            "revoked_reason": row.get("revoked_reason", ""),
            "device_count": int(row.get("device_count") or 0),
        }

    def _load_key(self, key_id: str, *, for_update: bool = False) -> dict[str, Any]:
        row = self.db.fetch_one(
            f"""
            SELECT k.*, p.project_name, p.status AS project_status,
                   p.expires_at AS project_expires_at,
                   p.archived_at AS project_archived_at,
                   t.name AS key_type_name, t.is_active AS key_type_active,
                   t.permissions AS type_permissions, t.scopes AS type_scopes,
                   t.device_policy, t.usage_limits, t.lifecycle_rules,
                   t.endpoint_permissions, t.validation_rules, t.metadata_schema,
                   t.expiration_policy,
                   (SELECT COUNT(*)::int FROM nexora_devices d
                     WHERE d.key_id = k.id AND d.status = 'active') AS device_count
              FROM reader_keys k
              JOIN nexora_projects p ON p.id = k.project_id
              JOIN nexora_key_types t ON t.id = k.key_type_id
             WHERE k.id = %s
             {"FOR UPDATE OF k" if for_update else ""}
            """,
            (key_id,),
        )
        if not row:
            raise ApiError(404, "KEY_NOT_FOUND", "Key not found.")
        return row

    # ---------------------------------------------------------------- roles
    def authorize(self, actor: dict[str, Any], permission: str) -> None:
        if not actor or not actor.get("id"):
            raise ApiError(401, "not_authenticated", "Sign in to continue.")
        if actor.get("role") == "owner":
            return
        row = self.db.fetch_one(
            """
            SELECT platform_role, platform_permissions
              FROM admin_users WHERE id = %s AND is_active = true
            """,
            (actor["id"],),
        )
        if not row:
            raise ApiError(401, "not_authenticated", "Sign in to continue.")
        platform_role = str(row.get("platform_role") or "read_only")
        allowed = ROLE_PERMISSIONS.get(platform_role, frozenset())
        if platform_role == "custom":
            allowed = frozenset(
                str(item) for item in _json_list(row.get("platform_permissions"))
            )
        if "*" in allowed or permission in allowed:
            return
        raise ApiError(
            403,
            "forbidden",
            "Your platform role does not permit this action.",
        )

    def list_roles(self) -> dict[str, Any]:
        definitions = []
        for role, permissions in ROLE_PERMISSIONS.items():
            definitions.append(
                {
                    "id": role,
                    "name": role.replace("_", " ").title(),
                    "permissions": sorted(permissions),
                    "custom": role == "custom",
                }
            )
        return {
            "roles": definitions,
            "permissions": self.db.fetch_all(
                "SELECT * FROM nexora_permission_definitions ORDER BY id"
            ),
        }

    def list_admins(self) -> dict[str, Any]:
        rows = self.db.fetch_all(
            """
            SELECT u.id, u.email, u.full_name, u.role, u.platform_role,
                   u.platform_permissions, u.is_active, u.last_login_at,
                   u.created_at,
                   (SELECT COUNT(*)::int FROM admin_sessions s
                     WHERE s.admin_user_id = u.id AND s.revoked_at IS NULL
                       AND s.expires_at > now()) AS active_sessions
              FROM admin_users u
             ORDER BY u.created_at ASC
            """
        )
        return {"admins": rows}

    def set_admin_role(
        self,
        admin_id: str,
        *,
        platform_role: str,
        platform_permissions: list[str] | None,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        platform_role = (platform_role or "").strip().lower()
        if platform_role not in PLATFORM_ROLES:
            raise ValidationError({"platform_role": "Unknown platform role."})
        target = self.db.fetch_one(
            "SELECT id, email, role, platform_role FROM admin_users WHERE id = %s",
            (admin_id,),
        )
        if not target:
            raise ApiError(404, "ADMIN_NOT_FOUND", "Administrator not found.")
        permissions = _string_list(platform_permissions or [], "platform_permissions")
        known = {
            row["id"] for row in self.db.fetch_all("SELECT id FROM nexora_permission_definitions")
        }
        unknown = set(permissions) - known
        if unknown:
            raise ValidationError({"platform_permissions": f"Unknown: {sorted(unknown)}"})
        if platform_role == "custom" and not permissions:
            raise ValidationError(
                {"platform_permissions": "Custom roles require at least one permission."}
            )
        base_role = {
            "owner": "owner",
            "read_only": "viewer",
        }.get(platform_role, "admin")
        if target["role"] == "owner" and platform_role != "owner":
            owners = self.db.fetch_value(
                "SELECT COUNT(*)::int FROM admin_users WHERE role = 'owner' AND is_active = true"
            )
            if int(owners or 0) <= 1:
                raise ApiError(
                    409,
                    "LAST_OWNER",
                    "The last active Owner cannot be demoted.",
                )
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_users
                   SET platform_role = %s, platform_permissions = %s::jsonb,
                       role = %s, updated_at = now()
                 WHERE id = %s
                """,
                (
                    platform_role,
                    json.dumps(permissions),
                    base_role,
                    admin_id,
                ),
            )
            self._audit(
                cursor,
                actor=actor,
                action="admin.role_change",
                target_type="admin_user",
                target_id=admin_id,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={
                    "target_email": target["email"],
                    "old_platform_role": target["platform_role"],
                    "new_platform_role": platform_role,
                    "permissions": permissions,
                },
                strict=True,
            )
        return self.list_admins()

    def revoke_admin_sessions(
        self,
        admin_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        target = self.db.fetch_one(
            "SELECT id, email FROM admin_users WHERE id = %s", (admin_id,)
        )
        if not target:
            raise ApiError(404, "ADMIN_NOT_FOUND", "Administrator not found.")
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_sessions
                   SET revoked_at = now()
                 WHERE admin_user_id = %s AND revoked_at IS NULL
                """,
                (admin_id,),
            )
            revoked = cursor.rowcount
            self._audit(
                cursor,
                actor=actor,
                action="admin.sessions_revoked",
                target_type="admin_user",
                target_id=admin_id,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={"target_email": target["email"], "revoked": revoked},
                strict=True,
            )
        return {"revoked": revoked}

    # -------------------------------------------------------------- projects
    def list_projects(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        search: str | None = None,
        project_type: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        clauses = ["p.deleted_at IS NULL"]
        params: list[Any] = []
        if search and search.strip():
            term = _like_term(search)
            clauses.append(
                "(LOWER(p.project_name) LIKE %s OR LOWER(p.description) LIKE %s OR p.id::text = %s)"
            )
            params.extend([term, term, search.strip()])
        if project_type:
            clauses.append("p.project_type = %s")
            params.append(project_type.strip().lower())
        if status:
            status = status.strip().lower()
            if status == "archived":
                clauses.append("p.archived_at IS NOT NULL")
            elif status == "expired":
                clauses.append(
                    "(p.status = 'expired' OR (p.expires_at IS NOT NULL AND p.expires_at <= now()))"
                )
            elif status in PROJECT_STATUSES:
                clauses.append("p.status = %s AND p.archived_at IS NULL")
                params.append(status)
            else:
                raise ValidationError({"status": "Unknown project status."})
        allowed_sort = {
            "created_at": "p.created_at",
            "updated_at": "p.updated_at",
            "name": "p.project_name",
            "expires_at": "p.expires_at",
        }
        sort_sql = allowed_sort.get(sort, "p.created_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"
        where = " AND ".join(clauses)
        total = (
            self.db.fetch_value(
                f"SELECT COUNT(*)::int FROM nexora_projects p WHERE {where}",
                tuple(params),
            )
            or 0
        )
        rows = self.db.fetch_all(
            f"""
            SELECT p.id, p.project_name, p.description, p.project_type,
                   p.status, p.created_at, p.updated_at, p.expires_at,
                   p.never_expires, p.archived_at, p.cloned_from,
                   CASE
                     WHEN p.archived_at IS NOT NULL THEN 'archived'
                     WHEN p.expires_at IS NOT NULL AND p.expires_at <= now() THEN 'expired'
                     ELSE p.status
                   END AS effective_status,
                   stats.total_keys, stats.active_keys, stats.deactivated_keys,
                   stats.expired_keys, stats.revoked_keys,
                   COALESCE(devices.active_devices, 0) AS active_devices
              FROM nexora_projects p
              LEFT JOIN LATERAL (
                  SELECT COUNT(*)::int AS total_keys,
                         COUNT(*) FILTER (WHERE k.status IN ('generated','active','used'))::int AS active_keys,
                         COUNT(*) FILTER (WHERE k.status = 'deactivated')::int AS deactivated_keys,
                         COUNT(*) FILTER (WHERE k.status = 'expired')::int AS expired_keys,
                         COUNT(*) FILTER (WHERE k.status = 'revoked')::int AS revoked_keys
                    FROM reader_keys k
                   WHERE k.project_id = p.id AND k.status <> 'deleted'
              ) stats ON true
              LEFT JOIN LATERAL (
                  SELECT COUNT(*)::int AS active_devices
                    FROM nexora_devices d
                   WHERE d.project_id = p.id AND d.status = 'active'
              ) devices ON true
             WHERE {where}
             ORDER BY {sort_sql} {direction}
             LIMIT %s OFFSET %s
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return {"items": rows, "pagination": pagination(page, page_size, total)}

    def update_project(
        self,
        project_id: str,
        *,
        changes: dict[str, Any],
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        allowed = {
            "project_name",
            "description",
            "project_type",
            "status",
            "expiration_preset",
            "expires_at",
            "settings",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError({"body": f"Unknown fields: {sorted(unknown)}"})
        project = self._project_row(project_id)
        if project.get("deleted_at"):
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        now = utcnow()
        name = (changes.get("project_name") or project["project_name"]).strip()
        if not name or len(name) > 200:
            raise ValidationError({"project_name": "Must be 1-200 characters."})
        try:
            normalized = normalize_project_name(name)
        except ValueError as exc:
            raise ValidationError({"project_name": str(exc)}) from None
        description = str(changes.get("description", project.get("description") or ""))[:1000]
        project_type = str(changes.get("project_type", project.get("project_type") or "database")).strip()
        if not project_type or len(project_type) > 40:
            raise ValidationError({"project_type": "Must be 1-40 characters."})
        status = str(changes.get("status", project.get("status") or "active")).lower()
        if status not in ("active", "inactive"):
            raise ValidationError({"status": "Only active or inactive can be set directly."})
        settings_value = changes.get("settings", project.get("settings") or {})
        if not isinstance(settings_value, dict):
            raise ValidationError({"settings": "Must be an object."})
        if len(json.dumps(settings_value, default=str)) > 12000:
            raise ValidationError({"settings": "Settings payload is too large."})

        preset = changes.get("expiration_preset")
        expires_at = changes.get("expires_at")
        if preset is None and expires_at is None:
            # Preserve the current expiry unless the caller changes it.
            resolved_expires = _utc(project.get("expires_at"))
            resolved_preset = "never" if not resolved_expires else "custom"
            never = resolved_expires is None
        else:
            resolved_preset, resolved_expires, never = self._resolve_expiration(
                preset or ("custom" if expires_at else "never"),
                expires_at,
                now,
                project=True,
            )
        if changes.get("expiration_preset") == "never":
            resolved_preset, resolved_expires, never = "never", None, True

        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM nexora_projects WHERE project_name_normalized = %s AND deleted_at IS NULL AND id <> %s",
                    (normalized, project_id),
                )
                if cursor.fetchone():
                    raise ApiError(409, "PROJECT_ALREADY_EXISTS", "This project name already exists.")
                cursor.execute(
                    """
                    UPDATE nexora_projects
                       SET project_name = %s, project_name_normalized = %s,
                           description = %s, project_type = %s, status = %s,
                           expires_at = %s, never_expires = %s, settings = %s::jsonb,
                           updated_at = %s
                     WHERE id = %s AND deleted_at IS NULL
                    """,
                    (
                        name,
                        normalized,
                        description,
                        project_type.lower(),
                        status,
                        resolved_expires,
                        never,
                        json.dumps(settings_value),
                        now,
                        project_id,
                    ),
                )
                self._audit(
                    cursor,
                    actor=actor,
                    action="project.update",
                    target_type="project",
                    target_id=project_id,
                    project_id=project_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                    details={
                        "project_name": name,
                        "status": status,
                        "expiration_preset": resolved_preset,
                        "expires_at": resolved_expires.isoformat() if resolved_expires else None,
                    },
                    strict=True,
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(409, "PROJECT_ALREADY_EXISTS", "This project name already exists.") from None
            raise
        self._emit("project.updated", project_id, {"project_id": project_id, "status": status})
        return self._project_row(project_id)

    def archive_project(
        self,
        project_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        project = self._project_row(project_id)
        if project.get("deleted_at"):
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_projects
                   SET archived_at = now(), status = 'inactive', updated_at = now()
                 WHERE id = %s AND deleted_at IS NULL
                """,
                (project_id,),
            )
            cursor.execute(
                "UPDATE nexora_devices SET status = 'deactivated', deactivated_at = now() WHERE project_id = %s AND status = 'active'",
                (project_id,),
            )
            self._audit(
                cursor,
                actor=actor,
                action="project.archive",
                target_type="project",
                target_id=project_id,
                project_id=project_id,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={"project_name": project["project_name"]},
                strict=True,
            )
        self._emit("project.archived", project_id, {"project_id": project_id})
        return self._project_row(project_id)

    def clone_project(
        self,
        project_id: str,
        *,
        new_name: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        source = self._project_row(project_id)
        if source.get("deleted_at"):
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        name = (new_name or "").strip()
        if not name or len(name) > 200:
            raise ValidationError({"project_name": "Must be 1-200 characters."})
        normalized = normalize_project_name(name)
        new_id = str(uuid4())
        now = utcnow()
        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM nexora_projects WHERE project_name_normalized = %s AND deleted_at IS NULL",
                    (normalized,),
                )
                if cursor.fetchone():
                    raise ApiError(409, "PROJECT_ALREADY_EXISTS", "This project name already exists.")
                cursor.execute(
                    """
                    INSERT INTO nexora_projects
                        (id, project_name, project_name_normalized, description,
                         project_type, status, expires_at, never_expires, settings,
                         cloned_from, created_at, updated_at, created_by)
                    VALUES (%s, %s, %s, %s, %s, 'active', NULL, true, %s::jsonb,
                            %s, %s, %s, %s)
                    """,
                    (
                        new_id,
                        name,
                        normalized,
                        source.get("description", ""),
                        source.get("project_type", "database"),
                        json.dumps(_json_obj(source.get("settings"))),
                        project_id,
                        now,
                        now,
                        actor.get("id"),
                    ),
                )
                self._audit(
                    cursor,
                    actor=actor,
                    action="project.clone",
                    target_type="project",
                    target_id=new_id,
                    project_id=new_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                    details={"source_project_id": project_id, "project_name": name},
                    strict=True,
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(409, "PROJECT_ALREADY_EXISTS", "This project name already exists.") from None
            raise
        self._emit("project.created", new_id, {"project_id": new_id, "cloned_from": project_id})
        return {"project": self._project_row(new_id), "keys_cloned": 0}

    def project_stats(self, project_id: str) -> dict[str, Any]:
        self._expire_overdue()
        project = self._project_row(project_id)
        key_counts = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE status IN ('generated','active','used'))::int AS active,
                   COUNT(*) FILTER (WHERE status = 'deactivated')::int AS deactivated,
                   COUNT(*) FILTER (WHERE status = 'expired')::int AS expired,
                   COUNT(*) FILTER (WHERE status = 'revoked')::int AS revoked,
                   COUNT(*) FILTER (WHERE expires_at IS NOT NULL
                                      AND expires_at > now()
                                      AND expires_at <= now() + interval '7 days')::int AS expiring_7d
              FROM reader_keys WHERE project_id = %s AND status <> 'deleted'
            """,
            (project_id,),
        ) or {}
        distribution = self.db.fetch_all(
            """
            SELECT k.key_type_id, t.name AS key_type_name, COUNT(*)::int AS count
              FROM reader_keys k JOIN nexora_key_types t ON t.id = k.key_type_id
             WHERE k.project_id = %s AND k.status <> 'deleted'
             GROUP BY k.key_type_id, t.name ORDER BY count DESC
            """,
            (project_id,),
        )
        devices = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE status = 'active')::int AS active
              FROM nexora_devices WHERE project_id = %s
            """,
            (project_id,),
        ) or {}
        usage = self.db.fetch_value(
            "SELECT COUNT(*)::int FROM nexora_api_usage WHERE project_id = %s AND created_at >= now() - interval '7 days'",
            (project_id,),
        )
        return {
            "project": project,
            "keys": key_counts,
            "key_type_distribution": distribution,
            "devices": devices,
            "api_usage_7d": int(usage or 0),
        }

    def project_timeline(self, project_id: str, *, limit: int = 100) -> dict[str, Any]:
        self._project_row(project_id)
        events = self.db.fetch_all(
            """
            SELECT id::text, event, fingerprint, result, actor_email, metadata, created_at
              FROM nexora_key_events WHERE project_id = %s
             ORDER BY created_at DESC LIMIT %s
            """,
            (project_id, min(limit, 200)),
        )
        audits = self.db.fetch_all(
            """
            SELECT id::text, action, target_type, target_id, outcome, actor_email,
                   details, created_at
              FROM admin_audit_logs WHERE project_id = %s
             ORDER BY created_at DESC LIMIT %s
            """,
            (project_id, min(limit, 200)),
        )
        merged = sorted(
            [dict(item, source="key_event") for item in events]
            + [dict(item, source="audit") for item in audits],
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )[: min(limit, 200)]
        return {"items": merged}

    # ------------------------------------------------------------- key types
    def list_key_types(
        self,
        *,
        project_id: str | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if not include_inactive:
            clauses.append("t.is_active = true")
        if project_id:
            clauses.append("(t.project_id IS NULL OR t.project_id = %s)")
            params.append(project_id)
        rows = self.db.fetch_all(
            f"""
            SELECT t.*,
                   (SELECT COUNT(*)::int FROM reader_keys k
                     WHERE k.key_type_id = t.id AND k.status <> 'deleted') AS key_count,
                   p.project_name
              FROM nexora_key_types t
              LEFT JOIN nexora_projects p ON p.id = t.project_id
             WHERE {' AND '.join(clauses)}
             ORDER BY t.is_builtin DESC, t.name ASC
            """,
            tuple(params),
        )
        return {
            "key_types": rows,
            "events": list(WEBHOOK_EVENTS),
            "permissions": self.db.fetch_all(
                "SELECT * FROM nexora_permission_definitions ORDER BY id"
            ),
        }

    def _validate_key_type_payload(
        self,
        data: dict[str, Any],
        *,
        existing_id: str | None = None,
    ) -> dict[str, Any]:
        type_id = str(data.get("id") or existing_id or "").strip().lower()
        if not TYPE_ID_RE.match(type_id):
            raise ValidationError(
                {"id": "Use 3-50 lowercase letters, digits or underscores, starting with a letter."}
            )
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValidationError({"name": "Must be 1-80 characters."})
        prefix = str(data.get("key_prefix") or "NX").strip().upper()
        if not re.match(r"^[A-Z0-9]{0,12}$", prefix):
            raise ValidationError({"key_prefix": "Use at most 12 uppercase letters or digits."})
        fmt = data.get("key_format") or {
            "groups": 4,
            "group_length": 4,
            "separator": "-",
        }
        if not isinstance(fmt, dict):
            raise ValidationError({"key_format": "Must be an object."})
        try:
            KeySpec(
                prefix=prefix,
                suffix=str(fmt.get("suffix") or "").upper(),
                groups=int(fmt.get("groups", 4)),
                group_length=int(fmt.get("group_length", 4)),
                separator=str(fmt.get("separator", "-")),
            )
        except (KeyFormatError, TypeError, ValueError) as exc:
            raise ValidationError({"key_format": str(exc)}) from None
        permissions = _string_list(data.get("permissions") or ["read"], "permissions")
        known = {
            row["id"]
            for row in self.db.fetch_all("SELECT id FROM nexora_permission_definitions")
        }
        unknown = set(permissions) - known
        if unknown:
            raise ValidationError({"permissions": f"Unknown permissions: {sorted(unknown)}"})
        scopes = _string_list(data.get("scopes") or [], "scopes")
        for scope in scopes:
            if not SCOPE_RE.match(scope):
                raise ValidationError({"scopes": f"Invalid scope: {scope}"})
        rules = data.get("validation_rules") or {}
        if not isinstance(rules, dict):
            raise ValidationError({"validation_rules": "Must be an object."})
        allowed_rule_keys = {"required_metadata", "min_length", "max_length", "allowed_metadata"}
        unknown_rules = set(rules) - allowed_rule_keys
        if unknown_rules:
            raise ValidationError(
                {
                    "validation_rules": (
                        "Only declarative rules are allowed: "
                        + ", ".join(sorted(allowed_rule_keys))
                    )
                }
            )
        metadata_schema = data.get("metadata_schema") or {}
        if not isinstance(metadata_schema, dict):
            raise ValidationError({"metadata_schema": "Must be an object."})
        if "fields" in metadata_schema and not isinstance(metadata_schema["fields"], dict):
            raise ValidationError({"metadata_schema.fields": "Must be an object."})
        for entry in (metadata_schema.get("fields") or {}).values():
            if isinstance(entry, dict) and entry.get("type") not in {
                None,
                "string",
                "number",
                "boolean",
                "array",
                "object",
            }:
                raise ValidationError({"metadata_schema": "Unsupported field type."})
        device_policy = data.get("device_policy") or {}
        if not isinstance(device_policy, dict):
            raise ValidationError({"device_policy": "Must be an object."})
        max_devices = int(device_policy.get("max_devices", 0) or 0)
        if not 0 <= max_devices <= 1000:
            raise ValidationError({"device_policy": "max_devices must be 0-1000."})
        project_id = data.get("project_id")
        if project_id:
            self._project_row(str(project_id))
        return {
            "id": type_id,
            "name": name,
            "description": str(data.get("description") or "")[:500],
            "project_id": str(project_id) if project_id else None,
            "key_prefix": prefix,
            "key_format": {
                "groups": int(fmt.get("groups", 4)),
                "group_length": int(fmt.get("group_length", 4)),
                "separator": str(fmt.get("separator", "-")),
                "suffix": str(fmt.get("suffix") or "").upper(),
            },
            "permissions": permissions,
            "scopes": scopes,
            "expiration_policy": data.get("expiration_policy") or {
                "presets": ["1_day", "7_days", "30_days", "90_days", "1_year", "custom", "never"],
                "default": "never",
            },
            "device_policy": {
                "binds": bool(device_policy.get("binds")),
                "required": bool(device_policy.get("required")),
                "max_devices": max_devices,
            },
            "usage_limits": data.get("usage_limits") or {
                "rate_per_minute": 60,
                "max_usage": None,
            },
            "lifecycle_rules": data.get("lifecycle_rules") or {
                "can_activate": True,
                "can_deactivate": True,
                "can_regenerate": True,
            },
            "endpoint_permissions": _string_list(
                data.get("endpoint_permissions") or [], "endpoint_permissions"
            ),
            "validation_rules": rules,
            "metadata_schema": metadata_schema,
            "is_active": bool(data.get("is_active", True)),
        }

    def create_key_type(
        self,
        data: dict[str, Any],
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        values = self._validate_key_type_payload(data)
        if self.db.fetch_one(
            "SELECT 1 FROM nexora_key_types WHERE id = %s", (values["id"],)
        ):
            raise ApiError(409, "KEY_TYPE_EXISTS", "This key type already exists.")
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nexora_key_types
                    (id, name, description, project_id, key_prefix, key_format,
                     permissions, scopes, expiration_policy, device_policy,
                     usage_limits, lifecycle_rules, endpoint_permissions,
                     validation_rules, metadata_schema, is_builtin, is_active,
                     created_by)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb, false, %s, %s)
                """,
                (
                    values["id"],
                    values["name"],
                    values["description"],
                    values["project_id"],
                    values["key_prefix"],
                    json.dumps(values["key_format"]),
                    json.dumps(values["permissions"]),
                    json.dumps(values["scopes"]),
                    json.dumps(values["expiration_policy"]),
                    json.dumps(values["device_policy"]),
                    json.dumps(values["usage_limits"]),
                    json.dumps(values["lifecycle_rules"]),
                    json.dumps(values["endpoint_permissions"]),
                    json.dumps(values["validation_rules"]),
                    json.dumps(values["metadata_schema"]),
                    values["is_active"],
                    actor.get("id"),
                ),
            )
            self._audit(
                cursor,
                actor=actor,
                action="key_type.create",
                target_type="key_type",
                target_id=values["id"],
                project_id=values["project_id"],
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={"name": values["name"]},
                strict=True,
            )
        return self._type_row(values["id"])

    def update_key_type(
        self,
        type_id: str,
        data: dict[str, Any],
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        existing = self._type_row(type_id)
        if existing.get("is_builtin"):
            raise ApiError(
                403,
                "BUILTIN_TYPE_IMMUTABLE",
                "Built-in key types cannot be modified. Create a custom type instead.",
            )
        merged = {
            **{key: existing.get(key) for key in (
                "name", "description", "project_id", "key_prefix", "key_format",
                "permissions", "scopes", "expiration_policy", "device_policy",
                "usage_limits", "lifecycle_rules", "endpoint_permissions",
                "validation_rules", "metadata_schema", "is_active",
            )},
            **data,
            "id": type_id,
        }
        values = self._validate_key_type_payload(merged, existing_id=type_id)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_key_types
                   SET name = %s, description = %s, project_id = %s,
                       key_prefix = %s, key_format = %s::jsonb,
                       permissions = %s::jsonb, scopes = %s::jsonb,
                       expiration_policy = %s::jsonb, device_policy = %s::jsonb,
                       usage_limits = %s::jsonb, lifecycle_rules = %s::jsonb,
                       endpoint_permissions = %s::jsonb,
                       validation_rules = %s::jsonb, metadata_schema = %s::jsonb,
                       is_active = %s, updated_at = now()
                 WHERE id = %s AND is_builtin = false
                """,
                (
                    values["name"],
                    values["description"],
                    values["project_id"],
                    values["key_prefix"],
                    json.dumps(values["key_format"]),
                    json.dumps(values["permissions"]),
                    json.dumps(values["scopes"]),
                    json.dumps(values["expiration_policy"]),
                    json.dumps(values["device_policy"]),
                    json.dumps(values["usage_limits"]),
                    json.dumps(values["lifecycle_rules"]),
                    json.dumps(values["endpoint_permissions"]),
                    json.dumps(values["validation_rules"]),
                    json.dumps(values["metadata_schema"]),
                    values["is_active"],
                    type_id,
                ),
            )
            self._audit(
                cursor,
                actor=actor,
                action="key_type.update",
                target_type="key_type",
                target_id=type_id,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={"name": values["name"], "is_active": values["is_active"]},
                strict=True,
            )
        return self._type_row(type_id)

    # ------------------------------------------------------------------ keys
    def generate_keys(
        self,
        *,
        project_id: str,
        key_type_id: str,
        quantity: int = 1,
        label: str = "",
        expiration_preset: str | None = "never",
        expires_at: datetime | None = None,
        permissions: Any = None,
        scopes: Any = None,
        tags: Any = None,
        notes: str = "",
        metadata: Any = None,
        max_usage: int | None = None,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 0
        limit = int(getattr(self.settings, "max_bulk_keys", 500))
        if not 1 <= quantity <= limit:
            raise ValidationError({"quantity": f"Must be between 1 and {limit}."})
        project = self._project_row(project_id)
        self._assert_project_live(project)
        key_type = self._type_row(key_type_id)
        if not key_type.get("is_active"):
            raise ApiError(403, "KEY_TYPE_INACTIVE", "This key type is inactive.")
        if key_type.get("project_id") and str(key_type["project_id"]) != str(project_id):
            raise ApiError(403, "KEY_TYPE_PROJECT_MISMATCH", "This custom key type belongs to another project.")
        now = utcnow()
        preset, resolved_expiry, permanent = self._resolve_expiration(
            expiration_preset, expires_at, now
        )
        policy = _json_obj(key_type.get("expiration_policy"))
        presets = policy.get("presets")
        if isinstance(presets, list) and preset not in presets and preset != "never":
            raise ValidationError({"expiration_preset": "This preset is not allowed for the key type."})
        effective_permissions = self._validate_permissions(permissions, key_type)
        effective_scopes = self._validate_scopes(scopes, key_type)
        metadata_obj = self._validate_metadata(metadata, key_type)
        tags_list = _string_list(tags or [], "tags", max_items=20)
        for tag in tags_list:
            if len(tag) > 40:
                raise ValidationError({"tags": "Every tag must be 40 characters or fewer."})
        notes_value = str(notes or "")[:2000]
        type_limits = _json_obj(key_type.get("usage_limits"))
        type_max_usage = type_limits.get("max_usage")
        if max_usage is None and type_max_usage:
            max_usage = int(type_max_usage)
        if max_usage is not None:
            try:
                max_usage = int(max_usage)
            except (TypeError, ValueError):
                raise ValidationError({"max_usage": "Must be an integer."}) from None
            if max_usage <= 0:
                raise ValidationError({"max_usage": "Must be greater than zero."})
        spec = self._key_spec(key_type)
        status = "generated" if key_type_id == "reader" else "active"
        batch_id = str(uuid4())
        generated: list[dict[str, Any]] = []
        try:
            with self.db.cursor() as cursor:
                for _ in range(quantity):
                    plaintext = ""
                    inserted = None
                    for _attempt in range(8):
                        plaintext = generate_plaintext_key(spec)
                        normalized = normalize_key(plaintext)
                        if not normalized:
                            continue
                        key_hash = hash_reader_key(self.pepper, normalized)
                        cursor.execute(
                            """
                            INSERT INTO reader_keys
                                (project_id, key_hash, key_hint, label, status,
                                 is_permanent, expires_at, expiration_preset,
                                 batch_id, created_by, key_type_id, fingerprint,
                                 permissions, scopes, tags, notes, metadata,
                                 max_usage, activated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s, %s, %s::jsonb, %s::jsonb, %s, %s,
                                    %s::jsonb, %s, %s)
                            ON CONFLICT (key_hash) DO NOTHING
                            RETURNING id
                            """,
                            (
                                project_id,
                                key_hash,
                                masked_hint(spec, plaintext),
                                (label or "")[:100],
                                status,
                                permanent,
                                resolved_expiry,
                                preset,
                                batch_id,
                                actor.get("id"),
                                key_type_id,
                                self._fingerprint(plaintext, key_type_id),
                                json.dumps(effective_permissions),
                                json.dumps(effective_scopes),
                                tags_list,
                                notes_value,
                                json.dumps(metadata_obj),
                                max_usage,
                                None if status == "generated" else now,
                            ),
                        )
                        inserted = cursor.fetchone()
                        if inserted:
                            break
                    if not inserted:
                        raise ApiError(
                            500,
                            "KEY_GENERATION_COLLISION",
                            "A unique key could not be generated. Retry the request.",
                        )
                    key_id = str(inserted[0])
                    self._event(
                        cursor,
                        key_id=key_id,
                        project_id=project_id,
                        fingerprint=self._fingerprint(plaintext, key_type_id),
                        event="key.generated",
                        actor=actor,
                        metadata={"key_type_id": key_type_id, "batch_id": batch_id},
                    )
                    generated.append(
                        {
                            "id": key_id,
                            "key": plaintext,
                            "key_hint": masked_hint(spec, plaintext),
                            "fingerprint": self._fingerprint(plaintext, key_type_id),
                            "status": status,
                            "expires_at": resolved_expiry.isoformat() if resolved_expiry else None,
                            "permissions": effective_permissions,
                            "scopes": effective_scopes,
                        }
                    )
                self._audit(
                    cursor,
                    actor=actor,
                    action="universal_key.generate",
                    target_type="project",
                    target_id=project_id,
                    project_id=project_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                    details={
                        "key_type_id": key_type_id,
                        "quantity": quantity,
                        "batch_id": batch_id,
                        "key_ids": [item["id"] for item in generated],
                        "fingerprints": [item["fingerprint"] for item in generated],
                    },
                    strict=True,
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409,
                    "KEY_ALREADY_EXISTS",
                    "A duplicate key was generated. Retry the request.",
                ) from None
            raise
        self._emit(
            "key.generated",
            project_id,
            {
                "project_id": project_id,
                "key_type_id": key_type_id,
                "quantity": quantity,
                "batch_id": batch_id,
            },
        )
        return {
            "batch_id": batch_id,
            "keys": generated,
            "project_id": project_id,
            "key_type": key_type,
        }

    def list_keys(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        project_id: str | None = None,
        key_type_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        expiring: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        self._expire_overdue()
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        clauses = ["k.status <> 'deleted'", "p.deleted_at IS NULL"]
        params: list[Any] = []
        if project_id:
            clauses.append("k.project_id = %s")
            params.append(project_id)
        if key_type_id:
            clauses.append("k.key_type_id = %s")
            params.append(key_type_id)
        if status:
            if status not in KEY_STATUSES:
                raise ValidationError({"status": "Unknown key status."})
            clauses.append("k.status = %s")
            params.append(status)
        if search and search.strip():
            term = _like_term(search)
            clauses.append(
                "(LOWER(k.key_hint) LIKE %s OR LOWER(k.label) LIKE %s "
                "OR LOWER(k.fingerprint) LIKE %s OR LOWER(p.project_name) LIKE %s "
                "OR LOWER(array_to_string(k.tags, ' ')) LIKE %s)"
            )
            params.extend([term, term, term, term, term])
        if expiring:
            days = {"7d": 7, "30d": 30, "expired": 0}.get(expiring)
            if days is None:
                raise ValidationError({"expiring": "Use 7d, 30d or expired."})
            if expiring == "expired":
                clauses.append(
                    "(k.status = 'expired' OR (k.expires_at IS NOT NULL AND k.expires_at <= now()))"
                )
            else:
                clauses.append(
                    "k.expires_at IS NOT NULL AND k.expires_at > now() "
                    f"AND k.expires_at <= now() + interval '{days} days'"
                )
        sort_map = {
            "created_at": "k.created_at",
            "expires_at": "k.expires_at",
            "status": "k.status",
            "usage_count": "k.usage_count",
            "label": "k.label",
        }
        direction = "ASC" if order.lower() == "asc" else "DESC"
        where = " AND ".join(clauses)
        total = self.db.fetch_value(
            f"""
            SELECT COUNT(*)::int FROM reader_keys k
            JOIN nexora_projects p ON p.id = k.project_id
            WHERE {where}
            """,
            tuple(params),
        ) or 0
        rows = self.db.fetch_all(
            f"""
            SELECT k.*, p.project_name, p.status AS project_status,
                   p.expires_at AS project_expires_at,
                   p.archived_at AS project_archived_at,
                   t.name AS key_type_name,
                   (SELECT COUNT(*)::int FROM nexora_devices d
                     WHERE d.key_id = k.id AND d.status = 'active') AS device_count
              FROM reader_keys k
              JOIN nexora_projects p ON p.id = k.project_id
              JOIN nexora_key_types t ON t.id = k.key_type_id
             WHERE {where}
             ORDER BY {sort_map.get(sort, 'k.created_at')} {direction}
             LIMIT %s OFFSET %s
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return {
            "items": [self._public_key(row) for row in rows],
            "pagination": pagination(page, page_size, total),
        }

    def get_key(self, key_id: str) -> dict[str, Any]:
        self._expire_overdue()
        row = self._load_key(key_id)
        events = self.db.fetch_all(
            """
            SELECT id::text, event, result, actor_email, metadata, created_at
              FROM nexora_key_events WHERE key_id = %s
             ORDER BY created_at DESC LIMIT 50
            """,
            (key_id,),
        )
        devices = self.db.fetch_all(
            """
            SELECT id, device_id, device_name, device_model, platform, status,
                   installation_id, created_at, last_seen_at
              FROM nexora_devices WHERE key_id = %s ORDER BY created_at DESC
            """,
            (key_id,),
        )
        return {"key": self._public_key(row), "events": events, "devices": devices}

    def _update_key_row(
        self,
        key_id: str,
        *,
        actor: dict[str, Any],
        action: str,
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
        event: str,
        sql: str,
        params: Sequence[Any],
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self._load_key(key_id)
        with self.db.cursor() as cursor:
            cursor.execute(
                f"UPDATE reader_keys SET {sql} WHERE id = %s",
                tuple(params) + (key_id,),
            )
            self._event(
                cursor,
                key_id=key_id,
                project_id=str(row["project_id"]),
                fingerprint=row.get("fingerprint", ""),
                event=event,
                actor=actor,
                metadata=details or {},
            )
            self._audit(
                cursor,
                actor=actor,
                action=action,
                target_type="universal_key",
                target_id=key_id,
                project_id=str(row["project_id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={
                    "key_type_id": row.get("key_type_id"),
                    "key_hint": row.get("key_hint"),
                    "fingerprint": row.get("fingerprint"),
                    **(details or {}),
                },
                strict=True,
            )
        return self.get_key(key_id)

    def activate_key(
        self,
        key_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self._load_key(key_id)
        project = self._project_row(str(row["project_id"]))
        self._assert_project_live(project)
        if row.get("deleted_at"):
            raise ApiError(404, "KEY_NOT_FOUND", "Key not found.")
        expires = _utc(row.get("expires_at"))
        if expires and expires <= utcnow():
            raise ApiError(410, "KEY_EXPIRED", "Key has expired.")
        if row["status"] == "revoked":
            raise ApiError(410, "KEY_REVOKED", "Revoked keys cannot be reactivated.")
        if row["status"] == "active" or row["status"] == "generated":
            return self.get_key(key_id)
        if row["status"] == "used":
            raise ApiError(409, "KEY_ALREADY_USED", "A consumed one-time key cannot be reactivated.")
        if row["status"] not in {"deactivated", "expired"}:
            raise ApiError(400, "KEY_INVALID_STATE", "Key cannot be activated from its current state.")
        target_status = "generated" if row.get("key_type_id") == "reader" else "active"
        return self._update_key_row(
            key_id,
            actor=actor,
            action="universal_key.activate",
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            event="key.activated",
            sql="status = %s, activated_at = now(), deactivated_at = NULL",
            params=(target_status,),
            details={"from_status": row["status"], "to_status": target_status},
        )

    def deactivate_key(
        self,
        key_id: str,
        *,
        reason: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self._load_key(key_id)
        if row["status"] in {"revoked", "deleted"}:
            raise ApiError(410, "KEY_REVOKED", "Key has already been revoked.")
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reader_keys
                   SET status = 'deactivated', deactivated_at = now()
                 WHERE id = %s AND status NOT IN ('revoked','deleted')
                """,
                (key_id,),
            )
            cursor.execute(
                """
                UPDATE nexora_devices
                   SET status = 'deactivated', deactivated_at = now()
                 WHERE key_id = %s AND status = 'active'
                """,
                (key_id,),
            )
            cursor.execute(
                """
                UPDATE reader_installations
                   SET status = 'revoked', revoked_at = now(),
                       revoked_reason = %s, device_token_hash = ''
                 WHERE reader_key_id = %s AND status = 'active'
                """,
                (reason[:500] or "Universal key deactivated", key_id),
            )
            cursor.execute(
                """
                UPDATE device_tokens SET is_active = false
                 WHERE installation_id IN (
                     SELECT id FROM reader_installations WHERE reader_key_id = %s
                 )
                """,
                (key_id,),
            )
            self._event(
                cursor,
                key_id=key_id,
                project_id=str(row["project_id"]),
                fingerprint=row.get("fingerprint", ""),
                event="key.deactivated",
                actor=actor,
                metadata={"reason": reason[:500]},
            )
            self._audit(
                cursor,
                actor=actor,
                action="universal_key.deactivate",
                target_type="universal_key",
                target_id=key_id,
                project_id=str(row["project_id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={
                    "reason": reason[:500],
                    "key_hint": row.get("key_hint"),
                    "fingerprint": row.get("fingerprint"),
                },
                strict=True,
            )
        self._emit(
            "key.deactivated",
            str(row["project_id"]),
            {
                "project_id": str(row["project_id"]),
                "key_id": key_id,
                "key_type_id": row.get("key_type_id"),
            },
        )
        return self.get_key(key_id)

    def revoke_key(
        self,
        key_id: str,
        *,
        reason: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Permanently destroy the key row and all active device bindings."""
        row = self._load_key(key_id, for_update=True)
        project_id = str(row["project_id"])
        now = utcnow()
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reader_installations
                   SET status = 'revoked', revoked_at = %s, revoked_reason = %s,
                       device_token_hash = ''
                 WHERE reader_key_id = %s AND status = 'active'
                """,
                (now, f"Universal key revoked: {reason[:400]}", key_id),
            )
            cursor.execute(
                "DELETE FROM device_tokens WHERE installation_id IN (SELECT id FROM reader_installations WHERE reader_key_id = %s)",
                (key_id,),
            )
            cursor.execute(
                "DELETE FROM reader_installations WHERE reader_key_id = %s", (key_id,)
            )
            cursor.execute("DELETE FROM nexora_devices WHERE key_id = %s", (key_id,))
            self._event(
                cursor,
                key_id=key_id,
                project_id=project_id,
                fingerprint=row.get("fingerprint", ""),
                event="key.revoked",
                actor=actor,
                metadata={
                    "reason": reason[:500],
                    "key_type_id": row.get("key_type_id"),
                    "key_hint": row.get("key_hint"),
                },
            )
            self._audit(
                cursor,
                actor=actor,
                action="universal_key.revoke",
                target_type="universal_key",
                target_id=key_id,
                project_id=project_id,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={
                    "reason": reason[:500],
                    "key_type_id": row.get("key_type_id"),
                    "key_hint": row.get("key_hint"),
                    "fingerprint": row.get("fingerprint"),
                    "key_record_deleted": True,
                },
                strict=True,
            )
            cursor.execute("DELETE FROM reader_keys WHERE id = %s", (key_id,))
        self._emit(
            "key.revoked",
            project_id,
            {
                "project_id": project_id,
                "key_id": key_id,
                "key_type_id": row.get("key_type_id"),
            },
        )
        return {
            "id": key_id,
            "status": "revoked",
            "deleted": True,
            "project_id": project_id,
            "fingerprint": row.get("fingerprint", ""),
        }

    def regenerate_key(
        self,
        key_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self._load_key(key_id)
        if row["status"] in {"revoked", "deleted"}:
            raise ApiError(410, "KEY_REVOKED", "Revoked keys cannot be regenerated.")
        type_row = self._type_row(str(row["key_type_id"]))
        rules = _json_obj(type_row.get("lifecycle_rules"))
        if rules.get("can_regenerate") is False:
            raise ApiError(403, "REGENERATION_DISABLED", "This key type cannot be regenerated.")
        spec = self._key_spec(type_row)
        plaintext = ""
        key_hash = ""
        fingerprint = ""
        for _ in range(8):
            plaintext = generate_plaintext_key(spec)
            normalized = normalize_key(plaintext)
            if not normalized:
                continue
            key_hash = hash_reader_key(self.pepper, normalized)
            fingerprint = self._fingerprint(plaintext, str(row["key_type_id"]))
            if not self.db.fetch_one(
                "SELECT 1 FROM reader_keys WHERE key_hash = %s AND id <> %s",
                (key_hash, key_id),
            ):
                break
        else:
            raise ApiError(500, "KEY_GENERATION_COLLISION", "Could not generate a unique key.")
        target_status = "generated" if row["key_type_id"] == "reader" else "active"
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reader_installations
                   SET status = 'revoked', revoked_at = now(),
                       revoked_reason = 'Universal key regenerated', device_token_hash = ''
                 WHERE reader_key_id = %s AND status = 'active'
                """,
                (key_id,),
            )
            cursor.execute(
                """
                UPDATE device_tokens SET is_active = false
                 WHERE installation_id IN (
                     SELECT id FROM reader_installations WHERE reader_key_id = %s
                 )
                """,
                (key_id,),
            )
            cursor.execute("DELETE FROM nexora_devices WHERE key_id = %s", (key_id,))
            cursor.execute(
                """
                UPDATE reader_keys
                   SET key_hash = %s, key_hint = %s, fingerprint = %s,
                       status = %s, used_at = NULL, usage_count = 0,
                       activated_at = now(), deactivated_at = NULL
                 WHERE id = %s
                """,
                (
                    key_hash,
                    masked_hint(spec, plaintext),
                    fingerprint,
                    target_status,
                    key_id,
                ),
            )
            self._event(
                cursor,
                key_id=key_id,
                project_id=str(row["project_id"]),
                fingerprint=fingerprint,
                event="key.regenerated",
                actor=actor,
                metadata={"key_type_id": row["key_type_id"]},
            )
            self._audit(
                cursor,
                actor=actor,
                action="universal_key.regenerate",
                target_type="universal_key",
                target_id=key_id,
                project_id=str(row["project_id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={
                    "key_type_id": row["key_type_id"],
                    "fingerprint": fingerprint,
                    "devices_revoked": True,
                },
                strict=True,
            )
        return {
            "key": {
                "id": key_id,
                "key": plaintext,
                "key_hint": masked_hint(spec, plaintext),
                "fingerprint": fingerprint,
                "status": target_status,
                "key_type_id": row["key_type_id"],
            }
        }

    def update_key(
        self,
        key_id: str,
        changes: dict[str, Any],
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        allowed = {
            "permissions",
            "scopes",
            "tags",
            "notes",
            "metadata",
            "max_usage",
            "expiration_preset",
            "expires_at",
            "label",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError({"body": f"Unknown fields: {sorted(unknown)}"})
        row = self._load_key(key_id)
        if row["status"] in {"revoked", "deleted"}:
            raise ApiError(410, "KEY_REVOKED", "Revoked keys cannot be edited.")
        type_row = self._type_row(str(row["key_type_id"]))
        updates: dict[str, Any] = {}
        if "permissions" in changes:
            updates["permissions"] = self._validate_permissions(changes["permissions"], type_row)
        if "scopes" in changes:
            updates["scopes"] = self._validate_scopes(changes["scopes"], type_row)
        if "tags" in changes:
            updates["tags"] = _string_list(changes["tags"], "tags", max_items=20)
        if "notes" in changes:
            updates["notes"] = str(changes["notes"] or "")[:2000]
        if "label" in changes:
            updates["label"] = str(changes["label"] or "")[:100]
        if "metadata" in changes:
            updates["metadata"] = self._validate_metadata(changes["metadata"], type_row)
        if "max_usage" in changes:
            value = changes["max_usage"]
            if value is None:
                updates["max_usage"] = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ValidationError({"max_usage": "Must be an integer."}) from None
                if value <= 0:
                    raise ValidationError({"max_usage": "Must be greater than zero."})
                updates["max_usage"] = value
        if "expiration_preset" in changes or "expires_at" in changes:
            preset, expiry, permanent = self._resolve_expiration(
                changes.get("expiration_preset", "custom" if changes.get("expires_at") else "never"),
                changes.get("expires_at"),
                utcnow(),
            )
            updates["expiration_preset"] = preset
            updates["expires_at"] = expiry
            updates["is_permanent"] = permanent
        if not updates:
            raise ValidationError({"body": "Provide at least one editable field."})
        assignments = ", ".join(
            f"{column} = %s::jsonb" if isinstance(value, (dict, list)) else f"{column} = %s"
            for column, value in updates.items()
        )
        params = [
            json.dumps(value) if isinstance(value, (dict, list)) else value
            for value in updates.values()
        ]
        return self._update_key_row(
            key_id,
            actor=actor,
            action="universal_key.update",
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            event="key.updated",
            sql=assignments,
            params=tuple(params),
            details={"fields": sorted(updates)},
        )

    # --------------------------------------------------------------- devices
    def list_devices(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        project_id: str | None = None,
        key_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if project_id:
            clauses.append("d.project_id = %s")
            params.append(project_id)
        if key_id:
            clauses.append("d.key_id = %s")
            params.append(key_id)
        if status:
            clauses.append("d.status = %s")
            params.append(status)
        if search and search.strip():
            term = _like_term(search)
            clauses.append(
                "(LOWER(d.device_id) LIKE %s OR LOWER(d.device_name) LIKE %s OR LOWER(d.device_model) LIKE %s)"
            )
            params.extend([term, term, term])
        where = " AND ".join(clauses)
        total = self.db.fetch_value(
            f"SELECT COUNT(*)::int FROM nexora_devices d WHERE {where}",
            tuple(params),
        ) or 0
        rows = self.db.fetch_all(
            f"""
            SELECT d.*, p.project_name, k.key_hint, k.key_type_id
              FROM nexora_devices d
              JOIN nexora_projects p ON p.id = d.project_id
              LEFT JOIN reader_keys k ON k.id = d.key_id
             WHERE {where}
             ORDER BY d.last_seen_at DESC
             LIMIT %s OFFSET %s
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        return {"items": rows, "pagination": pagination(page, page_size, total)}

    def revoke_device(
        self,
        device_id: str,
        *,
        reason: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT * FROM nexora_devices WHERE id = %s", (device_id,)
        )
        if not row:
            raise ApiError(404, "DEVICE_NOT_FOUND", "Device not found.")
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_devices
                   SET status = 'revoked', revoked_at = now(), credential_hash = ''
                 WHERE id = %s
                """,
                (device_id,),
            )
            self._event(
                cursor,
                key_id=str(row["key_id"]) if row.get("key_id") else None,
                project_id=str(row["project_id"]),
                fingerprint="",
                event="device.revoked",
                actor=actor,
                metadata={"device_id": row["device_id"], "reason": reason[:500]},
            )
            self._audit(
                cursor,
                actor=actor,
                action="device.revoke",
                target_type="device",
                target_id=device_id,
                project_id=str(row["project_id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={"device_id": row["device_id"], "reason": reason[:500]},
                strict=True,
            )
        return {"id": device_id, "status": "revoked"}

    def reset_device(
        self,
        device_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT * FROM nexora_devices WHERE id = %s", (device_id,)
        )
        if not row:
            raise ApiError(404, "DEVICE_NOT_FOUND", "Device not found.")
        with self.db.cursor() as cursor:
            cursor.execute("DELETE FROM nexora_devices WHERE id = %s", (device_id,))
            self._event(
                cursor,
                key_id=str(row["key_id"]) if row.get("key_id") else None,
                project_id=str(row["project_id"]),
                fingerprint="",
                event="device.reset",
                actor=actor,
                metadata={"device_id": row["device_id"]},
            )
            self._audit(
                cursor,
                actor=actor,
                action="device.reset",
                target_type="device",
                target_id=device_id,
                project_id=str(row["project_id"]),
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
                details={"device_id": row["device_id"]},
                strict=True,
            )
        return {"id": device_id, "reset": True}

    # --------------------------------------------------------------- access
    def authenticate_key(
        self,
        *,
        plaintext_key: str,
        permission: str | None = None,
        project_id: str | None = None,
        device_id: str | None = None,
        device_name: str = "",
        device_model: str = "",
        platform: str = "",
        endpoint: str = "/api/v1/access/authenticate",
        ip_address: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        started = __import__("time").perf_counter()
        normalized = normalize_key(plaintext_key)
        if not normalized:
            self._record_usage(
                None, None, "", endpoint, "POST", 401, started, ip_address
            )
            raise ApiError(401, "INVALID_KEY", "The access key is invalid.")
        key_hash = hash_reader_key(self.pepper, normalized)
        row = self.db.fetch_one(
            """
            SELECT k.*, p.project_name, p.status AS project_status,
                   p.expires_at AS project_expires_at,
                   p.archived_at AS project_archived_at,
                   t.name AS key_type_name, t.is_active AS key_type_active,
                   t.permissions AS type_permissions,
                   t.device_policy, t.usage_limits, t.endpoint_permissions
              FROM reader_keys k
              JOIN nexora_projects p ON p.id = k.project_id
              JOIN nexora_key_types t ON t.id = k.key_type_id
             WHERE k.key_hash = %s
            """,
            (key_hash,),
        )
        if not row:
            self._record_usage(None, None, "", endpoint, "POST", 401, started, ip_address)
            raise ApiError(401, "INVALID_KEY", "The access key is invalid.")

        key_id = str(row["id"])
        resolved_project = str(row["project_id"])
        type_id = str(row["key_type_id"])

        def reject(status: int, code: str, message: str) -> None:
            self._record_usage(
                resolved_project, key_id, type_id, endpoint, "POST", status, started, ip_address
            )
            raise ApiError(status, code, message)

        if project_id and str(project_id) != resolved_project:
            reject(403, "PROJECT_MISMATCH", "The key is not valid for this project.")
        if row["status"] in {"revoked", "deleted"}:
            reject(410, "KEY_REVOKED", "The access key has been revoked.")
        if row["status"] == "deactivated":
            reject(403, "KEY_DEACTIVATED", "The access key is deactivated.")
        if not row.get("key_type_active"):
            reject(403, "KEY_TYPE_INACTIVE", "The key type is inactive.")
        expires = _utc(row.get("expires_at"))
        if expires and expires <= utcnow():
            self.db.execute(
                "UPDATE reader_keys SET status = 'expired' WHERE id = %s AND status <> 'revoked'",
                (key_id,),
            )
            reject(410, "KEY_EXPIRED", "The access key has expired.")
        if row.get("status") not in {"active", "generated"}:
            reject(403, "KEY_INACTIVE", "The access key is not active.")
        project_expires = _utc(row.get("project_expires_at"))
        if row.get("project_archived_at"):
            reject(403, "PROJECT_ARCHIVED", "The project is archived.")
        if row.get("project_status") != "active":
            reject(403, "PROJECT_INACTIVE", "The project is not active.")
        if project_expires and project_expires <= utcnow():
            self.db.execute(
                "UPDATE nexora_projects SET status = 'expired' WHERE id = %s AND status = 'active'",
                (resolved_project,),
            )
            reject(403, "PROJECT_EXPIRED", "The project has expired.")
        key_permissions = set(str(item) for item in _json_list(row.get("permissions")))
        type_permissions = set(str(item) for item in _json_list(row.get("type_permissions")))
        if permission:
            if permission not in key_permissions or permission not in type_permissions:
                reject(403, "PERMISSION_DENIED", "The key does not grant this permission.")
            endpoint_permissions = set(
                str(item) for item in _json_list(row.get("endpoint_permissions"))
            )
            if endpoint_permissions and permission not in endpoint_permissions:
                reject(403, "ENDPOINT_PERMISSION_DENIED", "This endpoint is not permitted for the key type.")
        type_limits = _json_obj(row.get("usage_limits"))
        rate_limit = int(type_limits.get("rate_per_minute") or 60)
        recent = self.db.fetch_value(
            "SELECT COUNT(*)::int FROM nexora_api_usage WHERE key_id = %s AND created_at >= now() - interval '1 minute'",
            (key_id,),
        ) or 0
        if int(recent) >= rate_limit:
            reject(429, "KEY_RATE_LIMITED", "The key rate limit has been reached.")
        max_usage = row.get("max_usage")
        if max_usage is not None and int(row.get("usage_count") or 0) >= int(max_usage):
            reject(429, "USAGE_LIMIT_REACHED", "The key usage quota has been reached.")
        device_policy = _json_obj(row.get("device_policy"))
        binds = bool(device_policy.get("binds") or device_policy.get("required"))
        device = None
        if binds:
            if not device_id or not device_id.strip():
                reject(400, "DEVICE_ID_REQUIRED", "A device identifier is required for this key type.")
            device_id = device_id.strip()[:200]
            max_devices = int(device_policy.get("max_devices") or 0)
            existing = self.db.fetch_one(
                "SELECT * FROM nexora_devices WHERE project_id = %s AND device_id = %s",
                (resolved_project, device_id),
            )
            if existing and existing.get("status") == "revoked":
                reject(403, "DEVICE_REVOKED", "This device has been revoked.")
            active_count = self.db.fetch_value(
                "SELECT COUNT(*)::int FROM nexora_devices WHERE project_id = %s AND status = 'active'",
                (resolved_project,),
            ) or 0
            if not existing and max_devices > 0 and int(active_count) >= max_devices:
                reject(403, "DEVICE_LIMIT_REACHED", "The device limit has been reached.")
            now = utcnow()
            if existing:
                self.db.execute(
                    """
                    UPDATE nexora_devices
                       SET key_id = %s, status = 'active', last_seen_at = %s,
                           device_name = COALESCE(NULLIF(%s, ''), device_name),
                           device_model = COALESCE(NULLIF(%s, ''), device_model),
                           platform = COALESCE(NULLIF(%s, ''), platform)
                     WHERE id = %s
                    """,
                    (
                        key_id,
                        now,
                        device_name[:120],
                        device_model[:120],
                        platform[:80],
                        str(existing["id"]),
                    ),
                )
                device = str(existing["id"])
            else:
                device = str(uuid4())
                self.db.execute(
                    """
                    INSERT INTO nexora_devices
                        (id, project_id, key_id, installation_id, device_id,
                         device_name, device_model, platform, status, last_seen_at)
                    VALUES (%s, %s, %s, '', %s, %s, %s, %s, 'active', %s)
                    """,
                    (
                        device,
                        resolved_project,
                        key_id,
                        device_id,
                        device_name[:120],
                        device_model[:120],
                        platform[:80],
                        now,
                    ),
                )
                self.db.execute(
                    """
                    INSERT INTO nexora_key_events
                        (project_id, key_id, fingerprint, event, result, metadata)
                    VALUES (%s, %s, %s, 'device.activated', 'success', %s::jsonb)
                    """,
                    (
                        resolved_project,
                        key_id,
                        row.get("fingerprint", ""),
                        json.dumps({"device_id": device_id}),
                    ),
                )
        self.db.execute(
            "UPDATE reader_keys SET usage_count = usage_count + 1 WHERE id = %s",
            (key_id,),
        )
        self._record_usage(
            resolved_project, key_id, type_id, endpoint, "POST", 200, started, ip_address
        )
        return {
            "authenticated": True,
            "project": {"id": resolved_project, "name": row.get("project_name")},
            "key": {
                "id": key_id,
                "hint": row.get("key_hint"),
                "fingerprint": row.get("fingerprint"),
                "type": type_id,
                "type_name": row.get("key_type_name"),
                "permissions": sorted(key_permissions),
                "expires_at": expires.isoformat() if expires else None,
            },
            "device": {"id": device} if device else None,
            "human_admin_session": False,
        }

    def _record_usage(
        self,
        project_id: str | None,
        key_id: str | None,
        key_type_id: str,
        endpoint: str,
        method: str,
        status: int,
        started: float,
        ip_address: str | None,
    ) -> None:
        latency = max(0, int((__import__("time").perf_counter() - started) * 1000))
        ip_hash = ""
        if ip_address:
            ip_hash = hmac.new(
                self.pepper.encode("utf-8"),
                f"nexora:v1:ip:{ip_address}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:32]
        try:
            self.db.execute(
                """
                INSERT INTO nexora_api_usage
                    (project_id, key_id, key_type_id, endpoint, method,
                     status_code, latency_ms, ip_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
                    key_id,
                    key_type_id,
                    endpoint[:300],
                    method[:10],
                    status,
                    latency,
                    ip_hash,
                ),
            )
        except Exception:
            pass

    # ------------------------------------------------------------ analytics
    def overview(self) -> dict[str, Any]:
        self._expire_overdue()
        projects = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE status = 'active' AND archived_at IS NULL
                                      AND (expires_at IS NULL OR expires_at > now()))::int AS active,
                   COUNT(*) FILTER (WHERE status = 'expired'
                                      OR (expires_at IS NOT NULL AND expires_at <= now()))::int AS expired,
                   COUNT(*) FILTER (WHERE archived_at IS NOT NULL)::int AS archived
              FROM nexora_projects WHERE deleted_at IS NULL
            """
        ) or {}
        keys = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE status IN ('generated','active'))::int AS active,
                   COUNT(*) FILTER (WHERE status = 'deactivated')::int AS deactivated,
                   COUNT(*) FILTER (WHERE status = 'expired')::int AS expired,
                   COUNT(*) FILTER (WHERE status = 'revoked')::int AS revoked,
                   COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at > now()
                                      AND expires_at <= now() + interval '7 days')::int AS expiring_7d,
                   COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at > now()
                                      AND expires_at <= now() + interval '30 days')::int AS expiring_30d
              FROM reader_keys WHERE status <> 'deleted'
            """
        ) or {}
        devices = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(*) FILTER (WHERE status = 'active')::int AS active
              FROM nexora_devices
            """
        ) or {}
        usage = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS requests,
                   COUNT(*) FILTER (WHERE status_code >= 400)::int AS failed,
                   COALESCE(AVG(latency_ms), 0)::int AS avg_latency_ms
              FROM nexora_api_usage WHERE created_at >= now() - interval '24 hours'
            """
        ) or {}
        types = self.db.fetch_all(
            """
            SELECT k.key_type_id, t.name AS key_type_name, COUNT(*)::int AS count
              FROM reader_keys k JOIN nexora_key_types t ON t.id = k.key_type_id
             WHERE k.status <> 'deleted'
             GROUP BY k.key_type_id, t.name ORDER BY count DESC LIMIT 20
            """
        )
        timeline = self.db.fetch_all(
            """
            SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                   COUNT(*)::int AS generated
              FROM reader_keys
             WHERE created_at >= now() - interval '14 days'
             GROUP BY 1 ORDER BY 1
            """
        )
        usage_timeline = self.db.fetch_all(
            """
            SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                   COUNT(*)::int AS requests,
                   COUNT(*) FILTER (WHERE status_code >= 400)::int AS failed
              FROM nexora_api_usage
             WHERE created_at >= now() - interval '7 days'
             GROUP BY 1 ORDER BY 1
            """
        )
        recent = self.db.fetch_all(
            """
            SELECT id::text, event, fingerprint, result, actor_email, created_at
              FROM nexora_key_events ORDER BY created_at DESC LIMIT 12
            """
        )
        unread = self.db.fetch_value(
            """
            SELECT COUNT(*)::int FROM nexora_notifications
             WHERE read_at IS NULL AND (admin_user_id IS NULL)
            """
        ) or 0
        return {
            "projects": projects,
            "keys": keys,
            "devices": devices,
            "api_usage_24h": usage,
            "key_type_distribution": types,
            "generation_timeline": timeline,
            "usage_timeline": usage_timeline,
            "recent_events": recent,
            "unread_notifications": int(unread),
            "health": {
                "database": "connected" if self.db.ping() else "unavailable",
                "api_version": "v1",
                "server_time": utcnow().isoformat(),
            },
        }

    def list_api_usage(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        project_id: str | None = None,
        key_id: str | None = None,
        status: str | None = None,
        endpoint: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        clauses = ["1=1"]
        params: list[Any] = []
        if project_id:
            clauses.append("u.project_id = %s")
            params.append(project_id)
        if key_id:
            clauses.append("u.key_id = %s")
            params.append(key_id)
        if status == "failed":
            clauses.append("u.status_code >= 400")
        elif status == "ok":
            clauses.append("u.status_code BETWEEN 200 AND 299")
        elif status:
            try:
                clauses.append("u.status_code = %s")
                params.append(int(status))
            except ValueError:
                raise ValidationError({"status": "Use ok, failed or an HTTP status code."}) from None
        if endpoint:
            clauses.append("LOWER(u.endpoint) LIKE %s")
            params.append(_like_term(endpoint))
        where = " AND ".join(clauses)
        total = self.db.fetch_value(
            f"SELECT COUNT(*)::int FROM nexora_api_usage u WHERE {where}",
            tuple(params),
        ) or 0
        rows = self.db.fetch_all(
            f"""
            SELECT u.id, u.project_id, u.key_id, u.key_type_id, u.endpoint,
                   u.method, u.status_code, u.latency_ms, u.created_at,
                   p.project_name, k.key_hint
              FROM nexora_api_usage u
              LEFT JOIN nexora_projects p ON p.id = u.project_id
              LEFT JOIN reader_keys k ON k.id = u.key_id
             WHERE {where}
             ORDER BY u.created_at DESC LIMIT %s OFFSET %s
            """,
            tuple(params + [page_size, (page - 1) * page_size]),
        )
        summary = self.db.fetch_one(
            """
            SELECT COUNT(*)::int AS requests,
                   COUNT(*) FILTER (WHERE status_code >= 400)::int AS failed,
                   COALESCE(AVG(latency_ms), 0)::int AS avg_latency_ms
              FROM nexora_api_usage
             WHERE created_at >= now() - interval '24 hours'
            """
        ) or {}
        by_status = self.db.fetch_all(
            """
            SELECT status_code, COUNT(*)::int AS count
              FROM nexora_api_usage
             WHERE created_at >= now() - interval '24 hours'
             GROUP BY status_code ORDER BY status_code
            """
        )
        return {
            "items": rows,
            "pagination": pagination(page, page_size, total),
            "summary_24h": summary,
            "by_status_24h": by_status,
        }

    # ---------------------------------------------------------------- search
    def global_search(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        term_text = (query or "").strip()
        if len(term_text) < 2:
            raise ValidationError({"q": "Enter at least two characters."})
        if len(term_text) > 100:
            raise ValidationError({"q": "Search terms must be 100 characters or fewer."})
        term = _like_term(term_text)
        limit = max(1, min(20, limit))
        projects = self.db.fetch_all(
            """
            SELECT id, project_name, project_type, status, created_at
              FROM nexora_projects
             WHERE deleted_at IS NULL AND (LOWER(project_name) LIKE %s
                OR LOWER(description) LIKE %s OR id::text = %s)
             ORDER BY created_at DESC LIMIT %s
            """,
            (term, term, term_text, limit),
        )
        keys = self.db.fetch_all(
            """
            SELECT k.id, k.key_hint, k.fingerprint, k.status, k.key_type_id,
                   k.label, p.project_name, p.id AS project_id
              FROM reader_keys k JOIN nexora_projects p ON p.id = k.project_id
             WHERE k.status <> 'deleted' AND p.deleted_at IS NULL
               AND (LOWER(k.key_hint) LIKE %s OR LOWER(k.fingerprint) LIKE %s
                    OR LOWER(k.label) LIKE %s
                    OR LOWER(array_to_string(k.tags, ' ')) LIKE %s)
             ORDER BY k.created_at DESC LIMIT %s
            """,
            (term, term, term, term, limit),
        )
        types = self.db.fetch_all(
            """
            SELECT id, name, description, is_builtin, is_active
              FROM nexora_key_types
             WHERE LOWER(name) LIKE %s OR LOWER(description) LIKE %s OR id LIKE %s
             ORDER BY is_builtin DESC, name LIMIT %s
            """,
            (term, term, term, limit),
        )
        devices = self.db.fetch_all(
            """
            SELECT d.id, d.device_id, d.device_name, d.status, d.platform,
                   p.project_name
              FROM nexora_devices d JOIN nexora_projects p ON p.id = d.project_id
             WHERE LOWER(d.device_id) LIKE %s OR LOWER(d.device_name) LIKE %s
                OR LOWER(d.device_model) LIKE %s
             ORDER BY d.last_seen_at DESC LIMIT %s
            """,
            (term, term, term, limit),
        )
        audits = self.db.fetch_all(
            """
            SELECT id::text, action, target_type, target_id, outcome, actor_email,
                   created_at
              FROM admin_audit_logs
             WHERE LOWER(action) LIKE %s OR LOWER(COALESCE(actor_email,'')) LIKE %s
                OR LOWER(COALESCE(target_id,'')) LIKE %s
             ORDER BY created_at DESC LIMIT %s
            """,
            (term, term, term, limit),
        )
        return {
            "query": term_text,
            "groups": {
                "projects": projects,
                "keys": keys,
                "key_types": types,
                "devices": devices,
                "audit": audits,
            },
            "secrets_exposed": False,
        }

    # --------------------------------------------------------- import/export
    def export_keys(
        self,
        *,
        project_id: str | None = None,
        key_type_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        result = self.list_keys(
            page=1,
            page_size=100,
            project_id=project_id,
            key_type_id=key_type_id,
            status=status,
        )
        items = list(result["items"])
        page = 2
        while page <= int(result["pagination"]["total_pages"]) and len(items) < 10000:
            result = self.list_keys(
                page=page,
                page_size=100,
                project_id=project_id,
                key_type_id=key_type_id,
                status=status,
            )
            items.extend(result["items"])
            page += 1
        return items

    def import_preview(
        self, *, project_id: str, key_type_id: str, rows: Any
    ) -> dict[str, Any]:
        self._project_row(project_id)
        type_row = self._type_row(key_type_id)
        spec = self._key_spec(type_row)
        if not isinstance(rows, list):
            raise ValidationError({"keys": "Must be an array."})
        if not 1 <= len(rows) <= int(getattr(self.settings, "max_bulk_keys", 500)):
            raise ValidationError({"keys": "Import between 1 and the configured bulk limit."})
        seen: set[str] = set()
        valid: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        for index, entry in enumerate(rows):
            if not isinstance(entry, dict):
                invalid.append({"index": index, "reason": "Entry must be an object."})
                continue
            plaintext = entry.get("key")
            normalized = normalize_key(plaintext) if isinstance(plaintext, str) else None
            if not normalized:
                invalid.append({"index": index, "reason": "Invalid key format."})
                continue
            if normalized in seen:
                invalid.append({"index": index, "reason": "Duplicate within the import."})
                continue
            seen.add(normalized)
            key_hash = hash_reader_key(self.pepper, normalized)
            if self.db.fetch_one("SELECT 1 FROM reader_keys WHERE key_hash = %s", (key_hash,)):
                invalid.append({"index": index, "reason": "Key already exists."})
                continue
            try:
                permissions = self._validate_permissions(entry.get("permissions"), type_row)
                scopes = self._validate_scopes(entry.get("scopes"), type_row)
                self._validate_metadata(entry.get("metadata"), type_row)
            except ApiError as exc:
                invalid.append({"index": index, "reason": exc.message})
                continue
            valid.append(
                {
                    "index": index,
                    "key_hint": masked_hint(spec, plaintext),
                    "label": str(entry.get("label") or "")[:100],
                    "permissions": permissions,
                    "scopes": scopes,
                }
            )
        return {
            "valid_count": len(valid),
            "invalid_count": len(invalid),
            "valid": valid,
            "invalid": invalid,
            "secrets_echoed": False,
        }

    def import_keys(
        self,
        *,
        project_id: str,
        key_type_id: str,
        rows: Any,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        project = self._project_row(project_id)
        self._assert_project_live(project)
        type_row = self._type_row(key_type_id)
        if not type_row.get("is_active"):
            raise ApiError(403, "KEY_TYPE_INACTIVE", "The key type is inactive.")
        if not isinstance(rows, list) or not rows:
            raise ValidationError({"keys": "Must be a non-empty array."})
        if len(rows) > int(getattr(self.settings, "max_bulk_keys", 500)):
            raise ValidationError({"keys": "Import exceeds the bulk limit."})
        spec = self._key_spec(type_row)
        now = utcnow()
        inserted: list[str] = []
        try:
            with self.db.cursor() as cursor:
                for index, entry in enumerate(rows):
                    if not isinstance(entry, dict):
                        raise ValidationError({"keys": f"Row {index} must be an object."})
                    plaintext = entry.get("key")
                    normalized = normalize_key(plaintext) if isinstance(plaintext, str) else None
                    if not normalized:
                        raise ValidationError({"keys": f"Row {index} has an invalid key."})
                    key_hash = hash_reader_key(self.pepper, normalized)
                    cursor.execute(
                        "SELECT 1 FROM reader_keys WHERE key_hash = %s", (key_hash,)
                    )
                    if cursor.fetchone():
                        raise ApiError(409, "KEY_ALREADY_EXISTS", f"Row {index} already exists.")
                    permissions = self._validate_permissions(entry.get("permissions"), type_row)
                    scopes = self._validate_scopes(entry.get("scopes"), type_row)
                    metadata = self._validate_metadata(entry.get("metadata"), type_row)
                    preset, expires, permanent = self._resolve_expiration(
                        entry.get("expiration_preset", "never"),
                        entry.get("expires_at"),
                        now,
                    )
                    cursor.execute(
                        """
                        INSERT INTO reader_keys
                            (project_id, key_hash, key_hint, label, status,
                             is_permanent, expires_at, expiration_preset, created_by,
                             key_type_id, fingerprint, permissions, scopes, tags,
                             notes, metadata, activated_at)
                        VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s,
                                %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s)
                        RETURNING id
                        """,
                        (
                            project_id,
                            key_hash,
                            masked_hint(spec, plaintext),
                            str(entry.get("label") or "")[:100],
                            permanent,
                            expires,
                            preset,
                            actor.get("id"),
                            key_type_id,
                            self._fingerprint(plaintext, key_type_id),
                            json.dumps(permissions),
                            json.dumps(scopes),
                            _string_list(entry.get("tags") or [], "tags", max_items=20),
                            str(entry.get("notes") or "")[:2000],
                            json.dumps(metadata),
                            now,
                        ),
                    )
                    key_id = str(cursor.fetchone()[0])
                    inserted.append(key_id)
                    self._event(
                        cursor,
                        key_id=key_id,
                        project_id=project_id,
                        fingerprint=self._fingerprint(plaintext, key_type_id),
                        event="key.imported",
                        actor=actor,
                        metadata={"key_type_id": key_type_id},
                    )
                self._audit(
                    cursor,
                    actor=actor,
                    action="universal_key.import",
                    target_type="project",
                    target_id=project_id,
                    project_id=project_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                    details={
                        "key_type_id": key_type_id,
                        "count": len(inserted),
                        "key_ids": inserted,
                    },
                    strict=True,
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(409, "KEY_ALREADY_EXISTS", "An imported key already exists.") from None
            raise
        return {"imported": len(inserted), "key_ids": inserted, "secrets_echoed": False}

    # ----------------------------------------------------------- expiration
    def _expire_overdue(self) -> None:
        try:
            self.db.execute(
                """
                UPDATE nexora_projects
                   SET status = 'expired', updated_at = now()
                 WHERE status = 'active' AND deleted_at IS NULL
                   AND expires_at IS NOT NULL AND expires_at <= now()
                """
            )
            self.db.execute(
                """
                UPDATE reader_keys
                   SET status = 'expired'
                 WHERE status IN ('generated','active','deactivated')
                   AND expires_at IS NOT NULL AND expires_at <= now()
                   AND revoked_at IS NULL
                """
            )
        except Exception:
            # Authentication still performs authoritative date checks; this
            # sweep is an optimization and must not take the API down.
            pass

    # ------------------------------------------------------------- search/emits
    def _emit(self, event_type: str, project_id: str, payload: dict[str, Any]) -> None:
        try:
            from .webhook_service import WebhookService

            WebhookService(self.db, self.settings).enqueue_event(
                event_type, project_id, payload
            )
        except Exception:
            # Webhooks are best-effort and must never roll back a key operation.
            pass
