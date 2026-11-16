"""License key endpoints."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request

from ..errors import ValidationError
from ..security.decorators import require_auth
from ..security.keys import KeyFormatError, KeySpec
from ..serialization import jsonable
from ..services.audit import audit
from ..services.license_service import (
    ADMIN_SETTABLE_STATUSES,
    SORT_COLUMNS,
    STATUSES,
)
from ..validation import Payload, require_json
from .helpers import (
    license_service,
    optional_datetime,
    pagination_args,
    request_meta,
    settings,
)

bp = Blueprint("keys", __name__, url_prefix="/api/keys")


def _spec_from(payload: Payload) -> KeySpec:
    prefix = payload.string(
        "prefix",
        default="NXR",
        min_length=0,
        max_length=12,
        pattern=r"^[A-Z0-9]{0,12}$",
        pattern_message="Use 0-12 letters or digits.",
        upper=True,
        allow_null=True,
    )
    suffix = payload.string(
        "suffix",
        default="",
        min_length=0,
        max_length=12,
        pattern=r"^[A-Z0-9]{0,12}$",
        pattern_message="Use 0-12 letters or digits.",
        upper=True,
        allow_null=True,
    )
    groups = payload.integer("groups", default=4, minimum=1, maximum=8)
    group_length = payload.integer(
        "group_length", default=4, minimum=3, maximum=10
    )
    separator = payload.enum("separator", allowed={"-", "_", ""}, default="-")
    try:
        return KeySpec(
            prefix=prefix or "",
            suffix=suffix or "",
            groups=groups or 4,
            group_length=group_length or 4,
            separator=separator if separator is not None else "-",
        )
    except KeyFormatError as exc:
        raise ValidationError({"format": str(exc)}) from exc


def _metadata_from(payload: Payload) -> dict[str, Any]:
    if not payload.has("metadata") or payload.data.get("metadata") is None:
        return {}
    value = payload.data["metadata"]
    if not isinstance(value, dict):
        payload.error("metadata", "Must be a JSON object.")
        return {}
    if len(value) > 20:
        payload.error("metadata", "Use at most 20 metadata keys.")
    return value


def _generate(payload: Payload, *, quantity: int) -> dict[str, Any]:
    spec = _spec_from(payload)
    max_activations = payload.integer(
        "max_activations", default=1, minimum=1, maximum=10000
    )
    expires_at = payload.datetime_utc("expires_at")
    label = payload.string("label", default="", max_length=80)
    metadata = _metadata_from(payload)
    payload.finish()

    ip, agent = request_meta()
    return license_service().generate(
        actor=g.actor,
        spec=spec,
        quantity=quantity,
        max_activations=max_activations or 1,
        expires_at=expires_at,
        label=label or "",
        metadata=metadata,
        ip_address=ip,
        user_agent=agent,
    )


@bp.post("/generate")
@require_auth("owner", "admin")
def generate():
    payload = Payload(require_json(request))
    quantity = payload.integer(
        "quantity", default=1, minimum=1, maximum=settings().max_bulk_keys
    )
    result = _generate(payload, quantity=quantity or 1)
    return jsonify(jsonable(result)), 201


@bp.post("/bulk-generate")
@require_auth("owner", "admin")
def bulk_generate():
    payload = Payload(require_json(request))
    quantity = payload.integer(
        "quantity", required=True, minimum=2, maximum=settings().max_bulk_keys
    )
    result = _generate(payload, quantity=quantity or 2)
    return jsonify(jsonable(result)), 201


@bp.get("")
@require_auth("owner", "admin", "viewer")
def list_keys():
    page, page_size = pagination_args()
    status = request.args.get("status") or None
    if status is not None and status not in STATUSES:
        raise ValidationError({"status": "Unsupported status filter."})
    sort = request.args.get("sort", "created_at")
    if sort not in SORT_COLUMNS:
        raise ValidationError({"sort": "Unsupported sort field."})
    order = (request.args.get("order") or "desc").lower()
    if order not in {"asc", "desc"}:
        raise ValidationError({"order": "Must be asc or desc."})
    search = (request.args.get("search") or "").strip()[:200] or None

    result = license_service().list_keys(
        page=page,
        page_size=page_size,
        status=status,
        search=search,
        sort=sort,
        order=order,
        created_after=optional_datetime("created_after"),
        created_before=optional_datetime("created_before"),
        expires_before=optional_datetime("expires_before"),
    )
    return jsonify(jsonable(result)), 200


def _csv_safe(value: Any) -> str:
    if value is None:
        return ""
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


@bp.get("/export")
@require_auth("owner", "admin", "viewer")
def export_keys():
    status = request.args.get("status") or None
    if status is not None and status not in STATUSES:
        raise ValidationError({"status": "Unsupported status filter."})
    search = (request.args.get("search") or "").strip()[:200] or None

    rows = license_service().export_rows(
        status=status,
        search=search,
        created_after=optional_datetime("created_after"),
        created_before=optional_datetime("created_before"),
        expires_before=optional_datetime("expires_before"),
    )
    service_meta = request_meta()
    audit(
        current_app.extensions["db"],
        actor=g.actor,
        action="key.export",
        target_type="license_keys",
        outcome="success",
        ip_address=service_meta[0],
        user_agent=service_meta[1],
        details={
            "rows": len(rows),
            "status": status,
            "search": search,
        },
    )

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "key_hint",
            "key_format",
            "label",
            "status",
            "max_activations",
            "activation_count",
            "expires_at",
            "created_at",
            "revoked_at",
            "revoked_reason",
            "batch_id",
            "created_by_email",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                _csv_safe(row.get("key_hint")),
                _csv_safe(row.get("key_format")),
                _csv_safe(row.get("label")),
                _csv_safe(row.get("status")),
                _csv_safe(row.get("max_activations")),
                _csv_safe(row.get("activation_count")),
                _csv_safe(row.get("expires_at")),
                _csv_safe(row.get("created_at")),
                _csv_safe(row.get("revoked_at")),
                _csv_safe(row.get("revoked_reason")),
                _csv_safe(row.get("batch_id")),
                _csv_safe(row.get("created_by_email")),
            ]
        )

    filename = (
        "nexora-keys-"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + ".csv"
    )
    response = Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/<uuid:key_id>")
@require_auth("owner", "admin", "viewer")
def key_detail(key_id):
    return jsonify(jsonable(license_service().get_key(str(key_id)))), 200


@bp.patch("/<uuid:key_id>")
@require_auth("owner", "admin")
def update_key(key_id):
    payload = Payload(require_json(request))
    payload.reject_unknown(
        {"status", "max_activations", "expires_at", "label"}
    )
    changes: dict[str, Any] = {}
    if payload.has("status"):
        changes["status"] = payload.enum(
            "status", allowed=ADMIN_SETTABLE_STATUSES, required=True
        )
    if payload.has("max_activations"):
        changes["max_activations"] = payload.integer(
            "max_activations", required=True, minimum=1, maximum=10000
        )
    if payload.has("expires_at"):
        changes["expires_at"] = payload.datetime_utc(
            "expires_at", allow_null=True
        )
    if payload.has("label"):
        changes["label"] = payload.string("label", max_length=80) or ""
    payload.finish()
    if not changes:
        raise ValidationError({"_body": "Provide at least one field to update."})

    ip, agent = request_meta()
    result = license_service().update(
        key_id=str(key_id),
        changes=changes,
        actor=g.actor,
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


@bp.delete("/<uuid:key_id>")
@require_auth("owner")
def delete_key(key_id):
    ip, agent = request_meta()
    result = license_service().delete(
        key_id=str(key_id),
        actor=g.actor,
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


@bp.post("/activate")
def activate():
    payload = Payload(require_json(request))
    raw_key = payload.string(
        "key", required=True, min_length=8, max_length=96, strip=True
    )
    device_id = payload.string(
        "device_id", required=True, min_length=4, max_length=200, strip=True
    )
    account_id = payload.string(
        "account_id",
        default="",
        min_length=0,
        max_length=200,
        strip=True,
        allow_null=True,
    )
    payload.finish()

    ip, agent = request_meta()
    result = license_service().activate(
        raw_key=raw_key or "",
        device_id=device_id or "",
        account_id=account_id or None,
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


@bp.post("/verify")
def verify():
    payload = Payload(require_json(request))
    raw_key = payload.string(
        "key", required=True, min_length=8, max_length=96, strip=True
    )
    payload.finish()
    return jsonify(jsonable(license_service().verify(raw_key or ""))), 200


@bp.post("/revoke")
@require_auth("owner", "admin")
def revoke():
    payload = Payload(require_json(request))
    key_id = payload.uuid("key_id", required=True)
    reason = payload.string(
        "reason", required=True, min_length=3, max_length=500
    )
    payload.finish()

    ip, agent = request_meta()
    result = license_service().revoke(
        key_id=key_id or "",
        reason=reason or "",
        actor=g.actor,
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


@bp.post("/deactivate")
@require_auth("owner", "admin")
def deactivate():
    payload = Payload(require_json(request))
    key_id = payload.uuid("key_id", required=True)
    activation_id = payload.uuid("activation_id", required=False)
    reason = payload.string(
        "reason", required=True, min_length=3, max_length=500
    )
    payload.finish()

    ip, agent = request_meta()
    result = license_service().deactivate(
        key_id=key_id or "",
        activation_id=activation_id,
        reason=reason or "",
        actor=g.actor,
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200

