"""Regression tests for automatic expiration cleanup.

These tests use controlled database timestamps instead of waiting for real
time. They prove the hard-delete path, not just the ``expired`` status.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace

from backend.app import create_app
from backend.services.project_service import ProjectService
from backend.services.scheduler_service import SchedulerService


def _create_project(actor, name: str, **extra) -> dict:
    response = actor.post("/api/v1/projects", {"project_name": name, **extra})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def _generate_key(actor, project_id: str, **overrides) -> dict:
    body = {
        "project_id": project_id,
        "key_type_id": "reader",
        "quantity": 1,
        "expiration_preset": "never",
        "label": "cleanup test key",
    }
    body.update(overrides)
    response = actor.post("/api/v1/universal/keys", body)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["keys"][0]


def _authenticate(app, plaintext: str):
    return app.test_client().post(
        "/api/v1/access/authenticate",
        json={"key": plaintext, "device_id": "cleanup-test-device"},
    )


def _set_key_expiry(db, key_id: str, seconds: int) -> None:
    """Move a key's expires_at relative to the database clock."""
    db.execute(
        """
        UPDATE reader_keys
           SET expires_at = now() + (%s || ' seconds')::interval,
               is_permanent = false,
               expiration_preset = 'custom',
               status = 'generated'
         WHERE id = %s
        """,
        (seconds, key_id),
    )


def _set_project_expiry(db, project_id: str, seconds: int) -> None:
    """Set expires_at and auto_delete_at to the same instant."""
    db.execute(
        """
        UPDATE nexora_projects
           SET expires_at = now() + (%s || ' seconds')::interval,
               auto_delete_at = now() + (%s || ' seconds')::interval,
               never_expires = false
         WHERE id = %s
        """,
        (seconds, seconds, project_id),
    )


def _add_related_records(db, project_id: str, key_id: str) -> None:
    db.execute(
        """
        INSERT INTO reader_installations
            (project_id, reader_key_id, device_token_hash, device_id, status)
        VALUES (%s, %s, %s, 'cleanup-device', 'active')
        """,
        (project_id, key_id, "a" * 64),
    )
    installation = db.fetch_value(
        "SELECT id FROM reader_installations WHERE reader_key_id = %s",
        (key_id,),
    )
    db.execute(
        """
        INSERT INTO device_tokens (installation_id, project_id, token_hash)
        VALUES (%s, %s, %s)
        """,
        (installation, project_id, "b" * 64),
    )
    db.execute(
        """
        INSERT INTO nexora_devices (project_id, key_id, device_id)
        VALUES (%s, %s, 'cleanup-device')
        """,
        (project_id, key_id),
    )


def _service(app) -> ProjectService:
    return ProjectService(
        app.extensions["db"], app.extensions["settings"]
    )


# --------------------------------------------------------------------- keys
def test_expired_key_is_unusable_before_cleanup_then_hard_deleted(
    app, owner, db
):
    project = _create_project(owner, "Key Cleanup Project")
    key = _generate_key(owner, project["id"])
    other = _generate_key(owner, project["id"])
    never = _generate_key(owner, project["id"])
    future = _generate_key(
        owner,
        project["id"],
        expiration_preset="custom",
        expires_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat(),
    )
    _add_related_records(db, project["id"], key["id"])
    _set_key_expiry(db, key["id"], -60)

    # Enforcement happens immediately, before any cleanup sweep.
    denied = _authenticate(app, key["key"])
    assert denied.status_code == 410
    assert denied.get_json()["error"]["code"] == "KEY_EXPIRED"

    service = _service(app)
    result = service.purge_expired_keys()
    assert result["deleted_count"] == 1
    assert result["failed_count"] == 0

    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (key["id"],)) is None
    assert db.fetch_value(
        "SELECT id FROM reader_installations WHERE reader_key_id = %s", (key["id"],)
    ) is None
    assert db.fetch_value(
        "SELECT id FROM nexora_devices WHERE key_id = %s", (key["id"],)
    ) is None
    assert db.fetch_value("SELECT COUNT(*)::int FROM device_tokens") == 0

    # The deleted credential can no longer authenticate.
    missing = _authenticate(app, key["key"])
    assert missing.status_code == 401

    # Active, future and never-expire records are untouched.
    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (other["id"],))
    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (future["id"],))
    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (never["id"],))

    audit = db.fetch_one(
        """
        SELECT action, target_type, target_id, project_id, details
          FROM admin_audit_logs
         WHERE action = 'reader_key.auto_delete' AND target_id = %s
        """,
        (key["id"],),
    )
    assert audit is not None
    assert audit["target_type"] == "reader_key"
    assert str(audit["project_id"]) == project["id"]
    assert audit["details"]["reason"] == "expiration_auto_delete"
    assert audit["details"]["expires_at"]
    assert audit["details"]["deleted_at"]
    assert audit["details"]["result"] == "deleted"
    assert key["key"] not in json.dumps(audit["details"])

    # Idempotent: a second pass finds nothing and does not error.
    again = service.purge_expired_keys()
    assert again["deleted_count"] == 0
    assert again["failed_count"] == 0


def test_expired_key_at_current_instant_is_due_and_future_is_not(
    app, owner, db
):
    project = _create_project(owner, "Key Boundary Project")
    at_now = _generate_key(
        owner,
        project["id"],
        expiration_preset="custom",
        expires_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
    )
    future = _generate_key(
        owner,
        project["id"],
        expiration_preset="custom",
        expires_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat(),
    )
    _set_key_expiry(db, at_now["id"], 0)
    _set_key_expiry(db, future["id"], 3600)

    result = _service(app).purge_expired_keys()
    assert result["deleted_count"] == 1
    assert db.fetch_value(
        "SELECT id FROM reader_keys WHERE id = %s", (at_now["id"],)
    ) is None
    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (future["id"],))


def test_key_cleanup_failure_rolls_back_only_that_key(app, owner, db, monkeypatch):
    project = _create_project(owner, "Key Failure Project")
    failing = _generate_key(owner, project["id"])
    succeeding = _generate_key(owner, project["id"])
    _set_key_expiry(db, failing["id"], -60)
    _set_key_expiry(db, succeeding["id"], -60)

    service = _service(app)
    original = service._write_audit

    def flaky_audit(cursor_or_db, **kwargs):
        if str(kwargs.get("target_id")) == failing["id"]:
            raise RuntimeError("simulated audit outage")
        return original(cursor_or_db, **kwargs)

    monkeypatch.setattr(service, "_write_audit", flaky_audit)
    result = service.purge_expired_keys()

    assert result["deleted_count"] == 1
    assert result["failed_count"] == 1
    # The failed transaction rolled back; the row is retryable, not corrupt.
    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (failing["id"],))
    assert db.fetch_value(
        "SELECT id FROM reader_keys WHERE id = %s", (succeeding["id"],)
    ) is None


# ----------------------------------------------------------------- projects
def test_expired_project_and_related_records_are_hard_deleted(app, owner, db):
    doomed = _create_project(owner, "Expired Cleanup Project")
    keep = _create_project(owner, "Keep Cleanup Project")
    never = _create_project(owner, "Never Expire Cleanup Project")
    key = _generate_key(owner, doomed["id"])
    _add_related_records(db, doomed["id"], key["id"])
    db.execute(
        """
        INSERT INTO nexora_webhooks (project_id, name, url, events, secret_salt)
        VALUES (%s, 'cleanup hook', 'https://example.test/cleanup',
                ARRAY['key.created'], 'salt')
        """,
        (doomed["id"],),
    )
    _set_project_expiry(db, doomed["id"], -120)

    # A never-expire project with no explicit deletion schedule is never due,
    # even if an inconsistent expiry date was written directly by an operator.
    db.execute(
        """
        UPDATE nexora_projects
           SET expires_at = now() - interval '1 day',
               auto_delete_at = NULL,
               never_expires = true
         WHERE id = %s
        """,
        (never["id"],),
    )

    result = _service(app).purge_due_projects()
    assert result["deleted_count"] == 1
    assert result["failed_count"] == 0

    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (doomed["id"],)) is None
    assert db.fetch_value("SELECT id FROM reader_keys WHERE project_id = %s", (doomed["id"],)) is None
    assert db.fetch_value(
        "SELECT id FROM reader_installations WHERE project_id = %s", (doomed["id"],)
    ) is None
    assert db.fetch_value(
        "SELECT id FROM device_tokens WHERE project_id = %s", (doomed["id"],)
    ) is None
    assert db.fetch_value(
        "SELECT id FROM nexora_devices WHERE project_id = %s", (doomed["id"],)
    ) is None
    assert db.fetch_value(
        "SELECT id FROM nexora_webhooks WHERE project_id = %s", (doomed["id"],)
    ) is None

    # Unrelated live and never-expire projects remain untouched.
    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (keep["id"],))
    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (never["id"],))

    audit = db.fetch_one(
        """
        SELECT action, project_id, details
          FROM admin_audit_logs
         WHERE action = 'project.delete' AND target_id = %s
        """,
        (doomed["id"],),
    )
    assert audit is not None
    assert audit["details"]["source"] == "auto_delete"
    assert audit["details"]["deleted_at"]
    assert audit["details"]["snapshot"]["key_count"] == 1


def test_expired_project_blocks_new_key_generation(app, owner, db):
    project = _create_project(owner, "Expired Key Generation Project")
    _set_project_expiry(db, project["id"], -60)

    legacy = owner.post(
        f"/api/v1/projects/{project['id']}/keys", {"label": "should fail"}
    )
    assert legacy.status_code == 403
    assert legacy.get_json()["error"]["code"] == "PROJECT_EXPIRED"

    universal = owner.post(
        "/api/v1/universal/keys",
        {
            "project_id": project["id"],
            "key_type_id": "reader",
            "quantity": 1,
            "expiration_preset": "never",
        },
    )
    assert universal.status_code == 403
    assert universal.get_json()["error"]["code"] == "PROJECT_EXPIRED"
    assert db.fetch_value(
        "SELECT COUNT(*)::int FROM reader_keys WHERE project_id = %s",
        (project["id"],),
    ) == 0


# ---------------------------------------------------------------- scheduler
def test_scheduler_purges_due_keys_and_projects_automatically(app, owner, db):
    key_project = _create_project(owner, "Scheduler Key Project")
    due_key = _generate_key(owner, key_project["id"])
    active_key = _generate_key(
        owner,
        key_project["id"],
        expiration_preset="custom",
        expires_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat(),
    )
    _set_key_expiry(db, due_key["id"], -60)

    due_project = _create_project(owner, "Scheduler Due Project")
    _set_project_expiry(db, due_project["id"], -60)

    service = SchedulerService(db, app.extensions["settings"])
    result = service.run_once(jobs=["key_purge", "project_purge"])
    assert result["status"] == "success", result
    assert result["details"]["key_purge"]["keys_deleted"] == 1
    assert result["details"]["project_purge"]["projects_deleted"] == 1

    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (due_key["id"],)) is None
    assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (active_key["id"],))
    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (due_project["id"],)) is None
    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (key_project["id"],))

    # Idempotent repeated sweeps.
    again = service.run_once(jobs=["key_purge", "project_purge"])
    assert again["status"] == "success", again
    assert again["details"]["key_purge"]["keys_deleted"] == 0
    assert again["details"]["project_purge"]["projects_deleted"] == 0

    status = service.status()
    assert "key_purge" in status["jobs"]
    assert status["counts"]["keys_due"] == 0


def test_app_starts_the_embedded_worker_when_enabled(test_settings, monkeypatch):
    started = {}

    class _FakeWorker:
        interval_seconds = 60
        running = True

        def stop(self, timeout: float = 5.0):
            started["stopped"] = True
            return {"running": False, "sweeps": 0}

    def _fake_start(self, **kwargs):
        started["called"] = True
        return _FakeWorker()

    monkeypatch.setattr(
        SchedulerService, "start_background_worker", _fake_start
    )
    application = create_app(
        replace(
            test_settings,
            environment="development",
            scheduler_embedded=True,
        )
    )
    try:
        assert started["called"] is True
        assert isinstance(application.extensions["scheduler_worker"], _FakeWorker)
        assert application.extensions["scheduler_service"] is not None
    finally:
        application.extensions["db"].close()


def test_embedded_worker_runs_a_sweep_and_stops_gracefully(app, owner, db):
    project = _create_project(owner, "Embedded Worker Project")
    key = _generate_key(owner, project["id"])
    _set_key_expiry(db, key["id"], -60)

    service = SchedulerService(db, app.extensions["settings"])
    worker = service.start_background_worker(interval_seconds=5)
    try:
        assert worker.first_sweep.wait(timeout=10), "scheduler did not complete a sweep"
        assert worker.sweeps >= 1
        assert service.status()["embedded_worker"] is True
        assert db.fetch_value("SELECT id FROM reader_keys WHERE id = %s", (key["id"],)) is None
    finally:
        stopped = worker.stop()
    assert worker.running is False
    assert stopped["sweeps"] >= 1


# --------------------------------------------------------------------- time
def test_utc_and_configured_timezone_are_consistent_for_deletion(
    app, owner, db
):
    owner.patch("/api/v1/settings", json={"display_timezone": "Asia/Dhaka"})
    project = _create_project(
        owner,
        "Dhaka Cleanup Project",
        expiration="custom",
        expires_at="2030-01-01T00:00:00",
    )
    expected = dt.datetime(2029, 12, 31, 18, 0, tzinfo=dt.timezone.utc)
    assert dt.datetime.fromisoformat(
        project["expires_at"].replace("Z", "+00:00")
    ) == expected
    assert project["expires_at"] == project["auto_delete_at"]

    stored = db.fetch_one(
        "SELECT expires_at, auto_delete_at, never_expires FROM nexora_projects WHERE id = %s",
        (project["id"],),
    )
    assert stored["expires_at"] == expected
    assert stored["auto_delete_at"] == expected

    # A future instant is not due, regardless of the configured display zone.
    service = _service(app)
    assert service.purge_due_projects()["deleted_count"] == 0
    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (project["id"],))

    # Move only the canonical UTC instant into the past; deletion follows it.
    db.execute(
        """
        UPDATE nexora_projects
           SET expires_at = now() - interval '1 second',
               auto_delete_at = now() - interval '1 second'
         WHERE id = %s
        """,
        (project["id"],),
    )
    assert service.purge_due_projects()["deleted_count"] == 1
    assert db.fetch_value("SELECT id FROM nexora_projects WHERE id = %s", (project["id"],)) is None
