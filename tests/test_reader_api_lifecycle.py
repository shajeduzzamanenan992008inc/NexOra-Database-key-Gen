"""NexOra Reader API lifecycle and security coverage.

These tests exercise the real Flask routes and PostgreSQL schema used by the
separate Reader App.  They do not require the optional ``sms_reader`` console
package, production credentials, or any external service.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app import create_app

pytestmark = [pytest.mark.reader, pytest.mark.integration]

SUPABASE_URL = "https://reader-lifecycle.supabase.co"


@pytest.fixture
def reader_app(test_settings):
    application = create_app(replace(test_settings, supabase_url=SUPABASE_URL))
    application.config["TESTING"] = True
    yield application
    application.extensions["db"].close()


def generate(owner, name: str, **body) -> dict:
    response = owner.post(
        "/api/v1/projects/generate-key", {"project_name": name, **body}
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def activate(
    reader_app,
    project_name: str,
    reader_key: str,
    *,
    device_id: str | None = "reader-lifecycle-1",
    supabase_url: str = SUPABASE_URL,
    **extra,
):
    body = {
        "project_name": project_name,
        "supabase_url": supabase_url,
        "reader_key": reader_key,
    }
    if device_id is not None:
        body["device_id"] = device_id
    body.update(extra)
    return reader_app.test_client().post("/api/v1/reader/activate", json=body)


def bearer(client, credential: str):
    return client.get(
        "/api/v1/reader/status",
        headers={"Authorization": f"Bearer {credential}"},
    )


def test_activation_validation_rejects_incomplete_and_wrongly_typed_input(
    reader_app, owner
):
    created = generate(owner, "Reader Validation")
    name = created["project"]["project_name"]
    key = created["reader_key"]["plaintext_key"]
    client = reader_app.test_client()

    malformed = client.post(
        "/api/v1/reader/activate",
        data="{not-json",
        content_type="application/json",
    )
    assert malformed.status_code in (400, 415)

    missing = client.post("/api/v1/reader/activate", json={})
    assert missing.status_code == 422
    fields = missing.get_json()["error"]["details"]["fields"]
    assert {"project_name", "reader_key"} <= set(fields)

    wrong_types = client.post(
        "/api/v1/reader/activate",
        json={
            "project_name": 12,
            "supabase_url": SUPABASE_URL,
            "reader_key": [],
        },
    )
    assert wrong_types.status_code == 422

    long_device = activate(reader_app, name, key, device_id="d" * 201)
    assert long_device.status_code == 422
    assert "device_id" in long_device.get_json()["error"]["details"]["fields"]


def test_activation_enforces_the_supabase_url_contract_and_legacy_alias(
    reader_app, owner
):
    created = generate(owner, "Reader URL Contract")
    name = created["project"]["project_name"]
    key = created["reader_key"]["plaintext_key"]
    client = reader_app.test_client()

    missing_url = client.post(
        "/api/v1/reader/activate",
        json={"project_name": name, "reader_key": key},
    )
    assert missing_url.status_code == 422
    assert "supabase_url" in missing_url.get_json()["error"]["details"]["fields"]

    postgres = client.post(
        "/api/v1/reader/activate",
        json={
            "project_name": name,
            "supabase_url": "postgresql://user:pass@db.example/app",
            "reader_key": key,
        },
    )
    assert postgres.status_code == 400
    assert postgres.get_json()["error"]["code"] == "INVALID_DATABASE_URL"

    mismatch = activate(
        reader_app, name, key, supabase_url="https://different.supabase.co"
    )
    assert mismatch.status_code == 400
    assert mismatch.get_json()["error"]["code"] == "DATABASE_URL_MISMATCH"

    # The deprecated alias remains compatible and still means the project URL.
    legacy = client.post(
        "/api/v1/reader/activate",
        json={
            "project_name": name,
            "database_url": SUPABASE_URL,
            "reader_key": key,
            "device_id": "legacy-reader-1",
        },
    )
    assert legacy.status_code == 200, legacy.get_json()
    assert legacy.get_json()["project_id"] == created["project"]["id"]


def test_activation_binds_the_device_and_stores_only_digests(
    reader_app, app, owner
):
    created = generate(owner, "Reader Bind")
    name = created["project"]["project_name"]
    key = created["reader_key"]
    response = activate(
        reader_app, name, key["plaintext_key"], device_id="android-reader-42"
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    credential = payload["device_credential"]

    assert payload["status"] == "activated"
    assert payload["project_id"] == created["project"]["id"]
    assert payload["installation_id"]
    assert len(credential) >= 40
    assert key["plaintext_key"] not in json.dumps(payload)
    assert payload["device_credential"] == credential

    db = app.extensions["db"]
    key_row = db.fetch_one(
        "SELECT status, used_at, key_hash FROM reader_keys WHERE id = %s",
        (key["id"],),
    )
    assert key_row["status"] == "used"
    assert key_row["used_at"] is not None
    assert key["plaintext_key"] not in key_row["key_hash"]

    installation = db.fetch_one(
        "SELECT status, device_id, device_token_hash FROM reader_installations "
        "WHERE id = %s",
        (payload["installation_id"],),
    )
    assert installation["status"] == "active"
    assert installation["device_id"] == "android-reader-42"
    assert len(installation["device_token_hash"]) == 64

    token = db.fetch_one(
        "SELECT token_hash, is_active FROM device_tokens WHERE installation_id = %s",
        (payload["installation_id"],),
    )
    assert token["is_active"] is True
    assert len(token["token_hash"]) == 64
    assert token["token_hash"] == installation["device_token_hash"]
    assert credential not in repr(token)

    audit = db.fetch_one(
        "SELECT details FROM admin_audit_logs WHERE action = 'reader.activate'"
    )
    assert key["plaintext_key"] not in json.dumps(audit, default=str)
    assert "android-reader-42" in json.dumps(audit, default=str)


def test_replay_and_cross_project_activation_are_rejected(reader_app, owner):
    first = generate(owner, "Reader Replay A")
    second = generate(owner, "Reader Replay B")
    name = first["project"]["project_name"]
    key = first["reader_key"]["plaintext_key"]

    accepted = activate(reader_app, name, key, device_id="replay-device")
    assert accepted.status_code == 200

    replay = activate(reader_app, name, key, device_id="replay-device")
    assert replay.status_code == 410
    assert replay.get_json()["error"]["code"] == "KEY_ALREADY_USED"

    wrong_project = activate(
        reader_app,
        second["project"]["project_name"],
        key,
        device_id="replay-device",
    )
    assert wrong_project.status_code == 403
    assert wrong_project.get_json()["error"]["code"] == "KEY_PROJECT_MISMATCH"


def test_revoked_and_expired_keys_cannot_activate(reader_app, app, owner):
    revoked_created = generate(owner, "Reader Revoked")
    revoked = owner.post(
        f"/api/v1/keys/{revoked_created['reader_key']['id']}/revoke",
        {"reason": "Compromised during handoff"},
    )
    assert revoked.status_code == 200, revoked.get_json()
    rejected = activate(
        reader_app,
        revoked_created["project"]["project_name"],
        revoked_created["reader_key"]["plaintext_key"],
    )
    assert rejected.status_code == 410
    assert rejected.get_json()["error"]["code"] == "KEY_REVOKED"

    expired_created = generate(owner, "Reader Expired")
    key = expired_created["reader_key"]
    app.extensions["db"].execute(
        "UPDATE reader_keys SET is_permanent = false, expiration_preset = 'custom', "
        "expires_at = now() - interval '1 hour' WHERE id = %s",
        (key["id"],),
    )
    expired = activate(
        reader_app,
        expired_created["project"]["project_name"],
        key["plaintext_key"],
    )
    assert expired.status_code == 410
    assert expired.get_json()["error"]["code"] == "KEY_EXPIRED"
    assert (
        app.extensions["db"].fetch_value(
            "SELECT status FROM reader_keys WHERE id = %s", (key["id"],)
        )
        == "expired"
    )


def test_inactive_archived_and_expired_projects_block_activation(reader_app, app, owner):
    inactive = generate(owner, "Reader Inactive")
    owner.patch(
        f"/api/v1/projects/{inactive['project']['id']}/status",
        {"status": "inactive"},
    )
    blocked = activate(
        reader_app,
        inactive["project"]["project_name"],
        inactive["reader_key"]["plaintext_key"],
    )
    assert blocked.status_code == 404
    assert blocked.get_json()["error"]["code"] == "PROJECT_NOT_FOUND"

    archived = generate(owner, "Reader Archived")
    app.extensions["db"].execute(
        "UPDATE nexora_projects SET archived_at = now() WHERE id = %s",
        (archived["project"]["id"],),
    )
    archived_response = activate(
        reader_app,
        archived["project"]["project_name"],
        archived["reader_key"]["plaintext_key"],
    )
    assert archived_response.status_code == 403
    assert archived_response.get_json()["error"]["code"] == "PROJECT_ARCHIVED"

    expired = generate(owner, "Reader Project Expired")
    app.extensions["db"].execute(
        "UPDATE nexora_projects SET expires_at = now() - interval '1 hour' WHERE id = %s",
        (expired["project"]["id"],),
    )
    expired_response = activate(
        reader_app,
        expired["project"]["project_name"],
        expired["reader_key"]["plaintext_key"],
    )
    assert expired_response.status_code == 403
    assert expired_response.get_json()["error"]["code"] == "PROJECT_EXPIRED"


def test_concurrent_activation_has_exactly_one_winner(reader_app, app, owner):
    created = generate(owner, "Reader Race")
    name = created["project"]["project_name"]
    key = created["reader_key"]["plaintext_key"]

    def attempt(device_id: str):
        client = reader_app.test_client()
        return client.post(
            "/api/v1/reader/activate",
            json={
                "project_name": name,
                "supabase_url": SUPABASE_URL,
                "reader_key": key,
                "device_id": device_id,
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(attempt, ["race-device-a", "race-device-b"]))

    assert statuses == [200, 410]
    db = app.extensions["db"]
    assert (
        db.fetch_value(
            "SELECT count(*) FROM reader_installations WHERE project_id = %s",
            (created["project"]["id"],),
        )
        == 1
    )
    assert (
        db.fetch_value(
            "SELECT count(*) FROM device_tokens WHERE project_id = %s",
            (created["project"]["id"],),
        )
        == 1
    )


def test_only_one_active_installation_is_allowed_per_project(reader_app, owner):
    created = generate(owner, "Reader One Installation")
    project = created["project"]
    first = created["reader_key"]
    assert (
        activate(
            reader_app, project["project_name"], first["plaintext_key"],
            device_id="first-reader",
        ).status_code
        == 200
    )

    second = owner.post(
        f"/api/v1/projects/{project['id']}/keys",
        {"expiration": "30_days"},
    )
    assert second.status_code == 201, second.get_json()
    second_key = second.get_json()["reader_key"]
    conflict = activate(
        reader_app,
        project["project_name"],
        second_key["plaintext_key"],
        device_id="second-reader",
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "ACTIVE_INSTALLATION_EXISTS"


def test_status_and_heartbeat_update_last_seen_and_reject_bad_bearers(
    reader_app, app, owner
):
    created = generate(owner, "Reader Heartbeat")
    payload = activate(
        reader_app,
        created["project"]["project_name"],
        created["reader_key"]["plaintext_key"],
        device_id="heartbeat-reader",
    ).get_json()
    credential = payload["device_credential"]
    client = reader_app.test_client()

    status = bearer(client, credential)
    assert status.status_code == 200
    assert status.get_json()["status"] == "authorized"
    assert status.get_json()["project_id"] == created["project"]["id"]
    assert status.get_json()["installation_id"] == payload["installation_id"]

    before = app.extensions["db"].fetch_one(
        "SELECT last_seen_at FROM reader_installations WHERE id = %s",
        (payload["installation_id"],),
    )
    heartbeat = client.post(
        "/api/v1/reader/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
        json={"app_version": "1.0.0"},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.get_json()["status"] == "ok"
    after = app.extensions["db"].fetch_one(
        "SELECT last_seen_at FROM reader_installations WHERE id = %s",
        (payload["installation_id"],),
    )
    assert after["last_seen_at"] > before["last_seen_at"]
    token = app.extensions["db"].fetch_one(
        "SELECT last_used_at FROM device_tokens WHERE installation_id = %s",
        (payload["installation_id"],),
    )
    assert token["last_used_at"] is not None

    assert bearer(client, "A" * 43).status_code == 401
    missing = client.post("/api/v1/reader/heartbeat")
    assert missing.status_code == 401
    malformed = client.post(
        "/api/v1/reader/heartbeat",
        headers={"Authorization": "Basic abc"},
    )
    assert malformed.status_code == 401
    assert malformed.get_json()["error"]["code"] == "UNAUTHORIZED"


def test_installation_revocation_invalidates_the_credential(reader_app, app, owner):
    created = generate(owner, "Reader Installation Revoke")
    payload = activate(
        reader_app,
        created["project"]["project_name"],
        created["reader_key"]["plaintext_key"],
        device_id="revoked-installation",
    ).get_json()
    credential = payload["device_credential"]
    installation_id = payload["installation_id"]

    revoked = owner.post(
        f"/api/v1/installations/{installation_id}/revoke",
        {"reason": "Device retired"},
    )
    assert revoked.status_code == 200, revoked.get_json()
    assert revoked.get_json()["status"] == "revoked"

    db = app.extensions["db"]
    installation = db.fetch_one(
        "SELECT status, device_token_hash, revoked_at FROM reader_installations "
        "WHERE id = %s",
        (installation_id,),
    )
    assert installation["status"] == "revoked"
    assert installation["device_token_hash"] == ""
    assert installation["revoked_at"] is not None
    token = db.fetch_one(
        "SELECT is_active FROM device_tokens WHERE installation_id = %s",
        (installation_id,),
    )
    assert token["is_active"] is False

    client = reader_app.test_client()
    assert bearer(client, credential).status_code == 401
    heartbeat = client.post(
        "/api/v1/reader/heartbeat",
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert heartbeat.status_code == 401
    audit = db.fetch_one(
        "SELECT details FROM admin_audit_logs "
        "WHERE action = 'reader_installation.revoke'"
    )
    assert audit is not None
    assert "Device retired" in json.dumps(audit, default=str)


def test_reader_activation_is_rate_limited_and_reports_retry_after(test_settings):
    limited = create_app(
        replace(
            test_settings,
            supabase_url=SUPABASE_URL,
            rate_limit_enabled=True,
        )
    )
    limited.config["TESTING"] = True
    try:
        client = limited.test_client()
        body = {
            "project_name": "Rate Limited Reader",
            "supabase_url": SUPABASE_URL,
            "reader_key": "NXR-AAAA-BBBB-CCCC-DDDD",
            "device_id": "rate-device",
        }
        for _ in range(20):
            response = client.post("/api/v1/reader/activate", json=body)
            assert response.status_code in (404, 410)

        blocked = client.post("/api/v1/reader/activate", json=body)
        assert blocked.status_code == 429
        assert blocked.get_json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["Retry-After"]) >= 1
        limited.extensions["limiter"].reset()
    finally:
        limited.extensions["db"].close()


def test_activation_queries_are_parameterized_and_cross_project_safe(
    reader_app, app, owner
):
    other = generate(owner, "Reader Injection")
    before = app.extensions["db"].fetch_one(
        "SELECT (SELECT count(*) FROM reader_keys) AS keys, "
        "(SELECT count(*) FROM nexora_projects) AS projects"
    )
    response = activate(
        reader_app,
        "%' OR 1=1; DROP TABLE reader_keys; --",
        "NXR-AAAA-BBBB-CCCC-DDDD",
    )
    assert response.status_code in (404, 422)
    after = app.extensions["db"].fetch_one(
        "SELECT (SELECT count(*) FROM reader_keys) AS keys, "
        "(SELECT count(*) FROM nexora_projects) AS projects"
    )
    assert after == before
    assert other["reader_key"]["id"]
