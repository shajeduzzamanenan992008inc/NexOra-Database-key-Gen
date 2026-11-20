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
from ...validation import Payload, require_json
from ..helpers import (
    pagination_args,
    request_meta,
    settings,
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


def _authorize(permission: str) -> None:
    _service().authorize(_actor(), permission)


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
    _authorize("project:read")
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
        )
    )


@bp.get("/universal/projects/<project_id>/statistics")
@require_auth(*MANAGE_ROLES)
def universal_project_statistics(project_id: str):
    _authorize("analytics:read")
    return _json(_service().project_stats(project_id))


@bp.get("/universal/projects/<project_id>/timeline")
@require_auth(*MANAGE_ROLES)
def universal_project_timeline(project_id: str):
    _authorize("project:read")
    return _json(_service().project_timeline(project_id))


@bp.patch("/universal/projects/<project_id>")
@require_auth(*MANAGE_ROLES)
def universal_project_update(project_id: str):
    _authorize("project:write")
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
    _authorize("project:write")
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
    _authorize("project:write")
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


@bp.get("/universal/projects/<project_id>/export")
@require_auth(*MANAGE_ROLES)
def universal_project_export(project_id: str):
    _authorize("export:read")
    service = _service()
    stats = service.project_stats(project_id)
    types = service.list_key_types(project_id=project_id, include_inactive=True)
    return _json(
        {
            "format": "nexora-project-v1",
            "project": stats["project"],
            "statistics": stats,
            "key_types": [
                item
                for item in types["key_types"]
                if str(item.get("project_id") or "") == project_id
            ],
            "secrets_exposed": False,
        }
    )


# --------------------------------------------------------------- key types
@bp.get("/universal/api-usage")
@require_auth(*MANAGE_ROLES)
def universal_api_usage():
    _authorize("analytics:read")
    page, size = pagination_args()
    return _json(
        _service().list_api_usage(
            page=page,
            page_size=size,
            project_id=_query("project_id") or None,
            key_id=_query("key_id") or None,
            status=_query("status") or None,
            endpoint=_query("endpoint") or None,
        )
    )


@bp.get("/universal/key-types")
@require_auth(*MANAGE_ROLES)
def universal_key_types():
    _authorize("key:read")
    return _json(
        _service().list_key_types(
            project_id=_query("project_id") or None,
            include_inactive=_query("include_inactive").lower() == "true",
        )
    )


@bp.post("/universal/key-types")
@require_auth(*MANAGE_ROLES)
def universal_key_type_create():
    _authorize("key:write")
    ip, agent = request_meta()
    result = _service().create_key_type(
        require_json(request),
        actor=_actor(),
        ip_address=ip,
        user_agent=agent,
        request_id=_request_id(),
    )
    return _json({"key_type": result}, 201)


@bp.patch("/universal/key-types/<type_id>")
@require_auth(*MANAGE_ROLES)
def universal_key_type_update(type_id: str):
    _authorize("key:write")
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
    _authorize("key:read")
    page, size = pagination_args()
    return _json(
        _service().list_keys(
            page=page,
            page_size=size,
            project_id=_query("project_id") or None,
            key_type_id=_query("key_type_id") or None,
            status=_query("status") or None,
            search=_query("search") or None,
            expiring=_query("expiring") or None,
            sort=_query("sort", "created_at"),
            order=_query("order", "desc"),
        )
    )


@bp.post("/universal/keys")
@require_auth(*MANAGE_ROLES)
def universal_key_generate():
    _authorize("key:write")
    body = require_json(request)
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
    payload.finish()
    ip, agent = request_meta()
    result = _service().generate_keys(
        project_id=project_id or "",
        key_type_id=key_type_id or "",
        quantity=quantity or 1,
        label=label or "",
        expiration_preset=expiration or "never",
        expires_at=expires_at,
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
    _authorize("import:write")
    body = require_json(request)
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
    _authorize("import:write")
    body = require_json(request)
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
    _authorize("key:read")
    return _json(_service().get_key(key_id))


@bp.patch("/universal/keys/<key_id>")
@require_auth(*MANAGE_ROLES)
def universal_key_update(key_id: str):
    _authorize("key:write")
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
    _authorize("key:write")
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
    _authorize("key:write")
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
    _authorize("key:write")
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
    _authorize("key:write")
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
    _authorize("device:read")
    page, size = pagination_args()
    return _json(
        _service().list_devices(
            page=page,
            page_size=size,
            project_id=_query("project_id") or None,
            key_id=_query("key_id") or None,
            status=_query("status") or None,
            search=_query("search") or None,
        )
    )


@bp.post("/universal/devices/<device_id>/revoke")
@require_auth(*MANAGE_ROLES)
def universal_device_revoke(device_id: str):
    _authorize("device:write")
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
    _authorize("device:write")
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
    _authorize("webhook:read")
    return _json(_hooks().list_webhooks(_query("project_id") or None))


@bp.post("/universal/webhooks")
@require_auth(*MANAGE_ROLES)
def universal_webhook_create():
    _authorize("webhook:write")
    body = require_json(request)
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
    _authorize("webhook:write")
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
    _authorize("webhook:write")
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
    _authorize("webhook:write")
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
    _authorize("webhook:write")
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
    _authorize("webhook:read")
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


# ------------------------------------------------------------ roles/admins
@bp.get("/universal/roles")
@require_auth(*MANAGE_ROLES)
def universal_roles():
    _authorize("admin:read")
    return _json(_service().list_roles())


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
