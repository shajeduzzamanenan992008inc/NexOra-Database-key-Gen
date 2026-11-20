from __future__ import annotations

import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pg8000.native as pgnative
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import create_app
from backend.config import DatabaseSettings, Settings
from backend.migrations import apply_migrations
from backend.security.passwords import hash_password
try:
    from sms_reader import create_sms_app
    from sms_reader.config import SmsConfig
    HAS_SMS = True
except ImportError:
    create_sms_app = None
    SmsConfig = None
    HAS_SMS = False

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres@127.0.0.1:55432/nexora_keygen_test?sslmode=disable",
)
TEST_PEPPER = "nexora-test-pepper-" + "0123456789abcdef" * 3
TEST_PASSWORD = "Nx!KeyGen2026Pass"
COOKIE_NAME = "nexora_session"
SMS_COOKIE_NAME = "nexora_sms_session"
SMS_PEPPER = "nexora-sms-test-pepper-" + "fedcba9876543210" * 3

OWNER_EMAIL = "owner@nexora.test"
ADMIN_EMAIL = "admin@nexora.test"
VIEWER_EMAIL = "viewer@nexora.test"


def _database_settings() -> DatabaseSettings:
    return DatabaseSettings.from_dsn(TEST_DSN, pool_max=16)


def _ensure_database() -> None:
    settings = _database_settings()
    if not re.fullmatch(r"[A-Za-z0-9_]+", settings.database):
        raise RuntimeError("TEST_DATABASE_URL database name must be alphanumeric/underscore.")
    try:
        connection = pgnative.Connection(
            user=settings.user,
            password=settings.password or None,
            host=settings.host,
            port=settings.port,
            database="postgres",
            timeout=5,
            ssl_context=None,
        )
    except Exception as exc:
        pytest.exit(
            "PostgreSQL is not reachable for tests at "
            f"{settings.dsn_summary}. Start PostgreSQL first. Details: {exc}",
            returncode=3,
        )
    try:
        rows = connection.run(
            "SELECT 1 FROM pg_database WHERE datname = :name",
            name=settings.database,
        )
        if not rows:
            connection.run(f'CREATE DATABASE "{settings.database}"')
    finally:
        connection.close()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    _ensure_database()
    return Settings(
        database=_database_settings(),
        key_pepper=TEST_PEPPER,
        secret_key="test-secret-key-" + "0123456789abcdef" * 2,
        environment="testing",
        session_ttl_seconds=3600,
        session_cookie_name=COOKIE_NAME,
        cookie_secure=False,
        rate_limit_enabled=False,
        trust_proxy=False,
        max_bulk_keys=500,
    )


@pytest.fixture(scope="session")
def app(test_settings):
    application = create_app(test_settings)
    application.config["TESTING"] = True
    apply_migrations(application.extensions["db"], ROOT / "db" / "migrations")
    yield application
    application.extensions["db"].close()


@pytest.fixture(scope="session")
def sms_app(app, test_settings):
    """The isolated SMS Reader application, sharing the test database."""
    if not HAS_SMS:
        pytest.skip("sms_reader module is not installed (out of scope)")
    config = SmsConfig(
        key_pepper=SMS_PEPPER,
        max_bulk_keys=25,
        heartbeat_interval_seconds=300,
        heartbeat_log_seconds=60,
    )
    application = create_sms_app(test_settings, sms_config=config)
    application.config["TESTING"] = True
    yield application
    application.extensions["db"].close()


@pytest.fixture(autouse=True)
def reset_database(app):
    app.extensions["db"].execute(
        """
        TRUNCATE TABLE sms_reader_events, sms_reader_keys,
                       device_tokens, reader_installations, reader_keys,
                       nexora_webhook_deliveries, nexora_webhooks,
                       nexora_devices, nexora_api_usage, nexora_key_events,
                       nexora_notifications, nexora_notification_preferences,
                       nexora_automation_runs,
                       system_settings,
                       admin_audit_logs, generation_logs, key_activations,
                       license_keys, admin_sessions
        RESTART IDENTITY CASCADE
        """
    )
    # Projects own custom key types. Delete rather than TRUNCATE so the
    # seeded global key types (project_id IS NULL) survive every test.
    app.extensions["db"].execute("DELETE FROM nexora_projects")
    app.extensions["db"].execute("DELETE FROM admin_users")
    yield


@pytest.fixture(scope="session")
def password_digest() -> str:
    return hash_password(TEST_PASSWORD)


@pytest.fixture
def db(app):
    return app.extensions["db"]


@pytest.fixture
def admins(app, password_digest):
    accounts: dict[str, dict[str, str]] = {}
    with app.extensions["db"].cursor() as cursor:
        for role, email, name in (
            ("owner", OWNER_EMAIL, "Operations Owner"),
            ("admin", ADMIN_EMAIL, "License Administrator"),
            ("viewer", VIEWER_EMAIL, "Audit Viewer"),
        ):
            cursor.execute(
                """
                INSERT INTO admin_users (email, full_name, password_hash, role)
                VALUES (%s, %s, %s, %s)
                RETURNING id, email, role
                """,
                (email, name, password_digest, role),
            )
            row = cursor.fetchone()
            accounts[role] = {
                "id": str(row[0]),
                "email": row[1],
                "role": row[2],
            }
    return accounts


class Actor:
    def __init__(self, client, csrf_token: str, user: dict[str, Any]) -> None:
        self.client = client
        self.csrf = csrf_token
        self.user = user

    def request(
        self,
        method: str,
        path: str,
        json: Any = None,
        *,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ):
        merged = dict(headers or {})
        if csrf and method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            merged["X-CSRF-Token"] = self.csrf
        return self.client.open(
            path, method=method, json=json, headers=merged, **extra
        )

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs):
        return self.request("POST", path, json=json, **kwargs)

    def patch(self, path: str, json: Any = None, **kwargs):
        return self.request("PATCH", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)


def login(app, email: str, password: str = TEST_PASSWORD) -> Actor:
    client = app.test_client()
    response = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    return Actor(client, payload["csrf_token"], payload["user"])


@pytest.fixture
def owner(app, admins) -> Actor:
    return login(app, OWNER_EMAIL)


@pytest.fixture
def admin(app, admins) -> Actor:
    return login(app, ADMIN_EMAIL)


@pytest.fixture
def viewer(app, admins) -> Actor:
    return login(app, VIEWER_EMAIL)


@pytest.fixture
def login_fixture(app) -> Callable[..., Actor]:
    def _login(email: str, password: str = TEST_PASSWORD) -> Actor:
        return login(app, email, password)

    return _login


@pytest.fixture
def create_key(owner):
    def factory(actor: Actor | None = None, **overrides) -> dict[str, Any]:
        body = {
            "quantity": 1,
            "max_activations": 1,
            "label": "Test batch",
            "prefix": "NXR",
            "groups": 4,
            "group_length": 4,
            "separator": "-",
        }
        body.update(overrides)
        actor = actor or owner
        response = actor.post("/api/keys/generate", body)
        assert response.status_code == 201, response.get_json()
        return response.get_json()

    return factory


@pytest.fixture
def limited_app(test_settings):
    application = create_app(replace(test_settings, rate_limit_enabled=True))
    yield application
    application.extensions["db"].close()


# -- SMS Reader app fixtures -------------------------------------------------


def sms_login(app, email: str, password: str = TEST_PASSWORD) -> Actor:
    client = app.test_client()
    response = client.post(
        "/api/admin/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    return Actor(client, payload["csrf_token"], payload["user"])


@pytest.fixture
def sms_owner(sms_app, admins) -> Actor:
    return sms_login(sms_app, OWNER_EMAIL)


@pytest.fixture
def sms_admin(sms_app, admins) -> Actor:
    return sms_login(sms_app, ADMIN_EMAIL)


@pytest.fixture
def sms_viewer(sms_app, admins) -> Actor:
    return sms_login(sms_app, VIEWER_EMAIL)


@pytest.fixture
def sms_key(sms_owner):
    """Generate one SMS reader key through the real admin API."""

    def factory(actor: Actor | None = None, **overrides) -> dict[str, Any]:
        body: dict[str, Any] = {"quantity": 1, "reader_name": "Test reader"}
        body.update(overrides)
        response = (actor or sms_owner).post("/api/keys/generate", body)
        assert response.status_code == 201, response.get_json()
        return response.get_json()

    return factory


def redeem_sms_key(
    app,
    activation_key: str,
    *,
    device_id: str = "nexora-reader-001",
    reader_name: str = "Front desk reader",
):
    return app.test_client().post(
        "/api/reader/redeem",
        json={
            "key": activation_key,
            "device_id": device_id,
            "reader_name": reader_name,
        },
    )


def authenticate_sms_device(
    app,
    device_token: str,
    *,
    device_id: str = "nexora-reader-001",
    rotate_token: bool = False,
    headers: dict[str, str] | None = None,
):
    return app.test_client().post(
        "/api/reader/authenticate",
        json={
            "device_token": device_token,
            "device_id": device_id,
            "rotate_token": rotate_token,
        },
        headers=headers or {},
    )


