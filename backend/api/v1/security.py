"""NexOra Phase 2 — Security Center REST API."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ...security.decorators import require_auth
from ...serialization import jsonable
from ..helpers import project_service

bp = Blueprint("v1_security", __name__)


@bp.get("/security/status")
@require_auth("owner", "admin", "viewer")
def security_status():
    """Security Center posture and diagnostics (Zero secrets returned)."""
    status = project_service().get_security_status()
    return jsonify(jsonable(status)), 200
