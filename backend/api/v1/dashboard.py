"""NexOra Phase 2 — Dashboard Overview and Stats REST API."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ...security.decorators import require_auth
from ...serialization import jsonable
from ..helpers import project_service

bp = Blueprint("v1_dashboard", __name__)


@bp.get("/dashboard/stats")
@require_auth("owner", "admin", "viewer")
def dashboard_stats():
    """Live dashboard overview metrics and system status (Admin only)."""
    stats = project_service().dashboard_stats()
    return jsonify(jsonable(stats)), 200
