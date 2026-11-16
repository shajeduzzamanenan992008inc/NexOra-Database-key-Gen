from __future__ import annotations

from dataclasses import replace

import pytest

from backend.app import create_app
from backend.config import ConfigError
from backend.security.passwords import hash_password, verify_password
from backend.services.admin_bootstrap import ensure_initial_admin

from conftest import login

BOOTSTRAP_EMAIL = "bootstrap@nexora.test"
BOOTSTRAP_PASSWORD = "Bootstrap!Admin2026Pass"
NEW_PASSWORD = "Changed!Admin2026Pass"


@pytest.fixture
def bootstrap_settings(test_settings):
    return replace(
        test_settings,
        admin_email=BOOTSTRAP_EMAIL,
        admin_password_hash=hash_password(BOOTSTRAP_PASSWORD),
        admin_role="owner",
    )


def test_bootstrap_creates_an_argon2id_admin_from_environment(
    app, db, bootstrap_settings
):
    result = ensure_initial_admin(db, bootstrap_settings)

    assert result == {
        "id": result["id"],
        "email": BOOTSTRAP_EMAIL,
        "role": "owner",
        "is_active": True,
        "created": True,
    }
    row = db.fetch_one(
        """
        SELECT id, email, role, password_hash, is_active
          FROM admin_users
         WHERE lower(email) = lower(%s)
        """,
        (BOOTSTRAP_EMAIL,),
    )
    assert row is not None
    assert row["role"] == "owner"
    assert row["is_active"] is True
    assert row["password_hash"].startswith("$argon2id$")
    assert BOOTSTRAP_PASSWORD not in row["password_hash"]
    assert verify_password(BOOTSTRAP_PASSWORD, row["password_hash"])

    response = app.test_client().post(
        "/api/auth/login",
        json={"email": BOOTSTRAP_EMAIL, "password": BOOTSTRAP_PASSWORD},
    )
    assert response.status_code == 200
    assert response.get_json()["user"]["email"] == BOOTSTRAP_EMAIL


def test_bootstrap_preserves_an_existing_password_and_account_state(
    db, bootstrap_settings
):
    first = ensure_initial_admin(db, bootstrap_settings)
    original = db.fetch_one(
        "SELECT password_hash FROM admin_users WHERE id = %s", (first["id"],)
    )
    db.execute("UPDATE admin_users SET is_active = false WHERE id = %s", (first["id"],))

    changed = replace(
        bootstrap_settings,
        admin_password_hash=hash_password(NEW_PASSWORD),
    )
    second = ensure_initial_admin(db, changed)

    assert second["created"] is False
    assert second["id"] == first["id"]
    row = db.fetch_one(
        "SELECT password_hash, is_active FROM admin_users WHERE id = %s",
        (first["id"],),
    )
    assert row["password_hash"] == original["password_hash"]
    assert row["is_active"] is False
    assert verify_password(BOOTSTRAP_PASSWORD, row["password_hash"])
    assert not verify_password(NEW_PASSWORD, row["password_hash"])


def test_repeated_bootstrap_is_idempotent(db, bootstrap_settings):
    for _ in range(3):
        ensure_initial_admin(db, bootstrap_settings)
    assert db.fetch_value("SELECT count(*) FROM admin_users") == 1
    assert (
        db.fetch_value(
            "SELECT count(*) FROM admin_audit_logs WHERE action = 'admin.bootstrap'"
        )
        == 1
    )
    audit_details = db.fetch_value(
        "SELECT details::text FROM admin_audit_logs WHERE action = 'admin.bootstrap'"
    )
    assert BOOTSTRAP_PASSWORD not in audit_details
    assert "$argon2" not in audit_details


def test_bootstrap_rejects_missing_or_plaintext_credentials(db, bootstrap_settings):
    with pytest.raises(ConfigError):
        ensure_initial_admin(db, replace(bootstrap_settings, admin_email=""))
    with pytest.raises(ConfigError):
        ensure_initial_admin(
            db, replace(bootstrap_settings, admin_password_hash=BOOTSTRAP_PASSWORD)
        )
    assert db.fetch_value("SELECT count(*) FROM admin_users") == 0


def test_application_startup_bootstraps_and_never_resets(test_settings):
    first_settings = replace(
        test_settings,
        admin_email=BOOTSTRAP_EMAIL,
        admin_password_hash=hash_password(BOOTSTRAP_PASSWORD),
        admin_role="owner",
    )
    first_app = create_app(first_settings)
    try:
        first_login = first_app.test_client().post(
            "/api/auth/login",
            json={"email": BOOTSTRAP_EMAIL, "password": BOOTSTRAP_PASSWORD},
        )
        assert first_login.status_code == 200

        second_settings = replace(
            first_settings,
            admin_password_hash=hash_password(NEW_PASSWORD),
        )
        second_app = create_app(second_settings)
        try:
            old_password = second_app.test_client().post(
                "/api/auth/login",
                json={"email": BOOTSTRAP_EMAIL, "password": BOOTSTRAP_PASSWORD},
            )
            assert old_password.status_code == 200, old_password.get_json()

            new_password = second_app.test_client().post(
                "/api/auth/login",
                json={"email": BOOTSTRAP_EMAIL, "password": NEW_PASSWORD},
            )
            assert new_password.status_code == 401
        finally:
            second_app.extensions["db"].close()
    finally:
        first_app.extensions["db"].close()


def test_create_app_refuses_production_without_admin_credentials(test_settings):
    with pytest.raises(ConfigError):
        create_app(replace(test_settings, environment="production"))


def test_explicit_password_change_updates_hash_and_revokes_sessions(
    app, db, bootstrap_settings
):
    ensure_initial_admin(db, bootstrap_settings)
    actor = login(app, BOOTSTRAP_EMAIL, BOOTSTRAP_PASSWORD)

    admin = app.extensions["auth_service"].change_password(
        email=BOOTSTRAP_EMAIL,
        password=NEW_PASSWORD,
    )
    assert admin["email"] == BOOTSTRAP_EMAIL

    # The cookie issued before the change is revoked immediately.
    assert actor.get("/api/keys").status_code == 401

    old_login = app.test_client().post(
        "/api/auth/login",
        json={"email": BOOTSTRAP_EMAIL, "password": BOOTSTRAP_PASSWORD},
    )
    assert old_login.status_code == 401

    new_login = app.test_client().post(
        "/api/auth/login",
        json={"email": BOOTSTRAP_EMAIL, "password": NEW_PASSWORD},
    )
    assert new_login.status_code == 200

    row = db.fetch_one(
        "SELECT password_hash FROM admin_users WHERE lower(email) = lower(%s)",
        (BOOTSTRAP_EMAIL,),
    )
    assert verify_password(NEW_PASSWORD, row["password_hash"])
    assert not verify_password(BOOTSTRAP_PASSWORD, row["password_hash"])
