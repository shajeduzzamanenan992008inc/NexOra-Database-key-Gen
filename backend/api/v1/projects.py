"""NexOra Project and Reader Key Management API (Admin only).

Projects are created either standalone (``POST /projects``) or together with
their first Reader Key (``POST /projects/generate-key``). Existing projects can
receive unlimited additional keys through ``POST /projects/<id>/keys``.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ...errors import ApiError, ValidationError
from ...security.decorators import require_auth
from ...serialization import jsonable
from ...services.audit import audit
from ...validation import Payload, require_json
from ..helpers import auth_service, project_service, request_meta

bp = Blueprint("v1_projects", __name__)

DELETE_ALL_PHRASE = "DELETE ALL PROJECTS"


# ----------------------------------------------------------------------------
# Generate Key (creates the project automatically)
# ----------------------------------------------------------------------------

@bp.post("/projects/generate-key")
@require_auth("owner", "admin")
def generate_project_key():
    """Create one project and issue its first Reader Key.

    A 201 response contains the plaintext key once. A duplicate project name
    returns 409 with safe metadata and never a plaintext key. Use
    ``POST /projects/<id>/keys`` to add keys to an existing project.
    """
    payload = Payload(require_json(request))
    project_name = payload.string(
        "project_name", required=True, min_length=1, max_length=200
    )
    label = payload.string("label", required=False, max_length=100, default="")
    expiration = payload.string(
        "expiration", required=False, max_length=20, default="never"
    )
    expires_at = payload.datetime_utc(
        "expires_at", required=False, allow_null=True
    )
    payload.finish()

    ip, agent = request_meta()
    result = project_service().generate_project_key(
        project_name=project_name or "",
        label=label or "",
        expiration=expiration or "never",
        expires_at=expires_at,
        created_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 201


@bp.post("/projects")
@require_auth("owner", "admin")
def create_project():
    """Create a project without issuing a key."""
    payload = Payload(require_json(request))
    project_name = payload.string(
        "project_name", required=True, min_length=1, max_length=200
    )
    expiration = payload.string(
        "expiration", required=False, max_length=20, default="never"
    )
    expires_at = payload.datetime_utc(
        "expires_at", required=False, allow_null=True
    )
    payload.finish()

    ip, agent = request_meta()
    project = project_service().create_project(
        project_name=project_name or "",
        expiration=expiration or "never",
        expires_at=expires_at,
        created_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify({"project": jsonable(project)}), 201


@bp.post("/projects/<project_id>/keys")
@require_auth("owner", "admin")
def generate_key_for_project(project_id: str):
    """Issue an additional Reader Key for an existing live project."""
    payload = Payload(require_json(request))
    label = payload.string("label", required=False, max_length=100, default="")
    expiration = payload.string(
        "expiration", required=False, max_length=20, default="never"
    )
    expires_at = payload.datetime_utc(
        "expires_at", required=False, allow_null=True
    )
    payload.finish()

    ip, agent = request_meta()
    result = project_service().generate_key_for_project(
        project_id,
        label=label or "",
        expiration=expiration or "never",
        expires_at=expires_at,
        created_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 201


# ----------------------------------------------------------------------------
# Project Management
# ----------------------------------------------------------------------------

@bp.get("/projects")
@require_auth("owner", "admin", "viewer")
def list_projects():
    """List managed projects with reader-key, installation, and auto-delete data."""
    include_deleted = request.args.get("include_deleted", "false").lower() == "true"
    search = request.args.get("search", None)
    projects = project_service().list_projects(
        include_deleted=include_deleted,
        search=search,
    )
    return jsonify({"projects": jsonable(projects)}), 200


@bp.get("/projects/<project_id>")
@require_auth("owner", "admin", "viewer")
def get_project(project_id: str):
    """Get project details and its Reader Key / installation status."""
    project = project_service().get_project(project_id)
    return jsonify({"project": jsonable(project)}), 200


@bp.patch("/projects/<project_id>/status")
@require_auth("owner", "admin")
def set_project_status(project_id: str):
    """Activate or deactivate a project.

    Deactivation blocks new activations and existing device credentials without
    touching the key's lifecycle status.
    """
    payload = Payload(require_json(request))
    status = payload.string("status", required=True, min_length=1, max_length=20)
    payload.finish()

    ip, agent = request_meta()
    project = project_service().set_project_status(
        project_id,
        status=status or "active",
        actor_id=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify({"project": jsonable(project)}), 200


@bp.patch("/projects/<project_id>/auto-delete")
@require_auth("owner", "admin")
def set_project_auto_delete(project_id: str):
    """Configure or clear a project's scheduled hard-delete date.

    Send ``{"auto_delete_at": "2026-12-16T23:59:59Z"}`` to schedule deletion or
    ``{"auto_delete_at": null}`` to disable it. Automatic deletion only happens
    when the server-side scheduler command runs and the date is due.
    """
    payload = Payload(require_json(request))
    if not payload.has("auto_delete_at"):
        payload.error(
            "auto_delete_at",
            "This field is required (send null to clear the schedule).",
        )
        payload.finish()
    auto_delete_at = payload.datetime_utc(
        "auto_delete_at", required=False, allow_null=True
    )
    payload.finish()

    ip, agent = request_meta()
    project = project_service().set_project_auto_delete(
        project_id,
        auto_delete_at=auto_delete_at,
        actor_id=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify({"project": jsonable(project)}), 200


@bp.delete("/projects/<project_id>")
@require_auth("owner")
def delete_project(project_id: str):
    """Permanently delete one project and all project-owned records.

    The administrator must repeat the exact project name in
    ``confirm_project_name``. This is a database hard delete, not a soft delete.
    """
    payload = Payload(require_json(request))
    confirm_name = payload.string(
        "confirm_project_name", required=True, min_length=1, max_length=200
    )
    payload.finish()

    service = project_service()
    project = service.get_project(project_id)
    if (confirm_name or "").strip() != (project["project_name"] or "").strip():
        raise ValidationError(
            {
                "confirm_project_name": (
                    "Type the exact project name to confirm permanent deletion."
                )
            }
        )

    ip, agent = request_meta()
    result = service.hard_delete_project(
        project_id,
        actor_id=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


@bp.delete("/projects")
@require_auth("owner")
def delete_all_projects():
    """Permanently delete every NexOra project in one transaction.

    Requires the exact phrase ``DELETE ALL PROJECTS`` and a fresh password
    re-authentication. Admin accounts, sessions, settings, audit history, and
    the legacy SMS Reader tables are preserved.
    """
    payload = Payload(require_json(request))
    confirmation = payload.string(
        "confirmation", required=True, min_length=1, max_length=64
    )
    password = payload.string(
        "password", required=True, min_length=1, max_length=128, strip=False
    )
    payload.finish()

    if confirmation != DELETE_ALL_PHRASE:
        raise ValidationError(
            {"confirmation": f"Type {DELETE_ALL_PHRASE} exactly to confirm."}
        )

    if not auth_service().verify_admin_password(g.actor["id"], password or ""):
        audit(
            auth_service().db,
            actor=g.actor,
            action="project.delete_all",
            target_type="project",
            outcome="failure",
            details={"reason": "reauth_failed"},
        )
        raise ApiError(
            403,
            "reauth_failed",
            "Password confirmation failed. Sign in again and retry.",
        )

    ip, agent = request_meta()
    result = project_service().hard_delete_all_projects(
        actor_id=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200


# ----------------------------------------------------------------------------
# Reader Key Revocation (destructive operations are intentionally elsewhere)
# ----------------------------------------------------------------------------

@bp.post("/keys/<key_id>/revoke")
@require_auth("owner", "admin")
def revoke_reader_key(key_id: str):
    """Revoke a generated reader key permanently."""
    payload = Payload(require_json(request))
    reason = payload.string("reason", required=True, min_length=1, max_length=500)
    payload.finish()

    ip, agent = request_meta()
    result = project_service().revoke_key(
        key_id,
        reason=reason or "",
        revoked_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 200
