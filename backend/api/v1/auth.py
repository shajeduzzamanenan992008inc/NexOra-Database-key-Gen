"""NexOra Phase 2 — Versioned Authentication API endpoints."""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from ...security.decorators import require_auth
from ...security.sessions import issue_csrf
from ...serialization import jsonable
from ...validation import Payload, require_json
from ..helpers import (
    attach_session_cookie,
    auth_service,
    clear_session_cookie,
    request_meta,
    settings,
)

bp = Blueprint("v1_auth", __name__)


@bp.post("/auth/login")
def login():
    """Sign in as super administrator."""
    payload = Payload(require_json(request))
    email = payload.email("email", required=True)
    password = payload.string(
        "password", required=True, min_length=1, max_length=128, strip=False
    )
    payload.finish()

    ip, agent = request_meta()
    result = auth_service().login(
        email=email or "",
        password=password or "",
        ip_address=ip,
        user_agent=agent,
    )
    session = result["session"]
    response = jsonify(
        {
            "user": jsonable(result["user"]),
            "csrf_token": session["csrf_token"],
            "expires_at": jsonable(session["expires_at"]),
        }
    )
    attach_session_cookie(
        response, session["token"], settings().session_ttl_seconds
    )
    return response, 200


@bp.post("/auth/logout")
@require_auth()
def logout():
    """Sign out and invalidate current server-side session."""
    ip, agent = request_meta()
    auth_service().logout(
        token=g.auth_token,
        actor=g.actor,
        ip_address=ip,
        user_agent=agent,
    )
    response = jsonify({"ok": True})
    clear_session_cookie(response)
    return response, 200


@bp.get("/auth/session")
@require_auth()
def session():
    """Validate active session and refresh CSRF token."""
    db = current_app.extensions["db"]
    csrf_token = issue_csrf(db, str(g.session["session_id"]))
    return (
        jsonify(
            {
                "user": jsonable(g.actor),
                "csrf_token": csrf_token,
                "expires_at": jsonable(g.session["expires_at"]),
            }
        ),
        200,
    )
