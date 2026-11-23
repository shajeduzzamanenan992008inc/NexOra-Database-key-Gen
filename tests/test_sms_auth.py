from __future__ import annotations

import json
from dataclasses import replace

from backend.security.sessions import token_digest
import pytest
try:
    from sms_reader import create_sms_app
    from sms_reader.config import SmsConfig
except ImportError:
    pytest.skip(
        "optional SMS Reader console package sms_reader is not installed; "
        "NexOra Reader API coverage lives in tests/test_reader_api_lifecycle.py",
        allow_module_level=True,
    )

from conftest import (
    ADMIN_EMAIL,
    COOKIE_NAME,
    SMS_COOKIE_NAME,
    SMS_PEPPER,
    TEST_PASSWORD,
    VIEWER_EMAIL,
    sms_login,
)


def test_sms_login_sets_hardened_audience_scoped_cookie(sms_app, admins):
    client = sms_app.test_client()
    response = client.post(
        "/api/admin/login",
        json={"email": ADMIN_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["email"] == ADMIN_EMAIL
    assert payload["surface"] == "sms_reader"
    assert payload["csrf_token"]

    cookie_header = response.headers.get("Set-Cookie")
    assert "HttpOnly" in cookie_header
    assert "SameSite=Strict" in cookie_header
    assert "Path=/" in cookie_header
    assert SMS_COOKIE_NAME in cookie_header
    assert COOKIE_NAME not in cookie_header

    cookie = client.get_cookie(SMS_COOKIE_NAME)
    assert cookie is not None
    row = sms_app.extensions["db"].fetch_one(
        "SELECT audience, token_hash FROM admin_sessions WHERE admin_user_id = %s",
        (admins["admin"]["id"],),
    )
    assert row["audience"] == "sms_reader"
    assert row["token_hash"] == token_digest(cookie.value)
    assert cookie.value not in json.dumps(row, default=str)


def test_sms_session_endpoint_rotates_csrf_and_reports_surface(sms_owner):
    first = sms_owner.get("/api/admin/session")
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["surface"] == "sms_reader"
    assert first_payload["user"]["email"] == "owner@nexora.test"

    second = sms_owner.get("/api/admin/session")
    assert second.status_code == 200
    assert second.get_json()["csrf_token"] != first_payload["csrf_token"]

    # The rotated token is the one that authorizes the next mutation.
    sms_owner.csrf = second.get_json()["csrf_token"]
    response = sms_owner.post("/api/keys/generate", {"quantity": 1})
    assert response.status_code == 201, response.get_json()


def test_session_cookies_cannot_cross_console_audiences(app, sms_app, owner, sms_owner):
    # A license-console token replayed under the SMS cookie name is rejected.
    license_token = owner.client.get_cookie(COOKIE_NAME).value
    replay = sms_app.test_client()
    replay.set_cookie(SMS_COOKIE_NAME, license_token)
    assert replay.get("/api/admin/session").status_code == 401
    assert replay.get("/api/keys").status_code == 401
    assert replay.get("/dashboard").status_code == 302

    # An SMS-console token replayed under the license cookie name is rejected.
    sms_token = sms_owner.client.get_cookie(SMS_COOKIE_NAME).value
    replay_license = app.test_client()
    replay_license.set_cookie(COOKIE_NAME, sms_token)
    assert replay_license.get("/api/auth/session").status_code == 401

    # Each token remains valid on its own surface only.
    assert owner.client.get("/api/auth/session").status_code == 200
    assert sms_owner.client.get("/api/admin/session").status_code == 200


def test_sms_mutations_require_session_role_and_csrf(sms_app, admins, sms_owner, sms_viewer):
    anonymous = sms_app.test_client()
    blocked = anonymous.post("/api/keys/generate", json={"quantity": 1})
    assert blocked.status_code == 401
    assert blocked.get_json()["error"]["code"] == "not_authenticated"

    no_csrf = sms_owner.post("/api/keys/generate", {"quantity": 1}, csrf=False)
    assert no_csrf.status_code == 403
    assert no_csrf.get_json()["error"]["code"] == "csrf_failed"

    wrong_csrf = sms_owner.request(
        "POST",
        "/api/keys/generate",
        {"quantity": 1},
        csrf=False,
        headers={"X-CSRF-Token": "not-the-token"},
    )
    assert wrong_csrf.status_code == 403
    assert wrong_csrf.get_json()["error"]["code"] == "csrf_failed"

    forbidden = sms_viewer.post("/api/keys/generate", {"quantity": 1})
    assert forbidden.status_code == 403
    assert forbidden.get_json()["error"]["code"] == "forbidden"

    # Viewers may read but not issue, revoke or delete.
    created = sms_owner.post(
        "/api/keys/generate", {"quantity": 1, "reader_name": "Viewer target"}
    )
    assert created.status_code == 201
    key_id = created.get_json()["keys"][0]["id"]
    assert sms_viewer.get("/api/keys").status_code == 200
    assert sms_viewer.get(f"/api/keys/{key_id}").status_code == 200
    assert (
        sms_viewer.post(
            f"/api/keys/{key_id}/revoke", {"reason": "viewer should not"}
        ).status_code
        == 403
    )
    assert sms_viewer.delete(f"/api/keys/{key_id}").status_code == 403


def test_sms_logout_revokes_only_that_session(sms_app, admins):
    first = sms_login(sms_app, ADMIN_EMAIL)
    second = sms_login(sms_app, ADMIN_EMAIL)
    first_session = first.get("/api/admin/session")
    second_session = second.get("/api/admin/session")
    assert first_session.status_code == 200
    assert second_session.status_code == 200
    first.csrf = first_session.get_json()["csrf_token"]
    second.csrf = second_session.get_json()["csrf_token"]

    logout = first.post("/api/admin/logout")
    assert logout.status_code == 200
    assert logout.get_json()["ok"] is True
    assert first.get("/api/admin/session").status_code == 401
    assert second.get("/api/admin/session").status_code == 200

    rows = sms_app.extensions["db"].fetch_all(
        "SELECT audience, revoked_at FROM admin_sessions ORDER BY created_at"
    )
    assert all(row["audience"] == "sms_reader" for row in rows)
    assert sum(1 for row in rows if row["revoked_at"] is not None) == 1


def test_sms_login_errors_are_generic_and_rate_limited(test_settings, admins):
    limited = create_sms_app(
        replace(test_settings, rate_limit_enabled=True),
        sms_config=SmsConfig(
            key_pepper=SMS_PEPPER, max_bulk_keys=10, heartbeat_log_seconds=60
        ),
    )
    limited.config["TESTING"] = True
    try:
        client = limited.test_client()
        wrong = client.post(
            "/api/admin/login",
            json={"email": ADMIN_EMAIL, "password": "wrong-password"},
        )
        assert wrong.status_code == 401
        assert wrong.get_json()["error"]["code"] == "invalid_credentials"
        assert (
            wrong.get_json()["error"]["message"]
            == "Email or password is incorrect."
        )

        for _ in range(9):
            client.post(
                "/api/admin/login",
                json={"email": ADMIN_EMAIL, "password": "wrong-password"},
            )
        blocked = client.post(
            "/api/admin/login",
            json={"email": ADMIN_EMAIL, "password": TEST_PASSWORD},
        )
        assert blocked.status_code == 429
        assert blocked.get_json()["error"]["code"] == "rate_limited"
    finally:
        limited.extensions["db"].close()


def test_sms_auth_responses_never_expose_credentials(sms_app, admins):
    client = sms_app.test_client()
    login = client.post(
        "/api/admin/login",
        json={"email": VIEWER_EMAIL, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    session = client.get("/api/admin/session")
    assert session.status_code == 200

    for response in (login, session):
        body = json.dumps(response.get_json())
        for forbidden in (
            "password_hash",
            "$argon2",
            "scrypt$",
            "ADMIN_PASSWORD",
            "SECRET_KEY",
            "DATABASE_URL",
            "SERVICE_ROLE",
            "SUPABASE",
            SMS_PEPPER,
        ):
            assert forbidden not in body
