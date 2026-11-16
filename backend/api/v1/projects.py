"""NexOra Phase 1 & 2 — Project and Reader Key Management API endpoints (Admin only)."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ...security.decorators import require_auth
from ...serialization import jsonable
from ...validation import Payload, require_json
from ..helpers import project_service, request_meta

bp = Blueprint("v1_projects", __name__)


# ----------------------------------------------------------------------------
# Project Management
# ----------------------------------------------------------------------------

@bp.post("/projects")
@require_auth("owner", "admin")
def create_project():
    """Create a new database project managed by NexOra."""
    payload = Payload(require_json(request))
    name = payload.string("project_name", required=True, min_length=1, max_length=200)
    database_url_ref = payload.string("database_url_ref", required=False, max_length=500, default="")
    payload.finish()

    ip, agent = request_meta()
    project = project_service().create_project(
        name=name or "",
        database_url_ref=database_url_ref or "",
        created_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify({"project": jsonable(project)}), 201


@bp.get("/projects")
@require_auth("owner", "admin", "viewer")
def list_projects():
    """List all managed projects with key and installation counts."""
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
    """Get project details and active installation status."""
    project = project_service().get_project(project_id)
    return jsonify({"project": jsonable(project)}), 200


@bp.patch("/projects/<project_id>/status")
@require_auth("owner", "admin")
def set_project_status(project_id: str):
    """Activate or deactivate a project."""
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


@bp.delete("/projects/<project_id>")
@require_auth("owner")
def delete_project(project_id: str):
    """Soft-delete a project and revoke all active reader authorizations."""
    ip, agent = request_meta()
    result = project_service().soft_delete_project(
        project_id,
        actor_id=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify({"project": jsonable(result), "status": "deleted"}), 200


# ----------------------------------------------------------------------------
# Reader Key Management
# ----------------------------------------------------------------------------

@bp.post("/projects/<project_id>/keys")
@require_auth("owner", "admin")
def generate_reader_key(project_id: str):
    """Generate a single reader key for a project. Plaintext returned once."""
    payload = Payload(request.get_json(silent=True) or {})
    is_permanent = payload.boolean("is_permanent", default=True)
    label = payload.string("label", required=False, max_length=100, default="")
    payload.finish()

    ip, agent = request_meta()
    result = project_service().generate_key(
        project_id=project_id,
        is_permanent=is_permanent,
        label=label or "",
        created_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify(jsonable(result)), 201


@bp.post("/projects/<project_id>/keys/bulk")
@require_auth("owner", "admin")
def generate_reader_keys_bulk(project_id: str):
    """Generate a batch of reader keys for a project."""
    payload = Payload(require_json(request))
    quantity = payload.integer("quantity", required=True, minimum=1, maximum=2000)
    is_permanent = payload.boolean("is_permanent", default=True)
    label = payload.string("label", required=False, max_length=100, default="")
    payload.finish()

    ip, agent = request_meta()
    results = project_service().generate_bulk_keys(
        project_id=project_id,
        quantity=quantity or 1,
        is_permanent=is_permanent,
        label=label or "",
        created_by=g.actor["id"],
        ip_address=ip,
        user_agent=agent,
    )
    return jsonify({"keys": jsonable(results), "count": len(results)}), 201


@bp.post("/keys/<key_id>/revoke")
@require_auth("owner", "admin")
def revoke_reader_key(key_id: str):
    """Revoke a reader key permanently."""
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
