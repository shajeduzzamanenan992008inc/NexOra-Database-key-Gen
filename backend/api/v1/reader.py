"""NexOra Phase 1 — Reader App Connection and Authentication REST APIs.

Provides backend endpoints for the separate Reader App:
- POST /api/v1/reader/activate : Initial one-time key redemption
- POST /api/v1/reader/heartbeat: Periodic heartbeat with device credential
- GET  /api/v1/reader/status   : Authorization verification with device credential
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...errors import ApiError
from ...serialization import jsonable
from ...validation import Payload, require_json
from ..helpers import project_service, request_meta

bp = Blueprint("v1_reader", __name__)


def _extract_device_credential() -> str:
    """Extract bearer device credential from Authorization header."""
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header:
        raise ApiError(
            401,
            "UNAUTHORIZED",
            "Authorization header is required (Bearer <device_credential>).",
        )
    parts = auth_header.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ApiError(
            401,
            "UNAUTHORIZED",
            "Malformed Authorization header. Format: Bearer <device_credential>",
        )
    return parts[1].strip()


@bp.post("/reader/activate")
def activate_reader():
    """Initial one-time key redemption by the Reader App.

    Submits:
    - project_name
    - database_url
    - reader_key
    - device_id (optional hardware/app instance identifier)
    """
    payload = Payload(require_json(request))
    project_name = payload.string("project_name", required=True, min_length=1, max_length=200)
    database_url = payload.string("database_url", required=True, min_length=1, max_length=500)
    reader_key = payload.string("reader_key", required=True, min_length=1, max_length=100)
    device_id = payload.string("device_id", required=False, max_length=200, default="")
    payload.finish()

    ip, agent = request_meta()
    result = project_service().activate_reader(
        project_name=project_name or "",
        database_url=database_url or "",
        reader_key_plaintext=reader_key or "",
        device_id=device_id or "",
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


@bp.post("/reader/heartbeat")
def reader_heartbeat():
    """Reader App periodic heartbeat using issued device credential."""
    credential = _extract_device_credential()
    result = project_service().record_heartbeat(credential)
    return jsonify(jsonable(result)), 200


@bp.get("/reader/status")
def reader_status():
    """Reader App status verification using issued device credential."""
    credential = _extract_device_credential()
    info = project_service().verify_reader_credential(credential)
    return (
        jsonify(
            {
                "status": "authorized",
                "project_id": str(info["project_id"]),
                "project_name": info["project_name"],
                "installation_id": str(info["installation_id"]),
            }
        ),
        200,
    )
