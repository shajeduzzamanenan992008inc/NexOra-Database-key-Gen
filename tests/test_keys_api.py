from __future__ import annotations

import re

from backend.security.keys import hash_key, normalize_key

from conftest import TEST_PEPPER

DEFAULT_BODY = {
    "quantity": 1,
    "prefix": "NXR",
    "groups": 4,
    "group_length": 4,
    "separator": "-",
    "max_activations": 1,
    "label": "Integration batch",
}

KEY_PATTERN = re.compile(
    r"^NXR-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$"
)


def activate(app, key: str, device: str, account: str | None = None):
    body = {"key": key, "device_id": device}
    if account:
        body["account_id"] = account
    return app.test_client().post("/api/keys/activate", json=body)


def test_generate_reveals_plaintext_once_and_stores_hash(owner, db, admins):
    response = owner.post("/api/keys/generate", DEFAULT_BODY)
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["quantity"] == 1
    assert "never shown again" in payload["warning"]

    plaintext = payload["keys"][0]["key"]
    assert KEY_PATTERN.match(plaintext)
    assert payload["keys"][0]["key_hint"].endswith(plaintext[-4:])

    row = db.fetch_one("SELECT * FROM license_keys")
    assert row["key_hash"] == hash_key(TEST_PEPPER, normalize_key(plaintext))
    assert plaintext not in repr(row)
    assert row["key_hint"] != plaintext
    assert row["created_by"] is not None
    assert str(row["created_by"]) == admins["owner"]["id"]
    assert str(row["batch_id"]) == payload["batch_id"]

    log = db.fetch_one("SELECT * FROM generation_logs")
    assert log["quantity"] == 1
    assert str(log["batch_id"]) == payload["batch_id"]
    assert str(log["admin_user_id"]) == admins["owner"]["id"]

    audit = db.fetch_one(
        "SELECT action, target_id FROM admin_audit_logs WHERE action = 'key.generate'"
    )
    assert audit["target_id"] == payload["batch_id"]


def test_custom_format_prefix_suffix_and_length(owner):
    body = {
        **DEFAULT_BODY,
        "quantity": 3,
        "prefix": "OMEGA",
        "suffix": "END",
        "groups": 3,
        "group_length": 5,
        "separator": "_",
    }
    response = owner.post("/api/keys/generate", body)
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["key_format"] == "OMEGA_*****_*****_*****_END"
    for entry in payload["keys"]:
        assert re.fullmatch(
            r"OMEGA_[A-HJ-NP-Z2-9]{5}_[A-HJ-NP-Z2-9]{5}_[A-HJ-NP-Z2-9]{5}_END",
            entry["key"],
        )
    assert len({entry["key"] for entry in payload["keys"]}) == 3


def test_bulk_generation_and_limit(owner, db):
    response = owner.post("/api/keys/bulk-generate", {**DEFAULT_BODY, "quantity": 40})
    assert response.status_code == 201
    payload = response.get_json()
    assert len({entry["key"] for entry in payload["keys"]}) == 40
    assert db.fetch_value("SELECT count(*) FROM license_keys") == 40

    too_many = owner.post(
        "/api/keys/bulk-generate", {**DEFAULT_BODY, "quantity": 501}
    )
    assert too_many.status_code == 422
    assert "quantity" in too_many.get_json()["error"]["details"]["fields"]

    too_few = owner.post("/api/keys/bulk-generate", {**DEFAULT_BODY, "quantity": 1})
    assert too_few.status_code == 422


def test_generation_input_validation(owner):
    cases = [
        {**DEFAULT_BODY, "quantity": 0},
        {**DEFAULT_BODY, "groups": 2, "group_length": 3},
        {**DEFAULT_BODY, "prefix": "BAD!"},
        {**DEFAULT_BODY, "separator": "/"},
        {**DEFAULT_BODY, "max_activations": 10001},
        {**DEFAULT_BODY, "metadata": ["not", "an", "object"]},
        {**DEFAULT_BODY, "expires_at": "2020-01-01"},
    ]
    for body in cases:
        response = owner.post("/api/keys/generate", body)
        assert response.status_code == 422, (body, response.get_json())

    malformed = owner.client.post(
        "/api/keys/generate",
        data="{not json",
        content_type="application/json",
        headers={"X-CSRF-Token": owner.csrf},
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["error"]["code"] == "invalid_json"


def test_listing_masks_keys_and_paginates(owner, create_key):
    for index in range(5):
        create_key(label=f"Batch {index}")
    listing = owner.get("/api/keys?page=1&page_size=2").get_json()
    assert listing["pagination"]["total"] == 5
    assert listing["pagination"]["total_pages"] == 3
    assert len(listing["items"]) == 2
    for item in listing["items"]:
        assert "key" not in item
        assert "key_hash" not in item
        assert item["key_hint"]
        assert "****" in item["key_format"]

    second = owner.get("/api/keys?page=2&page_size=2").get_json()
    assert second["pagination"]["page"] == 2
    assert len(second["items"]) == 2


def test_search_by_label_full_key_and_status(owner, app, create_key):
    batch = create_key(label="Season seven roster", max_activations=1)
    plaintext = batch["keys"][0]["key"]

    by_label = owner.get("/api/keys?search=seven").get_json()
    assert by_label["pagination"]["total"] == 1

    by_key = owner.get(f"/api/keys?search={plaintext}").get_json()
    assert by_key["pagination"]["total"] == 1
    assert by_key["items"][0]["id"] == batch["keys"][0]["id"]

    activate_response = activate(app, plaintext, "device-search-1234")
    assert activate_response.status_code == 200

    used = owner.get("/api/keys?status=used").get_json()
    assert used["pagination"]["total"] == 1
    active = owner.get("/api/keys?status=active").get_json()
    assert active["pagination"]["total"] == 0

    invalid = owner.get("/api/keys?status=bogus")
    assert invalid.status_code == 422
    invalid_sort = owner.get("/api/keys?sort=key_hash")
    assert invalid_sort.status_code == 422


def test_key_detail_includes_activations_events_and_batch(owner, app, create_key):
    batch = create_key(max_activations=2)
    plaintext = batch["keys"][0]["key"]
    assert activate(app, plaintext, "device-detail-1234").status_code == 200

    detail = owner.get(f"/api/keys/{batch['keys'][0]['id']}")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["activation_count"] == 1
    assert len(payload["activations"]) == 1
    assert payload["activations"][0]["device_hint"].endswith("1234")
    assert any(event["action"] == "key.activate" for event in payload["events"])
    assert payload["batch"]["quantity"] == 1

    missing = owner.get("/api/keys/00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404


def test_patch_key_fields_and_transition_rules(owner, app, create_key):
    batch = create_key(max_activations=3)
    key_id = batch["keys"][0]["id"]

    renamed = owner.patch(f"/api/keys/{key_id}", {"label": "Renamed batch"})
    assert renamed.status_code == 200
    assert renamed.get_json()["label"] == "Renamed batch"

    expanded = owner.patch(f"/api/keys/{key_id}", {"max_activations": 5})
    assert expanded.get_json()["max_activations"] == 5

    paused = owner.patch(f"/api/keys/{key_id}", {"status": "inactive"})
    assert paused.get_json()["status"] == "inactive"
    resumed = owner.patch(f"/api/keys/{key_id}", {"status": "active"})
    assert resumed.get_json()["status"] == "active"

    assert (
        owner.patch(f"/api/keys/{key_id}", {"status": "revoked"}).status_code == 422
    )
    assert owner.patch(f"/api/keys/{key_id}", {"status": "used"}).status_code == 422
    assert owner.patch(f"/api/keys/{key_id}", {}).status_code == 422
    assert (
        owner.patch(f"/api/keys/{key_id}", {"unexpected": "field"}).status_code == 422
    )

    plaintext = batch["keys"][0]["key"]
    assert activate(app, plaintext, "device-one-1111").status_code == 200
    assert activate(app, plaintext, "device-two-2222").status_code == 200
    lowered = owner.patch(f"/api/keys/{key_id}", {"max_activations": 1})
    assert lowered.status_code == 422
    assert "max_activations" in lowered.get_json()["error"]["details"]["fields"]


def test_expired_key_cannot_be_reactivated_without_new_expiry(owner, db, create_key):
    batch = create_key()
    key_id = batch["keys"][0]["id"]
    db.execute(
        "UPDATE license_keys SET expires_at = now() - interval '1 day' WHERE id = %s",
        (key_id,),
    )
    owner.get("/api/keys")  # triggers lazy expiry
    assert db.fetch_value("SELECT status FROM license_keys WHERE id = %s", (key_id,)) == "expired"

    attempt = owner.patch(f"/api/keys/{key_id}", {"status": "active"})
    assert attempt.status_code == 422

    extended = owner.patch(
        f"/api/keys/{key_id}", {"expires_at": "2035-01-01", "status": "active"}
    )
    assert extended.status_code == 200
    assert extended.get_json()["status"] == "active"


def test_export_is_masked_and_csv_injection_safe(owner, create_key, db):
    create_key(label="=cmd|' /C calc'!A0")
    response = owner.get("/api/keys/export")
    assert response.status_code == 200
    assert "text/csv" in response.headers["Content-Type"]
    assert "attachment" in response.headers["Content-Disposition"]
    text = response.get_data(as_text=True)
    assert text.startswith("\ufeffkey_hint")
    assert "'=cmd" in text
    assert "****" in text
    assert db.fetch_value("SELECT count(*) FROM license_keys") == 1
    # No plaintext or hash columns are exported.
    assert "key_hash" not in text
    assert "NXR-" not in text or "****" in text


def test_export_audit_row_is_written(owner, create_key, db):
    create_key()
    owner.get("/api/keys/export")
    row = db.fetch_one(
        "SELECT action, outcome FROM admin_audit_logs WHERE action = 'key.export'"
    )
    assert row["action"] == "key.export"
    assert row["outcome"] == "success"


def test_delete_removes_key_and_activations(owner, app, create_key, db):
    batch = create_key(max_activations=1)
    key_id = batch["keys"][0]["id"]
    assert activate(app, batch["keys"][0]["key"], "device-delete-1234").status_code == 200

    response = owner.delete(f"/api/keys/{key_id}")
    assert response.status_code == 200
    assert response.get_json()["deleted"] is True
    assert db.fetch_value("SELECT count(*) FROM license_keys") == 0
    assert db.fetch_value("SELECT count(*) FROM key_activations") == 0
    audit = db.fetch_one(
        "SELECT action, target_id FROM admin_audit_logs WHERE action = 'key.delete'"
    )
    assert audit["target_id"] == key_id


def test_generation_metadata_is_preserved(owner, db):
    response = owner.post(
        "/api/keys/generate",
        {**DEFAULT_BODY, "metadata": {"season": 7, "tier": "premium"}},
    )
    assert response.status_code == 201
    metadata = db.fetch_value("SELECT metadata FROM license_keys")
    assert metadata["season"] == 7
    assert metadata["tier"] == "premium"
