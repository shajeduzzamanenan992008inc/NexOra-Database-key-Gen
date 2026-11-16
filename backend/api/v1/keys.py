"""NexOra Phase 2 — Reader Key Management and Listing REST APIs."""

from __future__ import annotations

import csv
import io
from flask import Blueprint, Response, jsonify, request

from ...security.decorators import require_auth
from ...serialization import jsonable
from ..helpers import pagination_args, project_service

bp = Blueprint("v1_keys", __name__)


@bp.get("/keys")
@require_auth("owner", "admin", "viewer")
def list_keys():
    """List reader keys with filtering, search, and pagination."""
    page, page_size = pagination_args()
    project_id = request.args.get("project_id", None)
    status = request.args.get("status", None)
    search = request.args.get("search", None)
    expiry_filter = request.args.get("expiry_filter", None)
    sort = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc")

    result = project_service().list_keys(
        project_id=project_id,
        status=status,
        search=search,
        expiry_filter=expiry_filter,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
    )
    return jsonify(jsonable(result)), 200


@bp.get("/keys/<key_id>")
@require_auth("owner", "admin", "viewer")
def get_key_detail(key_id: str):
    """Get reader key detail (masked hint only, never plaintext)."""
    key = project_service().get_key(key_id)
    return jsonify({"key": jsonable(key)}), 200


@bp.get("/keys/export")
@require_auth("owner", "admin")
def export_keys_csv():
    """Export filtered reader keys as CSV."""
    project_id = request.args.get("project_id", None)
    status = request.args.get("status", None)
    search = request.args.get("search", None)
    expiry_filter = request.args.get("expiry_filter", None)

    # Fetch up to 5000 keys for export
    result = project_service().list_keys(
        project_id=project_id,
        status=status,
        search=search,
        expiry_filter=expiry_filter,
        page=1,
        page_size=100,  # Or loop or large limit
    )
    items = result.get("items", [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Key ID",
        "Project ID",
        "Project Name",
        "Key Hint",
        "Label",
        "Status",
        "Is Permanent",
        "Expires At",
        "Created At",
        "Used At",
        "Revoked At",
        "Revoked Reason",
    ])
    for item in items:
        writer.writerow([
            item.get("id"),
            item.get("project_id"),
            item.get("project_name"),
            item.get("key_hint"),
            item.get("label"),
            item.get("status"),
            item.get("is_permanent"),
            item.get("expires_at"),
            item.get("created_at"),
            item.get("used_at"),
            item.get("revoked_at"),
            item.get("revoked_reason"),
        ])

    csv_data = output.getvalue()
    response = Response(csv_data, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=nexora-reader-keys.csv"
    response.headers["Cache-Control"] = "no-store"
    return response
