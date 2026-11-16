"""NexOra Phase 2 — Audit Logs REST API."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...security.decorators import require_auth
from ...serialization import jsonable
from ..helpers import optional_datetime, pagination_args, project_service

bp = Blueprint("v1_audit", __name__)


@bp.get("/audit-logs")
@require_auth("owner", "admin", "viewer")
def list_audit_logs():
    """List administrative audit log entries with filters and pagination."""
    page, page_size = pagination_args()
    action = request.args.get("action", None)
    target_type = request.args.get("target_type", None)
    outcome = request.args.get("outcome", None)
    search = request.args.get("search", None)
    project_id = request.args.get("project_id", None)
    date_from = optional_datetime("date_from")
    date_to = optional_datetime("date_to")

    result = project_service().get_audit_logs(
        page=page,
        page_size=page_size,
        action=action,
        target_type=target_type,
        outcome=outcome,
        search=search,
        date_from=date_from,
        date_to=date_to,
        project_id=project_id,
    )
    return jsonify(jsonable(result)), 200
