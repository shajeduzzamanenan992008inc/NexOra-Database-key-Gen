"""OMEGA NEX-ORA — Project & Reader Key Management Regression Suite.

Covers the Phase 4-10 requirements:
  * Generate Key creates exactly one project and one key.
  * One-key-per-project at the API, service, and database layers.
  * One-time activation lifecycle and deactivation behavior.
  * Auto-delete scheduling and due-only purging.
  * Permanent single delete and transactional delete-all.
  * Audit survival, name tombstones, CSRF, roles, and secret hygiene.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app import create_app
from backend.db import sqlstate
from backend.errors import ApiError
from backend.security.keys import hash_reader_key
from backend.services.project_service import ProjectService

TEST_SUPABASE_URL = "https://test-project.supabase.co"
ADMIN_PASSWORD = "Nx!KeyGen2026Pass"


@pytest.fixture
def reader_app(test_settings):
    """A second app instance with SUPABASE_URL configured for activation."""
    application = create_app(
        replace(test_settings, supabase_url=TEST_SUPABASE_URL)
    )
    application.config["TESTING"] = True
    yield application
    application.extensions["db"].close()


def generate(actor, name: str, label: str = ""):
    return actor.post(
        "/api/v1/projects/generate-key",
        {"project_name": name, "label": label},
    )


def generate_ok(actor, name: str, label: str = "") -> dict:
    response = generate(actor, name, label)
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def activate(
    reader_app,
    project_name: str,
    reader_key: str,
    *,
    device_id: str = "reader-test-001",
    supabase_url: str = TEST_SUPABASE_URL,
):
    return reader_app.test_client().post(
        "/api/v1/reader/activate",
        json={
            "project_name": project_name,
            "supabase_url": supabase_url,
            "reader_key": reader_key,
            "device_id": device_id,
        },
    )


def activate_legacy(
    reader_app,
    project_name: str,
    reader_key: str,
    *,
    device_id: str = "reader-test-legacy",
):
    """Legacy clients still send database_url; it means the Supabase URL."""
    return reader_app.test_client().post(
        "/api/v1/reader/activate",
        json={
            "project_name": project_name,
            "database_url": TEST_SUPABASE_URL,
            "reader_key": reader_key,
            "device_id": device_id,
        },
    )


def audit_details(row: dict) -> dict:
    details = row.get("details")
    if isinstance(details, str):
        return json.loads(details)
    return details or {}


# ---------------------------------------------------------------------------
# Phase 2 — Generate Key creates the project
# ---------------------------------------------------------------------------

class TestGenerateKeyWorkflow:
    def test_generate_creates_project_and_exactly_one_key(self, app, owner):
        result = generate_ok(owner, "Alpha Database", "Primary")
        project = result["project"]
        key = result["reader_key"]

        assert project["project_name"] == "Alpha Database"
        assert project["status"] == "active"
        assert key["project_id"] == project["id"]
        assert key["status"] == "generated"
        assert key["plaintext_key"].startswith("NXR-")

        row = app.extensions["db"].fetch_one(
            """
            SELECT p.id, p.project_name, count(k.id) AS key_count
            FROM nexora_projects p
            LEFT JOIN reader_keys k ON k.project_id = p.id
            GROUP BY p.id
            """
        )
        assert row["project_name"] == "Alpha Database"
        assert row["key_count"] == 1

    def test_plaintext_is_returned_only_once(self, app, owner):
        key = generate_ok(owner, "One Time Reveal")["reader_key"]
        list_payload = owner.get("/api/v1/projects").get_data(as_text=True)
        keys_payload = owner.get("/api/v1/keys").get_data(as_text=True)
        detail_payload = owner.get(f"/api/v1/keys/{key['id']}").get_data(as_text=True)

        assert key["plaintext_key"] not in list_payload
        assert key["plaintext_key"] not in keys_payload
        assert key["plaintext_key"] not in detail_payload


    def test_case_insensitive_duplicate_rejected_without_second_key(self, app, owner):
        first = generate_ok(owner, "Case Project")
        duplicate = generate(owner, "  case   PROJECT  ")

        assert duplicate.status_code == 409
        error = duplicate.get_json()["error"]
        assert error["code"] == "PROJECT_ALREADY_EXISTS"
        assert error["details"]["has_reader_key"] is True
        assert error["details"]["reader_key"]["key_hint"] == first["reader_key"]["key_hint"]
        assert "plaintext_key" not in duplicate.get_data(as_text=True)

        counts = app.extensions["db"].fetch_one(
            """
            SELECT (SELECT count(*) FROM nexora_projects) AS projects,
                   (SELECT count(*) FROM reader_keys) AS keys
            """
        )
        assert counts["projects"] == 1
        assert counts["keys"] == 1

    def test_existing_project_receives_additional_keys(self, app, owner):
        first = generate_ok(owner, "Multi Key Project")
        project_id = first["project"]["id"]

        response = owner.post(
            f"/api/v1/projects/{project_id}/keys",
            {"label": "Rotation", "expiration": "30_days"},
        )
        assert response.status_code == 201, response.get_json()
        second = response.get_json()["reader_key"]
        assert second["project_id"] == project_id
        assert second["id"] != first["reader_key"]["id"]
        assert second["expiration_preset"] == "30_days"
        assert second["plaintext_key"] not in owner.get("/api/v1/keys").get_data(
            as_text=True
        )

        counts = app.extensions["db"].fetch_one(
            """
            SELECT (SELECT count(*) FROM nexora_projects) AS projects,
                   (SELECT count(*) FROM reader_keys) AS keys
            """
        )
        assert counts == {"projects": 1, "keys": 2}

    def test_concurrent_requests_create_one_project_and_one_key(self, app, owner):
        settings = app.extensions["settings"]
        db = app.extensions["db"]

        def worker():
            try:
                return ProjectService(db, settings).generate_project_key(
                    project_name="Race Project"
                )
            except ApiError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))

        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [item for item in results if isinstance(item, ApiError)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert conflicts[0].status == 409

        counts = db.fetch_one(
            """
            SELECT (SELECT count(*) FROM nexora_projects) AS projects,
                   (SELECT count(*) FROM reader_keys) AS keys
            """
        )
        assert counts["projects"] == 1
        assert counts["keys"] == 1


# ---------------------------------------------------------------------------
# Phase 3 / 5 — One-time activation and project deactivation
# ---------------------------------------------------------------------------

class TestReaderKeyLifecycle:
    def test_first_activation_marks_key_used_and_second_is_rejected(
        self, app, reader_app, owner
    ):
        created = generate_ok(owner, "Lifecycle Project")
        project_name = created["project"]["project_name"]
        key = created["reader_key"]

        first = activate(reader_app, project_name, key["plaintext_key"])
        assert first.status_code == 200, first.get_json()
        credential = first.get_json()["device_credential"]

        heartbeat = reader_app.test_client().post(
            "/api/v1/reader/heartbeat",
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert heartbeat.status_code == 200

        second = activate(
            reader_app,
            project_name,
            key["plaintext_key"],
            device_id="reader-test-002",
        )
        assert second.status_code == 410
        assert second.get_json()["error"]["code"] == "KEY_ALREADY_USED"

        stored = owner.get(f"/api/v1/keys/{key['id']}").get_json()["key"]
        assert stored["status"] == "used"
        assert stored["used_at"] is not None

    def test_deactivation_blocks_credentials_and_preserves_used_key(
        self, app, reader_app, owner
    ):
        created = generate_ok(owner, "Toggle Project")
        project_id = created["project"]["id"]
        project_name = created["project"]["project_name"]
        key = created["reader_key"]

        activation = activate(reader_app, project_name, key["plaintext_key"])
        assert activation.status_code == 200
        credential = activation.get_json()["device_credential"]

        deactivate = owner.patch(
            f"/api/v1/projects/{project_id}/status", {"status": "inactive"}
        )
        assert deactivate.status_code == 200

        for method, path in (
            ("post", "/api/v1/reader/heartbeat"),
            ("get", "/api/v1/reader/status"),
        ):
            client = reader_app.test_client()
            response = getattr(client, method)(
                path, headers={"Authorization": f"Bearer {credential}"}
            )
            assert response.status_code == 403
            assert response.get_json()["error"]["code"] == "PROJECT_INACTIVE"

        assert (
            owner.get(f"/api/v1/keys/{key['id']}").get_json()["key"]["status"]
            == "used"
        )

        reactivate = owner.patch(
            f"/api/v1/projects/{project_id}/status", {"status": "active"}
        )
        assert reactivate.status_code == 200

        heartbeat = reader_app.test_client().post(
            "/api/v1/reader/heartbeat",
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert heartbeat.status_code == 200
        assert (
            owner.get(f"/api/v1/keys/{key['id']}").get_json()["key"]["status"]
            == "used"
        )

    def test_deactivated_project_rejects_new_activation(self, app, reader_app, owner):
        created = generate_ok(owner, "Blocked Activation")
        project_id = created["project"]["id"]
        owner.patch(f"/api/v1/projects/{project_id}/status", {"status": "inactive"})

        response = activate(
            reader_app,
            created["project"]["project_name"],
            created["reader_key"]["plaintext_key"],
        )
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "PROJECT_NOT_FOUND"

    def test_expired_key_persists_expired_status(self, app, reader_app, owner):
        created = generate_ok(owner, "Expired Project")
        key = created["reader_key"]
        app.extensions["db"].execute(
            """
            UPDATE reader_keys
               SET is_permanent = false,
                   expiration_preset = 'custom',
                   expires_at = now() - interval '1 day'
             WHERE id = %s
            """,
            (key["id"],),
        )

        response = activate(
            reader_app,
            created["project"]["project_name"],
            key["plaintext_key"],
        )
        assert response.status_code == 410
        assert response.get_json()["error"]["code"] == "KEY_EXPIRED"

        stored = owner.get(f"/api/v1/keys/{key['id']}").get_json()["key"]
        assert stored["status"] == "expired"

    def test_revoked_key_cannot_activate(self, app, reader_app, owner):
        created = generate_ok(owner, "Revoked Project")
        key = created["reader_key"]
        app.extensions["db"].execute(
            "UPDATE reader_keys SET status = 'revoked', revoked_at = now() WHERE id = %s",
            (key["id"],),
        )

        response = activate(
            reader_app,
            created["project"]["project_name"],
            key["plaintext_key"],
        )
        assert response.status_code == 410
        assert response.get_json()["error"]["code"] == "KEY_REVOKED"

    def test_used_key_cannot_create_replacement_installation(
        self, app, reader_app, owner
    ):
        created = generate_ok(owner, "No Replacement")
        key = created["reader_key"]
        activation = activate(
            reader_app,
            created["project"]["project_name"],
            key["plaintext_key"],
        )
        assert activation.status_code == 200
        installation_id = activation.get_json()["installation_id"]

        revoke = owner.post(
            f"/api/v1/installations/{installation_id}/revoke",
            {"reason": "Device retired"},
        )
        assert revoke.status_code == 200

        replacement = activate(
            reader_app,
            created["project"]["project_name"],
            key["plaintext_key"],
            device_id="replacement-device",
        )
        assert replacement.status_code == 410
        assert replacement.get_json()["error"]["code"] == "KEY_ALREADY_USED"

        installations = app.extensions["db"].fetch_one(
            "SELECT count(*) AS total FROM reader_installations"
        )
        assert installations["total"] == 1

    def test_legacy_database_url_alias_still_works(self, app, reader_app, owner):
        created = generate_ok(owner, "Legacy Alias")
        response = activate_legacy(
            reader_app,
            created["project"]["project_name"],
            created["reader_key"]["plaintext_key"],
        )
        assert response.status_code == 200, response.get_json()


# ---------------------------------------------------------------------------
# Phase 4 — Auto Delete scheduling
# ---------------------------------------------------------------------------

class TestAutoDelete:
    def test_schedule_is_saved_returned_and_cleared(self, app, owner):
        created = generate_ok(owner, "Scheduled Project")
        project_id = created["project"]["id"]

        response = owner.patch(
            f"/api/v1/projects/{project_id}/auto-delete",
            {"auto_delete_at": "2099-12-31T23:59:59Z"},
        )
        assert response.status_code == 200
        project = response.get_json()["project"]
        assert project["auto_delete_at"] is not None
        assert project["days_remaining"] >= 1

        listed = owner.get("/api/v1/projects").get_json()["projects"][0]
        assert listed["auto_delete_at"] == project["auto_delete_at"]
        assert listed["days_remaining"] == project["days_remaining"]

        cleared = owner.patch(
            f"/api/v1/projects/{project_id}/auto-delete",
            {"auto_delete_at": None},
        )
        assert cleared.status_code == 200
        assert cleared.get_json()["project"]["auto_delete_at"] is None

    def test_past_schedule_is_rejected(self, app, owner):
        created = generate_ok(owner, "Past Schedule")
        response = owner.patch(
            f"/api/v1/projects/{created['project']['id']}/auto-delete",
            {"auto_delete_at": "2000-01-01T00:00:00Z"},
        )
        assert response.status_code == 422

    def test_purge_only_deletes_due_scheduled_projects(self, app, owner):
        due = generate_ok(owner, "Due Project")
        keep = generate_ok(owner, "No Schedule Project")

        project_id = due["project"]["id"]
        app.extensions["db"].execute(
            """
            UPDATE nexora_projects
               SET auto_delete_at = now() - interval '1 hour'
             WHERE id = %s
            """,
            (project_id,),
        )

        service = ProjectService(
            app.extensions["db"], app.extensions["settings"]
        )
        result = service.purge_due_projects()
        assert result["deleted_count"] == 1

        assert owner.get(f"/api/v1/projects/{project_id}").status_code == 404
        assert (
            owner.get(f"/api/v1/projects/{keep['project']['id']}").status_code
            == 200
        )

        audit_row = app.extensions["db"].fetch_one(
            "SELECT action FROM admin_audit_logs WHERE action = 'project.delete'"
        )
        assert audit_row is not None

    def test_no_schedule_means_no_automatic_deletion(self, app, owner):
        created = generate_ok(owner, "Unscheduled Project")
        service = ProjectService(
            app.extensions["db"], app.extensions["settings"]
        )
        result = service.purge_due_projects()
        assert result["deleted_count"] == 0
        assert (
            owner.get(f"/api/v1/projects/{created['project']['id']}").status_code
            == 200
        )


# ---------------------------------------------------------------------------
# Phase 6 — Permanent single delete
# ---------------------------------------------------------------------------

class TestPermanentDelete:
    def test_single_delete_removes_owned_records_and_preserves_audit_and_sms(
        self, app, reader_app, owner
    ):
        doomed = generate_ok(owner, "Delete Alpha")
        survivor = generate_ok(owner, "Keep Beta")
        activation = activate(
            reader_app,
            doomed["project"]["project_name"],
            doomed["reader_key"]["plaintext_key"],
        )
        assert activation.status_code == 200
        credential = activation.get_json()["device_credential"]

        # A legacy SMS row must remain untouched by NexOra deletion.
        app.extensions["db"].execute(
            """
            INSERT INTO sms_reader_keys (key_hash, key_hint, reader_name, status)
            VALUES (%s, %s, %s, 'generated')
            """,
            ("a" * 64, "NEXORA-****-****-****-AAAA", "Legacy reader"),
        )

        response = owner.delete(
            f"/api/v1/projects/{doomed['project']['id']}",
            json={"confirm_project_name": "Delete Alpha"},
        )
        assert response.status_code == 200, response.get_json()

        counts = app.extensions["db"].fetch_one(
            """
            SELECT
              (SELECT count(*) FROM nexora_projects WHERE id = %s) AS projects,
              (SELECT count(*) FROM reader_keys WHERE project_id = %s) AS reader_keys,
              (SELECT count(*) FROM reader_installations WHERE project_id = %s) AS installations,
              (SELECT count(*) FROM device_tokens WHERE project_id = %s) AS device_tokens
            """,
            (
                doomed["project"]["id"],
                doomed["project"]["id"],
                doomed["project"]["id"],
                doomed["project"]["id"],
            ),
        )
        assert counts == {
            "projects": 0,
            "reader_keys": 0,
            "installations": 0,
            "device_tokens": 0,
        }

        # Unrelated project remains.
        assert (
            owner.get(f"/api/v1/projects/{survivor['project']['id']}").status_code
            == 200
        )

        # Audit survives with a null project_id and a safe name snapshot.
        audit_row = app.extensions["db"].fetch_one(
            """
            SELECT project_id, target_id, details
            FROM admin_audit_logs
            WHERE action = 'project.delete'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        assert audit_row is not None
        assert audit_row["project_id"] is None
        assert audit_details(audit_row)["snapshot"]["project_name"] == "Delete Alpha"

        # No tombstone remains: the name is immediately reusable.
        reuse = generate(owner, "delete alpha")
        assert reuse.status_code == 201, reuse.get_json()
        assert reuse.get_json()["project"]["id"] != doomed["project"]["id"]
        assert reuse.get_json()["reader_key"]["plaintext_key"].startswith("NXR-")

        # Legacy SMS data is unchanged.
        sms = app.extensions["db"].fetch_one(
            "SELECT reader_name FROM sms_reader_keys"
        )
        assert sms["reader_name"] == "Legacy reader"

        # The old device credential no longer authenticates.
        heartbeat = reader_app.test_client().post(
            "/api/v1/reader/heartbeat",
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert heartbeat.status_code == 401

    def test_delete_requires_exact_name_owner_and_csrf(self, app, owner, viewer):
        created = generate_ok(owner, "Protected Delete")
        project_id = created["project"]["id"]

        wrong = owner.delete(
            f"/api/v1/projects/{project_id}",
            json={"confirm_project_name": "protected delete"},
        )
        assert wrong.status_code == 422

        no_csrf = owner.delete(
            f"/api/v1/projects/{project_id}",
            json={"confirm_project_name": "Protected Delete"},
            csrf=False,
        )
        assert no_csrf.status_code == 403
        assert no_csrf.get_json()["error"]["code"] == "csrf_failed"

        forbidden = viewer.delete(
            f"/api/v1/projects/{project_id}",
            json={"confirm_project_name": "Protected Delete"},
        )
        assert forbidden.status_code == 403
        assert owner.get(f"/api/v1/projects/{project_id}").status_code == 200


# ---------------------------------------------------------------------------
# Phase 7 — Delete All Projects
# ---------------------------------------------------------------------------

class TestDeleteAllProjects:
    def _delete_all(self, actor, phrase="DELETE ALL PROJECTS", password=ADMIN_PASSWORD):
        return actor.delete(
            "/api/v1/projects",
            json={"confirmation": phrase, "password": password},
        )

    def test_delete_all_removes_projects_and_preserves_system_data(
        self, app, reader_app, owner
    ):
        first = generate_ok(owner, "Bulk One")
        second = generate_ok(owner, "Bulk Two")
        third = generate_ok(owner, "Bulk Three")
        activation = activate(
            reader_app,
            first["project"]["project_name"],
            first["reader_key"]["plaintext_key"],
        )
        assert activation.status_code == 200

        app.extensions["db"].execute(
            """
            INSERT INTO sms_reader_keys (key_hash, key_hint, reader_name, status)
            VALUES (%s, %s, %s, 'generated')
            """,
            ("b" * 64, "NEXORA-****-****-****-BBBB", "Legacy reader"),
        )

        admins_before = app.extensions["db"].fetch_one(
            "SELECT count(*) AS total FROM admin_users"
        )["total"]
        settings_before = app.extensions["db"].fetch_one(
            "SELECT count(*) AS total FROM system_settings"
        )["total"]

        response = self._delete_all(owner)
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["deleted_count"] == 3
        assert response.get_json()["mode"] == "permanent"

        counts = app.extensions["db"].fetch_one(
            """
            SELECT
              (SELECT count(*) FROM nexora_projects) AS projects,
              (SELECT count(*) FROM reader_keys) AS reader_keys,
              (SELECT count(*) FROM reader_installations) AS installations,
              (SELECT count(*) FROM device_tokens) AS device_tokens,
              (SELECT count(*) FROM admin_users) AS admins,
              (SELECT count(*) FROM system_settings) AS settings,
              (SELECT count(*) FROM sms_reader_keys) AS sms
            """
        )
        assert counts["projects"] == 0
        assert counts["reader_keys"] == 0
        assert counts["installations"] == 0
        assert counts["device_tokens"] == 0
        assert counts["admins"] == admins_before
        assert counts["settings"] == settings_before
        assert counts["sms"] == 1

        assert owner.get("/api/v1/projects").get_json()["projects"] == []

        audit_row = app.extensions["db"].fetch_one(
            """
            SELECT project_id, details
            FROM admin_audit_logs
            WHERE action = 'project.delete_all'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        assert audit_row is not None
        assert audit_row["project_id"] is None
        details = audit_details(audit_row)
        assert details["deleted_count"] == 3
        assert set(details["project_names"]) == {"Bulk One", "Bulk Two", "Bulk Three"}

        # Every old name is reusable after the hard delete.
        for name in (second["project"]["project_name"], third["project"]["project_name"]):
            reuse = generate(owner, name)
            assert reuse.status_code == 201, reuse.get_json()
            assert reuse.get_json()["project"]["project_name"] == name

    def test_delete_all_requires_phrase_password_and_owner(self, app, owner, viewer):
        generate_ok(owner, "Still Here")

        wrong_phrase = self._delete_all(owner, phrase="DELETE")
        assert wrong_phrase.status_code == 422

        wrong_password = self._delete_all(owner, password="WrongPassword!1")
        assert wrong_password.status_code == 403
        assert wrong_password.get_json()["error"]["code"] == "reauth_failed"

        forbidden = viewer.delete(
            "/api/v1/projects",
            json={
                "confirmation": "DELETE ALL PROJECTS",
                "password": ADMIN_PASSWORD,
            },
        )
        assert forbidden.status_code == 403

        assert len(owner.get("/api/v1/projects").get_json()["projects"]) == 1

    def test_delete_all_rolls_back_on_database_failure(self, app, owner):
        generate_ok(owner, "Rollback Project")
        db = app.extensions["db"]
        db.execute(
            """
            CREATE OR REPLACE FUNCTION nexora_test_block_project_delete()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'blocked for rollback test';
            END;
            $$
            """
        )
        db.execute(
            """
            CREATE TRIGGER trg_nexora_test_block_project_delete
            BEFORE DELETE ON nexora_projects
            FOR EACH ROW EXECUTE FUNCTION nexora_test_block_project_delete()
            """
        )

        try:
            response = owner.delete(
                "/api/v1/projects",
                json={
                    "confirmation": "DELETE ALL PROJECTS",
                    "password": ADMIN_PASSWORD,
                },
            )
            assert response.status_code == 500
        finally:
            db.execute(
                "DROP TRIGGER IF EXISTS trg_nexora_test_block_project_delete ON nexora_projects"
            )
            db.execute("DROP FUNCTION IF EXISTS nexora_test_block_project_delete()")

        counts = db.fetch_one(
            """
            SELECT
              (SELECT count(*) FROM nexora_projects) AS projects,
              (SELECT count(*) FROM reader_keys) AS reader_keys,
              (SELECT count(*) FROM admin_audit_logs WHERE action = 'project.delete_all') AS audits
            """
        )
        assert counts["projects"] == 1
        assert counts["reader_keys"] == 1
        assert counts["audits"] == 0


# ---------------------------------------------------------------------------
# Phase 10 — Security
# ---------------------------------------------------------------------------

class TestProjectSecurity:
    def test_generate_key_requires_csrf(self, app, owner):
        response = owner.post(
            "/api/v1/projects/generate-key",
            {"project_name": "No CSRF"},
            csrf=False,
        )
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "csrf_failed"

    def test_generate_key_forbidden_for_viewer(self, app, viewer):
        response = viewer.post(
            "/api/v1/projects/generate-key", {"project_name": "Viewer Attempt"}
        )
        assert response.status_code == 403

    def test_project_name_sql_injection_is_treated_literally(self, app, owner):
        hostile = "Test'; DROP TABLE nexora_projects; --"
        created = generate_ok(owner, hostile)
        assert created["project"]["project_name"] == hostile

        listed = owner.get("/api/v1/projects").get_json()["projects"]
        assert any(item["project_name"] == hostile for item in listed)
        assert owner.get("/api/v1/dashboard/stats").status_code == 200

    def test_no_plaintext_in_duplicate_or_listing_responses(self, app, owner):
        created = generate_ok(owner, "Secret Hygiene")
        plaintext = created["reader_key"]["plaintext_key"]

        duplicate = generate(owner, "secret hygiene")
        assert duplicate.status_code == 409
        for payload in (
            duplicate.get_data(as_text=True),
            owner.get("/api/v1/projects").get_data(as_text=True),
            owner.get("/api/v1/keys").get_data(as_text=True),
            owner.get(
                f"/api/v1/keys/{created['reader_key']['id']}"
            ).get_data(as_text=True),
        ):
            assert plaintext not in payload
            assert "key_hash" not in payload
