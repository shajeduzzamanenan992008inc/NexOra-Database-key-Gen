"""Authentication and role enforcement decorators."""

from __future__ import annotations

import hmac
from functools import wraps
from typing import Any, Callable, TypeVar

from flask import current_app, g, request

from ..db import Database
from ..errors import ApiError
from .sessions import find_session, token_digest

F = TypeVar("F", bound=Callable[..., Any])

ROLE_LEVELS = {"viewer": 1, "admin": 2, "owner": 3}


def _settings() -> Any:
    return current_app.extensions["settings"]


def _db() -> Database:
    return current_app.extensions["db"]


def _extract_token() -> tuple[str | None, bool]:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token, False
    cookie_token = request.cookies.get(_settings().session_cookie_name)
    if cookie_token:
        return cookie_token, True
    return None, False


def _csrf_valid(session: dict[str, Any]) -> bool:
    supplied = request.headers.get(_settings().csrf_header, "")
    if not supplied:
        return False
    return hmac.compare_digest(token_digest(supplied), session["csrf_hash"])


def require_auth(*roles: str, csrf: bool = True) -> Callable[[F], F]:
    """Require a valid session and, optionally, one of the supplied roles.

    CSRF is enforced for cookie-authenticated mutating requests. Bearer-token
    clients are not vulnerable to ambient-authority CSRF and are exempt, which
    keeps server-to-server administration possible.
    """

    unknown_roles = set(roles) - set(ROLE_LEVELS)
    if unknown_roles:
        raise ValueError(f"Unknown role(s): {sorted(unknown_roles)}")

    def decorator(function: F) -> F:
        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            token, via_cookie = _extract_token()
            if not token:
                raise ApiError(401, "not_authenticated", "Sign in to continue.")

            session = find_session(
                _db(),
                token,
                audience=getattr(_settings(), "session_audience", "keygen"),
            )
            if session is None:
                raise ApiError(
                    401, "session_expired", "Your session has expired. Sign in again."
                )

            if roles and session["role"] not in roles:
                raise ApiError(
                    403,
                    "forbidden",
                    "Your role does not permit this action.",
                )

            if (
                csrf
                and via_cookie
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and not _csrf_valid(session)
            ):
                raise ApiError(
                    403,
                    "csrf_failed",
                    "CSRF validation failed. Refresh the page and try again.",
                )

            g.session = session
            g.auth_token = token
            g.auth_via_cookie = via_cookie
            g.actor = {
                "id": str(session["user_id"]),
                "email": session["email"],
                "role": session["role"],
                "full_name": session["full_name"],
            }
            return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def require_role(minimum: str) -> Callable[[F], F]:
    """Require at least the given role level (viewer < admin < owner)."""
    if minimum not in ROLE_LEVELS:
        raise ValueError(f"Unknown role: {minimum}")
    allowed = tuple(
        role for role, level in ROLE_LEVELS.items() if level >= ROLE_LEVELS[minimum]
    )
    return require_auth(*allowed)
