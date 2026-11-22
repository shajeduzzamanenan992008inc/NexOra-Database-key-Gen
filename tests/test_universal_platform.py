from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

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


def test_project_creation_persists_custom_expiry_and_never(owner, db):
    future = datetime.now(timezone.utc) + timedelta(days=45)
    name = f"Custom Expiry {uuid4().hex[:8]}"
    response = owner.post(
        "/api/v1/projects",
        {
            "project_name": name,
            "expiration": "custom",
            "expires_at": future.isoformat(),
        },
    )
    assert response.status_code == 201, response.get_json()
    project = response.get_json()["project"]
    assert project["never_expires"] is False
    assert project["expires_at"] is not None

    stored = db.fetch_one(
        "SELECT expires_at, never_expires FROM nexora_projects WHERE id = %s",
        (project["id"],),
    )
    assert stored is not None
    assert stored["expires_at"] is not None
    assert stored["never_expires"] is False

    cleared = owner.patch(
        f"/api/v1/universal/projects/{project['id']}",
        {"expiration_preset": "never"},
    )
    assert cleared.status_code == 200, cleared.get_json()
    stored = db.fetch_one(
        "SELECT expires_at, never_expires FROM nexora_projects WHERE id = %s",
        (project["id"],),
    )
    assert stored["expires_at"] is None
    assert stored["never_expires"] is True


def test_project_custom_expiry_requires_a_future_date(owner):
    name = f"Invalid Expiry {uuid4().hex[:8]}"
    missing = owner.post(
        "/api/v1/projects", {"project_name": name, "expiration": "custom"}
    )
    assert missing.status_code == 422
    assert "expires_at" in missing.get_json()["error"]["details"]["fields"]

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    rejected = owner.post(
        "/api/v1/projects",
        {
            "project_name": name,
            "expiration": "custom",
            "expires_at": past.isoformat(),
        },
    )
    assert rejected.status_code == 422
    assert "expires_at" in rejected.get_json()["error"]["details"]["fields"]


def test_key_custom_expiry_edit_persists_and_never(owner):
    project = create_project(owner, f"Key Expiry {uuid4().hex[:8]}")
    key = generate(owner, project["id"]).get_json()["keys"][0]
    future = datetime.now(timezone.utc) + timedelta(days=14)

    edited = owner.patch(
        f"/api/v1/universal/keys/{key['id']}",
        {"expiration_preset": "custom", "expires_at": future.isoformat()},
    )
    assert edited.status_code == 200, edited.get_json()
    detail = owner.get(f"/api/v1/universal/keys/{key['id']}").get_json()["key"]
    assert detail["expiration_preset"] == "custom"
    assert detail["expires_at"] is not None
    assert detail["expires_at"].startswith(future.strftime("%Y-%m-%d"))

    never = owner.patch(
        f"/api/v1/universal/keys/{key['id']}", {"expiration_preset": "never"}
    )
    assert never.status_code == 200, never.get_json()
    detail = owner.get(f"/api/v1/universal/keys/{key['id']}").get_json()["key"]
    assert detail["expiration_preset"] == "never"
    assert detail["expires_at"] is None

    past = owner.patch(
        f"/api/v1/universal/keys/{key['id']}",
        {
            "expiration_preset": "custom",
            "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        },
    )
    assert past.status_code == 422


def test_project_expiry_edit_preserves_key_expiry(owner, db):
    project = create_project(owner, f"Preserve Keys {uuid4().hex[:8]}")
    key = generate(
        owner, project["id"], expiration_preset="30_days"
    ).get_json()["keys"][0]
    before = db.fetch_one(
        "SELECT expires_at, expiration_preset FROM reader_keys WHERE id = %s",
        (key["id"],),
    )

    changed = owner.patch(
        f"/api/v1/universal/projects/{project['id']}",
        {"expiration_preset": "7_days"},
    )
    assert changed.status_code == 200, changed.get_json()
    after = db.fetch_one(
        "SELECT expires_at, expiration_preset FROM reader_keys WHERE id = %s",
        (key["id"],),
    )
    assert after["expires_at"] == before["expires_at"]
    assert after["expiration_preset"] == "30_days"


def test_expired_key_reactivates_when_expiry_moves_forward(owner, app, db):
    project = create_project(owner, f"Reactivate Key {uuid4().hex[:8]}")
    key = generate(owner, project["id"]).get_json()["keys"][0]
    db.execute(
        """
        UPDATE reader_keys
           SET status = 'expired', expires_at = now() - interval '1 hour',
               is_permanent = false, expiration_preset = 'custom'
         WHERE id = %s
        """,
        (key["id"],),
    )
    assert authenticate(app, key["key"]).status_code == 410

    future = datetime.now(timezone.utc) + timedelta(days=10)
    edited = owner.patch(
        f"/api/v1/universal/keys/{key['id']}",
        {"expiration_preset": "custom", "expires_at": future.isoformat()},
    )
    assert edited.status_code == 200, edited.get_json()
    detail = owner.get(f"/api/v1/universal/keys/{key['id']}").get_json()["key"]
    assert detail["status"] in {"active", "generated"}
    assert authenticate(app, key["key"]).status_code == 200


def test_expired_project_reactivates_when_expiry_moves_forward(owner, app, db):
    project = create_project(owner, f"Reactivate Project {uuid4().hex[:8]}")
    key = generate(owner, project["id"]).get_json()["keys"][0]
    db.execute(
        """
        UPDATE nexora_projects
           SET status = 'expired', expires_at = now() - interval '1 hour',
               never_expires = false
         WHERE id = %s
        """,
        (project["id"],),
    )

    edited = owner.patch(
        f"/api/v1/universal/projects/{project['id']}",
        {"expiration_preset": "30_days"},
    )
    assert edited.status_code == 200, edited.get_json()
    refreshed = db.fetch_one(
        "SELECT status, expires_at, never_expires FROM nexora_projects WHERE id = %s",
        (project["id"],),
    )
    assert refreshed["status"] == "active"
    assert refreshed["expires_at"] is not None
    assert refreshed["never_expires"] is False
    assert authenticate(app, key["key"]).status_code == 200


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
            # A public literal address keeps the test deterministic without DNS.
            "url": "https://1.1.1.1/nexora",
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


def test_key_type_policy_builder_and_safe_delete(owner, app, db):
    project = create_project(owner, f"Builder Project {uuid4().hex[:8]}")
    payload = {
        "id": f"builder_{uuid4().hex[:8]}",
        "name": "Builder Custom",
        "description": "Declarative type created from the Classic builder.",
        "project_id": project["id"],
        "key_prefix": "BLD",
        "key_format": {"groups": 3, "group_length": 4, "separator": "-"},
        "permissions": ["read"],
        "scopes": ["reports.read"],
        "expiration_policy": {
            "presets": ["7_days", "custom", "never"],
            "default": "7_days",
        },
        "metadata_schema": {
            "fields": {"tenant": {"type": "string", "required": True, "max_length": 40}}
        },
        "validation_rules": {"required_metadata": ["tenant"]},
    }
    created = owner.post("/api/v1/universal/key-types", payload)
    assert created.status_code == 201, created.get_json()
    type_id = created.get_json()["key_type"]["id"]

    invalid_default = owner.post(
        "/api/v1/universal/key-types",
        {**payload, "id": f"bad_{uuid4().hex[:8]}", "expiration_policy": {"presets": ["never"], "default": "custom"}},
    )
    assert invalid_default.status_code == 422

    missing_metadata = generate(
        owner,
        project["id"],
        key_type_id=type_id,
        expiration_preset="7_days",
    )
    assert missing_metadata.status_code == 422

    future = datetime.now(timezone.utc) + timedelta(days=10)
    generated = generate(
        owner,
        project["id"],
        key_type_id=type_id,
        expiration_preset="custom",
        expires_at=future.isoformat(),
        metadata={"tenant": "classic-ui"},
    )
    assert generated.status_code == 201, generated.get_json()

    in_use = owner.delete(f"/api/v1/universal/key-types/{type_id}")
    assert in_use.status_code == 409
    assert in_use.get_json()["error"]["code"] == "KEY_TYPE_IN_USE"

    built_in = owner.delete("/api/v1/universal/key-types/reader")
    assert built_in.status_code == 403

    unused_id = f"unused_{uuid4().hex[:8]}"
    unused = owner.post(
        "/api/v1/universal/key-types",
        {
            "id": unused_id,
            "name": "Unused Custom",
            "project_id": project["id"],
            "key_prefix": "UNU",
            "permissions": ["read"],
            "expiration_policy": {"presets": ["never"], "default": "never"},
        },
    )
    assert unused.status_code == 201, unused.get_json()
    deleted = owner.delete(f"/api/v1/universal/key-types/{unused_id}")
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["deleted"] is True
    assert db.fetch_one(
        "SELECT 1 FROM nexora_key_types WHERE id = %s", (unused_id,)
    ) is None


def test_update_key_accepts_tags_and_jsonb_array_fields(owner, db):
    project = create_project(owner, f"Key Update Project {uuid4().hex[:8]}")
    created = generate(owner, project["id"])
    assert created.status_code == 201, created.get_json()
    key_id = created.get_json()["keys"][0]["id"]

    updated = owner.patch(
        f"/api/v1/universal/keys/{key_id}",
        {
            "tags": ["alpha", "beta"],
            "permissions": ["read"],
            "scopes": [],
            "notes": "tag update regression",
        },
    )
    assert updated.status_code == 200, updated.get_json()
    key = updated.get_json()["key"]
    assert key["tags"] == ["alpha", "beta"]
    assert key["permissions"] == ["read"]
    assert key["notes"] == "tag update regression"

    row = db.fetch_one(
        "SELECT tags, permissions, scopes FROM reader_keys WHERE id = %s",
        (key_id,),
    )
    assert row["tags"] == ["alpha", "beta"]
    assert row["permissions"] == ["read"]
    assert row["scopes"] == []


def test_generate_batch_id_is_idempotent(owner, db):
    project = create_project(owner, f"Idempotent Project {uuid4().hex[:8]}")
    batch_id = str(uuid4())
    first = generate(owner, project["id"], batch_id=batch_id)
    assert first.status_code == 201, first.get_json()
    second = generate(owner, project["id"], batch_id=batch_id)
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "BATCH_ALREADY_PROCESSED"
    count = db.fetch_one(
        "SELECT COUNT(*)::int AS count FROM reader_keys WHERE batch_id = %s",
        (batch_id,),
    )
    assert count["count"] == 1

    retry_after_failed_batch = str(uuid4())
    failed = generate(owner, project["id"], batch_id=retry_after_failed_batch, quantity=0)
    assert failed.status_code == 422
    success = generate(owner, project["id"], batch_id=retry_after_failed_batch)
    assert success.status_code == 201, success.get_json()
