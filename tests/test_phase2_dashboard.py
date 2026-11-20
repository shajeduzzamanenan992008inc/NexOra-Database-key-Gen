"""NexOra Phase 2/4 — Admin Dashboard & Backend Integration Tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from flask import Flask

from backend.api.v1 import v1_bp
from backend.errors import register_error_handlers


@pytest.fixture
def mock_project_service():
    return MagicMock()


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

    def test_unauthenticated_generate_key(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.post(
            "/api/v1/projects/generate-key", json={"project_name": "Test"}
        )
        assert resp.status_code == 401

    def test_unauthenticated_delete_all(self, phase2_app):
        client = phase2_app.test_client()
        resp = client.delete(
            "/api/v1/projects",
            json={"confirmation": "DELETE ALL PROJECTS", "password": "x"},
        )
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
    """Live database tests using the shared test app and fixtures."""

    def test_dashboard_stats_endpoint(self, app, owner):
        resp = owner.client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in (
            "total_projects",
            "active_projects",
            "scheduled_projects",
            "due_projects",
            "total_keys",
            "unused_keys",
            "used_keys",
            "expired_keys",
            "revoked_keys",
            "active_installations",
            "database_status",
        ):
            assert field in data
        assert data["database_status"] == "connected"
        assert data["backend_health"] == "ok"

    def test_generate_key_creates_project_and_single_key(self, app, owner):
        resp = owner.post(
            "/api/v1/projects/generate-key",
            {"project_name": "Omega Alpha", "label": "Primary deployment"},
        )
        assert resp.status_code == 201, resp.get_json()
        data = resp.get_json()
        project = data["project"]
        key = data["reader_key"]
        assert project["project_name"] == "Omega Alpha"
        assert project["status"] == "active"
        assert key["status"] == "generated"
        assert key["plaintext_key"].startswith("NXR-")
        assert key["key_hint"] != key["plaintext_key"]

        # The project list exposes status metadata but never the plaintext key.
        list_resp = owner.get("/api/v1/projects?search=omega")
        assert list_resp.status_code == 200
        rows = list_resp.get_json()["projects"]
        row = next(item for item in rows if item["id"] == project["id"])
        assert row["reader_key_status"] == "generated"
        assert row["reader_key_hint"] == key["key_hint"]
        assert "plaintext_key" not in str(row)
        assert row["auto_delete_at"] is None
        assert row["days_remaining"] is None

        # Key detail never returns plaintext either.
        detail_resp = owner.get(f"/api/v1/keys/{key['id']}")
        assert detail_resp.status_code == 200
        assert "plaintext_key" not in detail_resp.get_json()["key"]

        # A second request for the same normalized name is rejected and does
        # not create a second project or key.
        dup_resp = owner.post(
            "/api/v1/projects/generate-key", {"project_name": "  OMEGA   alpha  "}
        )
        assert dup_resp.status_code == 409
        error = dup_resp.get_json()["error"]
        assert error["code"] == "PROJECT_ALREADY_EXISTS"
        assert error["details"]["reader_key"]["key_hint"] == key["key_hint"]
        assert "plaintext_key" not in str(error)

        count = app.extensions["db"].fetch_one(
            "SELECT COUNT(*) AS projects FROM nexora_projects"
        )
        keys = app.extensions["db"].fetch_one(
            "SELECT COUNT(*) AS keys FROM reader_keys"
        )
        assert count["projects"] == 1
        assert keys["keys"] == 1

    def test_project_status_and_auto_delete_schedule(self, app, owner):
        created = owner.post(
            "/api/v1/projects/generate-key", {"project_name": "Schedule Target"}
        ).get_json()
        project_id = created["project"]["id"]
        key_id = created["reader_key"]["id"]

        schedule = owner.patch(
            f"/api/v1/projects/{project_id}/auto-delete",
            {"auto_delete_at": "2099-12-31T23:59:59Z"},
        )
        assert schedule.status_code == 200, schedule.get_json()
        scheduled = schedule.get_json()["project"]
        assert scheduled["auto_delete_at"] is not None
        assert scheduled["days_remaining"] >= 1

        clear = owner.patch(
            f"/api/v1/projects/{project_id}/auto-delete",
            {"auto_delete_at": None},
        )
        assert clear.status_code == 200
        assert clear.get_json()["project"]["auto_delete_at"] is None

        deactivate = owner.patch(
            f"/api/v1/projects/{project_id}/status", {"status": "inactive"}
        )
        assert deactivate.status_code == 200
        assert deactivate.get_json()["project"]["status"] == "inactive"

        # Key lifecycle is untouched by project deactivation.
        key = owner.get(f"/api/v1/keys/{key_id}").get_json()["key"]
        assert key["status"] == "generated"

        activate = owner.patch(
            f"/api/v1/projects/{project_id}/status", {"status": "active"}
        )
        assert activate.status_code == 200

    def test_permanent_delete_removes_project(self, app, owner):
        created = owner.post(
            "/api/v1/projects/generate-key", {"project_name": "Delete Me"}
        ).get_json()
        project_id = created["project"]["id"]
        key_id = created["reader_key"]["id"]

        wrong = owner.delete(
            f"/api/v1/projects/{project_id}",
            json={"confirm_project_name": "Not The Name"},
        )
        assert wrong.status_code == 422
        assert owner.get(f"/api/v1/projects/{project_id}").status_code == 200

        deleted = owner.delete(
            f"/api/v1/projects/{project_id}",
            json={"confirm_project_name": "Delete Me"},
        )
        assert deleted.status_code == 200, deleted.get_json()
        assert deleted.get_json()["deleted"] is True
        assert deleted.get_json()["mode"] == "permanent"

        assert owner.get(f"/api/v1/projects/{project_id}").status_code == 404
        assert owner.get(f"/api/v1/keys/{key_id}").status_code == 404
        assert owner.get("/api/v1/projects").get_json()["projects"] == []

        # A permanently deleted name is reusable for a new project.
        reused = owner.post(
            "/api/v1/projects/generate-key", {"project_name": "delete me"}
        )
        assert reused.status_code == 201, reused.get_json()
        new_project = reused.get_json()["project"]
        assert new_project["id"] != project_id
        assert new_project["project_name"] == "delete me"

    def test_settings_endpoint_includes_auto_delete_policy(self, app, owner):
        resp = owner.get("/api/v1/settings")
        assert resp.status_code == 200
        settings = resp.get_json()
        assert "auto_delete_days" in settings
        assert settings["auto_delete_days"] == 0

        patch_resp = owner.patch(
            "/api/v1/settings",
            json={"auto_delete_days": 30, "key_prefix": "NXTEST"},
        )
        assert patch_resp.status_code == 200
        updated = patch_resp.get_json()
        assert updated["auto_delete_days"] == 30
        assert updated["key_prefix"] == "NXTEST"

        invalid = owner.patch("/api/v1/settings", json={"auto_delete_days": -1})
        assert invalid.status_code == 422

    def test_security_center_status_endpoint(self, app, owner):
        resp = owner.get("/api/v1/security/status")
        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("auth", "session", "api_security", "database", "key_security"):
            assert field in data

        payload_str = resp.get_data(as_text=True)
        assert "password_hash" not in payload_str
        assert "KEY_PEPPER" not in payload_str
        assert "service_role" not in payload_str

    def test_audit_logs_endpoint(self, app, owner):
        resp = owner.get("/api/v1/audit-logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "pagination" in data
        assert isinstance(data["items"], list)

    def test_project_creation_and_additional_key_routes_work(self, app, owner):
        created = owner.post(
            "/api/v1/projects/generate-key", {"project_name": "Multi Key"}
        ).get_json()
        project_id = created["project"]["id"]

        # Bulk generation remains intentionally unsupported.
        bulk = owner.post(
            f"/api/v1/projects/{project_id}/keys/bulk", {"quantity": 5}
        )
        assert bulk.status_code in (404, 405)

        second = owner.post(
            f"/api/v1/projects/{project_id}/keys",
            {"label": "Secondary", "expiration": "7_days"},
        )
        assert second.status_code == 201, second.get_json()
        assert second.get_json()["reader_key"]["project_id"] == project_id
        assert second.get_json()["reader_key"]["expiration_preset"] == "7_days"

        standalone = owner.post(
            "/api/v1/projects", {"project_name": "No Key Yet"}
        )
        assert standalone.status_code == 201, standalone.get_json()
        assert standalone.get_json()["project"]["total_keys"] == 0

        keys = app.extensions["db"].fetch_one(
            "SELECT COUNT(*) AS keys FROM reader_keys"
        )
        assert keys["keys"] == 2

    def test_export_reader_keys_csv(self, app, owner):
        resp = owner.get("/api/v1/keys/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        content = resp.get_data(as_text=True)
        assert "Key ID,Project ID,Project Name,Key Hint" in content
