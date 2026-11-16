from __future__ import annotations

import re
import uuid

from conftest import redeem_sms_key

KEY_RE = re.compile(r"^NEXORA(?:-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){4}$")


def _json(response):
    payload = response.get_json()
    assert payload is not None
    return payload


def test_generate_stores_only_a_digest_and_masked_hint(sms_app, db, sms_key):
    result = sms_key(reader_name="Dhaka counter · SMS reader 01")
    assert result["quantity"] == 1
    assert result["batch_id"]
    assert result["reader_name"] == "Dhaka counter · SMS reader 01"
    assert "never stored" in result["warning"]
    entry = result["keys"][0]
    assert KEY_RE.fullmatch(entry["key"])
    assert entry["status"] == "generated"
    assert entry["key_hint"] == f"NEXORA-****-****-****-{entry['key'][-4:]}"
    assert entry["key_hint"] != entry["key"]
    assert entry["id"] == str(uuid.UUID(entry["id"]))

    row = db.fetch_one("SELECT * FROM sms_reader_keys WHERE id = %s", (entry["id"],))
    assert len(row["key_hash"]) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", row["key_hash"])
    assert row["key_hint"] == entry["key_hint"]
    assert row["status"] == "generated"
    assert row["device_id"] is None
    assert row["device_token_hash"] is None
    assert entry["key"] not in repr(row)

    event = db.fetch_one(
        "SELECT event_type, outcome, details FROM sms_reader_events WHERE key_id = %s",
        (entry["id"],),
    )
    assert event["event_type"] == "generated"
    assert event["outcome"] == "success"
    assert event["details"]["batch_id"] == result["batch_id"]

    audit = db.fetch_one(
        "SELECT action, outcome, target_id FROM admin_audit_logs "
        "WHERE action = 'sms.key.generate'"
    )
    assert audit["outcome"] == "success"
    assert audit["target_id"] == result["batch_id"]


def test_bulk_generate_is_unique_and_paginates(sms_app, db, sms_owner, sms_key):
    result = sms_key(quantity=8, reader_name="Production rollout")
    keys = [entry["key"] for entry in result["keys"]]
    assert len(keys) == 8
    assert len(set(keys)) == 8

    stored = db.fetch_value(
        "SELECT count(*) FROM sms_reader_keys WHERE batch_id = %s",
        (result["batch_id"],),
    )
    assert stored == 8

    first = sms_owner
    listing = first.get("/api/keys?status=generated&page_size=5")
    assert listing.status_code == 200
    payload = _json(listing)
    assert payload["pagination"]["total"] == 8
    assert payload["pagination"]["total_pages"] == 2
    assert payload["pagination"]["has_next"] is True
    assert len(payload["items"]) == 5
    assert payload["filters"]["status"] == "generated"

    second = first.get("/api/keys?status=generated&page=2&page_size=5")
    assert len(_json(second)["items"]) == 3

    # Searching by a full key resolves through the HMAC digest, never a
    # plaintext column.
    exact = first.get(f"/api/keys?search={keys[0]}")
    assert _json(exact)["pagination"]["total"] == 1
    assert _json(exact)["items"][0]["key_hint"] == result["keys"][0]["key_hint"]


def test_generation_validation_rejects_bad_quantities_and_fields(sms_owner):
    zero = sms_owner.post("/api/keys/generate", {"quantity": 0})
    assert zero.status_code == 422
    assert "quantity" in _json(zero)["error"]["details"]["fields"]

    too_many = sms_owner.post("/api/keys/generate", {"quantity": 26})
    assert too_many.status_code == 422

    bulk_one = sms_owner.post("/api/keys/bulk-generate", {"quantity": 1})
    assert bulk_one.status_code == 422

    unknown = sms_owner.post("/api/keys/generate", {"quantity": 1, "status": "activated"})
    assert unknown.status_code == 422
    assert "_body" in _json(unknown)["error"]["details"]["fields"]

    long_name = sms_owner.post(
        "/api/keys/generate", {"quantity": 1, "reader_name": "x" * 121}
    )
    assert long_name.status_code == 422


def test_revoke_activated_reader_clears_credential(sms_app, db, sms_key, sms_owner):
    result = sms_key(reader_name="Revocation target")
    entry = result["keys"][0]
    redeemed = redeem_sms_key(sms_app, entry["key"])
    assert redeemed.status_code == 200
    token = _json(redeemed)["device_token"]

    revoked = sms_owner.post(
        f"/api/keys/{entry['id']}/revoke", {"reason": "Device was decommissioned"}
    )
    assert revoked.status_code == 200
    payload = _json(revoked)
    assert payload["status"] == "revoked"
    assert payload["previous_status"] == "activated"
    assert payload["device_credential_revoked"] is True

    row = db.fetch_one(
        "SELECT status, device_token_hash, revoked_at, revoked_reason "
        "FROM sms_reader_keys WHERE id = %s",
        (entry["id"],),
    )
    assert row["status"] == "revoked"
    assert row["device_token_hash"] is None
    assert row["revoked_at"] is not None
    assert row["revoked_reason"] == "Device was decommissioned"

    auth = sms_app.test_client().post(
        "/api/reader/authenticate",
        json={"device_token": token, "device_id": "nexora-reader-001"},
    )
    assert auth.status_code == 401

    again = sms_owner.post(
        f"/api/keys/{entry['id']}/revoke", {"reason": "Second attempt"}
    )
    assert again.status_code == 409
    assert _json(again)["error"]["code"] == "already_revoked"


def test_delete_is_limited_to_unused_keys(sms_app, db, sms_key, sms_owner):
    unused = sms_key(reader_name="Unused target")["keys"][0]
    deleted = sms_owner.delete(f"/api/keys/{unused['id']}")
    assert deleted.status_code == 200
    assert _json(deleted)["deleted"] is True
    assert db.fetch_value(
        "SELECT count(*) FROM sms_reader_keys WHERE id = %s", (unused["id"],)
    ) == 0
    audit = db.fetch_one(
        "SELECT outcome FROM admin_audit_logs WHERE action = 'sms.key.delete'"
    )
    assert audit["outcome"] == "success"

    activated = sms_key(reader_name="Activated target")["keys"][0]
    assert redeem_sms_key(sms_app, activated["key"]).status_code == 200
    blocked = sms_owner.delete(f"/api/keys/{activated['id']}")
    assert blocked.status_code == 409
    assert _json(blocked)["error"]["code"] == "key_not_unused"
    assert db.fetch_value(
        "SELECT count(*) FROM sms_reader_keys WHERE id = %s", (activated["id"],)
    ) == 1
    failed_audit = db.fetch_one(
        "SELECT outcome, details FROM admin_audit_logs "
        "WHERE action = 'sms.key.delete' AND outcome = 'failure'"
    )
    assert failed_audit["details"]["reason"] == "key_not_unused"

    revoked = sms_key(reader_name="Revoked target")["keys"][0]
    assert (
        sms_owner.post(
            f"/api/keys/{revoked['id']}/revoke", {"reason": "Retire generated key"}
        ).status_code
        == 200
    )
    revoked_delete = sms_owner.delete(f"/api/keys/{revoked['id']}")
    assert revoked_delete.status_code == 409


def test_key_detail_unknown_id_and_event_history(sms_app, sms_owner, sms_key):
    missing = sms_owner.get(f"/api/keys/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert _json(missing)["error"]["code"] == "key_not_found"

    result = sms_key(quantity=2, reader_name="Detail target")
    entry = result["keys"][0]
    assert redeem_sms_key(sms_app, entry["key"]).status_code == 200

    detail = sms_owner.get(f"/api/keys/{entry['id']}")
    assert detail.status_code == 200
    payload = _json(detail)
    assert payload["batch_count"] == 2
    assert payload["reader_name"] == "Detail target"
    assert payload["device_id"] == "nexora-reader-001"
    labels = [event["label"] for event in payload["events"]]
    assert "Reader redeemed" in labels
    assert "Key generated" in labels


def test_online_filter_uses_recent_heartbeat_window(
    sms_app, db, sms_owner, sms_key
):
    first = sms_key(reader_name="Online reader")["keys"][0]
    second = sms_key(reader_name="Recent but stale")["keys"][0]
    assert redeem_sms_key(sms_app, first["key"], device_id="online-device").status_code == 200
    assert redeem_sms_key(sms_app, second["key"], device_id="stale-device").status_code == 200
    db.execute(
        "UPDATE sms_reader_keys SET last_seen_at = now() - interval '20 minutes' "
        "WHERE id = %s",
        (second["id"],),
    )

    client = sms_owner
    online = client.get("/api/keys?online=1&sort=last_seen_at&order=desc")
    assert online.status_code == 200
    payload = _json(online)
    assert payload["pagination"]["total"] == 1
    assert payload["items"][0]["id"] == first["id"]
    assert payload["filters"]["online"] is True

    stalled = client.get("/api/keys?online=0&status=generated")
    assert stalled.status_code == 200
    assert _json(stalled)["pagination"]["total"] == 0

    invalid = client.get("/api/keys?online=maybe")
    assert invalid.status_code == 422


def test_search_and_sort_are_parameterized(sms_app, db, sms_owner, sms_key):
    result = sms_key(quantity=3, reader_name="Search target")
    injection = sms_owner.get(
        "/api/keys",
        query_string={"search": "%' OR 1=1; DROP TABLE sms_reader_keys; --"},
    )
    assert injection.status_code == 200
    assert _json(injection)["pagination"]["total"] == 0
    assert db.fetch_value("SELECT count(*) FROM sms_reader_keys") == 3

    by_hint = sms_owner.get(
        "/api/keys", query_string={"search": result["keys"][0]["key_hint"]}
    )
    assert _json(by_hint)["pagination"]["total"] == 1

    by_name = sms_owner.get(
        "/api/keys", query_string={"search": "search target", "sort": "reader_name"}
    )
    assert _json(by_name)["pagination"]["total"] == 3

    bad_sort = sms_owner.get("/api/keys?sort=key_hash")
    assert bad_sort.status_code == 422
    bad_order = sms_owner.get("/api/keys?order=sideways")
    assert bad_order.status_code == 422


def test_stats_reflect_key_lifecycle(sms_app, sms_owner, sms_key):
    first = sms_key(reader_name="Stats one")["keys"][0]
    sms_key(reader_name="Stats two")
    assert redeem_sms_key(sms_app, first["key"]).status_code == 200

    response = sms_owner.get("/api/dashboard/stats")
    assert response.status_code == 200
    payload = _json(response)
    assert payload["totals"]["total"] == 2
    assert payload["totals"]["unused"] == 1
    assert payload["totals"]["activated"] == 1
    assert payload["totals"]["revoked"] == 0
    assert payload["totals"]["devices"] == 1
    assert payload["totals"]["online_readers"] == 1
    assert payload["activity"]["generated_24h"] == 2
    assert payload["activity"]["redemptions_24h"] == 1
    assert payload["activity"]["activation_rate"] == 50.0
    assert payload["configuration"]["max_bulk_keys"] == 25
    assert payload["recent_events"]
    assert payload["recent_events"][0]["key_id"] == first["id"]
