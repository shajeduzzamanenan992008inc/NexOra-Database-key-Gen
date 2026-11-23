"""Regression coverage for the Classic Universal Key Generator submit path.

The browser creates a project through ``POST /api/v1/projects`` and then issues
its key through ``POST /api/v1/universal/keys``.  These tests use that exact
two-request sequence and verify persistence, expiration, bulk generation and
idempotent failure/retry behavior.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.generator]


def create_project(owner, name: str, **body) -> dict:
    response = owner.post(
        "/api/v1/projects", {"project_name": name, **body}
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["project"]


def ui_payload(project_id: str, *, batch_id: str | None = None, **overrides) -> dict:
    body = {
        "project_id": project_id,
        "key_type_id": "reader",
        "batch_id": batch_id or str(uuid4()),
        "label": "Primary reader deployment",
        "expiration_preset": "30_days",
        "expires_at": None,
        "permissions": [],
        "scopes": [],
        "tags": ["production", "reader"],
        "notes": "Created from the Classic generator form.",
        "metadata": {},
    }
    body.update(overrides)
    return body


def test_ui_shaped_generation_persists_one_key_with_expiry(owner, app):
    project = create_project(owner, f"Generator UI {uuid4().hex[:8]}")
    response = owner.post(
        "/api/v1/universal/keys", ui_payload(project["id"])
    )
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    assert len(payload["keys"]) == 1
    key = payload["keys"][0]
    assert key["key"].startswith("RDR-")
    assert key["status"] == "generated"
    assert key["expires_at"] is not None

    row = app.extensions["db"].fetch_one(
        "SELECT project_id, key_hash, key_hint, status, expires_at, "
        "expiration_preset, tags, notes, batch_id "
        "FROM reader_keys WHERE id = %s",
        (key["id"],),
    )
    assert str(row["project_id"]) == project["id"]
    assert row["status"] == "generated"
    assert row["expires_at"] is not None
    assert row["expiration_preset"] == "30_days"
    assert row["key_hash"] != key["key"]
    assert key["key"] not in row["key_hash"]
    assert row["key_hint"] != key["key"]
    assert "production" in row["tags"]
    assert row["notes"] == "Created from the Classic generator form."
    assert str(row["batch_id"]) == payload["batch_id"]

    listing = owner.get(f"/api/v1/universal/keys?project_id={project['id']}")
    assert listing.status_code == 200
    listing_payload = listing.get_json()
    listed = listing_payload.get("keys") or listing_payload.get("items") or []
    assert any(item["id"] == key["id"] for item in listed)
    assert key["key"] not in listing.get_data(as_text=True)


def test_ui_generation_supports_bulk_quantity_and_unique_keys(owner, app):
    project = create_project(owner, f"Generator Bulk {uuid4().hex[:8]}")
    response = owner.post(
        "/api/v1/universal/keys",
        ui_payload(project["id"], quantity=3),
    )
    assert response.status_code == 201, response.get_json()
    keys = response.get_json()["keys"]
    assert len(keys) == 3
    assert len({item["id"] for item in keys}) == 3
    assert len({item["key"] for item in keys}) == 3
    count = app.extensions["db"].fetch_value(
        "SELECT count(*) FROM reader_keys WHERE project_id = %s",
        (project["id"],),
    )
    assert count == 3


def test_failed_generation_leaves_project_clean_and_retry_succeeds(owner, app):
    project = create_project(owner, f"Generator Retry {uuid4().hex[:8]}")
    failed = owner.post(
        "/api/v1/universal/keys",
        ui_payload(project["id"], key_type_id="type-that-does-not-exist"),
    )
    assert failed.status_code in (404, 422)
    assert (
        app.extensions["db"].fetch_value(
            "SELECT count(*) FROM reader_keys WHERE project_id = %s",
            (project["id"],),
        )
        == 0
    )

    retry = owner.post("/api/v1/universal/keys", ui_payload(project["id"]))
    assert retry.status_code == 201, retry.get_json()
    assert (
        app.extensions["db"].fetch_value(
            "SELECT count(*) FROM reader_keys WHERE project_id = %s",
            (project["id"],),
        )
        == 1
    )


def test_repeated_batch_is_rejected_without_duplicate_key(owner, app):
    project = create_project(owner, f"Generator Batch {uuid4().hex[:8]}")
    batch_id = str(uuid4())
    first = owner.post(
        "/api/v1/universal/keys",
        ui_payload(project["id"], batch_id=batch_id),
    )
    assert first.status_code == 201, first.get_json()
    plaintext = first.get_json()["keys"][0]["key"]

    second = owner.post(
        "/api/v1/universal/keys",
        ui_payload(project["id"], batch_id=batch_id),
    )
    assert second.status_code == 409
    payload = second.get_json()
    assert payload["error"]["code"] == "BATCH_ALREADY_PROCESSED"
    assert len(payload["error"]["details"]["key_hints"]) == 1
    assert plaintext not in json_text(payload)
    assert (
        app.extensions["db"].fetch_value(
            "SELECT count(*) FROM reader_keys WHERE batch_id = %s",
            (batch_id,),
        )
        == 1
    )


def test_custom_expiry_and_never_expire_are_persisted(owner, app):
    project = create_project(owner, f"Generator Expiry {uuid4().hex[:8]}")
    custom = owner.post(
        "/api/v1/universal/keys",
        ui_payload(
            project["id"],
            expiration_preset="custom",
            expires_at="2030-01-02T03:04:05+00:00",
        ),
    )
    assert custom.status_code == 201, custom.get_json()
    custom_key = custom.get_json()["keys"][0]
    assert custom_key["expires_at"].startswith("2030-01-02T03:04:05")

    forever = owner.post(
        "/api/v1/universal/keys",
        ui_payload(project["id"], expiration_preset="never"),
    )
    assert forever.status_code == 201, forever.get_json()
    forever_key = forever.get_json()["keys"][0]
    assert forever_key["expires_at"] is None
    row = app.extensions["db"].fetch_one(
        "SELECT is_permanent, expires_at FROM reader_keys WHERE id = %s",
        (forever_key["id"],),
    )
    assert row["is_permanent"] is True
    assert row["expires_at"] is None


def json_text(value) -> str:
    import json

    return json.dumps(value, default=str)
