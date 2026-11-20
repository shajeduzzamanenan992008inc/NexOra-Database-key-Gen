from __future__ import annotations

import json
from typing import Any

from backend.services.webhook_service import WebhookService
from conftest import ADMIN_EMAIL, OWNER_EMAIL, VIEWER_EMAIL, login


def create_project(actor, name: str = "Universal Test Project") -> dict[str, Any]:
    response = actor.post("/api/v1/projects", {"project_name": name})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def generate(actor, project_id: str, **overrides):
    body = {
        "project_id": project_id,
        "key_type_id": "reader",
        "quantity": 1,
        "expiration_preset": "never",
        "label": "Universal test key",
    }
    body.update(overrides)
    return actor.post("/api/v1/universal/keys", body)


def authenticate(app, key: str, **extra):
    body = {"key": key, "device_id": "universal-test-device"}
    body.update(extra)
    return app.test_client().post("/api/v1/access/authenticate", json=body)


def test_generate_type_isolated_auth_and_no_secret_leak(owner, app, db):
    project = create_project(owner)
    response = generate(owner, project["id"])
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    plaintext = body["keys"][0]["key"]
    assert len(plaintext) >= 12
    assert "key_hash" not in json.dumps(body)
    assert "fingerprint" in body["keys"][0]

    stored = db.fetch_one(
        "SELECT key_hash, key_type_id, permissions FROM reader_keys WHERE id = %s",
        (body["keys"][0]["id"],),
    )
    assert stored is not None
    assert stored["key_hash"] != plaintext
    assert len(stored["key_hash"]) == 64
    assert stored["key_type_id"] == "reader"
    assert "device_access" in stored["permissions"]

    auth = authenticate(
        app,
        plaintext,
        permission="read",
        device_id="universal-device-1",
        device_name="Test phone",
        platform="android",
    )
    assert auth.status_code == 200, auth.get_json()
    payload = auth.get_json()
    assert payload["authenticated"] is True
    assert payload["key"]["type"] == "reader"
    assert payload["human_admin_session"] is False
    assert payload["project"]["id"] == project["id"]
    assert "key_hash" not in json.dumps(payload)

    # A mismatched permission is denied, and the key type is taken from the
    # stored row rather than from any client-supplied type.
    denied = authenticate(app, plaintext, permission="admin_operation")
    assert denied.status_code == 403
    assert denied.get_json()["error"]["code"] == "PERMISSION_DENIED"


def test_key_lifecycle_and_project_expiry(owner, app, db):
    project = create_project(owner, "Lifecycle Project")
    response = generate(owner, project["id"])
    key_id = response.get_json()["keys"][0]["id"]
    plaintext = response.get_json()["keys"][0]["key"]

    deactivated = owner.post(f"/api/v1/universal/keys/{key_id}/deactivate", {"reason": "pause"})
    assert deactivated.status_code == 200, deactivated.get_json()
    assert authenticate(app, plaintext).status_code == 403

    activated = owner.post(f"/api/v1/universal/keys/{key_id}/activate")
    assert activated.status_code == 200, activated.get_json()
    assert authenticate(app, plaintext).status_code == 200

    db.execute(
        "UPDATE reader_keys SET expires_at = now() - interval '1 second', is_permanent = false, expiration_preset = 'custom' WHERE id = %s",
        (key_id,),
    )
    expired = authenticate(app, plaintext)
    assert expired.status_code == 410
    assert expired.get_json()["error"]["code"] == "KEY_EXPIRED"

    revoked = owner.post(
        f"/api/v1/universal/keys/{key_id}/revoke", {"reason": "compromised"}
    )
    assert revoked.status_code == 200, revoked.get_json()
    assert revoked.get_json()["deleted"] is True
    assert authenticate(app, plaintext).status_code == 401
    audit = db.fetch_one(
        "SELECT action FROM admin_audit_logs WHERE action = 'universal_key.revoke' AND target_id = %s",
        (key_id,),
    )
    assert audit is not None


def test_project_expiry_blocks_authentication(owner, app, db):
    project = create_project(owner, "Expiring Project")
    response = generate(owner, project["id"])
    key = response.get_json()["keys"][0]["key"]
    db.execute(
        "UPDATE nexora_projects SET expires_at = now() - interval '1 second', never_expires = false WHERE id = %s",
        (project["id"],),
    )
    result = authenticate(app, key)
    assert result.status_code == 403
    assert result.get_json()["error"]["code"] == "PROJECT_EXPIRED"


def test_viewer_and_platform_role_authorization(owner, admin, viewer, app):
    project = create_project(owner, "Authorization Project")
    assert viewer.get("/api/v1/universal/overview").status_code == 200
    forbidden = generate(viewer, project["id"])
    assert forbidden.status_code == 403

    admins = owner.get("/api/v1/universal/admins")
    assert admins.status_code == 200
    admin_row = next(
        item for item in admins.get_json()["admins"] if item["email"] == ADMIN_EMAIL
    )
    changed = owner.patch(
        f"/api/v1/universal/admins/{admin_row['id']}/role",
        {"platform_role": "read_only", "platform_permissions": []},
    )
    assert changed.status_code == 200, changed.get_json()
    fresh_admin = login(app, ADMIN_EMAIL)
    assert generate(fresh_admin, project["id"]).status_code == 403

    # A non-owner cannot assign platform roles.
    denied = admin.patch(
        f"/api/v1/universal/admins/{admin_row['id']}/role",
        {"platform_role": "owner"},
    )
    assert denied.status_code == 403


def test_webhooks_are_queued_without_secrets(owner, app, db):
    project = create_project(owner, "Webhook Project")
    created = owner.post(
        "/api/v1/universal/webhooks",
        {
            "project_id": project["id"],
            "name": "Test hook",
            "url": "https://example.invalid/nexora",
            "events": ["key.generated"],
        },
    )
    assert created.status_code == 201, created.get_json()
    payload = created.get_json()
    secret = payload["secret"]
    assert secret.startswith("whsec_")
    assert "secret_salt" not in json.dumps(payload["webhook"])
    listed = owner.get("/api/v1/universal/webhooks")
    assert listed.status_code == 200
    assert "secret_salt" not in json.dumps(listed.get_json())

    response = generate(owner, project["id"])
    assert response.status_code == 201
    plaintext = response.get_json()["keys"][0]["key"]
    delivery = db.fetch_one(
        "SELECT payload FROM nexora_webhook_deliveries WHERE webhook_id = %s",
        (payload["webhook"]["id"],),
    )
    assert delivery is not None
    text = json.dumps(delivery["payload"])
    assert plaintext not in text
    assert "key_hash" not in text
    assert "key.generated" in text

    first = WebhookService.signature("secret", 1700000000, '{"a":1}')
    second = WebhookService.signature("secret", 1700000000, '{"a":1}')
    assert first == second and len(first) == 64


def test_import_export_search_and_notifications(owner, app, db):
    project = create_project(owner, "Import Project")
    imported_key = "NXR-ABCD-EFGH-JKLM-NPQR"
    preview = owner.post(
        "/api/v1/universal/keys/import/preview",
        {
            "project_id": project["id"],
            "key_type_id": "reader",
            "keys": [{"key": imported_key, "label": "Imported"}],
        },
    )
    assert preview.status_code == 200, preview.get_json()
    assert preview.get_json()["valid_count"] == 1
    assert imported_key not in json.dumps(preview.get_json())

    imported = owner.post(
        "/api/v1/universal/keys/import",
        {
            "project_id": project["id"],
            "key_type_id": "reader",
            "keys": [{"key": imported_key, "label": "Imported"}],
        },
    )
    assert imported.status_code == 201, imported.get_json()
    assert imported.get_json()["secrets_echoed"] is False
    assert authenticate(app, imported_key, permission="read").status_code == 200

    exported = owner.get(f"/api/v1/universal/keys/export?project_id={project['id']}")
    assert exported.status_code == 200
    assert exported.get_json()["secrets_exposed"] is False
    assert imported_key not in json.dumps(exported.get_json())

    csv_export = owner.get(
        f"/api/v1/universal/keys/export?format=csv&project_id={project['id']}"
    )
    assert csv_export.status_code == 200
    assert b"key_hint" in csv_export.data
    assert imported_key.encode() not in csv_export.data

    search = owner.get("/api/v1/universal/search?q=Import")
    assert search.status_code == 200
    assert imported_key not in json.dumps(search.get_json())
    assert search.get_json()["groups"]["projects"]

    overview = owner.get("/api/v1/universal/overview")
    assert overview.status_code == 200
    assert overview.get_json()["keys"]["total"] >= 1

    notification = db.fetch_one("SELECT id FROM nexora_notifications LIMIT 1")
    if notification:
        marked = owner.post(f"/api/v1/universal/notifications/{notification['id']}/read")
        assert marked.status_code == 200


def test_custom_key_type_permissions_are_a_ceiling(owner, app):
    project = create_project(owner, "Custom Type Project")
    created = owner.post(
        "/api/v1/universal/key-types",
        {
            "id": "test_custom",
            "name": "Test Custom",
            "project_id": project["id"],
            "key_prefix": "TST",
            "key_format": {"groups": 4, "group_length": 4, "separator": "-"},
            "permissions": ["read"],
            "scopes": ["reports.read"],
        },
    )
    assert created.status_code == 201, created.get_json()
    denied = generate(
        owner,
        project["id"],
        key_type_id="test_custom",
        permissions=["read", "write"],
    )
    assert denied.status_code == 422
    allowed = generate(
        owner,
        project["id"],
        key_type_id="test_custom",
        permissions=["read"],
        scopes=["reports.read"],
    )
    assert allowed.status_code == 201, allowed.get_json()
    key = allowed.get_json()["keys"][0]["key"]
    assert authenticate(app, key, permission="read").status_code == 200
