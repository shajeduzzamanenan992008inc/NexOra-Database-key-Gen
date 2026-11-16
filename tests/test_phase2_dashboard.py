"""NexOra Phase 2 — Comprehensive Admin Dashboard & Backend Integration Tests."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from flask import Flask

from backend.api.v1 import v1_bp
from backend.errors import register_error_handlers
from backend.security.keys import (
    KeySpec,
    generate_plaintext_key,
    hash_reader_key,
    normalize_key,
    normalize_project_name,
)


@pytest.fixture
def mock_project_service():
    service = MagicMock()
    return service


@pytest.fixture
def phase2_app(mock_project_service):
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
    mock_settings.max_bulk_keys = 500
    mock_settings.session_ttl_seconds = 28800
    mock_settings.cookie_secure = False
    mock_settings.rate_limit_enabled = True

    app.extensions["db"] = mock_db
    app.extensions["settings"] = mock_settings
    app.extensions["project_service"] = mock_project_service

    register_error_handlers(app)
    app.register_blueprint(v1_bp)
    return app


class TestPhase2UnauthenticatedAccess:
    """Unauthenticated requests must be strictly rejected (401 Unauthorized)."""

    def test_unauthenticated_dashboard_stats(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 401

    def test_unauthenticated_projects_list(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 401

    def test_unauthenticated_create_project(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.post("/api/v1/projects", json={"project_name": "Test"})
        assert resp.status_code == 401

    def test_unauthenticated_keys_list(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/keys")
        assert resp.status_code == 401

    def test_unauthenticated_installations_list(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/installations")
        assert resp.status_code == 401

    def test_unauthenticated_security_status(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/security/status")
        assert resp.status_code == 401

    def test_unauthenticated_audit_logs(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/audit-logs")
        assert resp.status_code == 401

    def test_unauthenticated_settings(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 401


class TestPhase2DatabaseBackedIntegration:
    """Live database tests using existing test app & fixtures."""

    def test_dashboard_stats_endpoint(self, app, owner):
        resp = owner.client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_projects" in data
        assert "active_projects" in data
        assert "total_keys" in data
        assert "unused_keys" in data
        assert "used_keys" in data
        assert "expired_keys" in data
        assert "revoked_keys" in data
        assert "active_installations" in data
        assert "database_status" in data
        assert data["database_status"] == "connected"
        assert data["backend_health"] == "ok"

    def test_project_lifecycle_and_uniqueness(self, app, owner):
        # 1. Create Project
        resp = owner.client.post(
            "/api/v1/projects",
            json={"project_name": "Omega Alpha", "database_url_ref": "ref-001"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert resp.status_code == 201
        proj = resp.get_json()["project"]
        proj_id = proj["id"]
        assert proj["project_name"] == "Omega Alpha"
        assert proj["status"] == "active"

        # 2. Case-Insensitive Duplicate Check
        dup_resp = owner.client.post(
            "/api/v1/projects",
            json={"project_name": "omega alpha"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert dup_resp.status_code == 409
        assert dup_resp.get_json()["error"]["code"] == "PROJECT_NAME_CONFLICT"

        # 3. List Projects with search
        list_resp = owner.client.get("/api/v1/projects?search=omega")
        assert list_resp.status_code == 200
        projs = list_resp.get_json()["projects"]
        assert any(p["id"] == proj_id for p in projs)

        # 4. Deactivate Project
        status_resp = owner.client.patch(
            f"/api/v1/projects/{proj_id}/status",
            json={"status": "inactive"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert status_resp.status_code == 200
        assert status_resp.get_json()["project"]["status"] == "inactive"

        # 5. Reactivate Project
        status_resp2 = owner.client.patch(
            f"/api/v1/projects/{proj_id}/status",
            json={"status": "active"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert status_resp2.status_code == 200
        assert status_resp2.get_json()["project"]["status"] == "active"

        # 6. Soft Delete Project
        del_resp = owner.client.delete(
            f"/api/v1/projects/{proj_id}",
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert del_resp.status_code == 200
        assert del_resp.get_json()["status"] == "deleted"

    def test_reader_key_generation_and_storage(self, app, owner):
        # Create active project
        proj_resp = owner.client.post(
            "/api/v1/projects",
            json={"project_name": "Key Gen Project"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        proj_id = proj_resp.get_json()["project"]["id"]

        # Generate single key
        gen_resp = owner.client.post(
            f"/api/v1/projects/{proj_id}/keys",
            json={"is_permanent": True, "label": "Test Key 1"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert gen_resp.status_code == 201
        key_data = gen_resp.get_json()
        assert "plaintext_key" in key_data
        plaintext = key_data["plaintext_key"]
        key_id = key_data["id"]
        assert plaintext.startswith("NXR-")
        assert key_data["status"] == "generated"

        # List keys — MUST NOT return plaintext
        keys_resp = owner.client.get(f"/api/v1/keys?project_id={proj_id}")
        assert keys_resp.status_code == 200
        keys_list = keys_resp.get_json()["items"]
        found = next((k for k in keys_list if k["id"] == key_id), None)
        assert found is not None
        assert "plaintext_key" not in found
        assert found["key_hint"] == key_data["key_hint"]
        assert found["status"] == "generated"

        # Get single key detail — MUST NOT return plaintext
        detail_resp = owner.client.get(f"/api/v1/keys/{key_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.get_json()["key"]
        assert "plaintext_key" not in detail
        assert detail["key_hint"] == key_data["key_hint"]

        # Revoke Key
        rev_resp = owner.client.post(
            f"/api/v1/keys/{key_id}/revoke",
            json={"reason": "Security test revocation"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert rev_resp.status_code == 200
        assert rev_resp.get_json()["status"] == "revoked"

        # Check key status is now revoked
        detail_after = owner.client.get(f"/api/v1/keys/{key_id}").get_json()["key"]
        assert detail_after["status"] == "revoked"
        assert detail_after["revoked_reason"] == "Security test revocation"

    def test_bulk_reader_key_generation(self, app, owner):
        proj_resp = owner.client.post(
            "/api/v1/projects",
            json={"project_name": "Bulk Target Project"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        proj_id = proj_resp.get_json()["project"]["id"]

        bulk_resp = owner.client.post(
            f"/api/v1/projects/{proj_id}/keys/bulk",
            json={"quantity": 5, "is_permanent": True, "label": "Bulk 5"},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert bulk_resp.status_code == 201
        bulk_data = bulk_resp.get_json()
        assert bulk_data["count"] == 5
        assert len(bulk_data["keys"]) == 5
        for k in bulk_data["keys"]:
            assert "plaintext_key" in k
            assert k["status"] == "generated"

    def test_security_center_status_endpoint(self, app, owner):
        resp = owner.client.get("/api/v1/security/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "auth" in data
        assert "session" in data
        assert "api_security" in data
        assert "database" in data
        assert "key_security" in data

        # Security check: Zero secrets in payload
        payload_str = resp.get_data(as_text=True)
        assert "password_hash" not in payload_str
        assert "KEY_PEPPER" not in payload_str
        assert "service_role" not in payload_str

    def test_audit_logs_endpoint(self, app, owner):
        resp = owner.client.get("/api/v1/audit-logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "pagination" in data
        assert isinstance(data["items"], list)

    def test_system_settings_endpoint(self, app, owner):
        # GET settings
        resp = owner.client.get("/api/v1/settings")
        assert resp.status_code == 200
        settings = resp.get_json()
        assert "key_prefix" in settings
        assert "default_key_expiry_days" in settings
        assert "max_bulk_keys" in settings
        assert "heartbeat_interval_seconds" in settings

        # PATCH settings (Owner only)
        patch_resp = owner.client.patch(
            "/api/v1/settings",
            json={"key_prefix": "NXTEST", "default_key_expiry_days": 45},
            headers={"X-CSRF-Token": owner.csrf},
        )
        assert patch_resp.status_code == 200
        updated = patch_resp.get_json()
        assert updated["key_prefix"] == "NXTEST"
        assert updated["default_key_expiry_days"] == 45

    def test_export_reader_keys_csv(self, app, owner):
        resp = owner.client.get("/api/v1/keys/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        content = resp.get_data(as_text=True)
        assert "Key ID,Project ID,Project Name,Key Hint" in content
