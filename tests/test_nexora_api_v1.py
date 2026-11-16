"""NexOra Phase 1 — /api/v1/ REST API Unit and Route Tests (using test client and mocks)."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from flask import Flask

from backend.api.v1 import v1_bp
from backend.errors import register_error_handlers


@pytest.fixture
def test_app():
    """Lightweight test application for /api/v1 routes with mocked database and settings."""
    app = Flask(__name__)
    app.secret_key = "test-secret-key-at-least-32-chars-long"

    mock_db = MagicMock()
    mock_db.ping.return_value = True

    mock_settings = MagicMock()
    mock_settings.version = "1.0.0"
    mock_settings.supabase_url = "https://test-project.supabase.co"
    mock_settings.session_cookie_name = "nexora_session"
    mock_settings.session_audience = "keygen"
    mock_settings.trust_proxy = False
    mock_settings.key_pepper = "test-pepper-" + "a" * 32

    app.extensions["db"] = mock_db
    app.extensions["settings"] = mock_settings
    register_error_handlers(app)
    app.register_blueprint(v1_bp)

    return app


class TestHealthEndpoint:
    def test_health_ok_when_db_ping_succeeds(self, test_app):
        client = test_app.test_client()
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["api_version"] == "v1"
        assert data["service"] == "nexora-admin-backend"

    def test_health_degraded_when_db_ping_fails(self, test_app):
        test_app.extensions["db"].ping.return_value = False
        client = test_app.test_client()
        resp = client.get("/api/v1/health")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "degraded"
        assert data["database"] == "unavailable"


class TestReaderActivationValidation:
    def test_rejects_missing_json(self, test_app):
        client = test_app.test_client()
        resp = client.post("/api/v1/reader/activate", data="not json", content_type="text/plain")
        assert resp.status_code in (400, 415)

    def test_rejects_missing_database_url(self, test_app):
        client = test_app.test_client()
        resp = client.post(
            "/api/v1/reader/activate",
            json={
                "project_name": "NexOra Main",
                "reader_key": "NXR-ABCD-EFGH-JKLM-NPQR",
            },
        )
        assert resp.status_code in (400, 422)

    def test_rejects_mismatching_database_url(self, test_app):
        client = test_app.test_client()
        resp = client.post(
            "/api/v1/reader/activate",
            json={
                "project_name": "NexOra Main",
                "database_url": "https://different-phishing.supabase.co",
                "reader_key": "NXR-ABCD-EFGH-JKLM-NPQR",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["code"] == "DATABASE_URL_MISMATCH"


class TestReaderCredentialAuthentication:
    def test_heartbeat_requires_authorization_header(self, test_app):
        client = test_app.test_client()
        resp = client.post("/api/v1/reader/heartbeat")
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_heartbeat_requires_bearer_format(self, test_app):
        client = test_app.test_client()
        resp = client.post(
            "/api/v1/reader/heartbeat",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["error"]["code"] == "UNAUTHORIZED"

    def test_status_requires_authorization_header(self, test_app):
        client = test_app.test_client()
        resp = client.get("/api/v1/reader/status")
        assert resp.status_code == 401


class TestPrivilegedAdminEndpointsProtected:
    def test_generate_key_requires_auth(self, test_app):
        client = test_app.test_client()
        resp = client.post(
            "/api/v1/projects/generate-key",
            json={"project_name": "NexOra Production"},
        )
        assert resp.status_code in (401, 302)

    def test_list_projects_requires_auth(self, test_app):
        client = test_app.test_client()
        resp = client.get("/api/v1/projects")
        assert resp.status_code in (401, 302)

    def test_delete_project_requires_auth(self, test_app):
        client = test_app.test_client()
        resp = client.delete("/api/v1/projects/some-id")
        assert resp.status_code in (401, 302)

    def test_delete_all_projects_requires_auth(self, test_app):
        client = test_app.test_client()
        resp = client.delete(
            "/api/v1/projects",
            json={"confirmation": "DELETE ALL PROJECTS", "password": "x"},
        )
        assert resp.status_code in (401, 302)

    def test_revoke_installation_requires_auth(self, test_app):
        client = test_app.test_client()
        resp = client.post(
            "/api/v1/admin/installations/some-id/revoke",
            json={"reason": "Compromised"},
        )
        assert resp.status_code in (401, 302)
