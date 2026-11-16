"""Log endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..errors import ValidationError
from ..security.decorators import require_auth
from ..serialization import jsonable
from .helpers import license_service, pagination_args

bp = Blueprint("logs", __name__, url_prefix="/api/logs")

KINDS = ("generation", "activations", "audit")


@bp.get("")
@require_auth("owner", "admin", "viewer")
def logs():
    page, page_size = pagination_args()
    kind = request.args.get("kind", "generation")
    if kind not in KINDS:
        raise ValidationError({"kind": "Must be generation, activations or audit."})
    action = (request.args.get("action") or "").strip()[:80] or None
    outcome = request.args.get("outcome") or None
    if outcome is not None and outcome not in {"success", "failure"}:
        raise ValidationError({"outcome": "Must be success or failure."})

    result = license_service().logs(
        kind=kind,
        page=page,
        page_size=page_size,
        action=action,
        outcome=outcome,
    )
    return jsonify(jsonable(result)), 200
