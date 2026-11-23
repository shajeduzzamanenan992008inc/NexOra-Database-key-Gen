"""Integration tests for persisted timezone settings and UTC input handling."""

from __future__ import annotations

import datetime as dt

from backend.services.project_service import ProjectService


def _parse(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_timezone_endpoint_defaults_to_utc(owner):
    response = owner.get("/api/v1/timezone")
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["timezone"] == "UTC"
    assert payload["default_timezone"] == "UTC"
    assert payload["is_default"] is True
    assert payload["utc_offset"] == "UTC+00:00"
    assert payload["utc_offset_seconds"] == 0


def test_timezone_options_are_iana_identifiers(owner):
    response = owner.get("/api/v1/timezones")
    assert response.status_code == 200, response.get_json()
    identifiers = {item["id"] for item in response.get_json()["timezones"]}
    assert "UTC" in identifiers
    assert "Asia/Dhaka" in identifiers
    assert "America/New_York" in identifiers
    assert "+06:00" not in identifiers


def test_timezone_setting_persists_and_is_reported(owner, db):
    response = owner.patch(
        "/api/v1/settings", json={"display_timezone": "Asia/Dhaka"}
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["display_timezone"] == "Asia/Dhaka"

    stored = db.fetch_value(
        "SELECT value FROM system_settings WHERE key = 'nexora.display_timezone'"
    )
    assert stored == "Asia/Dhaka"

    payload = owner.get("/api/v1/timezone").get_json()
    assert payload["timezone"] == "Asia/Dhaka"
    assert payload["is_default"] is False
    assert payload["utc_offset"] == "UTC+06:00"
    assert payload["utc_offset_seconds"] == 6 * 3600

    # A fresh service instance reads the same persisted row (restart safety).
    service = ProjectService(db, owner.client.application.extensions["settings"])
    assert service.get_system_settings()["display_timezone"] == "Asia/Dhaka"


def test_invalid_timezone_is_rejected_and_previous_value_kept(owner):
    assert (
        owner.patch(
            "/api/v1/settings", json={"display_timezone": "Asia/Kolkata"}
        ).status_code
        == 200
    )
    response = owner.patch(
        "/api/v1/settings", json={"display_timezone": "Mars/Olympus"}
    )
    assert response.status_code == 422, response.get_json()
    fields = response.get_json()["error"]["details"]["fields"]
    assert "display_timezone" in fields
    assert owner.get("/api/v1/timezone").get_json()["timezone"] == "Asia/Kolkata"


def test_timezone_can_be_restored_to_utc(owner):
    owner.patch("/api/v1/settings", json={"display_timezone": "America/New_York"})
    response = owner.patch("/api/v1/settings", json={"display_timezone": "UTC"})
    assert response.status_code == 200, response.get_json()
    payload = owner.get("/api/v1/timezone").get_json()
    assert payload["timezone"] == "UTC"
    assert payload["is_default"] is True


def test_naive_custom_expiration_is_interpreted_in_configured_timezone(owner, db):
    owner.patch("/api/v1/settings", json={"display_timezone": "Asia/Dhaka"})

    response = owner.post(
        "/api/v1/projects",
        json={
            "project_name": "Dhaka wall-clock project",
            "expiration": "custom",
            "expires_at": "2030-01-01T00:00:00",
        },
    )
    assert response.status_code == 201, response.get_json()
    project = response.get_json()["project"]
    expected = dt.datetime(2029, 12, 31, 18, 0, tzinfo=dt.timezone.utc)
    assert _parse(project["expires_at"]) == expected

    stored = db.fetch_value(
        "SELECT expires_at FROM nexora_projects WHERE id = %s", (project["id"],)
    )
    assert stored == expected

    # Changing the display timezone must not rewrite the canonical instant.
    owner.patch("/api/v1/settings", json={"display_timezone": "America/New_York"})
    fetched = owner.get(f"/api/v1/projects/{project['id']}").get_json()["project"]
    assert _parse(fetched["expires_at"]) == expected
    assert _parse(fetched["created_at"]).utcoffset() == dt.timedelta(0)


def test_existing_timestamps_are_not_rewritten_by_timezone_change(owner):
    response = owner.post(
        "/api/v1/projects",
        json={"project_name": "Stable timestamp", "expiration": "30_days"},
    )
    assert response.status_code == 201, response.get_json()
    project = response.get_json()["project"]
    before = _parse(project["expires_at"])

    owner.patch("/api/v1/settings", json={"display_timezone": "Asia/Dhaka"})
    after = _parse(
        owner.get(f"/api/v1/projects/{project['id']}").get_json()["project"][
            "expires_at"
        ]
    )
    assert after == before


def test_api_health_reports_configured_timezone(owner):
    owner.patch("/api/v1/settings", json={"display_timezone": "Asia/Kolkata"})
    payload = owner.get("/api/v1/health").get_json()
    assert payload["timezone"] == "Asia/Kolkata"
    assert payload["utc_offset"] == "UTC+05:30"
