from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.db import is_unique_violation, sqlstate

from conftest import authenticate_sms_device, redeem_sms_key


def _json(response):
    payload = response.get_json()
    assert payload is not None
    return payload


def _activation_key(sms_key, **kwargs):
    return sms_key(**kwargs)["keys"][0]


def test_redeem_binds_one_device_and_returns_a_one_time_credential(sms_app, db, sms_key):
    entry = _activation_key(sms_key, reader_name="")
    response = redeem_sms_key(
        sms_app, entry["key"], device_id="android-reader-77", reader_name="Counter 7"
    )
    assert response.status_code == 200
    payload = _json(response)
    assert payload["reader"]["id"] == entry["id"]
    assert payload["reader"]["device_id"] == "android-reader-77"
    assert payload["reader"]["reader_name"] == "Counter 7"
    assert payload["reader"]["status"] == "activated"
    assert payload["token_type"] == "Bearer"
    assert payload["heartbeat_interval_seconds"] == 300
    assert len(payload["device_token"]) >= 40
    assert payload["device_token"] != entry["key"]
    assert entry["key"] not in json.dumps(payload)

    row = db.fetch_one("SELECT * FROM sms_reader_keys WHERE id = %s", (entry["id"],))
    assert row["status"] == "activated"
    assert row["device_id"] == "android-reader-77"
    assert row["reader_name"] == "Counter 7"
    assert row["activated_at"] is not None
    assert row["last_seen_at"] is not None
    assert len(row["device_token_hash"]) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", row["device_token_hash"])
    assert payload["device_token"] not in repr(row)
    assert row["key_hash"] != entry["key"]

    event = db.fetch_one(
        "SELECT event_type, outcome, device_id FROM sms_reader_events "
        "WHERE key_id = %s AND event_type = 'redeemed'",
        (entry["id"],),
    )
    assert event["outcome"] == "success"
    assert event["device_id"] == "android-reader-77"


def test_second_redemption_is_refused_without_invalidating_the_first(sms_app, sms_key):
    entry = _activation_key(sms_key)
    first = redeem_sms_key(sms_app, entry["key"], device_id="first-device")
    assert first.status_code == 200
    token = _json(first)["device_token"]

    second = redeem_sms_key(sms_app, entry["key"], device_id="second-device")
    assert second.status_code == 409
    assert _json(second)["error"]["code"] == "already_redeemed"
    assert "device_token" not in json.dumps(_json(second))

    auth = authenticate_sms_device(sms_app, token, device_id="first-device")
    assert auth.status_code == 200


def test_invalid_and_revoked_keys_cannot_be_redeemed(sms_app, sms_key, sms_owner):
    malformed = redeem_sms_key(sms_app, "NEXORA-!!!!-!!!!-!!!!-!!!!")
    assert malformed.status_code == 404
    assert _json(malformed)["error"]["code"] == "invalid_key"

    unknown = redeem_sms_key(sms_app, "NEXORA-ABCD-EFGH-JKLM-NPQR")
    assert unknown.status_code == 404

    entry = _activation_key(sms_key)
    assert (
        sms_owner.post(
            f"/api/keys/{entry['id']}/revoke", {"reason": "Key leaked during handoff"}
        ).status_code
        == 200
    )
    revoked = redeem_sms_key(sms_app, entry["key"])
    assert revoked.status_code == 409
    assert _json(revoked)["error"]["code"] == "key_revoked"


def test_one_reader_device_cannot_hold_two_active_keys(sms_app, sms_key):
    first = _activation_key(sms_key, reader_name="First key")
    second = _activation_key(sms_key, reader_name="Second key")
    assert redeem_sms_key(sms_app, first["key"], device_id="shared-reader").status_code == 200
    conflict = redeem_sms_key(sms_app, second["key"], device_id="shared-reader")
    assert conflict.status_code == 409
    assert _json(conflict)["error"]["code"] == "device_already_bound"


def test_authenticate_binds_token_to_device_and_supports_rotation(sms_app, sms_key):
    entry = _activation_key(sms_key, reader_name="Rotating reader")
    token = _json(
        redeem_sms_key(sms_app, entry["key"], device_id="rotate-device")
    )["device_token"]

    good = authenticate_sms_device(sms_app, token, device_id="rotate-device")
    assert good.status_code == 200
    assert _json(good)["reader"]["reader_name"] == "Rotating reader"
    assert _json(good)["token_rotated"] is False

    wrong_device = authenticate_sms_device(sms_app, token, device_id="different-device")
    assert wrong_device.status_code == 401
    assert _json(wrong_device)["error"]["code"] == "invalid_device_credential"

    wrong_token = authenticate_sms_device(
        sms_app, "A" * 43, device_id="rotate-device"
    )
    assert wrong_token.status_code == 401

    rotated = authenticate_sms_device(
        sms_app, token, device_id="rotate-device", rotate_token=True
    )
    assert rotated.status_code == 200
    rotated_payload = _json(rotated)
    assert rotated_payload["token_rotated"] is True
    new_token = rotated_payload["device_token"]
    assert new_token != token

    old = authenticate_sms_device(sms_app, token, device_id="rotate-device")
    assert old.status_code == 401
    new = authenticate_sms_device(sms_app, new_token, device_id="rotate-device")
    assert new.status_code == 200


def test_authenticate_accepts_bearer_and_device_headers(sms_app, sms_key):
    entry = _activation_key(sms_key)
    token = _json(
        redeem_sms_key(sms_app, entry["key"], device_id="header-device")
    )["device_token"]

    response = sms_app.test_client().post(
        "/api/reader/authenticate",
        json={},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": "header-device",
        },
    )
    assert response.status_code == 200
    assert _json(response)["reader"]["id"] == entry["id"]

    x_token = sms_app.test_client().post(
        "/api/reader/authenticate",
        json={"device_id": "header-device"},
        headers={"X-Device-Token": token},
    )
    assert x_token.status_code == 200


def test_heartbeat_updates_last_seen_and_logs_at_the_configured_cadence(
    sms_app, db, sms_key
):
    entry = _activation_key(sms_key)
    token = _json(
        redeem_sms_key(sms_app, entry["key"], device_id="heartbeat-device")
    )["device_token"]

    response = sms_app.test_client().post(
        "/api/reader/heartbeat",
        json={"app_version": "1.4.2"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": "heartbeat-device",
        },
    )
    assert response.status_code == 200
    payload = _json(response)
    assert payload["status"] == "ok"
    assert payload["reader"]["id"] == entry["id"]
    assert payload["server_time"]
    assert payload["heartbeat_interval_seconds"] == 300
    assert (
        db.fetch_value(
            "SELECT count(*) FROM sms_reader_events "
            "WHERE key_id = %s AND event_type = 'heartbeat'",
            (entry["id"],),
        )
        == 0
    )

    # Age the last seen timestamp beyond the 60-second logging cadence.
    db.execute(
        "UPDATE sms_reader_keys SET last_seen_at = now() - interval '10 minutes' "
        "WHERE id = %s",
        (entry["id"],),
    )
    logged = sms_app.test_client().post(
        "/api/reader/heartbeat",
        json={"app_version": "1.4.2"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": "heartbeat-device",
        },
    )
    assert logged.status_code == 200
    event = db.fetch_one(
        "SELECT details FROM sms_reader_events "
        "WHERE key_id = %s AND event_type = 'heartbeat'",
        (entry["id"],),
    )
    assert event["details"]["app_version"] == "1.4.2"

    wrong_device = sms_app.test_client().post(
        "/api/reader/heartbeat",
        json={},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": "intruder-device",
        },
    )
    assert wrong_device.status_code == 401


def test_revocation_immediately_invalidates_authentication_and_heartbeat(
    sms_app, sms_key, sms_owner
):
    entry = _activation_key(sms_key)
    token = _json(
        redeem_sms_key(sms_app, entry["key"], device_id="revoked-device")
    )["device_token"]
    assert (
        sms_owner.post(
            f"/api/keys/{entry['id']}/revoke", {"reason": "Lost device"}
        ).status_code
        == 200
    )

    auth = authenticate_sms_device(sms_app, token, device_id="revoked-device")
    assert auth.status_code == 401
    beat = sms_app.test_client().post(
        "/api/reader/heartbeat",
        json={},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": "revoked-device",
        },
    )
    assert beat.status_code == 401


def test_concurrent_redemption_of_the_same_key_has_exactly_one_winner(
    sms_app, db, sms_key
):
    entry = _activation_key(sms_key)
    barrier = threading.Barrier(2)

    def attempt(device_id: str):
        client = sms_app.test_client()
        barrier.wait(timeout=10)
        response = client.post(
            "/api/reader/redeem",
            json={
                "key": entry["key"],
                "device_id": device_id,
                "reader_name": device_id,
            },
        )
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["race-device-a", "race-device-b"]))

    statuses = sorted(status for status, _ in results)
    assert statuses == [200, 409]
    winner = next(payload for status, payload in results if status == 200)
    loser = next(payload for status, payload in results if status == 409)
    assert loser["error"]["code"] == "already_redeemed"
    assert db.fetch_value(
        "SELECT count(*) FROM sms_reader_keys WHERE id = %s AND status = 'activated'",
        (entry["id"],),
    ) == 1
    assert db.fetch_value(
        "SELECT count(*) FROM sms_reader_events "
        "WHERE key_id = %s AND event_type = 'redeemed'",
        (entry["id"],),
    ) == 2
    assert (
        db.fetch_value(
            "SELECT count(*) FROM sms_reader_events "
            "WHERE key_id = %s AND event_type = 'redeemed' AND outcome = 'success'",
            (entry["id"],),
        )
        == 1
    )

    auth = authenticate_sms_device(
        sms_app, winner["device_token"], device_id=winner["reader"]["device_id"]
    )
    assert auth.status_code == 200


def test_concurrent_redemption_of_one_device_has_exactly_one_winner(sms_app, db, sms_key):
    first = _activation_key(sms_key)
    second = _activation_key(sms_key)
    barrier = threading.Barrier(2)

    def attempt(entry):
        client = sms_app.test_client()
        barrier.wait(timeout=10)
        response = client.post(
            "/api/reader/redeem",
            json={
                "key": entry["key"],
                "device_id": "contended-device",
                "reader_name": "Contended reader",
            },
        )
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [first, second]))

    assert sorted(status for status, _ in results) == [200, 409]
    loser = next(payload for status, payload in results if status == 409)
    assert loser["error"]["code"] == "device_already_bound"
    assert db.fetch_value(
        "SELECT count(*) FROM sms_reader_keys "
        "WHERE device_id = 'contended-device' AND status = 'activated'"
    ) == 1


def test_database_constraints_reject_duplicate_hashes_and_invalid_states(db):
    duplicate = "a" * 64
    db.execute(
        "INSERT INTO sms_reader_keys (key_hash, status) VALUES (%s, 'generated')",
        (duplicate,),
    )
    with pytest.raises(Exception) as duplicate_error:
        db.execute(
            "INSERT INTO sms_reader_keys (key_hash, status) VALUES (%s, 'generated')",
            (duplicate,),
        )
    assert is_unique_violation(duplicate_error.value)

    with pytest.raises(Exception) as state_error:
        db.execute(
            "INSERT INTO sms_reader_keys (key_hash, status) VALUES (%s, 'activated')",
            ("b" * 64,),
        )
    assert sqlstate(state_error.value) == "23514"

    with pytest.raises(Exception) as status_error:
        db.execute(
            "INSERT INTO sms_reader_keys (key_hash, status) VALUES (%s, 'pending')",
            ("c" * 64,),
        )
    assert sqlstate(status_error.value) == "23514"
