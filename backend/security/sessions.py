"""Opaque server-side admin sessions.

A 256-bit random token is delivered in an HttpOnly cookie. Only its SHA-256
digest is stored, so a database dump cannot be replayed as a login. CSRF tokens
are bound to the session row and stored as digests as well.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from ..db import Database


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_digest(token: str) -> str:
    return _digest(token)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def insert_session(
    cursor: Any,
    admin_user_id: str,
    *,
    ttl_seconds: int,
    ip_address: str | None,
    user_agent: str | None,
    audience: str = "keygen",
) -> dict[str, Any]:
    """Insert a session on an existing transaction cursor."""
    token = _new_token()
    csrf_token = _new_token()
    cursor.execute(
        """
        INSERT INTO admin_sessions
            (admin_user_id, token_hash, csrf_hash, ip_address, user_agent,
             expires_at, audience)
        VALUES (%s, %s, %s, %s, %s, now() + (%s || ' seconds')::interval, %s)
        RETURNING id, expires_at
        """,
        (
            admin_user_id,
            _digest(token),
            _digest(csrf_token),
            ip_address,
            user_agent,
            str(ttl_seconds),
            audience,
        ),
    )
    row = cursor.fetchone()
    if row is None:  # pragma: no cover - insert always returns a row
        raise RuntimeError("Session could not be created.")
    return {
        "session_id": str(row[0]),
        "token": token,
        "csrf_token": csrf_token,
        "expires_at": row[1],
    }


def create_session(
    db: Database,
    admin_user_id: str,
    *,
    ttl_seconds: int,
    ip_address: str | None,
    user_agent: str | None,
    audience: str = "keygen",
) -> dict[str, Any]:
    with db.cursor() as cursor:
        return insert_session(
            cursor,
            admin_user_id,
            ttl_seconds=ttl_seconds,
            ip_address=ip_address,
            user_agent=user_agent,
            audience=audience,
        )


def find_session(
    db: Database, token: str, *, audience: str | None = None
) -> dict[str, Any] | None:
    """Return an active session joined to its active administrator.

    When ``audience`` is supplied, only a session issued for that application
    surface is accepted. This prevents a session minted by one console from
    being replayed against another console that shares the database.
    """
    if not token:
        return None
    audience_clause = "AND s.audience = %s" if audience is not None else ""
    params: tuple[Any, ...] = (
        (_digest(token), audience) if audience is not None else (_digest(token),)
    )
    row = db.fetch_one(
        f"""
        SELECT s.id            AS session_id,
               s.csrf_hash,
               s.expires_at,
               s.last_seen_at,
               s.audience,
               u.id            AS user_id,
               u.email,
               u.full_name,
               u.role,
               u.is_active
          FROM admin_sessions s
          JOIN admin_users u ON u.id = s.admin_user_id
         WHERE s.token_hash = %s
           AND s.revoked_at IS NULL
           AND s.expires_at > now()
           {audience_clause}
        """,
        params,
    )
    if row is None or not row["is_active"]:
        return None

    # Throttled last-seen update: at most once every five minutes.
    db.execute(
        """
        UPDATE admin_sessions
           SET last_seen_at = now()
         WHERE id = %s
           AND last_seen_at < now() - interval '5 minutes'
        """,
        (row["session_id"],),
    )
    return row


def issue_csrf(db: Database, session_id: str) -> str:
    """Rotate the CSRF token for an existing session and return the raw value."""
    token = _new_token()
    db.execute(
        "UPDATE admin_sessions SET csrf_hash = %s WHERE id = %s",
        (_digest(token), session_id),
    )
    return token


def revoke_session(db: Database, token: str) -> bool:
    if not token:
        return False
    return (
        db.execute(
            """
            UPDATE admin_sessions
               SET revoked_at = now()
             WHERE token_hash = %s
               AND revoked_at IS NULL
            """,
            (_digest(token),),
        )
        > 0
    )


def revoke_all_sessions(
    db: Database, admin_user_id: str, *, except_token: str | None = None
) -> int:
    if except_token:
        return db.execute(
            """
            UPDATE admin_sessions
               SET revoked_at = now()
             WHERE admin_user_id = %s
               AND revoked_at IS NULL
               AND token_hash <> %s
            """,
            (admin_user_id, _digest(except_token)),
        )
    return db.execute(
        """
        UPDATE admin_sessions
           SET revoked_at = now()
         WHERE admin_user_id = %s
           AND revoked_at IS NULL
        """,
        (admin_user_id,),
    )
