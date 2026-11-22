"""NexOra OMEGA universal platform API.

All management routes require an authenticated admin session and a platform
role permission. The public access route accepts a universal key and enforces
project/key/type/permission/scope/device/quota policy server-side.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from ...errors import ApiError, ValidationError
from ...security.decorators import require_auth
from ...serialization import jsonable
from ...services.audit import audit
from ...validation import Payload, require_json
from ..helpers import (
    auth_service,
    pagination_args,
    request_meta,
    role_service,
    scheduler_service,
    settings,
    transfer_service,
    universal_service,
    webhook_service,
)

bp = Blueprint("v1_universal", __name__)

MANAGE_ROLES = ("owner", "admin", "viewer")


def _json(data: Any, status: int = 200):
    return jsonify(jsonable(data)), status


def _request_id() -> str | None:
    return getattr(g, "request_id", None)


def _service():
    return universal_service()


def _hooks():
    return webhook_service()


def _actor() -> dict[str, Any]:
    return g.actor


def _authorize(permission: str, project_id: str | None = None) -> None:
    _service().authorize(_actor(), permission, project_id=project_id)


def _authorize_scope(permission: str) -> list[str] | None:
    """Authorize a list endpoint and return project ids, or None for global access."""
    allowed = _service().project_ids_for_permission(_actor(), permission)
    if allowed is not None and not allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this action.")
    return allowed


def _resource_project(kind: str, resource_id: str) -> str | None:
    tables = {
        "key": "reader_keys",
        "device": "nexora_devices",
        "webhook": "nexora_webhooks",
        "key_type": "nexora_key_types",
    }
    table = tables.get(kind)
    if not table:
        raise ValueError("Unknown resource kind")
    row = _service().db.fetch_one(
        f"SELECT project_id FROM {table} WHERE id = %s", (resource_id,)
    )
    if not row:
        raise ApiError(404, f"{kind.upper()}_NOT_FOUND", f"{kind.title()} not found.")
    return str(row["project_id"]) if row.get("project_id") else None


def _query(name: str, default: str = "") -> str:
    return (request.args.get(name) or default).strip()


def _parse_datetime(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    payload = Payload({"value": value})
    parsed = payload.datetime_utc("value", required=False, allow_null=True)
    if payload.errors or parsed is None:
        raise ValidationError({field: "Use an ISO-8601 timestamp with timezone."})
    return parsed


# ---------------------------------------------------------------- overview
@bp.get("/universal/overview")
@require_auth(*MANAGE_ROLES)
def universal_overview():
    _authorize("analytics:read")
    return _json(_service().overview())


# ---------------------------------------------------------------- projects
@bp.get("/universal/projects")
@require_auth(*MANAGE_ROLES)
def universal_projects():
    allowed = _authorize_scope("project:read")
    page, size = pagination_args()
    return _json(
        _service().list_projects(
            page=page,
            page_size=size,
            status=_query("status") or None,
            search=_query("search") or None,
            project_type=_query("project_type") or None,
            sort=_query("sort", "created_at"),
            order=_query("order", "desc"),
            allowed_project_ids=allowed,
        )
    )


@bp.get("/universal/projects/<project_id>/statistics")
@require_auth(*MANAGE_ROLES)
def universal_project_statistics(project_id: str):
    _authorize("analytics:read", project_id)
    return _json(_service().project_stats(project_id))


@bp.get("/universal/projects/<project_id>/timeline")
@require_auth(*MANAGE_ROLES)
def universal_project_timeline(project_id: str):
    _authorize("project:read", project_id)
    return _json(_service().project_timeline(project_id))


@bp.patch("/universal/projects/<project_id>")
@require_auth(*MANAGE_ROLES)
def universal_project_update(project_id: str):
    _authorize("project:write", project_id)
    body = require_json(request)
    if "expires_at" in body:
        body["expires_at"] = _parse_datetime(body["expires_at"], "expires_at")
    ip, agent = request_meta()
    project = _service().update_project(
        project_id,
        changes=body,
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json({"project": project})


@bp.post("/universal/projects/<project_id>/archive")
@require_auth(*MANAGE_ROLES)
def universal_project_archive(project_id: str):
    _authorize("project:write", project_id)
    ip, agent = request_meta()
    project = _service().archive_project(
        project_id,
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json({"project": project})


@bp.post("/universal/projects/<project_id>/clone")
@require_auth(*MANAGE_ROLES)
def universal_project_clone(project_id: str):
    _authorize("project:write", project_id)
    body = require_json(request)
    payload = Payload(body)
    name = payload.string("project_name", required=True, min_length=1, max_length=200)
    payload.finish()
    ip, agent = request_meta()
    result = _service().clone_project(
        project_id,
        new_name=name or "",
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json(result, 201)


def _bundle_upload():
    cfg = settings()
    limit = int(getattr(cfg, "project_import_max_bytes", 8 * 1024 * 1024))
    file = request.files.get("file")
    if file is not None:
        data = file.read(limit + 1)
        if len(data) > limit:
            raise ValidationError({"file": "The bundle exceeds the size limit."})
        return transfer_service().parse_upload(data, file.filename or "")
    body = require_json(request)
    bundle = body.get("bundle") if isinstance(body, dict) else None
    if isinstance(bundle, dict):
        return bundle
    raise ValidationError({"file": "Upload a .json or .zip project bundle."})


def _form_value(name: str, default: str = "") -> str:
    if request.form:
        return str(request.form.get(name) or default)
    body = request.get_json(silent=True) or {}
    value = body.get(name, default) if isinstance(body, dict) else default
    return str(value) if value is not None else default


@bp.get("/universal/projects/<project_id>/export/preview")
@require_auth(*MANAGE_ROLES)
def universal_project_export_preview(project_id: str):
    _authorize("project:export", project_id)
    return _json(transfer_service().export_preview(project_id))


@bp.get("/universal/projects/<project_id>/export")
@require_auth(*MANAGE_ROLES)
def universal_project_export(project_id: str):
    _authorize("project:export", project_id)
    service = transfer_service()
    export_format = _query("format", "json").lower()
    if export_format == "zip":
        data, filename = service.export_zip(project_id)
        return Response(
            data,
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    bundle = service.export_project(project_id)
    return _json(bundle)


@bp.post("/universal/projects/export")
@require_auth(*MANAGE_ROLES)
def universal_projects_export_bulk():
    _authorize("project:export")
    body = require_json(request)
    if not isinstance(body, dict):
        raise ValidationError({"body": "Must be an object."})
    export_format = str(body.get("format") or "zip").lower()
    if body.get("all"):
        rows = _service().list_projects(page=1, page_size=100)
        project_ids = [str(row["id"]) for row in rows.get("items", [])]
    else:
        raw = body.get("project_ids")
        if not isinstance(raw, list) or not raw:
            raise ValidationError({"project_ids": "Select at least one project."})
        project_ids = [str(value) for value in raw]
    service = transfer_service()
    if export_format == "json":
        return _json(
            {
                "format": "nexora-project-collection",
                "version": 1,
                "projects": [service.export_project(pid) for pid in project_ids[:50]],
                "secrets_exposed": False,
            }
        )
    data, filename = service.export_many_zip(project_ids)
    return Response(
        data,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@bp.post("/universal/projects/import/preview")
@require_auth(*MANAGE_ROLES)
def universal_project_import_preview():
    _authorize("project:import")
    bundle = _bundle_upload()
    mode = _form_value("mode", "keep_existing")
    project_name = _form_value("project_name", "").strip() or None
    validated = transfer_service().validate_bundle(
        bundle, mode=mode, project_name=project_name
    )
    return _json(
        {
            "bundle": {
                "format": bundle.get("format"),
                "version": bundle.get("version"),
                "exported_at": bundle.get("exported_at"),
            },
            "project": {
                "source_id": validated["project"]["source_id"],
                "project_name": validated["project"]["project_name"],
                "source_project_name": validated["project"]["source_project_name"],
                "project_type": validated["project"]["project_type"],
                "status": validated["project"]["status"],
            },
            "mode": validated["mode"],
            "action": validated["action"],
            "existing_project": validated["existing_project"],
            "counts": validated["counts"],
            "key_types": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "permissions": item["permissions"],
                    "scopes": item["scopes"],
                }
                for item in validated["key_types"]
            ],
            "warnings": validated["warnings"],
            "validation_errors": [],
            "secrets_exposed": False,
        }
    )


@bp.post("/universal/projects/import")
@require_auth(*MANAGE_ROLES)
def universal_project_import():
    _authorize("project:import")
    bundle = _bundle_upload()
    ip, agent = request_meta()
    result = transfer_service().import_bundle(
        bundle,
        mode=_form_value("mode", "keep_existing"),
        project_name=_form_value("project_name", "").strip() or None,
        confirm=_form_value("confirm", ""),
        replace_confirmed=_form_value("replace_confirmed", "").lower()
        in {"1", "true", "yes", "on"},
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json(result, 201)


@bp.delete("/universal/projects/<project_id>")
@require_auth(*MANAGE_ROLES)
def universal_project_delete(project_id: str):
    _authorize("project:delete", project_id)
    payload = Payload(require_json(request))
    confirm_name = payload.string(
        "confirm_project_name", required=True, min_length=1, max_length=200
    )
    password = payload.string(
        "password", required=True, min_length=1, max_length=128, strip=False
    )
    payload.finish()
    project = transfer_service()._project(project_id)
    if (confirm_name or "").strip() != str(project["project_name"]).strip():
        raise ValidationError(
            {"confirm_project_name": "Type the exact project name to confirm deletion."}
        )
    if not auth_service().verify_admin_password(_actor()["id"], password or ""):
        audit(
            auth_service().db,
            actor=_actor(),
            action="project.delete",
            target_type="project",
            target_id=project_id,
            outcome="failure",
            details={"reason": "reauth_failed", "surface": "universal"},
        )
        raise ApiError(
            403, "reauth_failed", "Password confirmation failed. Sign in again and retry."
        )
    ip, agent = request_meta()
    return _json(
        transfer_service().delete_project(
            project_id, actor=_actor(), ip_address=ip, user_agent=agent
        )
    )


@bp.get("/universal/projects/delete-preview")
@require_auth(*MANAGE_ROLES)
def universal_projects_delete_preview():
    _authorize("project:delete_all")
    if _actor().get("role") != "owner":
        raise ApiError(403, "owner_required", "Only an Owner can delete all projects.")
    return _json(transfer_service().delete_preview())


@bp.delete("/universal/projects")
@require_auth(*MANAGE_ROLES)
def universal_projects_delete_all():
    _authorize("project:delete_all")
    if _actor().get("role") != "owner":
        raise ApiError(403, "owner_required", "Only an Owner can delete all projects.")
    payload = Payload(require_json(request))
    confirmation = payload.string(
        "confirmation", required=True, min_length=1, max_length=64
    )
    password = payload.string(
        "password", required=True, min_length=1, max_length=128, strip=False
    )
    payload.finish()
    if confirmation != "DELETE ALL PROJECTS":
        raise ValidationError(
            {"confirmation": "Type DELETE ALL PROJECTS exactly to confirm."}
        )
    if not auth_service().verify_admin_password(_actor()["id"], password or ""):
        audit(
            auth_service().db,
            actor=_actor(),
            action="project.delete_all",
            target_type="project",
            outcome="failure",
            details={"reason": "reauth_failed", "surface": "universal"},
        )
        raise ApiError(
            403, "reauth_failed", "Password confirmation failed. Sign in again and retry."
        )
    ip, agent = request_meta()
    return _json(
        transfer_service().delete_all_projects(
            actor=_actor(), ip_address=ip, user_agent=agent
        )
    )


# --------------------------------------------------------------- key types
@bp.get("/universal/api-usage")
@require_auth(*MANAGE_ROLES)
def universal_api_usage():
    allowed = _authorize_scope("analytics:read")
    requested_project = _query("project_id") or None
    if allowed is not None and requested_project and requested_project not in allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this project.")
    page, size = pagination_args()
    return _json(
        _service().list_api_usage(
            page=page,
            page_size=size,
            project_id=requested_project,
            key_id=_query("key_id") or None,
            status=_query("status") or None,
            endpoint=_query("endpoint") or None,
            allowed_project_ids=allowed,
        )
    )


@bp.get("/universal/key-types")
@require_auth(*MANAGE_ROLES)
def universal_key_types():
    allowed = _authorize_scope("key:read")
    requested_project = _query("project_id") or None
    if allowed is not None and requested_project and requested_project not in allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this project.")
    return _json(
        _service().list_key_types(
            project_id=requested_project,
            include_inactive=_query("include_inactive").lower() == "true",
            allowed_project_ids=allowed,
        )
    )


@bp.post("/universal/key-types")
@require_auth(*MANAGE_ROLES)
def universal_key_type_create():
    body = require_json(request)
    project_id = str(body.get("project_id") or "").strip() or None
    _authorize("key:write", project_id)
    ip, agent = request_meta()
    result = _service().create_key_type(
        body,
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json({"key_type": result}, 201)


@bp.delete("/universal/key-types/<type_id>")
@require_auth(*MANAGE_ROLES)
def universal_key_type_delete(type_id: str):
    project_id = _resource_project("key_type", type_id)
    _authorize("key:write", project_id)
    ip, agent = request_meta()
    result = _service().delete_key_type(
        type_id,
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json(result)


@bp.patch("/universal/key-types/<type_id>")
@require_auth(*MANAGE_ROLES)
def universal_key_type_update(type_id: str):
    project_id = _resource_project("key_type", type_id)
    _authorize("key:write", project_id)
    ip, agent = request_meta()
    result = _service().update_key_type(
        type_id,
        require_json(request),
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json({"key_type": result})


# -------------------------------------------------------------------- keys
@bp.get("/universal/keys")
@require_auth(*MANAGE_ROLES)
def universal_keys():
    allowed = _authorize_scope("key:read")
    requested_project = _query("project_id") or None
    if allowed is not None and requested_project and requested_project not in allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this project.")
    page, size = pagination_args()
    return _json(
        _service().list_keys(
            page=page,
            page_size=size,
            project_id=requested_project,
            key_type_id=_query("key_type_id") or None,
            status=_query("status") or None,
            search=_query("search") or None,
            expiring=_query("expiring") or None,
            sort=_query("sort", "created_at"),
            order=_query("order", "desc"),
            allowed_project_ids=allowed,
        )
    )


@bp.post("/universal/keys")
@require_auth(*MANAGE_ROLES)
def universal_key_generate():
    body = require_json(request)
    _authorize("key:write", str(body.get("project_id") or "") or None)
    payload = Payload(body)
    project_id = payload.string("project_id", required=True, min_length=1, max_length=64)
    key_type_id = payload.string("key_type_id", required=True, min_length=1, max_length=50)
    label = payload.string("label", required=False, max_length=100, default="")
    notes = payload.string("notes", required=False, max_length=2000, default="")
    expiration = payload.string("expiration_preset", required=False, max_length=20, default="never")
    quantity = payload.integer("quantity", required=False, default=1, minimum=1, maximum=500)
    max_usage = None
    if body.get("max_usage") is not None:
        max_usage = payload.integer("max_usage", required=False, minimum=1)
    expires_at = payload.datetime_utc("expires_at", required=False, allow_null=True)
    batch_id = payload.string(
        "batch_id", required=False, min_length=36, max_length=36, default=""
    )
    payload.finish()
    ip, agent = request_meta()
    result = _service().generate_keys(
        project_id=project_id or "",
        key_type_id=key_type_id or "",
        quantity=quantity or 1,
        label=label or "",
        expiration_preset=expiration or "never",
        expires_at=expires_at,
        batch_id=batch_id or None,
        permissions=body.get("permissions"),
        scopes=body.get("scopes"),
        tags=body.get("tags"),
        notes=notes or "",
        metadata=body.get("metadata"),
        max_usage=max_usage,
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json(result, 201)


@bp.get("/universal/keys/export")
@require_auth(*MANAGE_ROLES)
def universal_key_export():
    _authorize("export:read")
    rows = _service().export_keys(
        project_id=_query("project_id") or None,
        key_type_id=_query("key_type_id") or None,
        status=_query("status") or None,
    )
    export_format = _query("format", "json").lower()
    if export_format == "csv":
        output = io.StringIO()
        columns = [
            "id", "project_id", "project_name", "key_type_id", "key_type_name",
            "label", "key_hint", "fingerprint", "status", "permissions", "scopes",
            "tags", "expires_at", "created_at", "usage_count",
        ]
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            safe = dict(row)
            for field in ("permissions", "scopes", "tags"):
                safe[field] = json.dumps(safe.get(field), default=str)
            writer.writerow(safe)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=nexora-keys.csv",
                "Cache-Control": "no-store",
            },
        )
    return _json({"exported": len(rows), "keys": rows, "secrets_exposed": False})


@bp.post("/universal/keys/import/preview")
@require_auth(*MANAGE_ROLES)
def universal_key_import_preview():
    body = require_json(request)
    _authorize("import:write", str(body.get("project_id") or "") or None)
    return _json(
        _service().import_preview(
            project_id=str(body.get("project_id") or ""),
            key_type_id=str(body.get("key_type_id") or ""),
            rows=body.get("keys"),
        )
    )


@bp.post("/universal/keys/import")
@require_auth(*MANAGE_ROLES)
def universal_key_import():
    body = require_json(request)
    _authorize("import:write", str(body.get("project_id") or "") or None)
    ip, agent = request_meta()
    return _json(
        _service().import_keys(
            project_id=str(body.get("project_id") or ""),
            key_type_id=str(body.get("key_type_id") or ""),
            rows=body.get("keys"),
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        ),
        201,
    )


@bp.get("/universal/keys/<key_id>")
@require_auth(*MANAGE_ROLES)
def universal_key_detail(key_id: str):
    _authorize("key:read", _resource_project("key", key_id))
    return _json(_service().get_key(key_id))


@bp.patch("/universal/keys/<key_id>")
@require_auth(*MANAGE_ROLES)
def universal_key_update(key_id: str):
    _authorize("key:write", _resource_project("key", key_id))
    body = require_json(request)
    if "expires_at" in body:
        body["expires_at"] = _parse_datetime(body["expires_at"], "expires_at")
    ip, agent = request_meta()
    return _json(
        _service().update_key(
            key_id,
            body,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/keys/<key_id>/activate")
@require_auth(*MANAGE_ROLES)
def universal_key_activate(key_id: str):
    _authorize("key:write", _resource_project("key", key_id))
    ip, agent = request_meta()
    return _json(
        _service().activate_key(
            key_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/keys/<key_id>/deactivate")
@require_auth(*MANAGE_ROLES)
def universal_key_deactivate(key_id: str):
    _authorize("key:write", _resource_project("key", key_id))
    body = require_json(request)
    reason = Payload(body).string("reason", required=False, max_length=500, default="")
    ip, agent = request_meta()
    return _json(
        _service().deactivate_key(
            key_id,
            reason=reason or "",
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/keys/<key_id>/revoke")
@require_auth(*MANAGE_ROLES)
def universal_key_revoke(key_id: str):
    _authorize("key:write", _resource_project("key", key_id))
    payload = Payload(require_json(request))
    reason = payload.string("reason", required=True, min_length=1, max_length=500)
    payload.finish()
    ip, agent = request_meta()
    return _json(
        _service().revoke_key(
            key_id,
            reason=reason or "",
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/keys/<key_id>/regenerate")
@require_auth(*MANAGE_ROLES)
def universal_key_regenerate(key_id: str):
    _authorize("key:write", _resource_project("key", key_id))
    ip, agent = request_meta()
    return _json(
        _service().regenerate_key(
            key_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


# ----------------------------------------------------------------- devices
@bp.get("/universal/devices")
@require_auth(*MANAGE_ROLES)
def universal_devices():
    allowed = _authorize_scope("device:read")
    requested_project = _query("project_id") or None
    if allowed is not None and requested_project and requested_project not in allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this project.")
    page, size = pagination_args()
    return _json(
        _service().list_devices(
            page=page,
            page_size=size,
            project_id=requested_project,
            key_id=_query("key_id") or None,
            status=_query("status") or None,
            search=_query("search") or None,
            allowed_project_ids=allowed,
        )
    )


@bp.post("/universal/devices/<device_id>/revoke")
@require_auth(*MANAGE_ROLES)
def universal_device_revoke(device_id: str):
    _authorize("device:write", _resource_project("device", device_id))
    payload = Payload(require_json(request))
    reason = payload.string("reason", required=False, max_length=500, default="")
    ip, agent = request_meta()
    return _json(
        _service().revoke_device(
            device_id,
            reason=reason or "",
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/devices/<device_id>/reset")
@require_auth(*MANAGE_ROLES)
def universal_device_reset(device_id: str):
    _authorize("device:write", _resource_project("device", device_id))
    ip, agent = request_meta()
    return _json(
        _service().reset_device(
            device_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


# ---------------------------------------------------------------- webhooks
@bp.get("/universal/webhooks")
@require_auth(*MANAGE_ROLES)
def universal_webhooks():
    allowed = _authorize_scope("webhook:read")
    requested_project = _query("project_id") or None
    if allowed is not None and requested_project and requested_project not in allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this project.")
    return _json(
        _hooks().list_webhooks(
            requested_project, allowed_project_ids=allowed
        )
    )


@bp.post("/universal/webhooks")
@require_auth(*MANAGE_ROLES)
def universal_webhook_create():
    body = require_json(request)
    _authorize("webhook:write", str(body.get("project_id") or "") or None)
    ip, agent = request_meta()
    result = _hooks().create_webhook(
        project_id=str(body.get("project_id") or ""),
        name=str(body.get("name") or ""),
        url=str(body.get("url") or ""),
        events=body.get("events"),
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json(result, 201)


@bp.patch("/universal/webhooks/<webhook_id>")
@require_auth(*MANAGE_ROLES)
def universal_webhook_update(webhook_id: str):
    _authorize("webhook:write", _resource_project("webhook", webhook_id))
    ip, agent = request_meta()
    result = _hooks().update_webhook(
        webhook_id,
        changes=require_json(request),
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json(result)


@bp.delete("/universal/webhooks/<webhook_id>")
@require_auth(*MANAGE_ROLES)
def universal_webhook_delete(webhook_id: str):
    _authorize("webhook:write", _resource_project("webhook", webhook_id))
    ip, agent = request_meta()
    return _json(
        _hooks().delete_webhook(
            webhook_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/webhooks/<webhook_id>/rotate-secret")
@require_auth(*MANAGE_ROLES)
def universal_webhook_rotate(webhook_id: str):
    _authorize("webhook:write", _resource_project("webhook", webhook_id))
    ip, agent = request_meta()
    return _json(
        _hooks().rotate_secret(
            webhook_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/webhooks/<webhook_id>/test")
@require_auth(*MANAGE_ROLES)
def universal_webhook_test(webhook_id: str):
    _authorize("webhook:write", _resource_project("webhook", webhook_id))
    ip, agent = request_meta()
    return _json(
        _hooks().test_webhook(
            webhook_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        ),
        202,
    )


@bp.get("/universal/webhooks/<webhook_id>/deliveries")
@require_auth(*MANAGE_ROLES)
def universal_webhook_deliveries(webhook_id: str):
    _authorize("webhook:read", _resource_project("webhook", webhook_id))
    return _json(_hooks().list_deliveries(webhook_id))


# ----------------------------------------------------------- notifications
@bp.get("/universal/notifications")
@require_auth(*MANAGE_ROLES)
def universal_notifications():
    _authorize("notification:read")
    return _json(
        _hooks().list_notifications(
            limit=int(request.args.get("limit", "50") or 50),
            unread_only=_query("unread").lower() == "true",
        )
    )


@bp.post("/universal/notifications/<notification_id>/read")
@require_auth(*MANAGE_ROLES)
def universal_notification_read(notification_id: str):
    _authorize("notification:write")
    return _json(_hooks().mark_notification(notification_id, read=True))


@bp.post("/universal/notifications/read-all")
@require_auth(*MANAGE_ROLES)
def universal_notifications_read_all():
    _authorize("notification:write")
    return _json(_hooks().mark_all_notifications_read())


@bp.get("/universal/notifications/preferences")
@require_auth(*MANAGE_ROLES)
def universal_notification_preferences():
    _authorize("notification:read")
    return _json(_hooks().get_preferences())


@bp.patch("/universal/notifications/preferences")
@require_auth(*MANAGE_ROLES)
def universal_notification_preferences_update():
    _authorize("notification:write")
    body = require_json(request)
    return _json(_hooks().update_preferences(body.get("preferences", body)))


# ---------------------------------------------------------------- search
@bp.get("/universal/search")
@require_auth(*MANAGE_ROLES)
def universal_search():
    _authorize("project:read")
    query = _query("q")
    try:
        limit = int(request.args.get("limit", "8") or 8)
    except ValueError:
        limit = 8
    return _json(_service().global_search(query, limit=limit))


# ------------------------------------------------------------- automation
@bp.post("/universal/automation/run")
@require_auth(*MANAGE_ROLES)
def universal_automation_run():
    _authorize("automation:run")
    return _json(_hooks().run_automation())


@bp.get("/universal/automation/runs")
@require_auth(*MANAGE_ROLES)
def universal_automation_runs():
    _authorize("automation:read")
    return _json(_hooks().list_automation_runs())


@bp.get("/universal/automation/scheduler")
@require_auth(*MANAGE_ROLES)
def universal_automation_scheduler():
    _authorize("automation:read")
    return _json(scheduler_service().status())


# ------------------------------------------------- imported key replacement
@bp.get("/universal/imported-keys")
@require_auth(*MANAGE_ROLES)
def universal_imported_keys():
    allowed = _authorize_scope("key:read")
    requested_project = _query("project_id") or None
    if allowed is not None and requested_project and requested_project not in allowed:
        raise ApiError(403, "forbidden", "Your role does not permit this project.")
    page, size = pagination_args()
    return _json(
        _service().list_imported_keys(
            page=page,
            page_size=size,
            project_id=requested_project,
            status=_query("status") or None,
            search=_query("search") or None,
            allowed_project_ids=allowed,
        )
    )


@bp.post("/universal/imported-keys/replacement-preview")
@require_auth(*MANAGE_ROLES)
def universal_imported_keys_replacement_preview():
    body = require_json(request)
    return _json(
        _service().replacement_preview(
            body.get("record_ids"),
            allow_duplicate=bool(body.get("allow_duplicate")),
            actor=_actor(),
        )
    )


@bp.post("/universal/imported-keys/replacements")
@require_auth(*MANAGE_ROLES)
def universal_imported_keys_replacements():
    body = require_json(request)
    if body.get("confirm") is not True:
        raise ValidationError({"confirm": "Set confirm to true after reviewing the preview."})
    ip, agent = request_meta()
    return _json(
        _service().generate_replacements(
            body.get("record_ids"),
            allow_duplicate=bool(body.get("allow_duplicate")),
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        ),
        201,
    )


@bp.get("/universal/imported-keys/<record_id>/history")
@require_auth(*MANAGE_ROLES)
def universal_imported_key_history(record_id: str):
    return _json(_service().replacement_history(record_id, actor=_actor()))


# ------------------------------------------------------------ roles/admins
@bp.get("/universal/projects/<project_id>/roles")
@require_auth(*MANAGE_ROLES)
def universal_project_roles(project_id: str):
    _authorize("admin:read", project_id)
    data = role_service().list_roles()
    roles = [
        role for role in data["roles"] if role.get("project_id") == project_id
    ]
    return _json({"project_id": project_id, "roles": roles})


@bp.get("/universal/projects/<project_id>/role-assignments")
@require_auth(*MANAGE_ROLES)
def universal_project_role_assignments(project_id: str):
    _authorize("admin:read", project_id)
    return _json(role_service().list_project_assignments(project_id))


@bp.post("/universal/projects/<project_id>/role-assignments")
@require_auth(*MANAGE_ROLES)
def universal_project_role_assignment_create(project_id: str):
    _authorize("admin:roles", project_id)
    ip, agent = request_meta()
    return _json(
        role_service().assign_project_role(
            project_id,
            require_json(request),
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        ),
        201,
    )


@bp.delete("/universal/projects/<project_id>/role-assignments/<assignment_id>")
@require_auth(*MANAGE_ROLES)
def universal_project_role_assignment_delete(project_id: str, assignment_id: str):
    _authorize("admin:roles", project_id)
    ip, agent = request_meta()
    return _json(
        role_service().remove_project_assignment(
            project_id,
            assignment_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.get("/universal/roles")
@require_auth(*MANAGE_ROLES)
def universal_roles():
    _authorize("admin:read")
    return _json(role_service().list_roles())


@bp.post("/universal/roles")
@require_auth(*MANAGE_ROLES)
def universal_role_create():
    _authorize("admin:roles")
    ip, agent = request_meta()
    return _json(
        role_service().create_role(
            require_json(request),
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        ),
        201,
    )


@bp.patch("/universal/roles/<role_id>")
@require_auth(*MANAGE_ROLES)
def universal_role_update(role_id: str):
    _authorize("admin:roles")
    ip, agent = request_meta()
    return _json(
        role_service().update_role(
            role_id,
            require_json(request),
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/roles/<role_id>/clone")
@require_auth(*MANAGE_ROLES)
def universal_role_clone(role_id: str):
    _authorize("admin:roles")
    ip, agent = request_meta()
    return _json(
        role_service().clone_role(
            role_id,
            require_json(request),
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        ),
        201,
    )


@bp.delete("/universal/roles/<role_id>")
@require_auth(*MANAGE_ROLES)
def universal_role_delete(role_id: str):
    _authorize("admin:roles")
    ip, agent = request_meta()
    return _json(
        role_service().delete_role(
            role_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.get("/universal/admins")
@require_auth(*MANAGE_ROLES)
def universal_admins():
    _authorize("admin:read")
    return _json(_service().list_admins())


@bp.patch("/universal/admins/<admin_id>/role")
@require_auth(*MANAGE_ROLES)
def universal_admin_role(admin_id: str):
    _authorize("admin:roles")
    body = require_json(request)
    platform_role_id = str(body.get("platform_role_id") or "").strip() or None
    body["platform_role"] = str(
        body.get("platform_role") or ("custom" if platform_role_id else "")
    ).strip()
    payload = Payload(body)
    platform_role = payload.string(
        "platform_role", required=True, min_length=1, max_length=30
    )
    payload.finish()
    ip, agent = request_meta()
    return _json(
        _service().set_admin_role(
            admin_id,
            platform_role=platform_role or "",
            platform_permissions=body.get("platform_permissions"),
            platform_role_id=platform_role_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.post("/universal/admins/<admin_id>/sessions/revoke")
@require_auth(*MANAGE_ROLES)
def universal_admin_sessions(admin_id: str):
    _authorize("admin:sessions")
    ip, agent = request_meta()
    return _json(
        _service().revoke_admin_sessions(
            admin_id,
            actor=_actor(),
            ip_address=ip,
            user_agent=agent,
            request_id=_request_id(),
        )
    )


@bp.get("/universal/export")
@require_auth(*MANAGE_ROLES)
def universal_export_inventory():
    _authorize("export:read")
    service = _service()
    return _json(
        {
            "format": "nexora-universal-v1",
            "exported_at": None,
            "projects": service.list_projects(page=1, page_size=100),
            "key_types": service.list_key_types(include_inactive=True),
            "secrets_exposed": False,
        }
    )


# --------------------------------------------------------- public access API
@bp.post("/access/authenticate")
def universal_access_authenticate():
    body = require_json(request)
    payload = Payload(body)
    key = payload.string("key", required=True, min_length=12, max_length=200, strip=False)
    permission = payload.string(
        "permission", required=False, max_length=50, default=""
    )
    project_id = payload.string("project_id", required=False, max_length=64, default="")
    device_id = payload.string("device_id", required=False, max_length=200, default="")
    device_name = payload.string("device_name", required=False, max_length=120, default="")
    device_model = payload.string("device_model", required=False, max_length=120, default="")
    platform = payload.string("platform", required=False, max_length=80, default="")
    payload.finish()
    ip, _agent = request_meta()
    result = _service().authenticate_key(
        plaintext_key=key or "",
        permission=permission or None,
        project_id=project_id or None,
        device_id=device_id or None,
        device_name=device_name or "",
        device_model=device_model or "",
        platform=platform or "",
        endpoint=request.path,
        ip_address=ip,
        request_id=_request_id(),
    )
    return _json(result)


# ----------------------------------------------------------------- OpenAPI
@bp.get("/universal/openapi.json")
@require_auth(*MANAGE_ROLES)
def universal_openapi():
    _authorize("admin:read")
    cfg = settings()
    paths = {}
    for rule in current_url_rules():
        if "/api/v1/" not in rule:
            continue
        path = rule.split("/api/v1", 1)[1] or "/"
        paths.setdefault(path, {})["x-nexora"] = {"server_side": True}
    return _json(
        {
            "openapi": "3.0.3",
            "info": {
                "title": "NexOra OMEGA Universal Key API",
                "version": getattr(cfg, "api_version", "v1"),
                "description": "Management endpoints require an authenticated session and platform role. Access authentication accepts a universal key.",
            },
            "servers": [{"url": "/api/v1"}],
            "paths": paths,
            "components": {
                "securitySchemes": {
                    "sessionCookie": {"type": "apiKey", "in": "cookie", "name": cfg.session_cookie_name}
                }
            },
        }
    )


def current_url_rules() -> list[str]:
    from flask import current_app

    return sorted(
        str(rule)
        for rule in current_app.url_map.iter_rules()
        if str(rule).startswith("/api/v1/")
    )
