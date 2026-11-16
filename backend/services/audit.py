"""Audit logging helpers.

Audit rows are written inside the same transaction as the action they record,
so a successful mutation always has its audit entry.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Database


def write_audit(
    cursor: Any,
    *,
    actor: dict[str, Any] | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    actor_id = actor.get("id") if actor else None
    actor_email = actor.get("email") if actor else None
    cursor.execute(
        """
        INSERT INTO admin_audit_logs
            (admin_user_id, actor_email, action, target_type, target_id,
             outcome, ip_address, user_agent, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            actor_id,
            actor_email,
            action,
            target_type,
            target_id,
            outcome,
            ip_address,
            user_agent,
            json.dumps(details or {}, default=str),
        ),
    )


def audit(
    db: Database,
    *,
    actor: dict[str, Any] | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with db.cursor() as cursor:
        write_audit(
            cursor,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
        )
