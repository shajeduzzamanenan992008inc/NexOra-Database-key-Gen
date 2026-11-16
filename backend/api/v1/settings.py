"""NexOra Phase 2 — System Settings REST API."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ...security.decorators import require_auth
from ...serialization import jsonable
from ...validation import require_json
from ..helpers import project_service, request_meta

bp = Blueprint("v1_settings", __name__)


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
