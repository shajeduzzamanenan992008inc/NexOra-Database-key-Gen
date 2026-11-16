from __future__ import annotations

import json

from backend.security.sessions import create_session, token_digest

from conftest import (
    ADMIN_EMAIL,
    COOKIE_NAME,
    TEST_PASSWORD,
    VIEWER_EMAIL,
)

VALID_BODY = {
    "quantity": 1,
    "prefix": "NXR",
    "groups": 4,
    "group_length": 4,
    "separator": "-",
    "max_activations": 1,
    "label": "Security test",
}


def test_login_sets_hardened_session_cookie(app, admins):
    client = app.test_client()
    response = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["user"]["email"] == ADMIN_EMAIL
    assert payload["user"]["role"] == "admin"
    assert payload["csrf_token"]
    cookie_header = response.headers.get("Set-Cookie")
    assert "HttpOnly" in cookie_header
    assert "SameSite=Strict" in cookie_header
    assert "Path=/" in cookie_header
    assert COOKIE_NAME in cookie_header


def test_wrong_password_returns_generic_error_and_audit_row(app, admins, db):
    client = app.test_client()
    response = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["message"] == "Email or password is incorrect."

    row = db.fetch_one(
        """
        SELECT outcome, details::text AS details
          FROM admin_audit_logs
         WHERE action = 'auth.login' AND outcome = 'failure'
         ORDER BY created_at DESC LIMIT 1
        """
    )
    assert row is not None
    assert row["outcome"] == "failure"
    assert "invalid_password" in row["details"]


def test_unknown_account_does_not_reveal_existence(app):
    response = app.test_client().post(
        "/api/auth/login",
        json={"email": "ghost@nexora.test", "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["message"] == "Email or password is incorrect."


def test_account_locks_after_five_failures(app, admins, db):
    client = app.test_client()
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": "bad-password"},
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": TEST_PASSWORD},
    )
    assert locked.status_code == 429
    assert locked.get_json()["error"]["code"] == "account_locked"

    record = db.fetch_one(
        "SELECT failed_login_attempts, locked_until FROM admin_users WHERE email = %s",
        (ADMIN_EMAIL,),
    )
    assert record["failed_login_attempts"] == 5
    assert record["locked_until"] is not None


def test_inactive_account_cannot_sign_in(app, admins, db):
    db.execute("UPDATE admin_users SET is_active = false WHERE email = %s", (VIEWER_EMAIL,))
    response = app.test_client().post(
        "/api/auth/login",
        json={"email": VIEWER_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["message"] == "Email or password is incorrect."


def test_logout_revokes_session(owner):
    assert owner.get("/api/keys").status_code == 200
    response = owner.post("/api/auth/logout", {})
    assert response.status_code == 200
    assert owner.get("/api/keys").status_code == 401


def test_session_rotation_replaces_csrf_token(owner):
    original = owner.csrf
    response = owner.get("/api/auth/session")
    assert response.status_code == 200
    rotated = response.get_json()["csrf_token"]
    assert rotated and rotated != original

    owner.csrf = original
    stale = owner.post("/api/keys/generate", VALID_BODY)
    assert stale.status_code == 403
    assert stale.get_json()["error"]["code"] == "csrf_failed"

    owner.csrf = rotated
    accepted = owner.post("/api/keys/generate", VALID_BODY)
    assert accepted.status_code == 201


def test_unauthenticated_key_generation_is_rejected(app):
    response = app.test_client().post("/api/keys/generate", json=VALID_BODY)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "not_authenticated"


def test_auth_responses_never_expose_credential_material(app, admins):
    client = app.test_client()
    login = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    session = client.get("/api/auth/session")
    assert session.status_code == 200

    for response in (login, session):
        body = json.dumps(response.get_json())
        for forbidden in (
            "password_hash",
            "password",
            "$argon2",
            "scrypt$",
            "ADMIN_PASSWORD",
            "SECRET_KEY",
            "DATABASE_URL",
            "KEY_PEPPER",
        ):
            assert forbidden not in body
        assert set(response.get_json()["user"]) == {
            "id",
            "email",
            "full_name",
            "role",
        }


def test_viewer_cannot_mutate(viewer, create_key):
    batch = create_key()
    key_id = batch["keys"][0]["id"]

    assert viewer.post("/api/keys/generate", VALID_BODY).status_code == 403
    assert (
        viewer.patch(f"/api/keys/{key_id}", {"label": "hijacked"}).status_code == 403
    )
    assert (
        viewer.post(
            "/api/keys/revoke", {"key_id": key_id, "reason": "viewer attempt"}
        ).status_code
        == 403
    )
    assert viewer.delete(f"/api/keys/{key_id}").status_code == 403
    # Read access is allowed.
    assert viewer.get("/api/keys").status_code == 200


def test_admin_cannot_delete_but_owner_can(admin, owner, create_key):
    batch = create_key()
    key_id = batch["keys"][0]["id"]
    assert admin.delete(f"/api/keys/{key_id}").status_code == 403
    assert owner.delete(f"/api/keys/{key_id}").status_code == 200


def test_missing_and_wrong_csrf_tokens_are_rejected(owner):
    missing = owner.post("/api/keys/generate", VALID_BODY, csrf=False)
    assert missing.status_code == 403
    assert missing.get_json()["error"]["code"] == "csrf_failed"

    wrong = owner.post(
        "/api/keys/generate",
        VALID_BODY,
        csrf=False,
        headers={"X-CSRF-Token": "not-the-token"},
    )
    assert wrong.status_code == 403
    assert wrong.get_json()["error"]["code"] == "csrf_failed"


def test_bearer_token_auth_is_not_csrf_susceptible(app, admins, db):
    session = create_session(
        db,
        admins["admin"]["id"],
        ttl_seconds=600,
        ip_address="127.0.0.1",
        user_agent="test",
    )
    client = app.test_client()
    response = client.post(
        "/api/keys/generate",
        json=VALID_BODY,
        headers={"Authorization": f"Bearer {session['token']}"},
    )
    assert response.status_code == 201


def test_expired_session_is_rejected(owner, db):
    cookie = owner.client.get_cookie(COOKIE_NAME)
    db.execute(
        """
        UPDATE admin_sessions
           SET expires_at = now() - interval '1 minute'
         WHERE token_hash = %s
        """,
        (token_digest(cookie.value),),
    )
    response = owner.get("/api/keys")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "session_expired"


def test_rate_limiting_blocks_login_burst(limited_app):
    client = limited_app.test_client()
    last = None
    for _ in range(11):
        last = client.post(
            "/api/auth/login",
            json={"email": "ghost@nexora.test", "password": "wrong-password"},
        )
    assert last is not None
    assert last.status_code == 429
    assert last.get_json()["error"]["code"] == "rate_limited"
    assert last.headers.get("Retry-After")


def test_security_headers_and_json_errors(app):
    client = app.test_client()
    health = client.get("/api/health")
    assert health.status_code == 200
    headers = health.headers
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"

    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert missing.is_json
    assert missing.get_json()["error"]["code"] == "not_found"

    wrong_method = client.get("/api/keys/activate")
    assert wrong_method.status_code == 405
    assert wrong_method.get_json()["error"]["code"] == "method_not_allowed"


def test_frontend_bundle_contains_no_secrets(app, owner):
    client = app.test_client()
    for path in (
        "/",
        "/dashboard",
        "/assets/js/api.js",
        "/assets/js/dashboard.js",
        "/assets/app.css",
    ):
        requester = owner.client if path == "/dashboard" else client
        response = requester.get(path)
        assert response.status_code == 200, path
        body = response.get_data(as_text=True)
        for secret in (
            "KEY_PEPPER",
            "DATABASE_URL",
            "service_role",
            "postgresql://",
            "ADMIN_PASSWORD_HASH",
            "SECRET_KEY",
            "$argon2",
            "scrypt$",
        ):
            assert secret not in body, f"{secret} leaked into {path}"


def test_sql_injection_search_is_parameterized(owner, create_key):
    create_key(label="Ordinary batch")
    attacks = [
        "/api/keys?search=%27%20OR%201%3D1%20--",
        "/api/keys?search=%25",
        "/api/keys?search=%3B%20DROP%20TABLE%20license_keys",
    ]
    for path in attacks:
        response = owner.get(path)
        assert response.status_code == 200
        assert response.get_json()["pagination"]["total"] == 0

    # The table still answers queries after the injection attempts.
    assert owner.get("/api/keys").get_json()["pagination"]["total"] == 1


def test_malicious_label_is_stored_not_executed(owner, db):
    payload = "<img src=x onerror=alert(1)>"
    response = owner.post("/api/keys/generate", {**VALID_BODY, "label": payload})
    assert response.status_code == 201
    listing = owner.get("/api/keys")
    labels = [item["label"] for item in listing.get_json()["items"]]
    assert payload in labels
    assert owner.get("/api/keys").status_code == 200
    assert db.fetch_value("SELECT count(*) FROM license_keys") == 1
