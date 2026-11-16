"""Idempotent provisioning of the initial administrator from the environment.

The operator supplies ``ADMIN_EMAIL`` and an already-hashed
``ADMIN_PASSWORD_HASH``. This module never sees the plaintext password and can
therefore not derive, reset or rotate it.

Startup semantics:

* First start on an empty database: the administrator is inserted with role
  ``ADMIN_ROLE`` (``owner`` by default).
* Every later start: the existing row is left exactly as it is. A changed
  ``ADMIN_PASSWORD_HASH`` in ``.env`` is deliberately ignored — password
  changes only happen through the explicit CLI operation.

No credential material is logged or written to the audit trail.
"""

from __future__ import annotations

from typing import Any

from ..config import ConfigError
from ..db import Database, is_unique_violation
from ..security.passwords import validate_password_hash
from .audit import write_audit


def _validated_settings(settings: Any) -> tuple[str, str, str]:
    email = (getattr(settings, "admin_email", "") or "").strip().lower()
    password_hash = getattr(settings, "admin_password_hash", "") or ""
    role = (getattr(settings, "admin_role", "owner") or "owner").strip().lower()

    if not email:
        raise ConfigError("ADMIN_EMAIL is required for initial administrator setup.")
    if not password_hash:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH is required for initial administrator setup. "
            "Generate one with: python -m backend.cli hash-password --email <email>"
        )
    try:
        validate_password_hash(password_hash)
    except ValueError as exc:
        raise ConfigError(f"ADMIN_PASSWORD_HASH is invalid: {exc}") from None
    if role not in {"owner", "admin"}:
        raise ConfigError("ADMIN_ROLE must be either 'owner' or 'admin'.")
    return email, password_hash, role


def _existing(db: Database, email: str) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT id, email, role, is_active
          FROM admin_users
         WHERE lower(email) = lower(%s)
        """,
        (email,),
    )


def _result(row: dict[str, Any], *, created: bool) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "created": created,
    }


def ensure_initial_admin(db: Database, settings: Any) -> dict[str, Any]:
    """Create the configured administrator if it does not exist yet.

    Returns a credential-free summary. An existing account is preserved
    untouched, including its password hash and active/disabled state.
    """
    email, password_hash, role = _validated_settings(settings)

    existing = _existing(db, email)
    if existing is not None:
        return _result(existing, created=False)

    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO admin_users (email, full_name, password_hash, role)
                VALUES (%s, %s, %s, %s)
                RETURNING id, email, role, is_active
                """,
                (email, "Initial Administrator", password_hash, role),
            )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - INSERT always returns a row
                raise RuntimeError("Initial administrator could not be created.")
            created = {
                "id": row[0],
                "email": row[1],
                "role": row[2],
                "is_active": row[3],
            }
            write_audit(
                cursor,
                actor=None,
                action="admin.bootstrap",
                target_type="admin_user",
                target_id=str(row[0]),
                outcome="success",
                details={"email": email, "role": role, "source": "environment"},
            )
        return _result(created, created=True)
    except Exception as exc:
        # Two workers can race on first boot. The unique lower(email) index
        # makes one of them lose; report that account as the existing one.
        if is_unique_violation(exc):
            existing = _existing(db, email)
            if existing is not None:
                return _result(existing, created=False)
        raise
