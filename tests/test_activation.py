from __future__ import annotations

import concurrent.futures

from conftest import TEST_PEPPER


def activate(app, key: str, device: str, account: str | None = None):
    body = {"key": key, "device_id": device}
    if account:
        body["account_id"] = account
    return app.test_client().post("/api/keys/activate", json=body)


def verify(app, key: str):
    return app.test_client().post("/api/keys/verify", json={"key": key})


def test_one_time_activation_replay_and_limit(app, create_key, db):
    batch = create_key(max_activations=1)
    plaintext = batch["keys"][0]["key"]

    first = activate(app, plaintext, "device-alpha-1234")
    assert first.status_code == 200
    payload = first.get_json()
    assert payload["replayed"] is False
    assert payload["key"]["activation_count"] == 1
    assert payload["key"]["remaining_activations"] == 0
    assert payload["key"]["status"] == "used"

    replay = activate(app, plaintext, "device-alpha-1234")
    assert replay.status_code == 200
    assert replay.get_json()["replayed"] is True
    assert replay.get_json()["key"]["activation_count"] == 1

    second_device = activate(app, plaintext, "device-beta-5678")
    assert second_device.status_code == 409
    assert second_device.get_json()["error"]["code"] == "activation_limit_reached"

    assert db.fetch_value("SELECT activation_count FROM license_keys") == 1
    assert db.fetch_value("SELECT count(*) FROM key_activations") == 1
    assert verify(app, plaintext).get_json()["status"] == "used"


def test_multi_use_activation_counts_devices(app, create_key, db):
    batch = create_key(max_activations=2)
    plaintext = batch["keys"][0]["key"]

    first = activate(app, plaintext, "device-one-1111")
    assert first.status_code == 200
    assert first.get_json()["key"]["status"] == "active"
    assert first.get_json()["key"]["remaining_activations"] == 1

    second = activate(app, plaintext, "device-two-2222")
    assert second.status_code == 200
    assert second.get_json()["key"]["status"] == "used"

    third = activate(app, plaintext, "device-three-3333")
    assert third.status_code == 409
    assert db.fetch_value("SELECT activation_count FROM license_keys") == 2

    activations = db.fetch_all("SELECT device_hash, device_hint FROM key_activations")
    assert len(activations) == 2
    assert all(len(row["device_hash"]) == 64 for row in activations)
    assert all("device" not in row["device_hint"] for row in activations)


def test_unknown_and_malformed_keys(app):
    unknown = activate(app, "NXR-AAAA-BBBB-CCCC-DDDD", "device-alpha-1234")
    assert unknown.status_code == 404
    assert unknown.get_json()["error"]["code"] == "key_not_found"

    malformed = activate(app, "!!!", "device-alpha-1234")
    assert malformed.status_code == 422

    missing_device = app.test_client().post(
        "/api/keys/activate", json={"key": "NXR-AAAA-BBBB-CCCC-DDDD"}
    )
    assert missing_device.status_code == 422
    assert "device_id" in missing_device.get_json()["error"]["details"]["fields"]


def test_inactive_and_pending_keys_cannot_activate(app, owner, create_key):
    batch = create_key()
    key_id = batch["keys"][0]["id"]
    plaintext = batch["keys"][0]["key"]

    assert owner.patch(f"/api/keys/{key_id}", {"status": "inactive"}).status_code == 200
    inactive = activate(app, plaintext, "device-inactive-1234")
    assert inactive.status_code == 409
    assert inactive.get_json()["error"]["code"] == "key_inactive"

    assert owner.patch(f"/api/keys/{key_id}", {"status": "pending"}).status_code == 200
    pending = activate(app, plaintext, "device-inactive-1234")
    assert pending.status_code == 409
    assert pending.get_json()["error"]["code"] == "key_pending"


def test_expired_keys_are_marked_and_rejected(app, owner, create_key, db):
    batch = create_key()
    key_id = batch["keys"][0]["id"]
    plaintext = batch["keys"][0]["key"]
    db.execute(
        "UPDATE license_keys SET expires_at = now() - interval '1 hour' WHERE id = %s",
        (key_id,),
    )

    assert owner.get("/api/keys").status_code == 200
    assert db.fetch_value("SELECT status FROM license_keys WHERE id = %s", (key_id,)) == "expired"

    expired = activate(app, plaintext, "device-expired-1234")
    assert expired.status_code == 409
    assert expired.get_json()["error"]["code"] == "key_expired"
    assert verify(app, plaintext).get_json()["status"] == "expired"


def test_revocation_terminates_activations(app, owner, create_key, db):
    batch = create_key(max_activations=2)
    key_id = batch["keys"][0]["id"]
    plaintext = batch["keys"][0]["key"]
    assert activate(app, plaintext, "device-revoke-1234").status_code == 200

    revoked = owner.post(
        "/api/keys/revoke",
        {"key_id": key_id, "reason": "Chargeback confirmed"},
    )
    assert revoked.status_code == 200
    assert revoked.get_json()["status"] == "revoked"
    assert revoked.get_json()["deactivated_activations"] == 1

    assert db.fetch_value("SELECT status FROM key_activations") == "deactivated"
    rejected = activate(app, plaintext, "device-revoke-1234")
    assert rejected.status_code == 409
    assert rejected.get_json()["error"]["code"] == "key_revoked"

    again = owner.post(
        "/api/keys/revoke", {"key_id": key_id, "reason": "Second attempt"}
    )
    assert again.status_code == 409


def test_deactivation_frees_a_slot_and_allows_reactivation(app, owner, create_key, db):
    batch = create_key(max_activations=1)
    key_id = batch["keys"][0]["id"]
    plaintext = batch["keys"][0]["key"]

    assert activate(app, plaintext, "device-reset-1234").status_code == 200
    deactivated = owner.post(
        "/api/keys/deactivate",
        {"key_id": key_id, "reason": "Customer device replacement"},
    )
    assert deactivated.status_code == 200
    payload = deactivated.get_json()
    assert payload["activation_count"] == 0
    assert payload["status"] == "active"
    assert payload["deactivated_activations"] == 1

    reactivated = activate(app, plaintext, "device-reset-1234")
    assert reactivated.status_code == 200
    assert reactivated.get_json()["replayed"] is False
    assert reactivated.get_json()["key"]["activation_count"] == 1
    assert db.fetch_value("SELECT count(*) FROM key_activations") == 1


def test_deactivate_specific_activation(app, owner, create_key, db):
    batch = create_key(max_activations=3)
    key_id = batch["keys"][0]["id"]
    plaintext = batch["keys"][0]["key"]
    first = activate(app, plaintext, "device-one-1111").get_json()
    assert activate(app, plaintext, "device-two-2222").status_code == 200
    activation_id = first["activation"]["id"]

    response = owner.post(
        "/api/keys/deactivate",
        {
            "key_id": key_id,
            "activation_id": activation_id,
            "reason": "Single device removal",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["activation_count"] == 1
    assert db.fetch_value("SELECT count(*) FROM key_activations WHERE status = 'active'") == 1


def test_concurrent_activation_is_race_safe(app, create_key, db):
    batch = create_key(max_activations=1)
    plaintext = batch["keys"][0]["key"]

    def attempt(device: str):
        return activate(app, plaintext, device).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, ["device-race-1111", "device-race-2222"]))

    assert sorted(statuses) == [200, 409]
    assert db.fetch_value("SELECT activation_count FROM license_keys") == 1
    assert db.fetch_value("SELECT count(*) FROM key_activations") == 1
    assert db.fetch_value("SELECT status FROM license_keys") == "used"


def test_activation_audit_never_contains_plaintext(app, owner, create_key, db):
    batch = create_key()
    plaintext = batch["keys"][0]["key"]
    assert activate(app, plaintext, "device-audit-1234").status_code == 200

    details = db.fetch_value(
        "SELECT string_agg(details::text, ' ') FROM admin_audit_logs"
    )
    assert plaintext not in (details or "")
    hashed = db.fetch_value("SELECT key_hash FROM license_keys")
    assert plaintext not in hashed


def test_activation_idempotency_after_exhaustion(app, create_key):
    batch = create_key(max_activations=1)
    plaintext = batch["keys"][0]["key"]
    assert activate(app, plaintext, "device-idem-1234").status_code == 200
    replay = activate(app, plaintext, "device-idem-1234")
    assert replay.status_code == 200
    assert replay.get_json()["replayed"] is True


def test_account_binding_hint_is_stored_not_raw(app, create_key, db):
    batch = create_key()
    plaintext = batch["keys"][0]["key"]
    response = activate(app, plaintext, "device-account-1234", "player-account-9876")
    assert response.status_code == 200
    row = db.fetch_one("SELECT account_hash, account_hint FROM key_activations")
    assert row["account_hash"] != "player-account-9876"
    assert len(row["account_hash"]) == 64
    assert row["account_hint"].endswith("9876")
