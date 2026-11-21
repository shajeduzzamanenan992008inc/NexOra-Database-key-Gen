from __future__ import annotations

import io
import json

from backend.services.scheduler_service import SCHEDULER_LOCK_KEY, SchedulerService
from conftest import ADMIN_EMAIL, login


def create_project(actor, name: str) -> dict:
    response = actor.post("/api/v1/projects", {"project_name": name})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def generate_key(actor, project_id: str, *, label: str = "Replacement source") -> dict:
    response = actor.post(
        "/api/v1/universal/keys",
        {
            "project_id": project_id,
            "key_type_id": "reader",
            "quantity": 1,
            "expiration_preset": "never",
            "label": label,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["keys"][0]


def import_copy(owner, project_id: str, name: str) -> dict:
    bundle = owner.get(f"/api/v1/universal/projects/{project_id}/export")
    assert bundle.status_code == 200, bundle.get_json()
    response = owner.client.open(
        "/api/v1/universal/projects/import",
        method="POST",
        data={
            "file": (io.BytesIO(json.dumps(bundle.get_json()).encode()), "bundle.json"),
            "mode": "import_as_new",
            "project_name": name,
        },
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": owner.csrf},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def merge_copy(owner, project_id: str) -> None:
    bundle = owner.get(f"/api/v1/universal/projects/{project_id}/export")
    assert bundle.status_code == 200, bundle.get_json()
    response = owner.client.open(
        "/api/v1/universal/projects/import",
        method="POST",
        data={
            "file": (io.BytesIO(json.dumps(bundle.get_json()).encode()), "bundle.json"),
            "mode": "keep_existing",
        },
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": owner.csrf},
    )
    assert response.status_code == 201, response.get_json()


def imported_records(owner, project_id: str) -> list[dict]:
    response = owner.get(
        f"/api/v1/universal/imported-keys?project_id={project_id}&page_size=100"
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()["items"]


def test_imported_replacement_preview_generation_duplicate_and_history(owner, db):
    source = create_project(owner, "Replacement Source")
    original = generate_key(owner, source["id"])
    target = import_copy(owner, source["id"], "Replacement Target")
    records = imported_records(owner, target["id"])
    assert len(records) == 1
    record = records[0]
    # The imported original was revoked before the metadata reached NexOra.
    db.execute(
        """UPDATE nexora_imported_key_records
              SET status = 'revoked',
                  data = jsonb_set(data, '{original_status}', '"revoked"'::jsonb)
            WHERE id = %s""",
        (record["id"],),
    )
    db.execute(
        """UPDATE reader_keys
              SET status = 'revoked', revoked_at = now(),
                  revoked_reason = 'Revoked before import'
            WHERE id = %s""",
        (original["id"],),
    )
    record = imported_records(owner, target["id"])[0]
    assert record["status"] == "revoked"
    assert record["requires_regeneration"] is True

    preview = owner.post(
        "/api/v1/universal/imported-keys/replacement-preview",
        {"record_ids": [record["id"]]},
    )
    assert preview.status_code == 200, preview.get_json()
    plan = preview.get_json()["records"][0]
    assert plan["eligible"] is True
    assert any("revoked" in warning.lower() for warning in plan["warnings"])
    assert "key" not in preview.get_json()

    generated = owner.post(
        "/api/v1/universal/imported-keys/replacements",
        {"record_ids": [record["id"]], "confirm": True},
    )
    assert generated.status_code == 201, generated.get_json()
    body = generated.get_json()
    assert body["generated_count"] == 1, body
    assert body["failed_count"] == 0
    replacement = body["keys"][0]
    assert replacement["key"]
    assert replacement["imported_record_id"] == record["id"]
    assert replacement["fingerprint"] != original["fingerprint"]

    stored = db.fetch_one(
        """SELECT status, requires_regeneration, replacement_count, replacement_key_id
             FROM nexora_imported_key_records WHERE id = %s""",
        (record["id"],),
    )
    assert stored["status"] == "replaced"
    assert stored["requires_regeneration"] is False
    assert stored["replacement_count"] == 1
    assert str(stored["replacement_key_id"]) == replacement["id"]

    # The original source credential remains revoked; the replacement is new.
    original_row = db.fetch_one(
        "SELECT status FROM reader_keys WHERE id = %s", (original["id"],)
    )
    assert original_row["status"] == "revoked"
    replacement_row = db.fetch_one(
        "SELECT status, key_type_id FROM reader_keys WHERE id = %s",
        (replacement["id"],),
    )
    assert replacement_row["status"] == "generated"

    history = owner.get(
        f"/api/v1/universal/imported-keys/{record['id']}/history"
    )
    assert history.status_code == 200, history.get_json()
    assert len(history.get_json()["history"]) == 1
    assert replacement["key"] not in json.dumps(history.get_json())

    # Duplicate generation is blocked unless explicitly authorized.
    duplicate = owner.post(
        "/api/v1/universal/imported-keys/replacements",
        {"record_ids": [record["id"]], "confirm": True},
    )
    assert duplicate.status_code == 201
    assert duplicate.get_json()["generated_count"] == 0
    assert duplicate.get_json()["failed_count"] == 1
    assert duplicate.get_json()["failures"][0]["code"] == "REPLACEMENT_ALREADY_EXISTS"

    duplicate_allowed = owner.post(
        "/api/v1/universal/imported-keys/replacements",
        {"record_ids": [record["id"]], "confirm": True, "allow_duplicate": True},
    )
    assert duplicate_allowed.status_code == 201, duplicate_allowed.get_json()
    assert duplicate_allowed.get_json()["generated_count"] == 1
    history = owner.get(
        f"/api/v1/universal/imported-keys/{record['id']}/history"
    )
    assert len(history.get_json()["history"]) == 2

    audit = db.fetch_one(
        """SELECT outcome FROM admin_audit_logs
            WHERE action = 'imported_key.replace_batch' ORDER BY created_at DESC LIMIT 1"""
    )
    assert audit["outcome"] == "success"


def test_replacement_partial_failure_is_safe(owner, db):
    first = create_project(owner, "Replacement Active")
    second = create_project(owner, "Replacement Inactive")
    generate_key(owner, first["id"], label="Active source")
    generate_key(owner, second["id"], label="Inactive source")
    active_copy = import_copy(owner, first["id"], "Replacement Active Copy")
    inactive_copy = import_copy(owner, second["id"], "Replacement Inactive Copy")
    active_record = imported_records(owner, active_copy["id"])[0]
    inactive_record = imported_records(owner, inactive_copy["id"])[0]
    db.execute(
        "UPDATE nexora_projects SET status = 'inactive' WHERE id = %s",
        (inactive_copy["id"],),
    )

    preview = owner.post(
        "/api/v1/universal/imported-keys/replacement-preview",
        {"record_ids": [active_record["id"], inactive_record["id"]]},
    )
    assert preview.status_code == 200, preview.get_json()
    by_id = {item["record_id"]: item for item in preview.get_json()["records"]}
    assert by_id[active_record["id"]]["eligible"] is True
    assert by_id[inactive_record["id"]]["eligible"] is False

    generated = owner.post(
        "/api/v1/universal/imported-keys/replacements",
        {"record_ids": [active_record["id"], inactive_record["id"]], "confirm": True},
    )
    assert generated.status_code == 201, generated.get_json()
    body = generated.get_json()
    assert body["generated_count"] == 1
    assert body["failed_count"] == 1
    assert body["failures"][0]["record_id"] == inactive_record["id"]
    assert db.fetch_value(
        "SELECT COUNT(*)::int FROM reader_keys WHERE project_id = %s",
        (inactive_copy["id"],),
    ) == 0


def test_project_role_isolation_escalation_and_cleanup(owner, admin, app, db):
    project = create_project(owner, "Scoped Project")
    other = create_project(owner, "Other Project")
    generate_key(owner, project["id"], label="Scoped key")
    generate_key(owner, other["id"], label="Other key")

    created = owner.post(
        "/api/v1/universal/roles",
        {
            "id": "scoped_operator",
            "name": "Scoped operator",
            "project_id": project["id"],
            "permissions": ["project:read", "key:read", "key:write", "key:generate"],
        },
    )
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["role"]["project_id"] == project["id"]

    forbidden_scope = owner.post(
        "/api/v1/universal/roles",
        {
            "id": "scoped_admin",
            "name": "Scoped admin",
            "project_id": project["id"],
            "permissions": ["admin:roles", "system:settings"],
        },
    )
    assert forbidden_scope.status_code == 422

    admins = owner.get("/api/v1/universal/admins").get_json()["admins"]
    admin_row = next(item for item in admins if item["email"] == ADMIN_EMAIL)
    denied_global = owner.patch(
        f"/api/v1/universal/admins/{admin_row['id']}/role",
        {"platform_role": "custom", "platform_role_id": "scoped_operator"},
    )
    assert denied_global.status_code == 409
    assert denied_global.get_json()["error"]["code"] == "PROJECT_ROLE_REQUIRES_PROJECT"

    # Remove global authority, then grant only the project role.
    db.execute(
        """UPDATE admin_users
              SET platform_role = 'custom', platform_permissions = '[]'::jsonb,
                  platform_role_id = NULL, role = 'admin'
            WHERE id = %s""",
        (admin_row["id"],),
    )
    assigned = owner.post(
        f"/api/v1/universal/projects/{project['id']}/role-assignments",
        {"admin_id": admin_row["id"], "role_id": "scoped_operator"},
    )
    assert assigned.status_code == 201, assigned.get_json()
    assignment_id = assigned.get_json()["assignment"]["id"]
    listed = owner.get(
        f"/api/v1/universal/projects/{project['id']}/role-assignments"
    )
    assert listed.status_code == 200, listed.get_json()
    assignment = listed.get_json()["assignments"][0]
    assert assignment["role_id"] == "scoped_operator"
    assert isinstance(assignment["role_id"], str)

    scoped = login(app, ADMIN_EMAIL)
    projects = scoped.get("/api/v1/universal/projects?page_size=100")
    assert projects.status_code == 200, projects.get_json()
    visible = {item["id"] for item in projects.get_json()["items"]}
    assert visible == {project["id"]}

    allowed = scoped.post(
        "/api/v1/universal/keys",
        {"project_id": project["id"], "key_type_id": "reader", "quantity": 1},
    )
    assert allowed.status_code == 201, allowed.get_json()
    denied = scoped.post(
        "/api/v1/universal/keys",
        {"project_id": other["id"], "key_type_id": "reader", "quantity": 1},
    )
    assert denied.status_code == 403
    assert scoped.get("/api/v1/universal/roles").status_code == 403

    merge_copy(owner, project["id"])
    import_copy(owner, other["id"], "Other Project Copy")
    visible_keys = scoped.get("/api/v1/universal/keys?page_size=100").get_json()
    assert {item["project_id"] for item in visible_keys["items"]} == {project["id"]}
    visible_imports = scoped.get(
        "/api/v1/universal/imported-keys?page_size=100"
    ).get_json()
    assert visible_imports["items"]
    assert {item["project_id"] for item in visible_imports["items"]} == {
        project["id"]
    }

    in_use = owner.delete("/api/v1/universal/roles/scoped_operator")
    assert in_use.status_code == 409
    removed = owner.delete(
        f"/api/v1/universal/projects/{project['id']}/role-assignments/{assignment_id}"
    )
    assert removed.status_code == 200, removed.get_json()
    scoped_after = login(app, ADMIN_EMAIL)
    assert scoped_after.get("/api/v1/universal/keys").status_code == 403
    deleted = owner.delete("/api/v1/universal/roles/scoped_operator")
    assert deleted.status_code == 200, deleted.get_json()
    audit = db.fetch_one(
        """SELECT action FROM admin_audit_logs
            WHERE action IN ('project_role.assign', 'project_role.remove')
            ORDER BY created_at ASC"""
    )
    assert audit is not None


def test_scheduler_status_reports_failure_metrics_and_jobs(owner, app, db):
    create_project(owner, "Scheduler Metrics")
    service = SchedulerService(db, app.extensions["settings"])
    first = service.run_once(dry_run=True)
    assert first["status"] == "success"
    db.execute(
        """INSERT INTO nexora_automation_runs
               (job, status, details, error, worker_id, duration_ms, finished_at)
           VALUES ('universal.scheduler', 'failed', '{}'::jsonb,
                   'TimeoutError: worker exceeded deadline', 'metrics-worker',
                   1250, now())"""
    )
    status = service.status()
    assert status["lock_key"] == SCHEDULER_LOCK_KEY
    assert status["worker_status"] in {"idle", "stale", "unknown"}
    assert status["last_failure"] is not None
    assert "deadline" in status["last_failure_reason"]
    assert status["per_job"]
    assert any(job["job"] == "universal.scheduler" for job in status["per_job"])
    assert "lock_held" in status
    assert "webhook_dead" in status["counts"]
    assert status["runs"]


def test_replacement_requires_project_permission(owner, admin, app, db):
    project = create_project(owner, "Replacement Permission")
    generate_key(owner, project["id"])
    copy = import_copy(owner, project["id"], "Replacement Permission Copy")
    record = imported_records(owner, copy["id"])[0]
    admins = owner.get("/api/v1/universal/admins").get_json()["admins"]
    admin_row = next(item for item in admins if item["email"] == ADMIN_EMAIL)
    db.execute(
        """UPDATE admin_users
              SET platform_role = 'custom', platform_permissions = '[]'::jsonb,
                  platform_role_id = NULL, role = 'admin'
            WHERE id = %s""",
        (admin_row["id"],),
    )
    denied = login(app, ADMIN_EMAIL).post(
        "/api/v1/universal/imported-keys/replacement-preview",
        {"record_ids": [record["id"]]},
    )
    assert denied.status_code == 403
