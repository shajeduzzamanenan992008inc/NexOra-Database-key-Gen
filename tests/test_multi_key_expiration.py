"""Multi-key projects, expiration presets, revocation, and deletion contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.app import create_app

TEST_SUPABASE_URL = "https://multi-key.supabase.co"
ADMIN_PASSWORD = "Nx!KeyGen2026Pass"
PRESETS = {
    "1_day": timedelta(days=1),
    "7_days": timedelta(days=7),
    "30_days": timedelta(days=30),
    "90_days": timedelta(days=90),
    "1_year": timedelta(days=365),
}


@pytest.fixture
def reader_app(test_settings):
    application = create_app(replace(test_settings, supabase_url=TEST_SUPABASE_URL))
    application.config["TESTING"] = True
    yield application
    application.extensions["db"].close()


def create_project(actor, name: str):
    response = actor.post("/api/v1/projects", {"project_name": name})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def generate_first(actor, name: str, **body):
    response = actor.post(
        "/api/v1/projects/generate-key", {"project_name": name, **body}
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def generate_more(actor, project_id: str, **body):
    response = actor.post(f"/api/v1/projects/{project_id}/keys", body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["reader_key"]


def activate(reader_app, project_name: str, plaintext: str, device_id="reader-multi-1"):
    return reader_app.test_client().post(
        "/api/v1/reader/activate",
        json={
            "project_name": project_name,
            "supabase_url": TEST_SUPABASE_URL,
            "reader_key": plaintext,
            "device_id": device_id,
        },
    )


def test_project_can_own_unlimited_reader_keys(app, owner):
    project = create_project(owner, "Unlimited Keys")
    first = generate_more(owner, project["id"], expiration="30_days")
    second = generate_more(owner, project["id"], expiration="never")
    third = generate_more(owner, project["id"], expiration="1_day")

    assert {first["project_id"], second["project_id"], third["project_id"]} == {
        project["id"]
    }
    assert len({first["id"], second["id"], third["id"]}) == 3
    assert first["plaintext_key"] != second["plaintext_key"] != third["plaintext_key"]

    listing = owner.get(f"/api/v1/keys?project_id={project['id']}").get_json()
    assert listing["pagination"]["total"] == 3
    assert {item["id"] for item in listing["items"]} == {
        first["id"],
        second["id"],
        third["id"],
    }
    assert all(item["project_name"] == "Unlimited Keys" for item in listing["items"])

    listed_project = owner.get(f"/api/v1/projects/{project['id']}").get_json()["project"]
    assert listed_project["total_keys"] == 3
    assert listed_project["available_keys"] == 3


@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_fixed_expiration_option_is_server_computed(app, owner, preset):
    before = datetime.now(timezone.utc)
    created = generate_first(owner, f"Expiry {preset}", expiration=preset)
    key = created["reader_key"]

    assert key["expiration_preset"] == preset
    assert key["is_permanent"] is False
    assert key["expiration_label"]

    expires_at = datetime.fromisoformat(key["expires_at"].replace("Z", "+00:00"))
    expected = before + PRESETS[preset]
    assert abs((expires_at - expected).total_seconds()) < 180

    row = app.extensions["db"].fetch_one(
        "SELECT expiration_preset, is_permanent, expires_at FROM reader_keys WHERE id = %s",
        (key["id"],),
    )
    assert row["expiration_preset"] == preset
    assert row["is_permanent"] is False
    assert row["expires_at"] is not None


def test_never_expire_is_stored_explicitly(app, owner):
    created = generate_first(owner, "Forever Project", expiration="never")
    key = created["reader_key"]

    assert key["expiration_preset"] == "never"
    assert key["is_permanent"] is True
    assert key["expires_at"] is None

    row = app.extensions["db"].fetch_one(
        "SELECT expiration_preset, is_permanent, expires_at FROM reader_keys WHERE id = %s",
        (key["id"],),
    )
    assert row == {
        "expiration_preset": "never",
        "is_permanent": True,
        "expires_at": None,
    }

    # Legacy clients that omit expiration retain explicit never-expire behavior.
    legacy = generate_first(owner, "Legacy Default")
    assert legacy["reader_key"]["expiration_preset"] == "never"
    assert legacy["reader_key"]["expires_at"] is None


def test_custom_expiration_is_validated_and_stored(app, owner):
    future = (datetime.now(timezone.utc) + timedelta(days=12, hours=3)).replace(
        microsecond=0
    )
    created = generate_first(
        owner,
        "Custom Expiry",
        expiration="custom",
        expires_at=future.isoformat(),
    )
    key = created["reader_key"]
    stored = datetime.fromisoformat(key["expires_at"].replace("Z", "+00:00"))
    assert stored == future
    assert key["expiration_preset"] == "custom"
    assert key["is_permanent"] is False

    missing = owner.post(
        "/api/v1/projects/generate-key",
        {"project_name": "Custom Missing", "expiration": "custom"},
    )
    assert missing.status_code == 422
    assert "expires_at" in missing.get_json()["error"]["details"]["fields"]

    past = owner.post(
        "/api/v1/projects/generate-key",
        {
            "project_name": "Custom Past",
            "expiration": "custom",
            "expires_at": "2000-01-01T00:00:00Z",
        },
    )
    assert past.status_code == 422

    invalid = owner.post(
        "/api/v1/projects/generate-key",
        {"project_name": "Invalid Preset", "expiration": "forever"},
    )
    assert invalid.status_code == 422

    counts = app.extensions["db"].fetch_one(
        "SELECT count(*) AS total FROM nexora_projects WHERE project_name IN "
        "('Custom Missing', 'Custom Past', 'Invalid Preset')"
    )
    assert counts["total"] == 0


def test_custom_expiration_for_existing_project(app, owner):
    project = create_project(owner, "Existing Custom")
    future = (datetime.now(timezone.utc) + timedelta(days=45)).replace(microsecond=0)
    key = generate_more(
        owner,
        project["id"],
        expiration="custom",
        expires_at=future.isoformat(),
    )
    assert key["expiration_preset"] == "custom"
    assert datetime.fromisoformat(
        key["expires_at"].replace("Z", "+00:00")
    ) == future


def test_revoke_is_independent_and_permanent(app, reader_app, owner):
    project = create_project(owner, "Independent Revoke")
    first = generate_more(owner, project["id"], expiration="never")
    second = generate_more(owner, project["id"], expiration="7_days")

    revoke = owner.post(
        f"/api/v1/keys/{first['id']}/revoke",
        {"reason": "Rotation window closed"},
    )
    assert revoke.status_code == 200, revoke.get_json()
    assert revoke.get_json()["status"] == "revoked"

    stored_first = owner.get(f"/api/v1/keys/{first['id']}").get_json()["key"]
    stored_second = owner.get(f"/api/v1/keys/{second['id']}").get_json()["key"]
    assert stored_first["status"] == "revoked"
    assert stored_first["revoked_at"] is not None
    assert stored_first["revoked_reason"] == "Rotation window closed"
    assert stored_second["status"] == "generated"

    activation = activate(reader_app, "Independent Revoke", first["plaintext_key"])
    assert activation.status_code == 410
    assert activation.get_json()["error"]["code"] == "KEY_REVOKED"

    # A revoked key cannot be revoked again or made active by another request.
    again = owner.post(
        f"/api/v1/keys/{first['id']}/revoke", {"reason": "Second attempt"}
    )
    assert again.status_code == 400
    assert again.get_json()["error"]["code"] == "ALREADY_REVOKED"

    # The second key remains usable.
    usable = activate(
        reader_app,
        "Independent Revoke",
        second["plaintext_key"],
        device_id="reader-multi-2",
    )
    assert usable.status_code == 200, usable.get_json()


def test_revoking_used_key_revokes_installation_and_device_token(
    app, reader_app, owner
):
    created = generate_first(owner, "Used Revoke", expiration="never")
    key = created["reader_key"]
    activation = activate(reader_app, "Used Revoke", key["plaintext_key"])
    assert activation.status_code == 200, activation.get_json()
    credential = activation.get_json()["device_credential"]

    heartbeats_before = reader_app.test_client().post(
        "/api/v1/reader/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert heartbeats_before.status_code == 200

    revoked = owner.post(
        f"/api/v1/keys/{key['id']}/revoke",
        {"reason": "Device compromise"},
    )
    assert revoked.status_code == 200, revoked.get_json()
    assert revoked.get_json()["installation_revoked"] is True

    after = reader_app.test_client().post(
        "/api/v1/reader/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert after.status_code == 401

    state = app.extensions["db"].fetch_one(
        """
        SELECT i.status, i.device_token_hash,
               (SELECT count(*) FROM device_tokens t
                 WHERE t.installation_id = i.id AND t.is_active) AS active_tokens
        FROM reader_installations i
        WHERE i.reader_key_id = %s
        """,
        (key["id"],),
    )
    assert state["status"] == "revoked"
    assert state["device_token_hash"] == ""
    assert state["active_tokens"] == 0

    reactivate = activate(
        reader_app, "Used Revoke", key["plaintext_key"], device_id="replacement"
    )
    assert reactivate.status_code == 410
    assert reactivate.get_json()["error"]["code"] == "KEY_REVOKED"


def test_single_project_delete_removes_all_keys_and_installations(
    app, reader_app, owner
):
    doomed = generate_first(owner, "Delete Multi")
    project_id = doomed["project"]["id"]
    second = generate_more(owner, project_id, expiration="never")
    activation = activate(
        reader_app, "Delete Multi", doomed["reader_key"]["plaintext_key"]
    )
    assert activation.status_code == 200
    credential = activation.get_json()["device_credential"]
    survivor = generate_first(owner, "Survivor Project")

    response = owner.delete(
        f"/api/v1/projects/{project_id}",
        json={"confirm_project_name": "Delete Multi"},
    )
    assert response.status_code == 200, response.get_json()

    counts = app.extensions["db"].fetch_one(
        """
        SELECT
          (SELECT count(*) FROM nexora_projects WHERE id = %s) AS projects,
          (SELECT count(*) FROM reader_keys WHERE project_id = %s) AS keys,
          (SELECT count(*) FROM reader_installations WHERE project_id = %s) AS installations,
          (SELECT count(*) FROM device_tokens WHERE project_id = %s) AS tokens
        """,
        (project_id, project_id, project_id, project_id),
    )
    assert counts == {"projects": 0, "keys": 0, "installations": 0, "tokens": 0}

    assert owner.get(f"/api/v1/keys/{second['id']}").status_code == 404

    audit = app.extensions["db"].fetch_one(
        """
        SELECT project_id, details
        FROM admin_audit_logs
        WHERE action = 'project.delete'
        ORDER BY created_at DESC LIMIT 1
        """
    )
    assert audit["project_id"] is None
    details = audit["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details["snapshot"]["key_count"] == 2
    assert details["snapshot"]["project_name"] == "Delete Multi"
    assert details["snapshot"]["installation_count"] == 1
    assert details["snapshot"]["device_token_count"] == 1
    key_snapshots = {item["id"]: item for item in details["snapshot"]["keys"]}
    assert set(key_snapshots) == {doomed["reader_key"]["id"], second["id"]}
    assert key_snapshots[doomed["reader_key"]["id"]]["status"] == "used"
    assert key_snapshots[second["id"]]["status"] == "generated"
    assert key_snapshots[doomed["reader_key"]["id"]]["key_hint"].startswith("NXR-")
    assert "plaintext" not in json.dumps(details).lower()

    heartbeat = reader_app.test_client().post(
        "/api/v1/reader/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert heartbeat.status_code == 401

    # Unrelated project and its key remain.
    assert (
        owner.get(f"/api/v1/projects/{survivor['project']['id']}").status_code
        == 200
    )
    assert owner.get(f"/api/v1/keys/{survivor['reader_key']['id']}").status_code == 200

    # The deleted name is immediately reusable.
    reused = generate_first(owner, "delete multi")
    assert reused["project"]["project_name"] == "delete multi"
    assert reused["project"]["id"] != project_id


def test_delete_all_preserves_audit_and_allows_name_reuse(app, owner):
    first = generate_first(owner, "Bulk Multi One")
    second = generate_first(owner, "Bulk Multi Two")
    generate_more(owner, first["project"]["id"], expiration="never")
    generate_more(owner, second["project"]["id"], expiration="1_day")

    admins = app.extensions["db"].fetch_one(
        "SELECT count(*) AS total FROM admin_users"
    )["total"]
    settings = app.extensions["db"].fetch_one(
        "SELECT count(*) AS total FROM system_settings"
    )["total"]
    audits_before = app.extensions["db"].fetch_one(
        "SELECT count(*) AS total FROM admin_audit_logs"
    )["total"]

    response = owner.delete(
        "/api/v1/projects",
        json={"confirmation": "DELETE ALL PROJECTS", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["deleted_count"] == 2

    counts = app.extensions["db"].fetch_one(
        """
        SELECT
          (SELECT count(*) FROM nexora_projects) AS projects,
          (SELECT count(*) FROM reader_keys) AS keys,
          (SELECT count(*) FROM admin_users) AS admins,
          (SELECT count(*) FROM system_settings) AS settings,
          (SELECT count(*) FROM admin_audit_logs) AS audits
        """
    )
    assert counts["projects"] == 0
    assert counts["keys"] == 0
    assert counts["admins"] == admins
    assert counts["settings"] == settings
    assert counts["audits"] > audits_before

    audit = app.extensions["db"].fetch_one(
        """
        SELECT details FROM admin_audit_logs
        WHERE action = 'project.delete_all'
        ORDER BY created_at DESC LIMIT 1
        """
    )
    details = audit["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details["deleted_count"] == 2
    assert set(details["project_names"]) == {"Bulk Multi One", "Bulk Multi Two"}

    for name in ("bulk multi one", "BULK MULTI TWO"):
        reused = generate_first(owner, name)
        assert reused["project"]["project_name"].lower() == name.lower()


def test_additional_key_generation_rolls_back_on_failure(app, owner):
    project = create_project(owner, "Atomic Additional Key")
    db = app.extensions["db"]
    db.execute(
        """
        CREATE OR REPLACE FUNCTION nexora_test_block_additional_key()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'blocked additional key insert';
        END;
        $$
        """
    )
    db.execute(
        """
        CREATE TRIGGER trg_nexora_test_block_additional_key
        BEFORE INSERT ON reader_keys
        FOR EACH ROW EXECUTE FUNCTION nexora_test_block_additional_key()
        """
    )
    try:
        response = owner.post(
            f"/api/v1/projects/{project['id']}/keys", {"expiration": "7_days"}
        )
        assert response.status_code == 500
    finally:
        db.execute(
            "DROP TRIGGER IF EXISTS trg_nexora_test_block_additional_key ON reader_keys"
        )
        db.execute("DROP FUNCTION IF EXISTS nexora_test_block_additional_key()")

    assert db.fetch_one(
        "SELECT count(*) AS total FROM reader_keys WHERE project_id = %s",
        (project["id"],),
    )["total"] == 0
    assert db.fetch_one(
        "SELECT count(*) AS total FROM admin_audit_logs "
        "WHERE action = 'reader_key.generate' AND project_id = %s",
        (project["id"],),
    )["total"] == 0
