"""NexOra Project and Reader Key Management Service.

Production-oriented service enforcing:
- Case-insensitive unique project names.
- Soft delete with authorization revocation.
- Cryptographically random reader keys with HMAC-SHA256 hashed storage.
- Strict one-time redemption.
- Exactly one active installation per project.
- High-entropy device credential generation and storage.
- Real dashboard analytics, key management, security center posture, and audit logs.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

from ..config import _PLACEHOLDER_MARKERS
from ..db import Database, is_unique_violation
from ..errors import ApiError, ValidationError
from ..security.keys import (
    KeySpec,
    generate_device_credential,
    generate_plaintext_key,
    hash_device_credential,
    hash_reader_key,
    masked_hint,
    normalize_key,
    normalize_project_name,
)
from ..serialization import utcnow


EXPIRATION_PRESETS: dict[str, tuple[str, timedelta]] = {
    "1_day": ("1 Day", timedelta(days=1)),
    "7_days": ("7 Days", timedelta(days=7)),
    "30_days": ("30 Days", timedelta(days=30)),
    "90_days": ("90 Days", timedelta(days=90)),
    "1_year": ("1 Year", timedelta(days=365)),
}
EXPIRATION_CUSTOM = "custom"
EXPIRATION_NEVER = "never"
EXPIRATION_CHOICES = tuple(EXPIRATION_PRESETS) + (EXPIRATION_CUSTOM, EXPIRATION_NEVER)


def expiration_label(preset: str | None) -> str:
    if not preset or preset == EXPIRATION_NEVER:
        return "Never Expires"
    if preset == EXPIRATION_CUSTOM:
        return "Custom Expiration"
    return EXPIRATION_PRESETS.get(preset, ("Custom Expiration", timedelta()))[0]


class ProjectService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self.pepper: str = getattr(settings, "key_pepper", "")

    # ------------------------------------------------------------------------
    # Internal Audit Helper
    # ------------------------------------------------------------------------

    def _write_audit(
        self,
        cursor_or_db: Any,
        *,
        actor_id: UUID | str | None = None,
        actor: dict[str, Any] | None = None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        outcome: str = "success",
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict[str, Any] | None = None,
        project_id: UUID | str | None = None,
        strict: bool = False,
    ) -> None:
        """Write an audit entry safely inside an existing cursor or via db connection.

        ``strict`` is used for destructive operations: a deletion must never
        commit without its audit record, so an audit failure aborts the whole
        transaction instead of being swallowed.
        """
        resolved_actor_id = None
        actor_email = None
        if actor and isinstance(actor, dict):
            resolved_actor_id = actor.get("id")
            actor_email = actor.get("email")
        if not resolved_actor_id and actor_id:
            resolved_actor_id = str(actor_id)

        sql = """
            INSERT INTO admin_audit_logs
                (admin_user_id, actor_email, action, target_type, target_id,
                 outcome, ip_address, user_agent, details, project_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        """
        params = (
            str(resolved_actor_id) if resolved_actor_id else None,
            actor_email,
            action,
            target_type,
            str(target_id) if target_id else None,
            outcome,
            ip_address,
            user_agent,
            json.dumps(details or {}, default=str),
            str(project_id) if project_id else None,
        )

        if strict:
            if hasattr(cursor_or_db, "execute"):
                cursor_or_db.execute(sql, params)
            else:
                with cursor_or_db.cursor() as cur:
                    cur.execute(sql, params)
            return

        try:
            if hasattr(cursor_or_db, "execute"):
                cursor_or_db.execute(sql, params)
            elif hasattr(cursor_or_db, "cursor"):
                with cursor_or_db.cursor() as cur:
                    cur.execute(sql, params)
        except Exception:
            # Non-destructive audit failures must not block business operations
            pass

    # ------------------------------------------------------------------------
    # Project Management
    # ------------------------------------------------------------------------

    def get_auto_delete_days(self) -> int:
        """Return the configured automatic-deletion retention period in days.

        ``0`` means automatic deletion is disabled. The setting is optional and
        has no seeded default, so a fresh installation never deletes projects
        on its own.
        """
        try:
            row = self.db.fetch_one(
                "SELECT value FROM system_settings WHERE key = 'nexora.auto_delete_days'"
            )
            value = int((row or {}).get("value") or 0)
        except Exception:
            return 0
        return value if 0 <= value <= 3650 else 0

    def _resolve_expiration(
        self,
        expiration: str | None,
        expires_at: datetime | None,
        now: datetime,
    ) -> tuple[str, datetime | None, bool]:
        """Resolve the administrator's expiration choice into stored fields.

        Fixed presets are computed server-side against the database clock so a
        client cannot backdate a key. ``never`` is explicit and always stores
        ``expires_at = NULL`` with ``is_permanent = true``.
        """
        preset = (expiration or EXPIRATION_NEVER).strip().lower()
        if preset in {"never", "never_expire", "permanent"}:
            return EXPIRATION_NEVER, None, True

        if preset == EXPIRATION_CUSTOM:
            if expires_at is None:
                raise ValidationError(
                    {"expires_at": "Choose a future date and time for a custom expiration."}
                )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            expires_at = expires_at.astimezone(timezone.utc)
            if expires_at <= now:
                raise ValidationError(
                    {"expires_at": "The custom expiration must be in the future."}
                )
            return EXPIRATION_CUSTOM, expires_at, False

        if preset not in EXPIRATION_PRESETS:
            raise ValidationError(
                {
                    "expiration": "Choose 1 Day, 7 Days, 30 Days, 90 Days, "
                    "1 Year, Custom Expiration or Never Expire."
                }
            )
        return preset, now + EXPIRATION_PRESETS[preset][1], False

    def _insert_reader_key(
        self,
        cursor: Any,
        *,
        project_id: str,
        project_name: str,
        label: str,
        preset: str,
        expires_at: datetime | None,
        is_permanent: bool,
        created_by: UUID | str | None,
        ip_address: str | None,
        user_agent: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        """Insert one key and its audit event inside the caller's transaction."""
        spec = KeySpec()
        plaintext = generate_plaintext_key(spec)
        normalized_key = normalize_key(plaintext)
        if not normalized_key:
            raise RuntimeError("Generated key failed normalization.")

        key_hash = hash_reader_key(self.pepper, normalized_key)
        hint = masked_hint(spec, plaintext)
        key_id = uuid4()

        cursor.execute(
            """
            INSERT INTO reader_keys
                (id, project_id, key_hash, key_hint, label, status,
                 is_permanent, expiration_preset, expires_at, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, 'generated', %s, %s, %s, %s, %s)
            """,
            (
                str(key_id),
                project_id,
                key_hash,
                hint,
                label.strip(),
                is_permanent,
                preset,
                expires_at,
                str(created_by) if created_by else None,
                now,
            ),
        )

        self._write_audit(
            cursor,
            actor_id=created_by,
            action="reader_key.generate",
            target_type="reader_key",
            target_id=str(key_id),
            project_id=project_id,
            outcome="success",
            ip_address=ip_address,
            user_agent=user_agent,
            details={
                "project_name": project_name,
                "key_hint": hint,
                "is_permanent": is_permanent,
                "expiration_preset": preset,
                "expires_at": expires_at,
            },
        )

        return {
            "id": str(key_id),
            "project_id": project_id,
            "project_name": project_name,
            "plaintext_key": plaintext,
            "key_hint": hint,
            "label": label.strip(),
            "status": "generated",
            "is_permanent": is_permanent,
            "expiration_preset": preset,
            "expiration_label": expiration_label(preset),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "created_at": now.isoformat(),
            "first_used_at": None,
            "used_at": None,
            "revoked_at": None,
            "revoked_reason": None,
        }

    def create_project(
        self,
        *,
        project_name: str,
        created_by: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Create a project without issuing a Reader Key."""
        try:
            normalized = normalize_project_name(project_name)
        except ValueError as exc:
            raise ValidationError({"project_name": str(exc)}) from None

        display_name = (project_name or "").strip()
        if not display_name or len(display_name) > 200:
            raise ValidationError(
                {"project_name": "Project name must be between 1 and 200 characters."}
            )

        now = utcnow()
        project_id = uuid4()
        auto_delete_days = self.get_auto_delete_days()
        auto_delete_at = (
            now + timedelta(days=auto_delete_days) if auto_delete_days > 0 else None
        )

        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (normalized,),
                )
                cursor.execute(
                    """
                    SELECT id, project_name, status, deleted_at
                    FROM nexora_projects
                    WHERE project_name_normalized = %s AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    (normalized,),
                )
                existing = cursor.fetchone()
                if existing:
                    self._raise_existing_project(cursor, existing)

                cursor.execute(
                    """
                    INSERT INTO nexora_projects
                        (id, project_name, project_name_normalized, database_url_ref,
                         status, auto_delete_at, created_at, updated_at, created_by)
                    VALUES (%s, %s, %s, '', 'active', %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        str(project_id),
                        display_name,
                        normalized,
                        auto_delete_at,
                        now,
                        now,
                        str(created_by) if created_by else None,
                    ),
                )

                self._write_audit(
                    cursor,
                    actor_id=created_by,
                    action="project.create",
                    target_type="project",
                    target_id=str(project_id),
                    project_id=str(project_id),
                    outcome="success",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={
                        "project_name": display_name,
                        "normalized": normalized,
                        "source": "create_project",
                        "auto_delete_at": auto_delete_at,
                    },
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409,
                    "PROJECT_ALREADY_EXISTS",
                    "This project already exists.",
                ) from None
            raise

        return {
            "id": str(project_id),
            "project_name": display_name,
            "project_name_normalized": normalized,
            "status": "active",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "deleted_at": None,
            "auto_delete_at": auto_delete_at.isoformat() if auto_delete_at else None,
            "days_remaining": auto_delete_days if auto_delete_at else None,
            "auto_delete_due": False,
            "total_keys": 0,
            "available_keys": 0,
            "used_keys": 0,
            "revoked_keys": 0,
            "expired_keys": 0,
        }

    def generate_project_key(
        self,
        *,
        project_name: str,
        label: str = "",
        expiration: str | None = None,
        expires_at: datetime | None = None,
        created_by: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Create a new project and issue its first Reader Key atomically.

        This path is for the "new project" workflow. Use
        ``generate_key_for_project`` to add another key to an existing project.
        A duplicate live name returns a safe 409 and never a plaintext key.
        """
        try:
            normalized = normalize_project_name(project_name)
        except ValueError as exc:
            raise ValidationError({"project_name": str(exc)}) from None

        display_name = (project_name or "").strip()
        if not display_name or len(display_name) > 200:
            raise ValidationError(
                {"project_name": "Project name must be between 1 and 200 characters."}
            )

        now = utcnow()
        preset, resolved_expires_at, is_permanent = self._resolve_expiration(
            expiration, expires_at, now
        )
        auto_delete_days = self.get_auto_delete_days()
        auto_delete_at = (
            now + timedelta(days=auto_delete_days) if auto_delete_days > 0 else None
        )
        project_id = uuid4()

        try:
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (normalized,),
                )
                cursor.execute(
                    """
                    SELECT id, project_name, status, deleted_at
                    FROM nexora_projects
                    WHERE project_name_normalized = %s AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    (normalized,),
                )
                existing = cursor.fetchone()
                if existing:
                    self._raise_existing_project(cursor, existing)

                cursor.execute(
                    """
                    INSERT INTO nexora_projects
                        (id, project_name, project_name_normalized, database_url_ref,
                         status, auto_delete_at, created_at, updated_at, created_by)
                    VALUES (%s, %s, %s, '', 'active', %s, %s, %s, %s)
                    ON CONFLICT (project_name_normalized) WHERE deleted_at IS NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    (
                        str(project_id),
                        display_name,
                        normalized,
                        auto_delete_at,
                        now,
                        now,
                        str(created_by) if created_by else None,
                    ),
                )
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        SELECT id, project_name, status, deleted_at
                        FROM nexora_projects
                        WHERE project_name_normalized = %s AND deleted_at IS NULL
                        LIMIT 1
                        """,
                        (normalized,),
                    )
                    raced = cursor.fetchone()
                    if raced:
                        self._raise_existing_project(cursor, raced)
                    raise ApiError(
                        409,
                        "PROJECT_ALREADY_EXISTS",
                        "This project already exists.",
                    )

                self._write_audit(
                    cursor,
                    actor_id=created_by,
                    action="project.create",
                    target_type="project",
                    target_id=str(project_id),
                    project_id=str(project_id),
                    outcome="success",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={
                        "project_name": display_name,
                        "normalized": normalized,
                        "source": "generate_key",
                        "auto_delete_at": auto_delete_at,
                    },
                )

                key = self._insert_reader_key(
                    cursor,
                    project_id=str(project_id),
                    project_name=display_name,
                    label=label,
                    preset=preset,
                    expires_at=resolved_expires_at,
                    is_permanent=is_permanent,
                    created_by=created_by,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    now=now,
                )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409,
                    "PROJECT_ALREADY_EXISTS",
                    "This project already exists.",
                ) from None
            raise

        return {
            "created": True,
            "project_created": True,
            "project": {
                "id": str(project_id),
                "project_name": display_name,
                "project_name_normalized": normalized,
                "status": "active",
                "created_at": now.isoformat(),
                "auto_delete_at": auto_delete_at.isoformat() if auto_delete_at else None,
                "total_keys": 1,
                "available_keys": 1,
                "used_keys": 0,
                "revoked_keys": 0,
                "expired_keys": 0,
            },
            "reader_key": key,
        }

    def generate_key_for_project(
        self,
        project_id: str,
        *,
        label: str = "",
        expiration: str | None = None,
        expires_at: datetime | None = None,
        created_by: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Issue another Reader Key for an existing live project."""
        now = utcnow()
        preset, resolved_expires_at, is_permanent = self._resolve_expiration(
            expiration, expires_at, now
        )

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, project_name, project_name_normalized, status, deleted_at
                FROM nexora_projects
                WHERE id = %s
                FOR UPDATE
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            if not row or row[4] is not None:
                raise ApiError(404, "NOT_FOUND", "Project not found.")

            key = self._insert_reader_key(
                cursor,
                project_id=str(row[0]),
                project_name=row[1],
                label=label,
                preset=preset,
                expires_at=resolved_expires_at,
                is_permanent=is_permanent,
                created_by=created_by,
                ip_address=ip_address,
                user_agent=user_agent,
                now=now,
            )

        return {
            "created": True,
            "project_created": False,
            "project": {
                "id": str(row[0]),
                "project_name": row[1],
                "status": row[3],
            },
            "reader_key": key,
        }

    def _raise_existing_project(self, cursor: Any, project_row: Sequence[Any]) -> None:
        """Raise a safe 409 conflict for an existing project name.

        Only status/hint metadata is returned. A plaintext key is never
        recoverable. The project may already own multiple keys; only the latest
        is included in the conflict details.
        """
        project_id = str(project_row[0])
        cursor.execute(
            """
            SELECT id, key_hint, status, created_at, used_at, revoked_at,
                   expiration_preset, expires_at
            FROM reader_keys
            WHERE project_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id,),
        )
        key = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*) FROM reader_keys WHERE project_id = %s",
            (project_id,),
        )
        count_row = cursor.fetchone()
        key_count = int(count_row[0]) if count_row else 0
        details: dict[str, Any] = {
            "project": {
                "id": project_id,
                "project_name": project_row[1],
                "status": "deleted" if project_row[3] else project_row[2],
                "deleted": bool(project_row[3]),
            },
            "reader_key": None,
            "has_reader_key": bool(key),
            "key_count": key_count,
        }
        if key:
            details["reader_key"] = {
                "id": str(key[0]),
                "key_hint": key[1],
                "status": key[2],
                "created_at": key[3],
                "used_at": key[4],
                "revoked_at": key[5],
                "expiration_preset": key[6],
                "expires_at": key[7],
            }
        if project_row[3]:
            raise ApiError(
                409,
                "PROJECT_NAME_RETIRED",
                "This project name belongs to a deleted project and cannot be reused.",
                details,
            )
        raise ApiError(
            409,
            "PROJECT_ALREADY_EXISTS",
            "This project already exists. Choose it in the generator to issue another key.",
            details,
        )

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Fetch project details with aggregate key counts and latest key."""
        row = self.db.fetch_one(
            """
            SELECT p.id, p.project_name, p.project_name_normalized,
                   p.database_url_ref, p.status, p.created_at, p.updated_at,
                   p.deleted_at, p.created_by, p.auto_delete_at,
                   CASE WHEN p.auto_delete_at IS NULL THEN NULL
                        ELSE GREATEST(
                            0,
                            CEIL(EXTRACT(EPOCH FROM (p.auto_delete_at - now())) / 86400)
                        )::int
                   END AS days_remaining,
                   CASE WHEN p.auto_delete_at IS NULL THEN false
                        WHEN p.auto_delete_at <= now() THEN true
                        ELSE false
                   END AS auto_delete_due,
                   stats.total_keys, stats.available_keys, stats.used_keys,
                   stats.revoked_keys, stats.expired_keys,
                   i.id AS active_installation_id,
                   i.device_id AS active_device_id,
                   i.activated_at AS active_installation_activated_at,
                   i.last_seen_at AS active_installation_last_seen,
                   k.id AS reader_key_id,
                   k.key_hint AS reader_key_hint,
                   k.status AS reader_key_status,
                   k.created_at AS reader_key_created_at,
                   k.used_at AS reader_key_used_at,
                   k.revoked_at AS reader_key_revoked_at,
                   k.expiration_preset AS reader_key_expiration_preset,
                   k.expires_at AS reader_key_expires_at
            FROM nexora_projects p
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::int AS total_keys,
                       COUNT(*) FILTER (WHERE k.status = 'generated')::int AS available_keys,
                       COUNT(*) FILTER (WHERE k.status = 'used')::int AS used_keys,
                       COUNT(*) FILTER (WHERE k.status = 'revoked')::int AS revoked_keys,
                       COUNT(*) FILTER (WHERE k.status = 'expired')::int AS expired_keys
                FROM reader_keys k
                WHERE k.project_id = p.id
            ) stats ON true
            LEFT JOIN LATERAL (
                SELECT k.id, k.key_hint, k.status, k.created_at, k.used_at,
                       k.revoked_at, k.expiration_preset, k.expires_at
                FROM reader_keys k
                WHERE k.project_id = p.id
                ORDER BY k.created_at DESC
                LIMIT 1
            ) k ON true
            LEFT JOIN LATERAL (
                SELECT i.id, i.device_id, i.activated_at, i.last_seen_at
                FROM reader_installations i
                WHERE i.project_id = p.id AND i.status = 'active'
                LIMIT 1
            ) i ON true
            WHERE p.id = %s
            """,
            (project_id,),
        )
        if not row:
            raise ApiError(404, "NOT_FOUND", "Project not found.")
        return row

    def list_projects(
        self,
        *,
        include_deleted: bool = False,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List managed projects with counts of reader keys and installations."""
        clauses: list[str] = []
        params: list[Any] = []

        if not include_deleted:
            clauses.append("p.deleted_at IS NULL")

        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(p.project_name) LIKE %s OR LOWER(p.database_url_ref) LIKE %s)")
            params.extend([term, term])

        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        sql = f"""
            SELECT p.id, p.project_name, p.status, p.database_url_ref,
                   p.created_at, p.updated_at, p.deleted_at, p.auto_delete_at,
                   CASE WHEN p.auto_delete_at IS NULL THEN NULL
                        ELSE GREATEST(
                            0,
                            CEIL(EXTRACT(EPOCH FROM (p.auto_delete_at - now())) / 86400)
                        )::int
                   END AS days_remaining,
                   CASE WHEN p.auto_delete_at IS NULL THEN false
                        WHEN p.auto_delete_at <= now() THEN true
                        ELSE false
                   END AS auto_delete_due,
                   stats.total_keys, stats.available_keys, stats.used_keys,
                   stats.revoked_keys, stats.expired_keys,
                   k.id AS reader_key_id,
                   k.key_hint AS reader_key_hint,
                   k.status AS reader_key_status,
                   k.created_at AS reader_key_created_at,
                   k.used_at AS reader_key_used_at,
                   k.expiration_preset AS reader_key_expiration_preset,
                   k.expires_at AS reader_key_expires_at,
                   i.id AS active_installation_id,
                   i.device_id AS active_device_id,
                   i.last_seen_at AS installation_last_seen_at
            FROM nexora_projects p
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::int AS total_keys,
                       COUNT(*) FILTER (WHERE k.status = 'generated')::int AS available_keys,
                       COUNT(*) FILTER (WHERE k.status = 'used')::int AS used_keys,
                       COUNT(*) FILTER (WHERE k.status = 'revoked')::int AS revoked_keys,
                       COUNT(*) FILTER (WHERE k.status = 'expired')::int AS expired_keys
                FROM reader_keys k
                WHERE k.project_id = p.id
            ) stats ON true
            LEFT JOIN LATERAL (
                SELECT k.id, k.key_hint, k.status, k.created_at, k.used_at,
                       k.expiration_preset, k.expires_at
                FROM reader_keys k
                WHERE k.project_id = p.id
                ORDER BY k.created_at DESC
                LIMIT 1
            ) k ON true
            LEFT JOIN LATERAL (
                SELECT i.id, i.device_id, i.last_seen_at
                FROM reader_installations i
                WHERE i.project_id = p.id AND i.status = 'active'
                LIMIT 1
            ) i ON true
            {where_clause}
            ORDER BY p.created_at DESC
        """
        return self.db.fetch_all(sql, tuple(params) if params else None)

    def set_project_status(
        self,
        project_id: str,
        *,
        status: str,
        actor_id: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Activate or deactivate a project."""
        status = (status or "").strip().lower()
        if status not in ("active", "inactive"):
            raise ValidationError({"status": "Status must be either 'active' or 'inactive'."})

        project = self.get_project(project_id)
        if project.get("deleted_at") or project["status"] == "deleted":
            raise ApiError(404, "NOT_FOUND", "Cannot change status of a deleted project.")

        now = utcnow()
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_projects
                SET status = %s, updated_at = %s
                WHERE id = %s AND deleted_at IS NULL
                """,
                (status, now, project_id),
            )
            self._write_audit(
                cursor,
                actor_id=actor_id,
                action="project.status_change",
                target_type="project",
                target_id=project_id,
                project_id=project_id,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "project_name": project["project_name"],
                    "old_status": project["status"],
                    "new_status": status,
                },
            )

        return self.get_project(project_id)

    def set_project_auto_delete(
        self,
        project_id: str,
        *,
        auto_delete_at: datetime | None,
        actor_id: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Configure or clear a project's scheduled hard-delete date."""
        project = self.get_project(project_id)
        if project.get("deleted_at"):
            raise ApiError(404, "NOT_FOUND", "Project not found.")

        if auto_delete_at is not None:
            if auto_delete_at.tzinfo is None:
                auto_delete_at = auto_delete_at.replace(tzinfo=timezone.utc)
            auto_delete_at = auto_delete_at.astimezone(timezone.utc)
            if auto_delete_at <= utcnow():
                raise ValidationError(
                    {"auto_delete_at": "The scheduled deletion date must be in the future."}
                )

        now = utcnow()
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_projects
                SET auto_delete_at = %s, updated_at = %s
                WHERE id = %s AND deleted_at IS NULL
                """,
                (auto_delete_at, now, project_id),
            )
            self._write_audit(
                cursor,
                actor_id=actor_id,
                action="project.auto_delete_change",
                target_type="project",
                target_id=project_id,
                project_id=project_id,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "project_name": project["project_name"],
                    "old_auto_delete_at": project.get("auto_delete_at"),
                    "new_auto_delete_at": auto_delete_at,
                },
            )

        return self.get_project(project_id)

    def hard_delete_project(
        self,
        project_id: str,
        *,
        actor_id: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Permanently delete one project and every project-owned record.

        The deletion event is written before the rows are removed and with a
        NULL ``project_id``, so ``ON DELETE SET NULL`` cannot erase it. The
        snapshot lives in the audit trail; the project name is deliberately
        reusable after the transaction commits.
        """
        project = self.db.fetch_one(
            """
            SELECT id, project_name, project_name_normalized, status,
                   auto_delete_at, created_at
            FROM nexora_projects
            WHERE id = %s AND deleted_at IS NULL
            """,
            (project_id,),
        )
        if not project:
            raise ApiError(404, "NOT_FOUND", "Project not found.")

        with self.db.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (project["project_name_normalized"],),
            )
            cursor.execute(
                """
                SELECT id, project_name, project_name_normalized, status,
                       auto_delete_at, created_at
                FROM nexora_projects
                WHERE id = %s AND deleted_at IS NULL
                FOR UPDATE
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise ApiError(404, "NOT_FOUND", "Project not found.")

            cursor.execute(
                """
                SELECT id, key_hint, label, status, expiration_preset,
                       is_permanent, expires_at, created_at, used_at,
                       revoked_at, revoked_reason
                FROM reader_keys
                WHERE project_id = %s
                ORDER BY created_at ASC
                """,
                (str(row[0]),),
            )
            key_rows = cursor.fetchall()
            key_count = len(key_rows)
            key_snapshots = [
                {
                    "id": str(key[0]),
                    "key_hint": key[1],
                    "label": key[2],
                    "status": key[3],
                    "expiration_preset": key[4],
                    "is_permanent": bool(key[5]),
                    "expires_at": key[6],
                    "created_at": key[7],
                    "used_at": key[8],
                    "revoked_at": key[9],
                    "revoked_reason": key[10],
                }
                for key in key_rows[:100]
            ]

            cursor.execute(
                """
                SELECT
                  (SELECT count(*) FROM reader_installations WHERE project_id = %s) AS installations,
                  (SELECT count(*) FROM device_tokens WHERE project_id = %s) AS device_tokens
                """,
                (str(row[0]), str(row[0])),
            )
            related = cursor.fetchone()
            installation_count = int(related[0]) if related else 0
            device_token_count = int(related[1]) if related else 0

            snapshot = {
                "project_id": str(row[0]),
                "project_name": row[1],
                "normalized": row[2],
                "status": row[3],
                "auto_delete_at": row[4],
                "created_at": row[5],
                "key_count": key_count,
                "installation_count": installation_count,
                "device_token_count": device_token_count,
                "keys": key_snapshots,
                "keys_truncated": key_count > len(key_snapshots),
            }

            self._write_audit(
                cursor,
                actor_id=actor_id,
                action="project.delete",
                target_type="project",
                target_id=str(row[0]),
                project_id=None,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "mode": "permanent",
                    "snapshot": snapshot,
                    "key_count": key_count,
                    "installation_count": installation_count,
                    "device_token_count": device_token_count,
                },
                strict=True,
            )

            cursor.execute(
                "DELETE FROM device_tokens WHERE project_id = %s", (str(row[0]),)
            )
            cursor.execute(
                "DELETE FROM reader_installations WHERE project_id = %s",
                (str(row[0]),),
            )
            cursor.execute(
                "DELETE FROM reader_keys WHERE project_id = %s", (str(row[0]),)
            )
            cursor.execute(
                "DELETE FROM nexora_projects WHERE id = %s", (str(row[0]),)
            )

            for table, column in (
                ("nexora_projects", "id"),
                ("reader_keys", "project_id"),
                ("reader_installations", "project_id"),
                ("device_tokens", "project_id"),
            ):
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} = %s",
                    (str(row[0]),),
                )
                remaining = cursor.fetchone()
                if remaining and int(remaining[0]) > 0:
                    raise RuntimeError(
                        "Permanent project deletion could not be verified; transaction rolled back."
                    )

        return {
            "id": str(project["id"]),
            "project_name": project["project_name"],
            "deleted": True,
            "mode": "permanent",
        }

    def hard_delete_all_projects(
        self,
        *,
        actor_id: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Permanently delete every NexOra project in one transaction.

        Only NexOra-owned tables are touched. Administrators, sessions, system
        settings, security audit history, license keys, and legacy SMS Reader
        tables are preserved.
        """
        with self.db.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("nexora:delete-all-projects",),
            )
            cursor.execute(
                """
                SELECT id, project_name, project_name_normalized
                FROM nexora_projects
                ORDER BY created_at ASC
                """
            )
            rows = cursor.fetchall()
            if not rows:
                return {"deleted_count": 0, "mode": "permanent"}

            self._write_audit(
                cursor,
                actor_id=actor_id,
                action="project.delete_all",
                target_type="project",
                target_id=None,
                project_id=None,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "mode": "permanent",
                    "deleted_count": len(rows),
                    "project_names": [row[1] for row in rows[:100]],
                    "project_names_truncated": len(rows) > 100,
                },
                strict=True,
            )

            cursor.execute("DELETE FROM device_tokens")
            cursor.execute("DELETE FROM reader_installations")
            cursor.execute("DELETE FROM reader_keys")
            cursor.execute("DELETE FROM nexora_projects")

            for table in (
                "nexora_projects",
                "reader_keys",
                "reader_installations",
                "device_tokens",
            ):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                remaining = cursor.fetchone()
                if remaining and int(remaining[0]) > 0:
                    raise RuntimeError(
                        "Delete-all verification failed; transaction rolled back."
                    )

        return {"deleted_count": len(rows), "mode": "permanent"}

    def purge_due_projects(self, *, limit: int = 100) -> dict[str, Any]:
        """Hard-delete projects whose configured auto-delete date has passed.

        Intended for a server-side scheduler (``python -m backend.cli
        purge-expired-projects``). Each project is deleted in its own
        transaction with its own audit event.
        """
        limit = max(1, min(1000, int(limit)))
        rows = self.db.fetch_all(
            """
            SELECT id, project_name
            FROM nexora_projects
            WHERE deleted_at IS NULL
              AND auto_delete_at IS NOT NULL
              AND auto_delete_at <= now()
            ORDER BY auto_delete_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        deleted: list[str] = []
        for row in rows:
            try:
                result = self.hard_delete_project(str(row["id"]))
                deleted.append(result["project_name"])
            except ApiError as exc:
                if exc.status == 404:
                    continue
                raise
        return {"deleted_count": len(deleted), "project_names": deleted}

    # ------------------------------------------------------------------------
    # Reader Key Management
    # ------------------------------------------------------------------------

    def _expire_overdue_reader_keys(self) -> None:
        """Persist expiry for generated keys whose time has passed.

        Activation performs the same transition under a row lock. This lazy
        sweep keeps the admin list and dashboard counts truthful without a
        background worker.
        """
        try:
            self.db.execute(
                """
                UPDATE reader_keys
                   SET status = 'expired'
                 WHERE status = 'generated'
                   AND is_permanent = false
                   AND expires_at IS NOT NULL
                   AND expires_at <= now()
                """
            )
        except Exception:
            # A read-only account or a transient failure must not hide the
            # existing records; the stored status remains the last known value.
            pass

    def revoke_key(
        self,
        key_id: str,
        *,
        reason: str,
        revoked_by: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Revoke any non-revoked key permanently.

        Revoking a used (redeemed) key also revokes the installation bound to
        it and deactivates every device token, so an already-connected Reader
        cannot bypass the revocation.
        """
        if not reason.strip():
            raise ValidationError({"reason": "A revocation reason is required."})

        key = self.get_key(key_id)
        if key["status"] == "revoked":
            raise ApiError(400, "ALREADY_REVOKED", "Key is already revoked.")

        now = utcnow()
        installation_revoked = False
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reader_keys
                SET status = 'revoked',
                    revoked_at = %s,
                    revoked_by = %s,
                    revoked_reason = %s
                WHERE id = %s AND status <> 'revoked'
                RETURNING id, project_id, status, used_at
                """,
                (now, str(revoked_by) if revoked_by else None, reason.strip(), key_id),
            )
            row = cursor.fetchone()
            if not row:
                raise ApiError(
                    404,
                    "KEY_NOT_FOUND",
                    "Key not found or not in a revocable state.",
                )

            if row[3] is not None:
                cursor.execute(
                    """
                    UPDATE reader_installations
                       SET status = 'revoked',
                           revoked_at = %s,
                           revoked_by = %s,
                           revoked_reason = %s,
                           device_token_hash = ''
                     WHERE reader_key_id = %s AND status = 'active'
                     RETURNING id
                    """,
                    (
                        now,
                        str(revoked_by) if revoked_by else None,
                        f"Reader key revoked: {reason.strip()}",
                        key_id,
                    ),
                )
                installation = cursor.fetchone()
                if installation:
                    installation_revoked = True
                    cursor.execute(
                        """
                        UPDATE device_tokens
                           SET is_active = false
                         WHERE installation_id = %s
                        """,
                        (str(installation[0]),),
                    )
                    self._write_audit(
                        cursor,
                        actor_id=revoked_by,
                        action="reader_installation.revoke",
                        target_type="reader_installation",
                        target_id=str(installation[0]),
                        project_id=str(row[1]),
                        outcome="success",
                        ip_address=ip_address,
                        user_agent=user_agent,
                        details={
                            "reason": reason.strip(),
                            "source": "reader_key.revoke",
                            "reader_key_id": key_id,
                        },
                    )

            self._write_audit(
                cursor,
                actor_id=revoked_by,
                action="reader_key.revoke",
                target_type="reader_key",
                target_id=key_id,
                project_id=str(row[1]) if row else None,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "reason": reason.strip(),
                    "key_hint": key["key_hint"],
                    "installation_revoked": installation_revoked,
                },
            )

        return {
            "id": key_id,
            "status": "revoked",
            "installation_revoked": installation_revoked,
        }

    def list_keys(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        expiry_filter: str | None = None,
        page: int = 1,
        page_size: int = 25,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        """List reader keys with pagination and filtering (NEVER returns plaintext)."""
        self._expire_overdue_reader_keys()
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        allowed_sorts = {
            "created_at": "k.created_at",
            "expires_at": "k.expires_at",
            "used_at": "k.used_at",
            "status": "k.status",
            "label": "k.label",
        }
        sort_col = allowed_sorts.get(sort, "k.created_at")
        sort_dir = "ASC" if order.lower() == "asc" else "DESC"

        clauses: list[str] = []
        params: list[Any] = []

        if project_id and project_id.strip():
            clauses.append("k.project_id = %s")
            params.append(project_id.strip())

        if status and status.strip():
            clauses.append("k.status = %s")
            params.append(status.strip().lower())

        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            clauses.append(
                "(LOWER(k.key_hint) LIKE %s OR LOWER(k.label) LIKE %s OR LOWER(p.project_name) LIKE %s)"
            )
            params.extend([term, term, term])

        if expiry_filter:
            if expiry_filter in ("permanent", "never"):
                clauses.append("k.expiration_preset = 'never'")
            elif expiry_filter == "expired":
                clauses.append(
                    "(k.status = 'expired' OR (k.is_permanent = false AND k.expires_at < now()))"
                )
            elif expiry_filter == "active":
                clauses.append(
                    "(k.status = 'generated' AND (k.is_permanent = true OR k.expires_at >= now()))"
                )

        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        # Count total
        count_sql = f"""
            SELECT COUNT(*) AS total
            FROM reader_keys k
            JOIN nexora_projects p ON p.id = k.project_id
            {where_clause}
        """
        count_row = self.db.fetch_one(count_sql, tuple(params) if params else None)
        total = count_row["total"] if count_row else 0

        # Query page
        data_sql = f"""
            SELECT k.id, k.project_id, p.project_name, k.key_hint, k.label,
                   k.status, k.is_permanent, k.expiration_preset,
                   k.expires_at, k.created_at,
                   k.used_at, k.used_at AS first_used_at,
                   k.revoked_at, k.revoked_reason, k.revoked_by,
                   revoked_by_user.email AS revoked_by_email,
                   i.id AS installation_id, i.device_id, i.status AS installation_status
            FROM reader_keys k
            JOIN nexora_projects p ON p.id = k.project_id
            LEFT JOIN admin_users revoked_by_user ON revoked_by_user.id = k.revoked_by
            LEFT JOIN reader_installations i ON i.reader_key_id = k.id
            {where_clause}
            ORDER BY {sort_col} {sort_dir}
            LIMIT %s OFFSET %s
        """
        query_params = list(params) + [page_size, offset]
        rows = self.db.fetch_all(data_sql, tuple(query_params))

        return {
            "items": rows,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if total > 0 else 1,
            },
        }

    def get_key(self, key_id: str) -> dict[str, Any]:
        """Fetch reader key details (NEVER returns plaintext)."""
        self._expire_overdue_reader_keys()
        row = self.db.fetch_one(
            """
            SELECT k.id, k.project_id, p.project_name, k.key_hint, k.label,
                   k.status, k.is_permanent, k.expiration_preset,
                   k.expires_at, k.created_at,
                   k.used_at, k.used_at AS first_used_at,
                   k.revoked_at, k.revoked_reason, k.revoked_by,
                   revoked_by_user.email AS revoked_by_email,
                   i.id AS installation_id, i.device_id,
                   i.activated_at AS installation_activated_at,
                   i.status AS installation_status,
                   i.last_seen_at AS installation_last_seen
            FROM reader_keys k
            JOIN nexora_projects p ON p.id = k.project_id
            LEFT JOIN admin_users revoked_by_user ON revoked_by_user.id = k.revoked_by
            LEFT JOIN reader_installations i ON i.reader_key_id = k.id
            WHERE k.id = %s
            """,
            (key_id,),
        )
        if not row:
            raise ApiError(404, "NOT_FOUND", "Reader key not found.")
        return row

    # ------------------------------------------------------------------------
    # Installation Management
    # ------------------------------------------------------------------------

    def list_installations(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List reader installations with pagination and filtering."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        clauses: list[str] = []
        params: list[Any] = []

        if project_id and project_id.strip():
            clauses.append("i.project_id = %s")
            params.append(project_id.strip())

        if status and status.strip():
            clauses.append("i.status = %s")
            params.append(status.strip().lower())

        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            clauses.append(
                "(LOWER(p.project_name) LIKE %s OR LOWER(i.device_id) LIKE %s OR LOWER(k.key_hint) LIKE %s)"
            )
            params.extend([term, term, term])

        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        count_sql = f"""
            SELECT COUNT(*) AS total
            FROM reader_installations i
            JOIN nexora_projects p ON p.id = i.project_id
            JOIN reader_keys k ON k.id = i.reader_key_id
            {where_clause}
        """
        count_row = self.db.fetch_one(count_sql, tuple(params) if params else None)
        total = count_row["total"] if count_row else 0

        data_sql = f"""
            SELECT i.id, i.project_id, p.project_name, i.reader_key_id, k.key_hint,
                   i.device_id, i.status, i.activated_at, i.last_seen_at,
                   i.revoked_at, i.revoked_reason, i.ip_address
            FROM reader_installations i
            JOIN nexora_projects p ON p.id = i.project_id
            JOIN reader_keys k ON k.id = i.reader_key_id
            {where_clause}
            ORDER BY i.activated_at DESC
            LIMIT %s OFFSET %s
        """
        query_params = list(params) + [page_size, offset]
        rows = self.db.fetch_all(data_sql, tuple(query_params))

        return {
            "items": rows,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if total > 0 else 1,
            },
        }

    def get_installation(self, installation_id: str) -> dict[str, Any]:
        """Fetch details of a single reader installation."""
        row = self.db.fetch_one(
            """
            SELECT i.id, i.project_id, p.project_name, i.reader_key_id, k.key_hint,
                   i.device_id, i.status, i.activated_at, i.last_seen_at,
                   i.revoked_at, i.revoked_reason, i.ip_address, i.user_agent
            FROM reader_installations i
            JOIN nexora_projects p ON p.id = i.project_id
            JOIN reader_keys k ON k.id = i.reader_key_id
            WHERE i.id = %s
            """,
            (installation_id,),
        )
        if not row:
            raise ApiError(404, "NOT_FOUND", "Installation not found.")

        # Also get recent audit events for this installation
        recent_events = self.db.fetch_all(
            """
            SELECT id, action, outcome, created_at, ip_address, details
            FROM admin_audit_logs
            WHERE target_id = %s OR (project_id = %s AND action LIKE 'reader%%')
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (installation_id, str(row["project_id"])),
        )
        row_dict = dict(row)
        row_dict["recent_events"] = recent_events
        return row_dict

    def revoke_installation(
        self,
        installation_id: str,
        *,
        reason: str,
        revoked_by: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Revoke a Reader installation (Admin-only)."""
        if not reason.strip():
            raise ValidationError({"reason": "A revocation reason is required."})

        now = utcnow()
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE reader_installations
                SET status = 'revoked',
                    revoked_at = %s,
                    revoked_by = %s,
                    revoked_reason = %s,
                    device_token_hash = ''
                WHERE id = %s AND status = 'active'
                RETURNING project_id
                """,
                (now, str(revoked_by) if revoked_by else None, reason.strip(), installation_id),
            )
            row = cursor.fetchone()
            if not row:
                raise ApiError(404, "NOT_FOUND", "Active installation not found.")

            cursor.execute(
                """
                UPDATE device_tokens
                SET is_active = false
                WHERE installation_id = %s
                """,
                (installation_id,),
            )

            self._write_audit(
                cursor,
                actor_id=revoked_by,
                action="reader_installation.revoke",
                target_type="reader_installation",
                target_id=installation_id,
                project_id=str(row[0]) if row else None,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={"reason": reason.strip()},
            )

        return {"id": installation_id, "status": "revoked"}

    # ------------------------------------------------------------------------
    # Reader App Connection & Activation Architecture
    # ------------------------------------------------------------------------

    def activate_reader(
        self,
        *,
        project_name: str,
        reader_key_plaintext: str,
        supabase_url: str | None = None,
        database_url: str | None = None,
        device_id: str = "",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Validate and redeem a Reader Key atomically.

        Contract:
        The Reader App submits the public Supabase Project URL (e.g.
        https://<project-ref>.supabase.co), NEVER a direct PostgreSQL
        connection string, database password, or service role key. The field is
        named ``supabase_url``. ``database_url`` remains accepted as a
        deprecated alias so already-deployed Reader builds keep working; the
        server validates whichever value is supplied against its configured
        Supabase Project URL.
        """
        url_field = "supabase_url" if supabase_url is not None else "database_url"
        submitted_url = (supabase_url or database_url or "").strip().rstrip("/")

        # 1. Verify Supabase URL contract
        configured_url = (getattr(self.settings, "supabase_url", "") or "").strip().rstrip("/")
        if not configured_url:
            # Fail closed if the expected Supabase Project URL is not configured on the server
            raise ApiError(
                503,
                "SERVER_CONFIGURATION_ERROR",
                "Supabase Project URL is not configured on the server. Reader activation is unavailable.",
            )

        if not submitted_url:
            raise ValidationError(
                {url_field: f"{url_field} is required (the Supabase Project URL)."}
            )

        # Reject attempts to submit a direct PostgreSQL connection string
        if submitted_url.lower().startswith(("postgres://", "postgresql://")):
            raise ApiError(
                400,
                "INVALID_DATABASE_URL",
                "Reader App must submit the Supabase Project URL (https://<project-ref>.supabase.co), not a PostgreSQL connection string.",
            )

        # Reject placeholder values
        lowered_submitted = submitted_url.lower()
        for marker in _PLACEHOLDER_MARKERS:
            if marker in lowered_submitted:
                raise ApiError(
                    400,
                    "DATABASE_URL_MISMATCH",
                    f"Provided {url_field} contains invalid placeholder content.",
                )

        # Validate against the exact server-configured Supabase Project URL (case-insensitive and trailing slash normalized)
        if submitted_url.lower() != configured_url.lower():
            raise ApiError(
                400,
                "DATABASE_URL_MISMATCH",
                f"Provided {url_field} does not match the configured server-side Supabase URL.",
            )

        # 2 & 3. Verify Project Name
        try:
            normalized_name = normalize_project_name(project_name)
        except ValueError as exc:
            raise ValidationError({"project_name": str(exc)}) from None

        # Pre-check project existence outside the write transaction to fail fast on 404
        project = self.db.fetch_one(
            """
            SELECT id, project_name, status, deleted_at, expires_at, archived_at
            FROM nexora_projects
            WHERE project_name_normalized = %s AND deleted_at IS NULL
            """,
            (normalized_name,),
        )
        if not project or project["status"] != "active":
            raise ApiError(
                404,
                "PROJECT_NOT_FOUND",
                "Project does not exist or is inactive.",
            )
        if project.get("archived_at"):
            raise ApiError(403, "PROJECT_ARCHIVED", "Project is archived.")
        if project.get("expires_at") and project["expires_at"] <= utcnow():
            raise ApiError(403, "PROJECT_EXPIRED", "Project has expired.")
        project_id = str(project["id"])

        # 4, 5, 6. Validate Reader Key inside atomic transaction
        normalized_key = normalize_key(reader_key_plaintext)
        if not normalized_key:
            raise ValidationError({"reader_key": "Invalid reader key format."})

        key_hash = hash_reader_key(self.pepper, normalized_key)
        now = utcnow()
        device_credential = generate_device_credential()
        cred_hash = hash_device_credential(self.pepper, device_credential)
        installation_id = uuid4()
        token_id = uuid4()

        key_is_expired = False

        try:
            with self.db.cursor() as cursor:
                # Lock the project row to serialize concurrent activations for this project
                cursor.execute(
                    """
                    SELECT id, status, expires_at, archived_at
                    FROM nexora_projects
                    WHERE id = %s AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (project_id,),
                )
                proj_lock = cursor.fetchone()
                if not proj_lock or proj_lock[1] != "active":
                    raise ApiError(
                        404,
                        "PROJECT_NOT_FOUND",
                        "Project does not exist or is inactive.",
                    )
                if proj_lock[3] is not None:
                    raise ApiError(403, "PROJECT_ARCHIVED", "Project is archived.")
                if proj_lock[2] is not None and proj_lock[2] <= utcnow():
                    raise ApiError(403, "PROJECT_EXPIRED", "Project has expired.")

                # Row lock key to prevent concurrent redemption
                cursor.execute(
                    """
                    SELECT id, project_id, status, is_permanent, expires_at
                    FROM reader_keys
                    WHERE key_hash = %s
                    FOR UPDATE
                    """,
                    (key_hash,),
                )
                key_row = cursor.fetchone()
                if not key_row:
                    raise ApiError(
                        404,
                        "KEY_NOT_FOUND",
                        "Reader key not found.",
                    )

                key_id = str(key_row[0])
                key_project_id = str(key_row[1])
                key_status = key_row[2]
                is_permanent = key_row[3]
                expires_at = key_row[4]

                if key_project_id != project_id:
                    raise ApiError(
                        403,
                        "KEY_PROJECT_MISMATCH",
                        "Reader key does not belong to this project.",
                    )

                if key_status == "used":
                    raise ApiError(
                        410,
                        "KEY_ALREADY_USED",
                        "Reader key has already been redeemed and cannot be reused.",
                    )

                if key_status == "revoked":
                    raise ApiError(
                        410,
                        "KEY_REVOKED",
                        "Reader key has been revoked.",
                    )

                if key_status == "expired":
                    raise ApiError(
                        410,
                        "KEY_EXPIRED",
                        "Reader key has expired.",
                    )

                if not is_permanent and expires_at and expires_at < now:
                    cursor.execute(
                        "UPDATE reader_keys SET status = 'expired' WHERE id = %s",
                        (key_id,),
                    )
                    key_is_expired = True
                    # Key expiry update commits when exiting the cursor context
                elif key_status != "generated":
                    raise ApiError(
                        400,
                        "KEY_INVALID_STATE",
                        f"Reader key is not in a redeemable state ({key_status}).",
                    )
                else:
                    # A project may own many keys, but it still supports one
                    # active Reader installation at a time. Check this only
                    # after validating the key, so a revoked key always fails
                    # with KEY_REVOKED even while another device is connected.
                    cursor.execute(
                        """
                        SELECT id
                        FROM reader_installations
                        WHERE project_id = %s AND status = 'active'
                        """,
                        (project_id,),
                    )
                    if cursor.fetchone():
                        raise ApiError(
                            409,
                            "ACTIVE_INSTALLATION_EXISTS",
                            "Project already has an active installation. Only one active Reader installation is permitted per project.",
                        )

                    # Mark key permanently USED
                    cursor.execute(
                        """
                        UPDATE reader_keys
                        SET status = 'used', used_at = %s
                        WHERE id = %s
                        """,
                        (now, key_id),
                    )

                    # Register installation
                    cursor.execute(
                        """
                        INSERT INTO reader_installations
                            (id, project_id, reader_key_id, device_token_hash,
                             device_id, status, activated_at, last_seen_at, ip_address, user_agent)
                        VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s, %s)
                        """,
                        (
                            str(installation_id),
                            project_id,
                            key_id,
                            cred_hash,
                            device_id.strip() if device_id else None,
                            now,
                            now,
                            ip_address,
                            user_agent,
                        ),
                    )

                    # Issue device token
                    cursor.execute(
                        """
                        INSERT INTO device_tokens
                            (id, installation_id, project_id, token_hash, issued_at, last_used_at, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, true)
                        """,
                        (str(token_id), str(installation_id), project_id, cred_hash, now, now),
                    )

                    self._write_audit(
                        cursor,
                        action="reader.activate",
                        target_type="project",
                        target_id=project_id,
                        project_id=project_id,
                        outcome="success",
                        ip_address=ip_address,
                        user_agent=user_agent,
                        details={
                            "project_name": project["project_name"],
                            "installation_id": str(installation_id),
                            "device_id": device_id,
                        },
                    )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409,
                    "ACTIVE_INSTALLATION_EXISTS",
                    "Project already has an active installation. Only one active Reader installation is permitted per project.",
                ) from None
            raise

        if key_is_expired:
            raise ApiError(
                410,
                "KEY_EXPIRED",
                "Reader key has expired.",
            )

        return {
            "status": "activated",
            "project_id": project_id,
            "project_name": project["project_name"],
            "installation_id": str(installation_id),
            "device_credential": device_credential,  # Sent ONCE to the Reader App
            "activated_at": now.isoformat(),
        }

    def verify_reader_credential(self, device_credential: str) -> dict[str, Any]:
        """Authenticate an incoming Reader App request using its device credential."""
        if not device_credential or not device_credential.strip():
            raise ApiError(401, "UNAUTHORIZED", "Device credential is required.")

        cred_hash = hash_device_credential(self.pepper, device_credential.strip())
        row = self.db.fetch_one(
            """
            SELECT t.id AS token_id, t.installation_id, t.project_id,
                   i.status AS installation_status,
                   p.project_name, p.status AS project_status,
                   p.expires_at AS project_expires_at,
                   p.archived_at AS project_archived_at
            FROM device_tokens t
            JOIN reader_installations i ON i.id = t.installation_id
            JOIN nexora_projects p ON p.id = t.project_id
            WHERE t.token_hash = %s AND t.is_active = true
            """,
            (cred_hash,),
        )
        if not row:
            raise ApiError(401, "UNAUTHORIZED", "Invalid or revoked device credential.")

        if row["installation_status"] != "active":
            raise ApiError(403, "INSTALLATION_REVOKED", "Reader installation has been revoked.")

        if row["project_status"] != "active":
            raise ApiError(403, "PROJECT_INACTIVE", "Associated project is inactive or deleted.")
        if row.get("project_archived_at"):
            raise ApiError(403, "PROJECT_ARCHIVED", "Associated project is archived.")
        if row.get("project_expires_at") and row["project_expires_at"] <= utcnow():
            raise ApiError(403, "PROJECT_EXPIRED", "Associated project has expired.")

        return row

    def record_heartbeat(self, device_credential: str) -> dict[str, Any]:
        """Record heartbeat from an active Reader installation."""
        token_info = self.verify_reader_credential(device_credential)
        now = utcnow()

        with self.db.cursor() as cursor:
            cursor.execute(
                "UPDATE device_tokens SET last_used_at = %s WHERE id = %s",
                (now, str(token_info["token_id"])),
            )
            cursor.execute(
                "UPDATE reader_installations SET last_seen_at = %s WHERE id = %s",
                (now, str(token_info["installation_id"])),
            )

        return {
            "status": "ok",
            "project_id": str(token_info["project_id"]),
            "project_name": token_info["project_name"],
            "timestamp": now.isoformat(),
        }

    # ------------------------------------------------------------------------
    # Dashboard Analytics & Security Center
    # ------------------------------------------------------------------------

    def dashboard_stats(self) -> dict[str, Any]:
        """Fetch live system pulse and dashboard counts."""
        self._expire_overdue_reader_keys()
        proj_row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS total_projects,
                   COUNT(CASE WHEN status = 'active' THEN 1 END) AS active_projects,
                   COUNT(CASE WHEN auto_delete_at IS NOT NULL THEN 1 END) AS scheduled_projects,
                   COUNT(CASE WHEN auto_delete_at IS NOT NULL
                               AND auto_delete_at <= now() THEN 1 END) AS due_projects
            FROM nexora_projects
            WHERE deleted_at IS NULL
            """
        ) or {
            "total_projects": 0,
            "active_projects": 0,
            "scheduled_projects": 0,
            "due_projects": 0,
        }

        key_row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS total_keys,
                   COUNT(CASE WHEN status = 'generated' THEN 1 END) AS unused_keys,
                   COUNT(CASE WHEN status = 'used' THEN 1 END) AS used_keys,
                   COUNT(CASE WHEN status = 'expired' THEN 1 END) AS expired_keys,
                   COUNT(CASE WHEN status = 'revoked' THEN 1 END) AS revoked_keys
            FROM reader_keys
            """
        ) or {"total_keys": 0, "unused_keys": 0, "used_keys": 0, "expired_keys": 0, "revoked_keys": 0}

        inst_row = self.db.fetch_one(
            """
            SELECT COUNT(CASE WHEN status = 'active' THEN 1 END) AS active_installations,
                   COUNT(*) AS total_installations
            FROM reader_installations
            """
        ) or {"active_installations": 0, "total_installations": 0}

        recent_audit = self.db.fetch_all(
            """
            SELECT a.id, a.action, a.target_type, a.target_id, a.outcome,
                   a.created_at, a.ip_address, a.details, a.project_id,
                   p.project_name
            FROM admin_audit_logs a
            LEFT JOIN nexora_projects p ON p.id = a.project_id
            ORDER BY a.created_at DESC
            LIMIT 10
            """
        )

        db_healthy = bool(self.db.ping())

        return {
            "total_projects": proj_row["total_projects"],
            "active_projects": proj_row["active_projects"],
            "scheduled_projects": proj_row["scheduled_projects"],
            "due_projects": proj_row["due_projects"],
            "total_keys": key_row["total_keys"],
            "unused_keys": key_row["unused_keys"],
            "used_keys": key_row["used_keys"],
            "expired_keys": key_row["expired_keys"],
            "revoked_keys": key_row["revoked_keys"],
            "active_installations": inst_row["active_installations"],
            "total_installations": inst_row["total_installations"],
            "recent_audit": recent_audit,
            "database_status": "connected" if db_healthy else "unavailable",
            "backend_health": "ok" if db_healthy else "degraded",
            "system_version": getattr(self.settings, "version", "1.0.0"),
        }

    def get_security_status(self) -> dict[str, Any]:
        """Fetch Security Center posture (Zero secrets exposed)."""
        admin_row = self.db.fetch_one(
            """
            SELECT email, role, is_active, failed_login_attempts, locked_until
            FROM admin_users
            WHERE role IN ('owner', 'admin')
            ORDER BY created_at ASC
            LIMIT 1
            """
        ) or {}

        recent_security_events = self.db.fetch_all(
            """
            SELECT id, action, target_type, target_id, outcome,
                   created_at, ip_address, details
            FROM admin_audit_logs
            WHERE action IN ('auth.login', 'auth.logout', 'project.delete', 'reader_key.revoke', 'reader_installation.revoke')
            ORDER BY created_at DESC
            LIMIT 8
            """
        )

        db_healthy = bool(self.db.ping())
        db_settings = getattr(self.settings, "database", None)

        return {
            "auth": {
                "super_admin_configured": bool(admin_row),
                "admin_email": admin_row.get("email", ""),
                "admin_role": admin_row.get("role", "owner"),
                "account_active": admin_row.get("is_active", True),
                "failed_login_attempts": admin_row.get("failed_login_attempts", 0),
                "is_locked": bool(admin_row.get("locked_until") and admin_row["locked_until"] > utcnow()),
                "password_algorithm": "Argon2id (m=65536, t=3, p=4)",
                "public_registration": False,
            },
            "session": {
                "session_storage": "Server-side hashed opaque tokens (SHA-256)",
                "session_ttl_seconds": getattr(self.settings, "session_ttl_seconds", 28800),
                "cookie_name": getattr(self.settings, "session_cookie_name", "nexora_session"),
                "cookie_httponly": True,
                "cookie_samesite": "Strict",
                "cookie_secure": getattr(self.settings, "cookie_secure", True),
                "csrf_protection": "Double-submit cookie + X-CSRF-Token header validation",
            },
            "api_security": {
                "rate_limiting": "Sliding window rate limiter (per-IP)",
                "rate_limit_enabled": getattr(self.settings, "rate_limit_enabled", True),
                "trust_proxy": getattr(self.settings, "trust_proxy", False),
                "csp_policy": "Strict CSP (no unsafe-inline, no external CDNs)",
                "hsts_enabled": getattr(self.settings, "cookie_secure", True),
                "x_frame_options": "DENY",
                "x_content_type_options": "nosniff",
            },
            "database": {
                "status": "connected" if db_healthy else "unavailable",
                "ssl_mode": getattr(db_settings, "sslmode", "require") if db_settings else "require",
                "uses_ssl": getattr(db_settings, "uses_ssl", True) if db_settings else True,
                "rls_enforced": True,
            },
            "key_security": {
                "storage_method": "HMAC-SHA256 keyed digest with server-side pepper",
                "plaintext_storage": False,
                "domain_separation": "nexora:v1:reader-key:*",
                "max_bulk_limit": getattr(self.settings, "max_bulk_keys", 500),
            },
            "recent_events": recent_security_events,
        }

    # ------------------------------------------------------------------------
    # Audit Logs Query
    # ------------------------------------------------------------------------

    def get_audit_logs(
        self,
        *,
        page: int = 1,
        page_size: int = 25,
        action: str | None = None,
        target_type: str | None = None,
        outcome: str | None = None,
        search: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch audit log events with filtering and pagination."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        clauses: list[str] = []
        params: list[Any] = []

        if action and action.strip():
            clauses.append("a.action = %s")
            params.append(action.strip())

        if target_type and target_type.strip():
            clauses.append("a.target_type = %s")
            params.append(target_type.strip())

        if outcome and outcome.strip():
            clauses.append("a.outcome = %s")
            params.append(outcome.strip())

        if project_id and project_id.strip():
            clauses.append("a.project_id = %s")
            params.append(project_id.strip())

        if date_from:
            clauses.append("a.created_at >= %s")
            params.append(date_from)

        if date_to:
            clauses.append("a.created_at <= %s")
            params.append(date_to)

        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            clauses.append(
                "(LOWER(a.action) LIKE %s OR LOWER(COALESCE(a.actor_email, '')) LIKE %s OR LOWER(COALESCE(p.project_name, '')) LIKE %s OR LOWER(COALESCE(a.ip_address, '')) LIKE %s)"
            )
            params.extend([term, term, term, term])

        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        count_sql = f"""
            SELECT COUNT(*) AS total
            FROM admin_audit_logs a
            LEFT JOIN nexora_projects p ON p.id = a.project_id
            {where_clause}
        """
        count_row = self.db.fetch_one(count_sql, tuple(params) if params else None)
        total = count_row["total"] if count_row else 0

        data_sql = f"""
            SELECT a.id, a.action, a.target_type, a.target_id, a.outcome,
                   a.created_at, a.ip_address, a.user_agent, a.details,
                   a.actor_email, a.project_id, p.project_name
            FROM admin_audit_logs a
            LEFT JOIN nexora_projects p ON p.id = a.project_id
            {where_clause}
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s
        """
        query_params = list(params) + [page_size, offset]
        rows = self.db.fetch_all(data_sql, tuple(query_params))

        return {
            "items": rows,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if total > 0 else 1,
            },
        }

    # ------------------------------------------------------------------------
    # System Settings
    # ------------------------------------------------------------------------

    def get_system_settings(self) -> dict[str, Any]:
        """Fetch runtime system settings (non-secret)."""
        rows = self.db.fetch_all(
            "SELECT key, value, description FROM system_settings WHERE is_secret = false"
        )
        settings_map = {r["key"]: r["value"] for r in rows}
        return {
            "key_prefix": settings_map.get("nexora.key_prefix", "NXR"),
            "default_key_expiry_days": int(settings_map.get("nexora.default_key_expiry_days", "30")),
            "max_bulk_keys": int(settings_map.get("nexora.max_bulk_keys", str(getattr(self.settings, "max_bulk_keys", 500)))),
            "heartbeat_interval_seconds": int(settings_map.get("nexora.heartbeat_interval_seconds", "300")),
            "api_rate_limits": settings_map.get("nexora.api_rate_limits", "60/min"),
            "maintenance_mode": settings_map.get("nexora.maintenance_mode", "false").lower() == "true",
            "display_timezone": settings_map.get("nexora.display_timezone", "UTC"),
            "auto_delete_days": int(settings_map.get("nexora.auto_delete_days", "0") or 0),
            "version": getattr(self.settings, "version", "1.0.0"),
        }

    def update_system_settings(
        self,
        updates: dict[str, Any],
        *,
        actor_id: UUID | str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Update validated runtime system settings."""
        allowed_keys = {
            "key_prefix": ("nexora.key_prefix", str),
            "default_key_expiry_days": ("nexora.default_key_expiry_days", int),
            "max_bulk_keys": ("nexora.max_bulk_keys", int),
            "heartbeat_interval_seconds": ("nexora.heartbeat_interval_seconds", int),
            "api_rate_limits": ("nexora.api_rate_limits", str),
            "maintenance_mode": ("nexora.maintenance_mode", bool),
            "display_timezone": ("nexora.display_timezone", str),
            "auto_delete_days": ("nexora.auto_delete_days", int),
        }

        validated_updates: dict[str, str] = {}
        now = utcnow()

        for user_key, (db_key, expected_type) in allowed_keys.items():
            if user_key in updates:
                val = updates[user_key]
                if expected_type is int:
                    try:
                        int_val = int(val)
                    except (ValueError, TypeError):
                        raise ValidationError({user_key: "Must be a valid integer."})
                    if user_key == "default_key_expiry_days" and not 1 <= int_val <= 3650:
                        raise ValidationError({user_key: "Must be between 1 and 3650 days."})
                    if user_key == "max_bulk_keys" and not 1 <= int_val <= 2000:
                        raise ValidationError({user_key: "Must be between 1 and 2000."})
                    if user_key == "heartbeat_interval_seconds" and not 10 <= int_val <= 86400:
                        raise ValidationError({user_key: "Must be between 10 and 86400 seconds."})
                    if user_key == "auto_delete_days" and not 0 <= int_val <= 3650:
                        raise ValidationError(
                            {user_key: "Must be between 0 and 3650 days (0 disables automatic deletion)."}
                        )
                    validated_updates[db_key] = str(int_val)
                elif expected_type is bool:
                    validated_updates[db_key] = "true" if val else "false"
                elif expected_type is str:
                    str_val = str(val).strip()
                    if user_key == "key_prefix" and not 1 <= len(str_val) <= 12:
                        raise ValidationError({user_key: "Prefix must be 1 to 12 characters."})
                    validated_updates[db_key] = str_val

        with self.db.cursor() as cursor:
            for db_key, db_val in validated_updates.items():
                cursor.execute(
                    """
                    INSERT INTO system_settings (key, value, updated_at, updated_by)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by
                    """,
                    (db_key, db_val, now, str(actor_id) if actor_id else None),
                )

            self._write_audit(
                cursor,
                actor_id=actor_id,
                action="system.settings_update",
                target_type="system_settings",
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={"updated_keys": list(validated_updates.keys())},
            )

        return self.get_system_settings()
