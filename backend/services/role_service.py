"""Custom platform roles with server-side anti-escalation guarantees.

Built-in roles remain code-defined in :mod:`universal_service`. This service
adds project-independent custom roles stored in ``nexora_roles``. A custom
role is only a named permission set; authorization is still evaluated on every
request from the database, never from frontend state.

Rules enforced here:

* only known ``platform`` permissions can be granted;
* a non-owner may never grant a permission they do not hold themselves;
* a non-owner may never grant a ``critical`` permission;
* the ``owner`` role can only be assigned by an owner;
* the last active owner cannot be demoted;
* a role that is still assigned cannot be deleted;
* every mutation is auditable.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..db import Database, is_unique_violation
from ..errors import ApiError, ValidationError
from .universal_service import ROLE_PERMISSIONS

ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")
BUILTIN_ROLE_NAMES = {
    "owner": "Owner",
    "administrator": "Administrator",
    "project_manager": "Project manager",
    "key_manager": "Key manager",
    "read_only": "Read only",
}
ROLE_TEMPLATES = {
    "audit_reader": {
        "name": "Audit reader",
        "description": "Read-only oversight of audit history, projects and keys.",
        "permissions": [
            "analytics:read",
            "project:read",
            "key:read",
            "audit:read",
            "export:read",
        ],
    },
    "key_operator": {
        "name": "Key operator",
        "description": "Generate and manage keys without destructive project rights.",
        "permissions": [
            "project:read",
            "key:read",
            "key:write",
            "device:read",
            "notification:read",
            "export:read",
        ],
    },
    "webhook_operator": {
        "name": "Webhook operator",
        "description": "Manage webhook destinations, retries and delivery history.",
        "permissions": [
            "project:read",
            "webhook:read",
            "webhook:write",
            "automation:read",
            "automation:run",
        ],
    },
    "project_auditor": {
        "name": "Project auditor",
        "description": "Read projects, analytics, devices and audit events.",
        "permissions": [
            "analytics:read",
            "project:read",
            "key:read",
            "device:read",
            "audit:read",
            "export:read",
        ],
    },
}


def _clean_permissions(value: Any, *, known: set[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError({"permissions": "Must be an array of permission ids."})
    if len(value) > 64:
        raise ValidationError({"permissions": "At most 64 permissions are allowed."})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError({"permissions": "Every permission must be a string."})
        permission = item.strip()
        if permission not in known:
            raise ValidationError({"permissions": f"Unknown permission: {permission}"})
        if permission not in result:
            result.append(permission)
    return result


def _slug(value: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    if not candidate or not candidate[0].isalpha():
        candidate = "role_" + candidate
    return candidate[:50]


class RoleService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------------ audit
    def _audit(
        self,
        cursor: Any,
        *,
        actor: dict[str, Any],
        action: str,
        target_id: str | None,
        details: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
        outcome: str = "success",
    ) -> None:
        cursor.execute(
            """
            INSERT INTO admin_audit_logs
                (admin_user_id, actor_email, action, target_type, target_id,
                 outcome, ip_address, user_agent, request_id, details)
            VALUES (%s, %s, %s, 'platform_role', %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                actor.get("id"),
                actor.get("email"),
                action,
                target_id,
                outcome,
                ip_address,
                user_agent,
                request_id,
                json.dumps(details, default=str),
            ),
        )

    # ------------------------------------------------------------- authority
    def _catalogue(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, name, description, risk, scope
              FROM nexora_permission_definitions
             WHERE scope = 'platform'
             ORDER BY id ASC
            """
        )

    def _known_permissions(self) -> dict[str, str]:
        return {
            str(row["id"]): str(row.get("risk") or "standard")
            for row in self._catalogue()
        }

    def _effective_permissions(self, actor: dict[str, Any]) -> set[str]:
        if actor.get("role") == "owner":
            return {"*"}
        row = self.db.fetch_one(
            """
            SELECT platform_role, platform_permissions, platform_role_id
              FROM admin_users WHERE id = %s AND is_active = true
            """,
            (actor.get("id"),),
        )
        if not row:
            raise ApiError(401, "not_authenticated", "Sign in to continue.")
        if row.get("platform_role_id"):
            role = self.db.fetch_one(
                "SELECT permissions FROM nexora_roles WHERE id = %s",
                (row["platform_role_id"],),
            )
            if role:
                return set(self._json_list(role.get("permissions")))
        platform_role = str(row.get("platform_role") or "read_only")
        if platform_role == "custom":
            return set(self._json_list(row.get("platform_permissions")))
        return set(ROLE_PERMISSIONS.get(platform_role, frozenset()))

    def assert_can_grant(self, actor: dict[str, Any], permissions: list[str]) -> None:
        known = self._known_permissions()
        requested = _clean_permissions(permissions, known=set(known))
        if actor.get("role") == "owner":
            return
        effective = self._effective_permissions(actor)
        beyond = sorted(set(requested) - effective)
        if beyond:
            raise ApiError(
                403,
                "privilege_escalation",
                "You cannot grant permissions beyond your own authority.",
                {"permissions": beyond},
            )
        critical = sorted(
            permission
            for permission in requested
            if known.get(permission) == "critical"
        )
        if critical:
            raise ApiError(
                403,
                "privilege_escalation",
                "Critical permissions can only be granted by an Owner.",
                {"permissions": critical},
            )

    # ------------------------------------------------------------------- read
    def list_roles(self) -> dict[str, Any]:
        custom_rows = self.db.fetch_all(
            """
            SELECT r.id, r.name, r.description, r.permissions, r.is_system,
                   r.created_at, r.updated_at,
                   (SELECT COUNT(*)::int FROM admin_users u
                     WHERE u.platform_role_id = r.id) AS assigned_count
              FROM nexora_roles r
             ORDER BY r.name ASC
            """
        )
        roles: list[dict[str, Any]] = []
        for role_id, permissions in ROLE_PERMISSIONS.items():
            roles.append(
                {
                    "id": role_id,
                    "name": BUILTIN_ROLE_NAMES.get(role_id, role_id.replace("_", " ").title()),
                    "description": "Built-in immutable platform role.",
                    "permissions": sorted(permissions),
                    "custom": False,
                    "built_in": True,
                    "assigned_count": None,
                }
            )
        for row in custom_rows:
            roles.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "description": row.get("description") or "",
                    "permissions": sorted(self._json_list(row.get("permissions"))),
                    "custom": True,
                    "built_in": False,
                    "assigned_count": int(row.get("assigned_count") or 0),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        return {
            "roles": roles,
            "permissions": self._catalogue(),
            "templates": [
                {"id": key, **value} for key, value in ROLE_TEMPLATES.items()
            ],
        }

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _json_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    def _row(self, role_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM nexora_roles WHERE id = %s", (role_id,))
        if not row:
            raise ApiError(404, "ROLE_NOT_FOUND", "Custom role not found.")
        return row

    def _assert_unique_name(self, name: str, *, exclude: str | None = None) -> None:
        row = self.db.fetch_one(
            "SELECT id FROM nexora_roles WHERE lower(name) = lower(%s) AND id <> %s",
            (name, exclude or ""),
        )
        if row:
            raise ApiError(409, "ROLE_NAME_EXISTS", "A custom role already uses that name.")

    def _validate_payload(
        self, body: Any, *, role_id: str | None = None
    ) -> tuple[str, str, str, list[str]]:
        if not isinstance(body, dict):
            raise ValidationError({"body": "Must be an object."})
        name = str(body.get("name") or "").strip()
        if not 1 <= len(name) <= 80:
            raise ValidationError({"name": "Must be 1-80 characters."})
        description = str(body.get("description") or "").strip()
        if len(description) > 500:
            raise ValidationError({"description": "Must be 500 characters or fewer."})
        known = self._known_permissions()
        permissions_value = body.get("permissions")
        template = str(body.get("template") or "").strip()
        if template:
            if template not in ROLE_TEMPLATES:
                raise ValidationError({"template": "Unknown role template."})
            if permissions_value in (None, []):
                permissions_value = list(ROLE_TEMPLATES[template]["permissions"])
        permissions = _clean_permissions(permissions_value, known=set(known))
        if not permissions:
            raise ValidationError({"permissions": "Select at least one permission."})
        if role_id:
            final_id = role_id
        else:
            final_id = str(body.get("id") or "").strip().lower() or _slug(name)
            if not ROLE_ID_RE.match(final_id):
                raise ValidationError(
                    {"id": "Use 3-50 lowercase letters, digits or underscores."}
                )
        return final_id, name, description, permissions

    # ------------------------------------------------------------------- CRUD
    def create_role(
        self,
        body: Any,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        role_id, name, description, permissions = self._validate_payload(body)
        self.assert_can_grant(actor, permissions)
        if role_id in ROLE_PERMISSIONS:
            raise ApiError(409, "ROLE_ID_RESERVED", "That role id is built in.")
        self._assert_unique_name(name)
        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nexora_roles
                        (id, name, description, permissions, is_system, created_by)
                    VALUES (%s, %s, %s, %s::jsonb, false, %s)
                    """,
                    (
                        role_id,
                        name,
                        description,
                        json.dumps(permissions),
                        actor.get("id"),
                    ),
                )
                self._audit(
                    cursor,
                    actor=actor,
                    action="platform_role.create",
                    target_id=role_id,
                    details={"name": name, "permissions": permissions},
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409, "ROLE_ID_EXISTS", "A role with that id already exists."
                ) from None
            raise
        return {"role": next(r for r in self.list_roles()["roles"] if r["id"] == role_id)}

    def update_role(
        self,
        role_id: str,
        body: Any,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        existing = self._row(role_id)
        if existing.get("is_system"):
            raise ApiError(409, "ROLE_IMMUTABLE", "Built-in roles cannot be edited.")
        _, name, description, permissions = self._validate_payload(body, role_id=role_id)
        self.assert_can_grant(actor, permissions)
        self._assert_unique_name(name, exclude=role_id)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_roles
                   SET name = %s, description = %s, permissions = %s::jsonb,
                       updated_at = now()
                 WHERE id = %s
                """,
                (name, description, json.dumps(permissions), role_id),
            )
            cursor.execute(
                """
                UPDATE admin_users
                   SET platform_permissions = %s::jsonb
                 WHERE platform_role_id = %s
                """,
                (json.dumps(permissions), role_id),
            )
            self._audit(
                cursor,
                actor=actor,
                action="platform_role.update",
                target_id=role_id,
                details={"name": name, "permissions": permissions},
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )
        return {"role": next(r for r in self.list_roles()["roles"] if r["id"] == role_id)}

    def clone_role(
        self,
        role_id: str,
        body: Any,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        body = body if isinstance(body, dict) else {}
        source_permissions = (
            sorted(ROLE_PERMISSIONS[role_id])
            if role_id in ROLE_PERMISSIONS
            else sorted(self._json_list(self._row(role_id).get("permissions")))
        )
        if "*" in source_permissions:
            raise ApiError(
                409,
                "ROLE_IMMUTABLE",
                "The Owner role cannot be cloned; assign it explicitly instead.",
            )
        name = str(body.get("name") or "").strip() or f"{role_id} copy"
        payload = {
            "name": name,
            "description": body.get("description")
            or f"Cloned from {role_id}.",
            "permissions": source_permissions,
            "id": body.get("id"),
        }
        return self.create_role(
            payload,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )

    def delete_role(
        self,
        role_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        existing = self._row(role_id)
        if existing.get("is_system"):
            raise ApiError(409, "ROLE_IMMUTABLE", "Built-in roles cannot be deleted.")
        assigned = int(
            self.db.fetch_value(
                "SELECT COUNT(*)::int FROM admin_users WHERE platform_role_id = %s",
                (role_id,),
            )
            or 0
        )
        if assigned:
            raise ApiError(
                409,
                "ROLE_IN_USE",
                "Reassign the administrators using this role before deleting it.",
                {"assigned_count": assigned},
            )
        with self.db.cursor() as cursor:
            cursor.execute("DELETE FROM nexora_roles WHERE id = %s", (role_id,))
            self._audit(
                cursor,
                actor=actor,
                action="platform_role.delete",
                target_id=role_id,
                details={"name": existing.get("name")},
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )
        return {"id": role_id, "deleted": True}

    # -------------------------------------------------------------- assignment
    def assign_role(
        self,
        admin_id: str,
        body: Any,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        body = body if isinstance(body, dict) else {}
        role_id = str(body.get("platform_role_id") or "").strip()
        if not role_id:
            raise ValidationError({"platform_role_id": "Select a role."})
        if role_id in ROLE_PERMISSIONS:
            platform_role = role_id
            permissions = sorted(ROLE_PERMISSIONS[role_id])
            custom = False
        else:
            role = self._row(role_id)
            platform_role = "custom"
            permissions = sorted(self._json_list(role.get("permissions")))
            custom = True
        if permission_set_contains_owner(permissions) or platform_role == "owner":
            if actor.get("role") != "owner":
                raise ApiError(
                    403, "owner_required", "Only an Owner can assign the Owner role."
                )
        self.assert_can_grant(actor, permissions)
        target = self.db.fetch_one(
            "SELECT id, email, role, platform_role, platform_role_id FROM admin_users WHERE id = %s",
            (admin_id,),
        )
        if not target:
            raise ApiError(404, "ADMIN_NOT_FOUND", "Administrator not found.")
        if target.get("role") == "owner" and platform_role != "owner":
            owners = int(
                self.db.fetch_value(
                    "SELECT COUNT(*)::int FROM admin_users WHERE role = 'owner' AND is_active = true"
                )
                or 0
            )
            if owners <= 1:
                raise ApiError(
                    409, "LAST_OWNER", "The last active Owner cannot be demoted."
                )
        base_role = self._base_role(platform_role)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_users
                   SET platform_role = %s, platform_role_id = %s,
                       platform_permissions = %s::jsonb, role = %s,
                       updated_at = now()
                 WHERE id = %s
                """,
                (
                    platform_role,
                    role_id if custom else None,
                    json.dumps(permissions),
                    base_role,
                    admin_id,
                ),
            )
            self._audit(
                cursor,
                actor=actor,
                action="admin.role_change",
                target_id=admin_id,
                details={
                    "target_email": target.get("email"),
                    "old_platform_role": target.get("platform_role"),
                    "old_platform_role_id": target.get("platform_role_id"),
                    "new_platform_role": platform_role,
                    "new_platform_role_id": role_id if custom else None,
                    "permissions": permissions,
                },
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            )
        return {
            "admin_id": admin_id,
            "platform_role": platform_role,
            "platform_role_id": role_id if custom else None,
            "permissions": permissions,
        }

    @staticmethod
    def _base_role(platform_role: str) -> str:
        if platform_role == "owner":
            return "owner"
        if platform_role == "read_only":
            return "viewer"
        return "admin"


def permission_set_contains_owner(permissions: list[str]) -> bool:
    return "*" in permissions
