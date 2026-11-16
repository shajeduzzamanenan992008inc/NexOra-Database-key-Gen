from __future__ import annotations

import datetime as dt


def activate(app, key: str, device: str):
    return app.test_client().post(
        "/api/keys/activate", json={"key": key, "device_id": device}
    )


def test_dashboard_stats_reflect_key_lifecycle(owner, app, create_key, db):
    first = create_key(max_activations=1, label="Alpha")
    second = create_key(max_activations=1, label="Beta")

    assert activate(app, first["keys"][0]["key"], "device-stats-1111").status_code == 200
    assert (
        owner.post(
            "/api/keys/revoke",
            {"key_id": second["keys"][0]["id"], "reason": "Refund processed"},
        ).status_code
        == 200
    )

    stats = owner.get("/api/dashboard/stats").get_json()
    assert stats["totals"]["total"] == 2
    assert stats["totals"]["used"] == 1
    assert stats["totals"]["revoked"] == 1
    assert stats["totals"]["active"] == 0
    assert stats["activity"]["activations_total"] == 1
    assert stats["activity"]["activation_rate"] == 50.0
    assert len(stats["recent_generations"]) == 2
    assert stats["recent_audit"]


def test_expiring_soon_is_reported(owner, create_key):
    soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    create_key(expires_at=soon)
    stats = owner.get("/api/dashboard/stats").get_json()
    assert stats["activity"]["expiring_soon"] == 1


def test_logs_cover_generation_activation_and_audit(owner, app, create_key):
    first = create_key(label="Log batch one")
    create_key(label="Log batch two")
    assert activate(app, first["keys"][0]["key"], "device-logs-1234").status_code == 200

    generation = owner.get("/api/logs?kind=generation&page_size=1").get_json()
    assert generation["kind"] == "generation"
    assert generation["pagination"]["total"] == 2
    assert generation["pagination"]["total_pages"] == 2
    assert len(generation["items"]) == 1
    assert generation["items"][0]["quantity"] == 1

    activations = owner.get("/api/logs?kind=activations").get_json()
    assert activations["pagination"]["total"] == 1
    assert activations["items"][0]["device_hint"].endswith("1234")

    audit = owner.get("/api/logs?kind=audit&page_size=100").get_json()
    actions = {item["action"] for item in audit["items"]}
    assert {"key.generate", "key.activate", "auth.login"} <= actions
    assert all(item["actor_email"] is None or "@" in item["actor_email"] for item in audit["items"])

    failure = owner.get("/api/logs?kind=audit&outcome=success").get_json()
    assert all(item["outcome"] == "success" for item in failure["items"])


def test_log_filters_and_validation(owner, viewer, create_key):
    create_key()
    assert viewer.get("/api/logs?kind=generation").status_code == 200
    assert viewer.get("/api/dashboard/stats").status_code == 200

    assert owner.get("/api/logs?kind=unknown").status_code == 422
    assert owner.get("/api/logs?page_size=500").status_code == 422
    assert owner.get("/api/logs?outcome=maybe").status_code == 422

    actor_filter = owner.get("/api/logs?kind=audit&action=auth.login").get_json()
    assert all("auth.login" in item["action"] for item in actor_filter["items"])


def test_health_endpoint_reports_database(app):
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["database"] == "connected"
    assert payload["service"] == "nexora-keygen"


def test_audit_records_actor_and_target(owner, admins, create_key, db):
    batch = create_key()
    key_id = batch["keys"][0]["id"]
    owner.post("/api/keys/revoke", {"key_id": key_id, "reason": "Audit check"})

    row = db.fetch_one(
        """
        SELECT actor_email, action, target_id, outcome, details::text AS details
          FROM admin_audit_logs
         WHERE action = 'key.revoke'
        """
    )
    assert row["actor_email"] == admins["owner"]["email"]
    assert row["target_id"] == key_id
    assert row["outcome"] == "success"
    assert "Audit check" in row["details"]


def test_deletion_keeps_audit_but_removes_license(owner, create_key, db):
    batch = create_key()
    key_id = batch["keys"][0]["id"]
    assert owner.delete(f"/api/keys/{key_id}").status_code == 200
    assert db.fetch_value("SELECT count(*) FROM license_keys") == 0
    row = db.fetch_one(
        "SELECT target_id, details::text AS details FROM admin_audit_logs WHERE action = 'key.delete'"
    )
    assert row["target_id"] == key_id
    assert "key_hint" in row["details"]


def test_dashboard_requires_authentication(app):
    assert app.test_client().get("/api/dashboard/stats").status_code == 401
