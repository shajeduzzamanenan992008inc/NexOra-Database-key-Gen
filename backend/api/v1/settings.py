"""NexOra Phase 2 — System Settings REST API."""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from ...security.decorators import require_auth
from ...serialization import jsonable
from ...timezone import timezone_options, timezone_payload
from ...validation import require_json
from ..helpers import project_service, request_meta

bp = Blueprint("v1_settings", __name__)


@bp.get("/timezone")
@require_auth("owner", "admin", "viewer")
def get_timezone():
    """Return the effective global timezone and its current local time.

    Timestamps in every API response remain canonical UTC; this endpoint only
    describes how local input and display should be interpreted.
    """
    payload = timezone_payload(current_app.extensions["db"])
    return jsonify(jsonable(payload)), 200


@bp.get("/timezones")
@require_auth("owner", "admin", "viewer")
def list_timezones():
    """List installed IANA timezones with their current UTC offsets."""
    return (
        jsonify(
            {
                "timezones": jsonable(timezone_options()),
                "default_timezone": "UTC",
            }
        ),
        200,
    )


@bp.get("/settings")
@require_auth("owner", "admin", "viewer")
def get_settings():
    """Get system runtime settings."""
    settings = project_service().get_system_settings()
    return jsonify(jsonable(settings)), 200


@bp.patch("/settings")
@require_auth("owner")
def update_settings():
    """Update runtime settings (Owner only). Critical secrets cannot be modified here."""
    payload = require_json(request)
    ip, agent = request_meta()
    result = project_service().update_system_settings(
        payload,
        actor_id=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200
