from __future__ import annotations

import io
import json
import socket
import zipfile

import pytest

from backend.services.scheduler_service import SchedulerService
from backend.services.webhook_security import (
    WebhookResponseTooLarge,
    WebhookSecurityError,
    is_forbidden_address,
    open_pinned_request,
    resolve_webhook_target,
    safe_error,
)
from conftest import ADMIN_EMAIL, login


def create_project(actor, name: str) -> dict:
    response = actor.post("/api/v1/projects", {"project_name": name})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def generate(actor, project_id: str) -> dict:
    response = actor.post(
        "/api/v1/universal/keys",
        {
            "project_id": project_id,
            "key_type_id": "reader",
            "quantity": 1,
            "expiration_preset": "never",
            "label": "Transfer test key",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["keys"][0]


def upload_bundle(actor, path: str, bundle: dict, **fields):
    data = {
        "file": (io.BytesIO(json.dumps(bundle).encode("utf-8")), "bundle.json"),
    }
    data.update({key: str(value) for key, value in fields.items()})
    return actor.client.open(
        path,
        method="POST",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": actor.csrf},
    )


# --------------------------------------------------------------- export/import
def test_project_export_preview_and_zip(owner, db):
    project = create_project(owner, "Export Project")
    generate(owner, project["id"])
    db.execute(
        """
        INSERT INTO nexora_webhooks (project_id, name, url, events, secret_salt)
        VALUES (%s, 'Export hook',
                'https://example.test/hook?token=must-not-leak',
                ARRAY['key.created'], 'salt')
        """,
        (project["id"],),
    )

    preview = owner.get(f"/api/v1/universal/projects/{project['id']}/export/preview")
    assert preview.status_code == 200, preview.get_json()
    body = preview.get_json()
    assert body["counts"]["keys"] == 1
    assert body["security"]["secrets_exposed"] is False

    exported = owner.get(f"/api/v1/universal/projects/{project['id']}/export")
    assert exported.status_code == 200
    bundle = exported.get_json()
    assert bundle["format"] == "nexora-project-bundle"
    assert bundle["version"] == 1
    assert bundle["security"]["key_material_included"] is False
    assert '"key_hash"' not in json.dumps(bundle)
    assert bundle["keys"][0]["key_hint"]
    assert "key_hash" not in bundle["keys"][0]
    assert bundle["keys"][0]["requires_regeneration"] is True
    assert bundle["webhooks"][0]["url"] == "https://example.test/hook"
    assert bundle["webhooks"][0]["url_query_redacted"] is True
    assert "must-not-leak" not in json.dumps(bundle)

    archive = owner.get(
        f"/api/v1/universal/projects/{project['id']}/export?format=zip"
    )
    assert archive.status_code == 200
    assert archive.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.data)) as bundle_zip:
        names = set(bundle_zip.namelist())
        assert {"manifest.json", "project.json", "README.txt"} <= names
        manifest = json.loads(bundle_zip.read("manifest.json"))
        assert manifest["secrets_exposed"] is False


def test_project_import_modes_and_transaction(owner, db):
    project = create_project(owner, "Import Source")
    generate(owner, project["id"])
    bundle = owner.get(f"/api/v1/universal/projects/{project['id']}/export").get_json()

    # Keep existing merges into the same project and is idempotent for source ids.
    merged = upload_bundle(
        owner,
        "/api/v1/universal/projects/import",
        bundle,
        mode="keep_existing",
    )
    assert merged.status_code == 201, merged.get_json()
    assert merged.get_json()["project"]["action"] == "merge"

    # Import as new with a fresh name creates a separate project.
    created = upload_bundle(
        owner,
        "/api/v1/universal/projects/import",
        bundle,
        mode="import_as_new",
        project_name="Imported Copy",
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["project"]["action"] == "create"
    assert created.get_json()["project"]["id"] != project["id"]

    # Import as new with the same name renames instead of overwriting.
    renamed = upload_bundle(
        owner,
        "/api/v1/universal/projects/import",
        bundle,
        mode="import_as_new",
    )
    assert renamed.status_code == 201, renamed.get_json()
    assert renamed.get_json()["project"]["action"] == "create_with_renamed_project"
    assert renamed.get_json()["project"]["id"] != project["id"]

    # Replacement requires an explicit flag and the exact existing name.
    denied = upload_bundle(
        owner,
        "/api/v1/universal/projects/import",
        bundle,
        mode="replace_existing",
    )
    assert denied.status_code == 422
    assert db.fetch_one(
        "SELECT id FROM nexora_projects WHERE id = %s", (project["id"],)
    )

    replaced = upload_bundle(
        owner,
        "/api/v1/universal/projects/import",
        bundle,
        mode="replace_existing",
        replace_confirmed="true",
        confirm="Import Source",
    )
    assert replaced.status_code == 201, replaced.get_json()
    assert replaced.get_json()["project"]["action"] == "replace"
    assert not db.fetch_one(
        "SELECT id FROM nexora_projects WHERE id = %s", (project["id"],)
    )

    # An invalid bundle rolls back without creating a project.
    before = db.fetch_value("SELECT COUNT(*)::int FROM nexora_projects")
    invalid = upload_bundle(
        owner,
        "/api/v1/universal/projects/import",
        {"format": "wrong", "version": 1},
        mode="import_as_new",
    )
    assert invalid.status_code == 422
    assert db.fetch_value("SELECT COUNT(*)::int FROM nexora_projects") == before


def test_import_preview_reports_conflicts(owner):
    project = create_project(owner, "Conflict Project")
    bundle = owner.get(f"/api/v1/universal/projects/{project['id']}/export").get_json()
    preview = upload_bundle(
        owner,
        "/api/v1/universal/projects/import/preview",
        bundle,
        mode="import_as_new",
    )
    assert preview.status_code == 200, preview.get_json()
    body = preview.get_json()
    assert body["action"] == "create_with_renamed_project"
    assert body["existing_project"]["id"] == project["id"]
    assert body["counts"]["keys"] >= 0
    assert body["secrets_exposed"] is False


# -------------------------------------------------------------------- deletion
def test_project_delete_requires_password_and_is_audited(owner, db):
    project = create_project(owner, "Delete Project")
    generate(owner, project["id"])

    wrong = owner.delete(
        f"/api/v1/universal/projects/{project['id']}",
        json={"confirm_project_name": "Delete Project", "password": "wrong-password"},
    )
    assert wrong.status_code == 403
    assert db.fetch_one(
        "SELECT id FROM nexora_projects WHERE id = %s", (project["id"],)
    )

    deleted = owner.delete(
        f"/api/v1/universal/projects/{project['id']}",
        json={"confirm_project_name": "Delete Project", "password": "Nx!KeyGen2026Pass"},
    )
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["deleted"] is True
    assert deleted.get_json()["counts"]["keys"] == 1
    assert deleted.get_json()["project_name_reusable"] is True
    assert not db.fetch_one(
        "SELECT id FROM nexora_projects WHERE id = %s", (project["id"],)
    )
    event = db.fetch_one(
        "SELECT action FROM admin_audit_logs WHERE action = 'project.delete' AND target_id = %s",
        (project["id"],),
    )
    assert event is not None

    # The name is reusable after a permanent deletion.
    recreated = owner.post("/api/v1/projects", {"project_name": "Delete Project"})
    assert recreated.status_code == 201, recreated.get_json()


def test_delete_all_is_owner_only_phrase_and_password_checked(owner, admin, db):
    create_project(owner, "Bulk One")
    create_project(owner, "Bulk Two")

    preview = owner.get("/api/v1/universal/projects/delete-preview")
    assert preview.status_code == 200, preview.get_json()
    assert preview.get_json()["counts"]["projects"] >= 2
    assert preview.get_json()["mode"] == "permanent"
    assert admin.get("/api/v1/universal/projects/delete-preview").status_code == 403

    denied = admin.delete(
        "/api/v1/universal/projects",
        json={"confirmation": "DELETE ALL PROJECTS", "password": "Nx!KeyGen2026Pass"},
    )
    assert denied.status_code == 403

    phrase = owner.delete(
        "/api/v1/universal/projects",
        json={"confirmation": "delete all", "password": "Nx!KeyGen2026Pass"},
    )
    assert phrase.status_code == 422

    wrong = owner.delete(
        "/api/v1/universal/projects",
        json={"confirmation": "DELETE ALL PROJECTS", "password": "bad"},
    )
    assert wrong.status_code == 403

    admins_before = db.fetch_value("SELECT COUNT(*)::int FROM admin_users")
    deleted = owner.delete(
        "/api/v1/universal/projects",
        json={"confirmation": "DELETE ALL PROJECTS", "password": "Nx!KeyGen2026Pass"},
    )
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["deleted_count"] >= 2
    assert db.fetch_value("SELECT COUNT(*)::int FROM nexora_projects") == 0
    assert db.fetch_value("SELECT COUNT(*)::int FROM admin_users") == admins_before
    event = db.fetch_one(
        "SELECT action FROM admin_audit_logs WHERE action = 'project.delete_all'"
    )
    assert event is not None


# ----------------------------------------------------------------------- roles
def test_custom_role_crud_assignment_and_escalation_guard(owner, admin, app, db):
    created = owner.post(
        "/api/v1/universal/roles",
        {
            "id": "ops_reader",
            "name": "Ops Reader",
            "description": "Read-only operations role.",
            "permissions": ["analytics:read", "project:read", "key:read"],
        },
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["role"]["custom"] is True

    listed = owner.get("/api/v1/universal/roles")
    assert listed.status_code == 200
    role = next(item for item in listed.get_json()["roles"] if item["id"] == "ops_reader")
    assert "project:read" in role["permissions"]
    assert listed.get_json()["templates"]

    updated = owner.patch(
        "/api/v1/universal/roles/ops_reader",
        {
            "name": "Ops Reader",
            "permissions": ["analytics:read", "project:read", "key:read"],
        },
    )
    assert updated.status_code == 200, updated.get_json()

    cloned = owner.post(
        "/api/v1/universal/roles/ops_reader/clone", {"name": "Ops Reader Clone"}
    )
    assert cloned.status_code == 201, cloned.get_json()

    # Assign the custom role to the administrator and verify server enforcement.
    admins = owner.get("/api/v1/universal/admins").get_json()["admins"]
    admin_row = next(item for item in admins if item["email"] == ADMIN_EMAIL)
    assigned = owner.patch(
        f"/api/v1/universal/admins/{admin_row['id']}/role",
        {"platform_role": "custom", "platform_role_id": "ops_reader"},
    )
    assert assigned.status_code == 200, assigned.get_json()
    fresh_admin = login(app, ADMIN_EMAIL)
    assert fresh_admin.get("/api/v1/universal/overview").status_code == 200
    project = create_project(owner, "Role Enforcement")
    forbidden = fresh_admin.post(
        "/api/v1/universal/keys",
        {"project_id": project["id"], "key_type_id": "reader", "quantity": 1},
    )
    assert forbidden.status_code == 403

    # A role still assigned cannot be deleted.
    in_use = owner.delete("/api/v1/universal/roles/ops_reader")
    assert in_use.status_code == 409

    # Reassign a built-in role, then delete the custom role.
    reassigned = owner.patch(
        f"/api/v1/universal/admins/{admin_row['id']}/role",
        {"platform_role": "administrator"},
    )
    assert reassigned.status_code == 200, reassigned.get_json()
    deleted = owner.delete("/api/v1/universal/roles/ops_reader")
    assert deleted.status_code == 200, deleted.get_json()

    # A non-owner with admin:roles cannot grant critical permissions.
    delegator = owner.post(
        "/api/v1/universal/roles",
        {
            "id": "role_delegator",
            "name": "Role Delegator",
            "permissions": ["admin:roles", "admin:read", "project:read"],
        },
    )
    assert delegator.status_code == 201, delegator.get_json()
    owner.patch(
        f"/api/v1/universal/admins/{admin_row['id']}/role",
        {"platform_role": "custom", "platform_role_id": "role_delegator"},
    )
    fresh_admin = login(app, ADMIN_EMAIL)
    escalation = fresh_admin.post(
        "/api/v1/universal/roles",
        {
            "id": "danger_role",
            "name": "Danger Role",
            "permissions": ["project:delete"],
        },
    )
    assert escalation.status_code == 403
    assert escalation.get_json()["error"]["code"] == "privilege_escalation"

    narrowed = fresh_admin.post(
        "/api/v1/universal/roles",
        {
            "id": "narrow_role",
            "name": "Narrow Role",
            "permissions": ["project:read"],
        },
    )
    assert narrowed.status_code == 201, narrowed.get_json()

    # The last owner cannot be demoted through the role endpoint.
    owners = owner.get("/api/v1/universal/admins").get_json()["admins"]
    owner_row = next(item for item in owners if item["role"] == "owner")
    last_owner = owner.patch(
        f"/api/v1/universal/admins/{owner_row['id']}/role",
        {"platform_role": "read_only"},
    )
    assert last_owner.status_code == 409


# ------------------------------------------------------------------- scheduler
def test_scheduler_dry_run_is_bounded_and_idempotent(owner, app, db):
    create_project(owner, "Scheduler Project")
    service = SchedulerService(db, app.extensions["settings"])
    first = service.run_once(dry_run=True)
    second = service.run_once(dry_run=True)
    assert first["status"] == "success"
    assert second["status"] == "success"
    assert first["run_id"] != second["run_id"]
    assert first["details"]["automation"]["status"] == "dry_run"
    assert first["details"]["automation"]["dry_run"] is True

    status = service.status()
    assert status["counts"]["projects"] >= 1
    assert status["runs"]
    assert status["lock_key"] == 0x4E455850


# ---------------------------------------------------------- webhook security
class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"ok") -> None:
        self.status = status
        self._body = body

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


class _FakeConnection:
    def __init__(self, status: int = 200, body: bytes = b"ok") -> None:
        self.status = status
        self.body = body
        self.calls = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))

    def getresponse(self):
        return _FakeResponse(self.status, self.body)

    def close(self):
        self.closed = True


def _resolver(*addresses):
    def resolve(host, port, type=None):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return resolve


def test_ssrf_rejects_private_and_mixed_answers():
    for address in ("127.0.0.1", "10.0.0.1", "192.168.1.2", "169.254.169.254", "::1"):
        assert is_forbidden_address(__import__("ipaddress").ip_address(address))

    with pytest.raises(WebhookSecurityError) as caught:
        resolve_webhook_target("https://example.test/hook", require_https=True, resolver=_resolver("10.0.0.5"))
    assert caught.value.code == "private_destination"

    # One private answer in a public DNS answer set invalidates the whole host.
    with pytest.raises(WebhookSecurityError):
        resolve_webhook_target("https://example.test/hook", require_https=True, resolver=_resolver("1.1.1.1", "127.0.0.1"))

    target = resolve_webhook_target(
        "https://example.test/hook?token=secret",
        require_https=True,
        resolver=_resolver("1.1.1.1"),
    )
    assert target.primary_address == "1.1.1.1"
    assert target.host_header == "example.test"
    assert target.request_path == "/hook?token=secret"


def test_webhook_transport_never_follows_redirects_and_bounds_response():
    target = resolve_webhook_target(
        "https://example.test/hook", require_https=True, resolver=_resolver("1.1.1.1")
    )
    connection = _FakeConnection(status=302, body=b"")
    status, body, size = open_pinned_request(
        target,
        body=b"{}",
        headers={"Content-Type": "application/json"},
        connection_factory=lambda target, timeout, context: connection,
    )
    assert status == 302
    assert body == b""
    assert size == 0
    # The transport never performs a second request for a redirect.
    assert len(connection.calls) == 1
    assert connection.calls[0][0] == "POST"

    large = _FakeConnection(status=200, body=b"x" * 2048)
    with pytest.raises(WebhookResponseTooLarge):
        open_pinned_request(
            target,
            body=b"{}",
            headers={},
            max_response_bytes=128,
            connection_factory=lambda target, timeout, context: large,
        )

    assert safe_error(TimeoutError())[0] == "timeout"
    assert safe_error(OSError("boom"))[0] == "network_error"
    assert safe_error(RuntimeError("secret-url?token=1")) == (
        "delivery_error",
        "The webhook delivery failed.",
    )
