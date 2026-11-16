"""License key lifecycle service.

Every state change is validated server-side and written in a transaction.
Activation takes a row lock (``SELECT ... FOR UPDATE``) so concurrent requests
cannot over-activate a key, and a unique index on
``(license_key_id, device_hash)`` makes replay impossible.
"""

from __future__ import annotations

import json
from typing import Any, Iterable
from uuid import UUID, uuid4

from ..db import Database, is_unique_violation
from ..errors import ApiError, ValidationError
from ..security.keys import (
    KeyFormatError,
    KeySpec,
    generate_plaintext_key,
    hash_identifier,
    hash_key,
    key_template,
    masked_hint,
    normalize_key,
)
from ..serialization import utcnow
from .audit import write_audit

STATUSES = ("active", "inactive", "pending", "expired", "revoked", "used")
ADMIN_SETTABLE_STATUSES = ("active", "inactive", "pending")
SORT_COLUMNS = {
    "created_at": "k.created_at",
    "updated_at": "k.updated_at",
    "expires_at": "k.expires_at",
    "activation_count": "k.activation_count",
    "status": "k.status",
    "label": "lower(k.label)",
}
MAX_PAGE_SIZE = 100
MAX_EXPORT_ROWS = 10000


def _row(cursor: Any) -> dict[str, Any] | None:
    if not cursor.description:
        return None
    columns = [column[0] for column in cursor.description]
    row = cursor.fetchone()
    return dict(zip(columns, row)) if row else None


def _all_rows(cursor: Any) -> list[dict[str, Any]]:
    if not cursor.description:
        return []
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _pagination(page: int, page_size: int, total: int) -> dict[str, Any]:
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }


class LicenseService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self.pepper: str = settings.key_pepper

    # -- internals -----------------------------------------------------------

    def _hash(self, normalized_key: str) -> str:
        return hash_key(self.pepper, normalized_key)

    def _identifier(self, purpose: str, value: str) -> str:
        return hash_identifier(self.pepper, purpose, value)

    def expire_overdue(self) -> int:
        return int(self.db.fetch_value("SELECT rh_expire_overdue_keys()") or 0)

    def _key_select(self) -> str:
        return """
            SELECT k.id, k.key_hint, k.key_format, k.key_prefix, k.key_suffix,
                   k.label, k.status, k.max_activations, k.activation_count,
                   k.expires_at, k.metadata, k.batch_id, k.created_at,
                   k.updated_at, k.revoked_at, k.revoked_reason,
                   k.created_by, u.email AS created_by_email,
                   k.revoked_by, ru.email AS revoked_by_email
              FROM license_keys k
              LEFT JOIN admin_users u ON u.id = k.created_by
              LEFT JOIN admin_users ru ON ru.id = k.revoked_by
        """

    def _filters(
        self,
        *,
        status: str | None,
        search: str | None,
        created_after: Any = None,
        created_before: Any = None,
        expires_before: Any = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("k.status = %s")
            params.append(status)

        if search:
            term = search.strip()[:200]
            or_parts = [
                "position(lower(%s) in lower(k.label)) > 0",
                "position(lower(%s) in lower(k.key_hint)) > 0",
            ]
            params.extend([term, term])
            normalized = normalize_key(term)
            if normalized:
                or_parts.append("k.key_hash = %s")
                params.append(self._hash(normalized))
            try:
                UUID(term)
            except (ValueError, AttributeError):
                pass
            else:
                or_parts.append("k.id = %s")
                params.append(term)
            clauses.append("(" + " OR ".join(or_parts) + ")")

        if created_after is not None:
            clauses.append("k.created_at >= %s")
            params.append(created_after)
        if created_before is not None:
            clauses.append("k.created_at <= %s")
            params.append(created_before)
        if expires_before is not None:
            clauses.append("k.expires_at IS NOT NULL AND k.expires_at <= %s")
            params.append(expires_before)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # -- generation ----------------------------------------------------------

    def generate(
        self,
        *,
        actor: dict[str, Any],
        spec: KeySpec,
        quantity: int,
        max_activations: int,
        expires_at: Any,
        label: str,
        metadata: dict[str, Any] | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        if not 1 <= quantity <= int(self.settings.max_bulk_keys):
            raise ApiError(
                422,
                "validation_error",
                f"Quantity must be between 1 and {self.settings.max_bulk_keys}.",
            )
        if expires_at is not None and expires_at <= utcnow():
            raise ValidationError({"expires_at": "Must be a future date and time."})

        metadata = metadata or {}
        encoded_metadata = json.dumps(metadata, default=str)
        if len(encoded_metadata.encode("utf-8")) > 4096:
            raise ValidationError({"metadata": "Metadata must be 4 KB or smaller."})

        batch_id = str(uuid4())
        template = key_template(spec)
        created: list[dict[str, Any]] = []

        with self.db.cursor() as cursor:
            for _ in range(quantity):
                inserted = None
                for _attempt in range(8):
                    plaintext = generate_plaintext_key(spec)
                    normalized = normalize_key(plaintext)
                    assert normalized is not None
                    key_hash = self._hash(normalized)
                    hint = masked_hint(spec, plaintext)
                    cursor.execute(
                        """
                        INSERT INTO license_keys
                            (key_hash, key_format, key_hint, key_prefix,
                             key_suffix, label, status, max_activations,
                             expires_at, metadata, batch_id, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s,
                                %s::jsonb, %s, %s)
                        ON CONFLICT (key_hash) DO NOTHING
                        RETURNING id, created_at, status
                        """,
                        (
                            key_hash,
                            template,
                            hint,
                            spec.prefix,
                            normalized[-4:],
                            label,
                            max_activations,
                            expires_at,
                            encoded_metadata,
                            batch_id,
                            actor["id"],
                        ),
                    )
                    inserted = _row(cursor)
                    if inserted is not None:
                        break
                if inserted is None:  # pragma: no cover - 80-bit collision safety
                    raise ApiError(
                        500,
                        "key_generation_failed",
                        "A unique key could not be generated. Try again.",
                    )
                created.append(
                    {
                        "id": str(inserted["id"]),
                        "key": plaintext,
                        "key_hint": hint,
                        "key_format": template,
                        "status": inserted["status"],
                        "max_activations": max_activations,
                        "expires_at": expires_at,
                        "created_at": inserted["created_at"],
                    }
                )

            cursor.execute(
                """
                INSERT INTO generation_logs
                    (batch_id, admin_user_id, quantity, key_format, key_prefix,
                     key_suffix, groups, group_length, separator,
                     max_activations, expires_at, label, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    batch_id,
                    actor["id"],
                    quantity,
                    template,
                    spec.prefix,
                    spec.suffix,
                    spec.groups,
                    spec.group_length,
                    spec.separator,
                    max_activations,
                    expires_at,
                    label,
                    ip_address,
                    user_agent,
                ),
            )
            write_audit(
                cursor,
                actor=actor,
                action="key.generate",
                target_type="generation_batch",
                target_id=batch_id,
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={
                    "quantity": quantity,
                    "format": template,
                    "max_activations": max_activations,
                    "expires_at": expires_at,
                },
            )

        return {
            "batch_id": batch_id,
            "quantity": quantity,
            "key_format": template,
            "keys": created,
            "warning": "Store these keys now. Plaintext keys are never shown again.",
        }

    # -- queries -------------------------------------------------------------

    def list_keys(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        search: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
        created_after: Any = None,
        created_before: Any = None,
        expires_before: Any = None,
    ) -> dict[str, Any]:
        self.expire_overdue()
        page = max(1, page)
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        column = SORT_COLUMNS.get(sort, "k.created_at")
        direction = "ASC" if order.lower() == "asc" else "DESC"
        where, params = self._filters(
            status=status,
            search=search,
            created_after=created_after,
            created_before=created_before,
            expires_before=expires_before,
        )

        total = int(
            self.db.fetch_value(
                "SELECT count(*) FROM license_keys k" + where, params
            )
            or 0
        )
        offset = (page - 1) * page_size
        items = self.db.fetch_all(
            self._key_select()
            + where
            + f" ORDER BY {column} {direction} NULLS LAST, k.id"
            + " LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        return {
            "items": items,
            "pagination": _pagination(page, page_size, total),
            "filters": {
                "status": status,
                "search": search,
                "sort": sort,
                "order": direction.lower(),
            },
        }

    def get_key(self, key_id: str) -> dict[str, Any]:
        self.expire_overdue()
        key = self.db.fetch_one(
            self._key_select() + " WHERE k.id = %s", (key_id,)
        )
        if key is None:
            raise ApiError(404, "key_not_found", "License key not found.")

        activations = self.db.fetch_all(
            """
            SELECT id, device_hint, account_hint, status, activated_at,
                   deactivated_at, deactivated_reason
              FROM key_activations
             WHERE license_key_id = %s
             ORDER BY activated_at DESC
            """,
            (key_id,),
        )
        events = self.db.fetch_all(
            """
            SELECT a.id, a.action, a.outcome, a.actor_email, a.details,
                   a.created_at
              FROM admin_audit_logs a
             WHERE a.target_type = 'license_key'
               AND a.target_id = %s
             ORDER BY a.created_at DESC
             LIMIT 50
            """,
            (key_id,),
        )
        batch = None
        if key.get("batch_id"):
            batch = self.db.fetch_one(
                """
                SELECT g.id, g.batch_id, g.quantity, g.key_format, g.label,
                       g.max_activations, g.expires_at, g.created_at,
                       u.email AS generated_by
                  FROM generation_logs g
                  LEFT JOIN admin_users u ON u.id = g.admin_user_id
                 WHERE g.batch_id = %s
                 LIMIT 1
                """,
                (key["batch_id"],),
            )
        key["activations"] = activations
        key["events"] = events
        key["batch"] = batch
        return key

    def verify(self, raw_key: str) -> dict[str, Any]:
        normalized = normalize_key(raw_key)
        if normalized is None:
            raise ValidationError(
                {"key": "Enter a valid license key (letters and digits, 12+ characters)."}
            )
        row = self.db.fetch_one(
            """
            SELECT key_hint, status, max_activations, activation_count, expires_at
              FROM license_keys
             WHERE key_hash = %s
            """,
            (self._hash(normalized),),
        )
        if row is None:
            return {
                "valid": False,
                "status": "unknown",
                "remaining_activations": 0,
            }

        status = row["status"]
        if (
            status in {"active", "inactive", "pending"}
            and row["expires_at"] is not None
            and row["expires_at"] <= utcnow()
        ):
            status = "expired"
        return {
            "valid": True,
            "key_hint": row["key_hint"],
            "status": status,
            "max_activations": row["max_activations"],
            "activation_count": row["activation_count"],
            "remaining_activations": max(
                0, int(row["max_activations"]) - int(row["activation_count"])
            ),
            "expires_at": row["expires_at"],
        }

    # -- activation ----------------------------------------------------------

    def activate(
        self,
        *,
        raw_key: str,
        device_id: str,
        account_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        normalized = normalize_key(raw_key)
        if normalized is None:
            raise ValidationError(
                {"key": "Enter a valid license key (letters and digits, 12+ characters)."}
            )
        device_hash = self._identifier("device", device_id)
        device_hint = "..." + device_id.strip()[-4:]
        account_hash = (
            self._identifier("account", account_id) if account_id else None
        )
        account_hint = ("..." + account_id.strip()[-4:]) if account_id else None
        ip_hash = self._identifier("ip", ip_address) if ip_address else None

        failure: ApiError | None = None
        result: dict[str, Any] | None = None

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, key_hint, status, max_activations, activation_count,
                       expires_at
                  FROM license_keys
                 WHERE key_hash = %s
                 FOR UPDATE
                """,
                (self._hash(normalized),),
            )
            key = _row(cursor)

            if key is None:
                failure = ApiError(
                    404, "key_not_found", "This license key is not valid."
                )
                write_audit(
                    cursor,
                    actor=None,
                    action="key.activate",
                    outcome="failure",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={"reason": "unknown_key", "device_hint": device_hint},
                )
            else:
                key_id = str(key["id"])

                if key["expires_at"] is not None and key["expires_at"] <= utcnow():
                    cursor.execute(
                        "UPDATE license_keys SET status = 'expired' WHERE id = %s",
                        (key_id,),
                    )
                    key["status"] = "expired"

                cursor.execute(
                    """
                    SELECT id, status, activated_at
                      FROM key_activations
                     WHERE license_key_id = %s AND device_hash = %s
                    """,
                    (key_id, device_hash),
                )
                existing = _row(cursor)

                if key["status"] == "revoked":
                    failure = ApiError(409, "key_revoked", "This key has been revoked.")
                elif key["status"] == "expired":
                    failure = ApiError(409, "key_expired", "This key has expired.")
                elif key["status"] == "pending":
                    failure = ApiError(
                        409, "key_pending", "This key is not active yet."
                    )
                elif key["status"] == "inactive":
                    failure = ApiError(409, "key_inactive", "This key is not active.")
                elif key["status"] == "used" and not (
                    existing is not None and existing["status"] == "active"
                ):
                    failure = ApiError(
                        409,
                        "activation_limit_reached",
                        "This key has reached its activation limit.",
                    )
                elif existing is not None and existing["status"] == "active":
                    result = {
                        "replayed": True,
                        "activation": {
                            "id": str(existing["id"]),
                            "device_hint": device_hint,
                            "account_hint": account_hint,
                            "activated_at": existing["activated_at"],
                            "status": "active",
                        },
                        "key": {
                            "id": key_id,
                            "key_hint": key["key_hint"],
                            "status": key["status"],
                            "max_activations": key["max_activations"],
                            "activation_count": key["activation_count"],
                            "remaining_activations": max(
                                0,
                                int(key["max_activations"])
                                - int(key["activation_count"]),
                            ),
                            "expires_at": key["expires_at"],
                        },
                    }
                    write_audit(
                        cursor,
                        actor=None,
                        action="key.activate",
                        target_type="license_key",
                        target_id=key_id,
                        outcome="success",
                        ip_address=ip_address,
                        user_agent=user_agent,
                        details={
                            "replayed": True,
                            "activation_id": str(existing["id"]),
                            "device_hint": device_hint,
                        },
                    )
                elif int(key["activation_count"]) >= int(key["max_activations"]):
                    failure = ApiError(
                        409,
                        "activation_limit_reached",
                        "This key has reached its activation limit.",
                    )
                else:
                    new_count = int(key["activation_count"]) + 1
                    new_status = (
                        "used"
                        if new_count >= int(key["max_activations"])
                        else "active"
                    )
                    try:
                        if existing is not None:
                            cursor.execute(
                                """
                                UPDATE key_activations
                                   SET status = 'active',
                                       activated_at = now(),
                                       deactivated_at = NULL,
                                       deactivated_reason = NULL,
                                       account_hash = %s,
                                       account_hint = %s,
                                       ip_hash = %s,
                                       user_agent = %s
                                 WHERE id = %s
                                RETURNING id, activated_at
                                """,
                                (
                                    account_hash,
                                    account_hint,
                                    ip_hash,
                                    user_agent,
                                    existing["id"],
                                ),
                            )
                        else:
                            cursor.execute(
                                """
                                INSERT INTO key_activations
                                    (license_key_id, device_hash, device_hint,
                                     account_hash, account_hint, ip_hash, user_agent)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                RETURNING id, activated_at
                                """,
                                (
                                    key_id,
                                    device_hash,
                                    device_hint,
                                    account_hash,
                                    account_hint,
                                    ip_hash,
                                    user_agent,
                                ),
                            )
                        activation = _row(cursor)
                        cursor.execute(
                            """
                            UPDATE license_keys
                               SET activation_count = %s,
                                   status = %s
                             WHERE id = %s
                            """,
                            (new_count, new_status, key_id),
                        )
                    except Exception as exc:
                        if is_unique_violation(exc):
                            failure = ApiError(
                                409,
                                "activation_conflict",
                                "This key was activated concurrently. Try again.",
                            )
                        else:
                            raise

                    if failure is None and activation is not None:
                        result = {
                            "replayed": False,
                            "activation": {
                                "id": str(activation["id"]),
                                "device_hint": device_hint,
                                "account_hint": account_hint,
                                "activated_at": activation["activated_at"],
                                "status": "active",
                            },
                            "key": {
                                "id": key_id,
                                "key_hint": key["key_hint"],
                                "status": new_status,
                                "max_activations": key["max_activations"],
                                "activation_count": new_count,
                                "remaining_activations": max(
                                    0, int(key["max_activations"]) - new_count
                                ),
                                "expires_at": key["expires_at"],
                            },
                        }
                        write_audit(
                            cursor,
                            actor=None,
                            action="key.activate",
                            target_type="license_key",
                            target_id=key_id,
                            outcome="success",
                            ip_address=ip_address,
                            user_agent=user_agent,
                            details={
                                "replayed": False,
                                "reactivated": existing is not None,
                                "activation_id": str(activation["id"]),
                                "device_hint": device_hint,
                                "remaining": max(
                                    0, int(key["max_activations"]) - new_count
                                ),
                            },
                        )

                if failure is not None:
                    write_audit(
                        cursor,
                        actor=None,
                        action="key.activate",
                        target_type="license_key",
                        target_id=key_id,
                        outcome="failure",
                        ip_address=ip_address,
                        user_agent=user_agent,
                        details={
                            "reason": failure.code,
                            "device_hint": device_hint,
                        },
                    )

        if failure is not None:
            raise failure
        if result is None:  # pragma: no cover - defensive
            raise ApiError(500, "internal_error", "Activation could not be completed.")
        return result

    # -- administration ------------------------------------------------------

    def revoke(
        self,
        *,
        key_id: str,
        reason: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        failure: ApiError | None = None
        key: dict[str, Any] | None = None
        deactivated = 0

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, key_hint, status FROM license_keys
                 WHERE id = %s FOR UPDATE
                """,
                (key_id,),
            )
            key = _row(cursor)
            if key is None:
                failure = ApiError(404, "key_not_found", "License key not found.")
            elif key["status"] == "revoked":
                failure = ApiError(409, "already_revoked", "This key is already revoked.")
            else:
                cursor.execute(
                    """
                    UPDATE key_activations
                       SET status = 'deactivated',
                           deactivated_at = now(),
                           deactivated_reason = 'key_revoked'
                     WHERE license_key_id = %s
                       AND status = 'active'
                    """,
                    (key_id,),
                )
                deactivated = cursor.rowcount
                cursor.execute(
                    """
                    UPDATE license_keys
                       SET status = 'revoked',
                           revoked_at = now(),
                           revoked_by = %s,
                           revoked_reason = %s
                     WHERE id = %s
                    """,
                    (actor["id"], reason, key_id),
                )
                write_audit(
                    cursor,
                    actor=actor,
                    action="key.revoke",
                    target_type="license_key",
                    target_id=key_id,
                    outcome="success",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={
                        "reason": reason,
                        "previous_status": key["status"],
                        "deactivated_activations": deactivated,
                    },
                )

        if failure is not None:
            raise failure
        return {
            "id": key_id,
            "status": "revoked",
            "deactivated_activations": deactivated,
        }

    def deactivate(
        self,
        *,
        key_id: str,
        activation_id: str | None,
        reason: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        failure: ApiError | None = None
        result: dict[str, Any] | None = None

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, key_hint, status, max_activations, activation_count
                  FROM license_keys WHERE id = %s FOR UPDATE
                """,
                (key_id,),
            )
            key = _row(cursor)

            if key is None:
                failure = ApiError(404, "key_not_found", "License key not found.")
            elif key["status"] in {"revoked", "expired"}:
                failure = ApiError(
                    409,
                    "key_not_active",
                    f"This key is {key['status']} and cannot be deactivated.",
                )
            else:
                selected: list[dict[str, Any]]
                if activation_id:
                    cursor.execute(
                        """
                        SELECT id, status FROM key_activations
                         WHERE id = %s AND license_key_id = %s
                         FOR UPDATE
                        """,
                        (activation_id, key_id),
                    )
                    activation = _row(cursor)
                    if activation is None:
                        failure = ApiError(
                            404, "activation_not_found", "Activation not found for this key."
                        )
                        selected = []
                    elif activation["status"] != "active":
                        failure = ApiError(
                            409,
                            "activation_already_deactivated",
                            "This activation is already deactivated.",
                        )
                        selected = []
                    else:
                        selected = [activation]
                else:
                    cursor.execute(
                        """
                        SELECT id, status FROM key_activations
                         WHERE license_key_id = %s AND status = 'active'
                         FOR UPDATE
                        """,
                        (key_id,),
                    )
                    selected = _all_rows(cursor)
                    if not selected:
                        failure = ApiError(
                            409, "no_active_activations", "This key has no active activations."
                        )

                if failure is None:
                    ids = [str(item["id"]) for item in selected]
                    id_array = "{" + ",".join(ids) + "}"
                    cursor.execute(
                        """
                        UPDATE key_activations
                           SET status = 'deactivated',
                               deactivated_at = now(),
                               deactivated_reason = %s
                         WHERE license_key_id = %s
                           AND status = 'active'
                           AND (%s = false OR id = ANY(%s::uuid[]))
                        """,
                        (reason, key_id, activation_id is not None, id_array),
                    )
                    new_count = max(0, int(key["activation_count"]) - len(ids))
                    new_status = key["status"]
                    if (
                        new_status == "used"
                        and new_count < int(key["max_activations"])
                    ):
                        new_status = "active"
                    cursor.execute(
                        """
                        UPDATE license_keys
                           SET activation_count = %s, status = %s
                         WHERE id = %s
                        """,
                        (new_count, new_status, key_id),
                    )
                    write_audit(
                        cursor,
                        actor=actor,
                        action="key.deactivate",
                        target_type="license_key",
                        target_id=key_id,
                        outcome="success",
                        ip_address=ip_address,
                        user_agent=user_agent,
                        details={
                            "reason": reason,
                            "activation_ids": ids[:20],
                            "deactivated_count": len(ids),
                            "remaining_activations": max(
                                0, int(key["max_activations"]) - new_count
                            ),
                        },
                    )
                    result = {
                        "id": key_id,
                        "status": new_status,
                        "activation_count": new_count,
                        "max_activations": key["max_activations"],
                        "deactivated_activations": len(ids),
                    }

        if failure is not None:
            raise failure
        if result is None:  # pragma: no cover
            raise ApiError(500, "internal_error", "Deactivation could not be completed.")
        return result

    def delete(
        self,
        *,
        key_id: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        failure: ApiError | None = None
        deleted: dict[str, Any] | None = None

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, key_hint, status, batch_id FROM license_keys
                 WHERE id = %s FOR UPDATE
                """,
                (key_id,),
            )
            key = _row(cursor)
            if key is None:
                failure = ApiError(404, "key_not_found", "License key not found.")
            else:
                write_audit(
                    cursor,
                    actor=actor,
                    action="key.delete",
                    target_type="license_key",
                    target_id=key_id,
                    outcome="success",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={
                        "key_hint": key["key_hint"],
                        "previous_status": key["status"],
                        "batch_id": str(key["batch_id"]) if key["batch_id"] else None,
                    },
                )
                cursor.execute("DELETE FROM license_keys WHERE id = %s", (key_id,))
                deleted = {"id": key_id, "deleted": True}

        if failure is not None:
            raise failure
        if deleted is None:  # pragma: no cover
            raise ApiError(500, "internal_error", "Key could not be deleted.")
        return deleted

    def update(
        self,
        *,
        key_id: str,
        changes: dict[str, Any],
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        failure: ApiError | None = None
        before: dict[str, Any] | None = None

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, key_hint, label, status, max_activations,
                       activation_count, expires_at
                  FROM license_keys WHERE id = %s FOR UPDATE
                """,
                (key_id,),
            )
            key = _row(cursor)
            if key is None:
                failure = ApiError(404, "key_not_found", "License key not found.")
            else:
                before = dict(key)
                fields: list[str] = []
                values: list[Any] = []
                note: dict[str, Any] = {}

                if "label" in changes:
                    fields.append("label = %s")
                    values.append(changes["label"])
                    note["label"] = changes["label"]

                if "max_activations" in changes:
                    new_max = int(changes["max_activations"])
                    if new_max < int(key["activation_count"]):
                        failure = ValidationError(
                            {
                                "max_activations": (
                                    "Cannot be lower than the current activation count "
                                    f"({key['activation_count']})."
                                )
                            }
                        )
                    else:
                        fields.append("max_activations = %s")
                        values.append(new_max)
                        note["max_activations"] = new_max

                if "expires_at" in changes:
                    fields.append("expires_at = %s")
                    values.append(changes["expires_at"])
                    note["expires_at"] = changes["expires_at"]

                target_status = changes.get("status")
                if target_status is not None:
                    if key["status"] == "revoked":
                        failure = ApiError(
                            409,
                            "key_revoked",
                            "A revoked key cannot be reactivated.",
                        )
                    elif target_status == key["status"]:
                        note["status"] = target_status
                    elif target_status not in ADMIN_SETTABLE_STATUSES:
                        failure = ValidationError(
                            {
                                "status": "Admins can set active, inactive or pending."
                            }
                        )
                    else:
                        current = key["status"]
                        allowed = {
                            "active": {"inactive", "pending"},
                            "inactive": {"active", "pending"},
                            "pending": {"active", "inactive"},
                            "expired": {"active", "inactive"},
                            "used": {"inactive"},
                        }.get(current, set())
                        if target_status not in allowed:
                            failure = ValidationError(
                                {
                                    "status": (
                                        f"Cannot change status from {current} to "
                                        f"{target_status}."
                                    )
                                }
                            )
                        elif target_status == "active":
                            activation_limit = int(
                                changes.get("max_activations", key["max_activations"])
                            )
                            if int(key["activation_count"]) >= activation_limit:
                                failure = ValidationError(
                                    {
                                        "status": (
                                            "Increase max_activations above the current "
                                            "activation count before activating."
                                        )
                                    }
                                )
                            else:
                                expiry = changes.get(
                                    "expires_at", key["expires_at"]
                                )
                                if expiry is not None and expiry <= utcnow():
                                    failure = ValidationError(
                                        {
                                            "status": (
                                                "Extend the expiry date before activating "
                                                "this key."
                                            )
                                        }
                                    )
                                else:
                                    fields.append("status = %s")
                                    values.append(target_status)
                                    note["status"] = target_status
                        else:
                            fields.append("status = %s")
                            values.append(target_status)
                            note["status"] = target_status

                if failure is None and fields:
                    values.append(key_id)
                    cursor.execute(
                        "UPDATE license_keys SET "
                        + ", ".join(fields)
                        + " WHERE id = %s",
                        values,
                    )
                    # An exhausted key must not remain active after its limit
                    # is reduced to the current activation count.
                    if "max_activations" in changes and failure is None:
                        cursor.execute(
                            """
                            UPDATE license_keys
                               SET status = 'used'
                             WHERE id = %s
                               AND status = 'active'
                               AND activation_count >= max_activations
                            """,
                            (key_id,),
                        )
                    write_audit(
                        cursor,
                        actor=actor,
                        action="key.update",
                        target_type="license_key",
                        target_id=key_id,
                        outcome="success",
                        ip_address=ip_address,
                        user_agent=user_agent,
                        details={
                            "changes": note,
                            "previous_status": key["status"],
                            "key_hint": key["key_hint"],
                        },
                    )

        if failure is not None:
            raise failure

        assert before is not None
        return self.get_key(key_id)

    # -- dashboard and logs --------------------------------------------------

    def stats(self) -> dict[str, Any]:
        self.expire_overdue()
        counts = self.db.fetch_one(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'active')   AS active,
                   count(*) FILTER (WHERE status = 'inactive') AS inactive,
                   count(*) FILTER (WHERE status = 'pending')  AS pending,
                   count(*) FILTER (WHERE status = 'used')     AS used,
                   count(*) FILTER (WHERE status = 'expired')  AS expired,
                   count(*) FILTER (WHERE status = 'revoked')  AS revoked,
                   count(*) FILTER (
                       WHERE status IN ('active', 'inactive', 'pending')
                         AND expires_at IS NOT NULL
                         AND expires_at <= now() + interval '7 days'
                   ) AS expiring_soon,
                   count(*) FILTER (
                       WHERE created_at >= now() - interval '24 hours'
                   ) AS generated_24h
              FROM license_keys
            """
        ) or {}

        activations = self.db.fetch_one(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (
                       WHERE activated_at >= now() - interval '24 hours'
                         AND status = 'active'
                   ) AS active_24h
              FROM key_activations
            """
        ) or {}

        recent_generations = self.db.fetch_all(
            """
            SELECT g.batch_id, g.quantity, g.key_format, g.label, g.created_at,
                   u.email AS generated_by
              FROM generation_logs g
              LEFT JOIN admin_users u ON u.id = g.admin_user_id
             ORDER BY g.created_at DESC
             LIMIT 8
            """
        )
        recent_audit = self.db.fetch_all(
            """
            SELECT id, actor_email, action, target_type, target_id, outcome,
                   created_at
              FROM admin_audit_logs
             ORDER BY created_at DESC
             LIMIT 6
            """
        )

        total = int(counts.get("total") or 0)
        used = int(counts.get("used") or 0)
        return {
            "totals": {
                "total": total,
                "active": int(counts.get("active") or 0),
                "inactive": int(counts.get("inactive") or 0),
                "pending": int(counts.get("pending") or 0),
                "used": used,
                "expired": int(counts.get("expired") or 0),
                "revoked": int(counts.get("revoked") or 0),
            },
            "activity": {
                "generated_24h": int(counts.get("generated_24h") or 0),
                "activations_total": int(activations.get("total") or 0),
                "activations_24h": int(activations.get("active_24h") or 0),
                "expiring_soon": int(counts.get("expiring_soon") or 0),
                "activation_rate": round((used / total) * 100, 1) if total else 0.0,
            },
            "recent_generations": recent_generations,
            "recent_audit": recent_audit,
        }

    def logs(
        self,
        *,
        kind: str,
        page: int,
        page_size: int,
        action: str | None = None,
        outcome: str | None = None,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))
        offset = (page - 1) * page_size

        if kind == "audit":
            clauses: list[str] = []
            params: list[Any] = []
            if action:
                clauses.append("position(lower(%s) in lower(a.action)) > 0")
                params.append(action[:80])
            if outcome:
                clauses.append("a.outcome = %s")
                params.append(outcome)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            total = int(
                self.db.fetch_value(
                    "SELECT count(*) FROM admin_audit_logs a" + where, params
                )
                or 0
            )
            items = self.db.fetch_all(
                """
                SELECT a.id, a.admin_user_id, a.actor_email, a.action,
                       a.target_type, a.target_id, a.outcome, a.ip_address,
                       a.details, a.created_at
                  FROM admin_audit_logs a
                """
                + where
                + " ORDER BY a.created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
        elif kind == "activations":
            total = int(
                self.db.fetch_value("SELECT count(*) FROM key_activations") or 0
            )
            items = self.db.fetch_all(
                """
                SELECT a.id, a.license_key_id, k.key_hint, a.device_hint,
                       a.account_hint, a.status, a.activated_at,
                       a.deactivated_at, a.deactivated_reason
                  FROM key_activations a
                  JOIN license_keys k ON k.id = a.license_key_id
                 ORDER BY a.activated_at DESC
                 LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )
        else:
            total = int(
                self.db.fetch_value("SELECT count(*) FROM generation_logs") or 0
            )
            items = self.db.fetch_all(
                """
                SELECT g.id, g.batch_id, g.quantity, g.key_format,
                       g.key_prefix, g.key_suffix, g.max_activations,
                       g.expires_at, g.label, g.created_at,
                       u.email AS generated_by
                  FROM generation_logs g
                  LEFT JOIN admin_users u ON u.id = g.admin_user_id
                 ORDER BY g.created_at DESC
                 LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )

        return {
            "kind": kind,
            "items": items,
            "pagination": _pagination(page, page_size, total),
        }

    def export_rows(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        created_after: Any = None,
        created_before: Any = None,
        expires_before: Any = None,
    ) -> list[dict[str, Any]]:
        self.expire_overdue()
        where, params = self._filters(
            status=status,
            search=search,
            created_after=created_after,
            created_before=created_before,
            expires_before=expires_before,
        )
        return self.db.fetch_all(
            """
            SELECT k.key_hint, k.key_format, k.label, k.status,
                   k.max_activations, k.activation_count, k.expires_at,
                   k.created_at, k.revoked_at, k.revoked_reason,
                   k.batch_id, u.email AS created_by_email
              FROM license_keys k
              LEFT JOIN admin_users u ON u.id = k.created_by
            """
            + where
            + " ORDER BY k.created_at DESC LIMIT %s",
            params + [MAX_EXPORT_ROWS],
        )
