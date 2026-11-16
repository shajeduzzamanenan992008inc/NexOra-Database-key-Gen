"""Administrator authentication service."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..db import Database, is_unique_violation
from ..errors import ApiError
from ..security.passwords import (
    dummy_password_hash,
    hash_password,
    password_problems,
    verify_password,
)
from ..security.sessions import insert_session, token_digest
from ..serialization import utcnow
from ..validation import ensure_email
from .audit import audit, write_audit

GENERIC_LOGIN_ERROR = "Email or password is incorrect."
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
ROLES = ("owner", "admin", "viewer")


class AuthService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings

    # -- login ---------------------------------------------------------------

    def login(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> dict[str, Any]:
        attempted_email = (email or "").strip().lower()[:254]
        user = self.db.fetch_one(
            """
            SELECT id, email, full_name, role, password_hash, is_active,
                   failed_login_attempts, locked_until
              FROM admin_users
             WHERE lower(email) = lower(%s)
            """,
            (attempted_email,),
        )
        now = utcnow()

        if user is None:
            # Equalize timing between unknown and known accounts.
            verify_password(password, dummy_password_hash())
            self._record_failure(
                None, attempted_email, ip_address, user_agent, "unknown_account"
            )
            raise ApiError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)

        actor = {"id": str(user["id"]), "email": user["email"]}

        if user["locked_until"] is not None and user["locked_until"] > now:
            remaining = max(1, int((user["locked_until"] - now).total_seconds()))
            self._record_failure(
                actor, attempted_email, ip_address, user_agent, "account_locked"
            )
            raise ApiError(
                429,
                "account_locked",
                "Too many failed sign-in attempts. Try again later.",
                {"retry_after_seconds": remaining},
                {"Retry-After": str(remaining)},
            )

        if not verify_password(password, user["password_hash"]):
            attempts = int(user["failed_login_attempts"]) + 1
            locked_until = (
                now + dt.timedelta(minutes=LOCKOUT_MINUTES)
                if attempts >= MAX_FAILED_ATTEMPTS
                else None
            )
            with self.db.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE admin_users
                       SET failed_login_attempts = %s,
                           locked_until = %s
                     WHERE id = %s
                    """,
                    (attempts, locked_until, user["id"]),
                )
                write_audit(
                    cursor,
                    actor=actor,
                    action="auth.login",
                    target_type="admin_user",
                    target_id=str(user["id"]),
                    outcome="failure",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={
                        "reason": "invalid_password",
                        "attempts": attempts,
                        "locked": locked_until is not None,
                    },
                )
            raise ApiError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)

        if not user["is_active"]:
            self._record_failure(
                actor, attempted_email, ip_address, user_agent, "inactive_account"
            )
            raise ApiError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)

        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_users
                   SET failed_login_attempts = 0,
                       locked_until = NULL,
                       last_login_at = now()
                 WHERE id = %s
                """,
                (user["id"],),
            )
            session = insert_session(
                cursor,
                str(user["id"]),
                ttl_seconds=self.settings.session_ttl_seconds,
                ip_address=ip_address,
                user_agent=user_agent,
                audience=getattr(self.settings, "session_audience", "keygen"),
            )
            write_audit(
                cursor,
                actor=actor,
                action="auth.login",
                target_type="admin_user",
                target_id=str(user["id"]),
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
                details={"role": user["role"]},
            )

        return {
            "user": self._public_user(user),
            "session": session,
        }

    def logout(
        self,
        *,
        token: str,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE admin_sessions
                   SET revoked_at = now()
                 WHERE token_hash = %s
                   AND revoked_at IS NULL
                """,
                (token_digest(token),),
            )
            write_audit(
                cursor,
                actor=actor,
                action="auth.logout",
                target_type="admin_user",
                target_id=actor["id"],
                outcome="success",
                ip_address=ip_address,
                user_agent=user_agent,
            )

    # -- administrator management -------------------------------------------

    def create_admin(
        self,
        *,
        email: str,
        password: str,
        full_name: str = "",
        role: str = "admin",
        ip_address: str | None = None,
        user_agent: str | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_email = ensure_email(email)
        if role not in ROLES:
            raise ApiError(422, "validation_error", "Unsupported role.")
        problems = password_problems(password, normalized_email)
        if problems:
            raise ApiError(
                422,
                "weak_password",
                "The password does not meet the administrator policy.",
                {"problems": problems},
            )
        try:
            row = self.db.fetch_one(
                """
                INSERT INTO admin_users (email, full_name, password_hash, role)
                VALUES (%s, %s, %s, %s)
                RETURNING id, email, full_name, role, is_active, created_at
                """,
                (
                    normalized_email,
                    (full_name or "").strip()[:120],
                    hash_password(password),
                    role,
                ),
            )
        except Exception as exc:
            if is_unique_violation(exc):
                raise ApiError(
                    409, "email_taken", "An administrator with this email already exists."
                ) from exc
            raise

        if row is None:  # pragma: no cover
            raise ApiError(500, "internal_error", "Administrator could not be created.")

        audit(
            self.db,
            actor=actor,
            action="admin.create",
            target_type="admin_user",
            target_id=str(row["id"]),
            ip_address=ip_address,
            user_agent=user_agent,
            details={"email": normalized_email, "role": role},
        )
        return self._public_user(row)

    def verify_admin_password(self, admin_user_id: str, password: str) -> bool:
        """Re-authenticate an administrator for a high-risk operation.

        Returns ``False`` for an unknown, inactive, or mismatched account. The
        caller is responsible for recording a failed-attempt audit event.
        """
        if not admin_user_id or not password:
            return False
        row = self.db.fetch_one(
            """
            SELECT password_hash, is_active
              FROM admin_users
             WHERE id = %s
            """,
            (admin_user_id,),
        )
        if not row or not row.get("is_active"):
            return False
        return verify_password(password, row["password_hash"])

    def change_password(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Explicitly replace an administrator's password.

        This is the only operation allowed to change an existing password. It
        clears the lockout counters and revokes every active session for the
        account so a stolen cookie cannot survive the change.
        """
        normalized_email = ensure_email(email)
        problems = password_problems(password, normalized_email)
        if problems:
            raise ApiError(
                422,
                "weak_password",
                "The password does not meet the administrator policy.",
                {"problems": problems},
            )

        encoded = hash_password(password)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, email, full_name, role
                  FROM admin_users
                 WHERE lower(email) = lower(%s)
                 FOR UPDATE
                """,
                (normalized_email,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ApiError(
                    404,
                    "admin_not_found",
                    "No administrator exists with that email address.",
                )

            cursor.execute(
                """
                UPDATE admin_users
                   SET password_hash = %s,
                       failed_login_attempts = 0,
                       locked_until = NULL
                 WHERE id = %s
                """,
                (encoded, row[0]),
            )
            cursor.execute(
                """
                UPDATE admin_sessions
                   SET revoked_at = now()
                 WHERE admin_user_id = %s
                   AND revoked_at IS NULL
                """,
                (row[0],),
            )
            write_audit(
                cursor,
                actor=actor,
                action="admin.password_change",
                target_type="admin_user",
                target_id=str(row[0]),
                ip_address=ip_address,
                user_agent=user_agent,
                details={"email": normalized_email},
            )

        return self._public_user(
            {
                "id": row[0],
                "email": row[1],
                "full_name": row[2],
                "role": row[3],
            }
        )

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(user["id"]),
            "email": user["email"],
            "full_name": user.get("full_name") or "",
            "role": user["role"],
        }

    def _record_failure(
        self,
        actor: dict[str, Any] | None,
        email: str,
        ip_address: str | None,
        user_agent: str | None,
        reason: str,
    ) -> None:
        audit(
            self.db,
            actor=actor,
            action="auth.login",
            target_type="admin_user" if actor else None,
            target_id=actor["id"] if actor else None,
            outcome="failure",
            ip_address=ip_address,
            user_agent=user_agent,
            details={"reason": reason, "attempted_email": email},
        )
