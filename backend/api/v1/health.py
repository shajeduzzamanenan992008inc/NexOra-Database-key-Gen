"""Version 1 Health API."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

bp = Blueprint("v1_health", __name__)


@bp.get("/health")
def health():
    db = current_app.extensions["db"]
    database_ok = db.ping()
    settings = current_app.extensions["settings"]

    return (
        jsonify(
            {
                "status": "ok" if database_ok else "degraded",
                "database": "connected" if database_ok else "unavailable",
                "version": settings.version,
                "api_version": "v1",
                "service": "nexora-admin-backend",
            }
        ),
        200 if database_ok else 503,
    )
