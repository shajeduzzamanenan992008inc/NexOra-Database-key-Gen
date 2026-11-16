"""NexOra Phase 1 & 2 — Admin Installation Management and Revocation."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ...security.decorators import require_auth
from ...serialization import jsonable
from ...validation import Payload, require_json
from ..helpers import pagination_args, project_service, request_meta

bp = Blueprint("v1_installations", __name__)


@bp.get("/installations")
@bp.get("/admin/installations")
@require_auth("owner", "admin", "viewer")
def list_installations():
    """List reader installations with search and pagination."""
    page, page_size = pagination_args()
    project_id = request.args.get("project_id", None)
    status = request.args.get("status", None)
    search = request.args.get("search", None)

    result = project_service().list_installations(
        project_id=project_id,
        status=status,
        search=search,
        page=page,
        page_size=page_size,
    )
    return jsonify(jsonable(result)), 200


@bp.get("/installations/<installation_id>")
@bp.get("/admin/installations/<installation_id>")
@require_auth("owner", "admin", "viewer")
def get_installation(installation_id: str):
    """Get reader installation details."""
    result = project_service().get_installation(installation_id)
    return jsonify({"installation": jsonable(result)}), 200


@bp.post("/installations/<installation_id>/revoke")
@bp.post("/admin/installations/<installation_id>/revoke")
@require_auth("owner", "admin")
def revoke_installation(installation_id: str):
    """Revoke an active Reader App installation (Admin only)."""
    payload = Payload(require_json(request))
    reason = payload.string("reason", required=True, min_length=1, max_length=500)
    payload.finish()

    ip, agent = request_meta()
    result = project_service().revoke_installation(
        installation_id,
        reason=reason or "",
        revoked_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200
