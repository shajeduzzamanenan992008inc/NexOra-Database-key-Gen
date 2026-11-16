"""Dashboard endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..security.decorators import require_auth
from ..serialization import jsonable
from .helpers import license_service

bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


@bp.get("/stats")
@require_auth("owner", "admin", "viewer")
def stats():
    return jsonify(jsonable(license_service().stats())), 200
