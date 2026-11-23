"""Unit and static contracts for the global timezone system.

These tests need no database or browser. They pin the IANA conversion rules,
DST edge handling, the persisted-setting fallback, and the frontend wiring that
makes every timestamp use the configured zone.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from backend.serialization import jsonable
from backend.timezone import (
    DEFAULT_TIMEZONE,
    TimezoneError,
    format_utc_offset,
    get_configured_timezone,
    parse_datetime,
    resolve_timezone_setting,
    timezone_options,
    timezone_payload,
    validate_timezone,
)
from backend.validation import Payload

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
TIMEZONE_JS = (FRONTEND / "assets" / "js" / "timezone.js").read_text(encoding="utf-8")
UI_JS = (FRONTEND / "assets" / "js" / "ui.js").read_text(encoding="utf-8")
PLATFORM_JS = (FRONTEND / "assets" / "js" / "platform.js").read_text(encoding="utf-8")
DASHBOARD_JS = (FRONTEND / "assets" / "js" / "dashboard.js").read_text(encoding="utf-8")
DASHBOARD_HTML = (FRONTEND / "dashboard.html").read_text(encoding="utf-8")
MIGRATION_012 = (ROOT / "db" / "migrations" / "012_timezone_setting.sql").read_text(
    encoding="utf-8"
)


class FakeDatabase:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def fetch_one(self, *_args, **_kwargs):
        return {"value": self.value} if self.value is not None else None


# ---------------------------------------------------------------------------
# IANA validation and fallbacks
# ---------------------------------------------------------------------------


def test_default_timezone_is_utc():
    assert DEFAULT_TIMEZONE == "UTC"
    assert validate_timezone("UTC") == "UTC"
    assert validate_timezone(" utc ") == "UTC"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        None,
        123,
        "+06:00",
        "-05:00",
        "Asia/Nowhere",
        "../etc/passwd",
        "/etc/localtime",
        "Asia/Dhaka\x00",
    ],
)
def test_invalid_timezone_identifiers_are_rejected(value):
    with pytest.raises(TimezoneError):
        validate_timezone(value)


def test_missing_setting_falls_back_to_utc():
    state = resolve_timezone_setting(FakeDatabase(None))
    assert state.name == "UTC"
    assert state.configured is False
    assert state.valid is True


def test_invalid_persisted_setting_falls_back_to_utc():
    state = resolve_timezone_setting(FakeDatabase("Not/AZone"))
    assert state.name == "UTC"
    assert state.configured is True
    assert state.valid is False
    assert get_configured_timezone(FakeDatabase("Not/AZone")) == "UTC"


def test_persisted_timezone_is_effective():
    db = FakeDatabase("Asia/Dhaka")
    assert get_configured_timezone(db) == "Asia/Dhaka"
    payload = timezone_payload(
        db, now=dt.datetime(2026, 11, 23, 0, 0, tzinfo=dt.timezone.utc)
    )
    assert payload["timezone"] == "Asia/Dhaka"
    assert payload["utc_offset"] == "UTC+06:00"
    assert payload["utc_offset_seconds"] == 6 * 3600
    assert payload["is_default"] is False
    assert payload["local_time"].startswith("2026-11-23T06:00")


def test_timezone_options_include_utc_and_major_zones():
    ids = {option["id"] for option in timezone_options()}
    assert {"UTC", "Asia/Dhaka", "Asia/Kolkata", "America/New_York", "Europe/London"} <= ids


# ---------------------------------------------------------------------------
# UTC / local conversion
# ---------------------------------------------------------------------------


def test_naive_input_is_interpreted_in_configured_timezone():
    parsed = parse_datetime("2026-11-23T00:00:00", "Asia/Dhaka")
    assert parsed == dt.datetime(2026, 11, 22, 18, 0, tzinfo=dt.timezone.utc)


def test_kolkata_half_hour_offset_is_preserved():
    parsed = parse_datetime("2026-11-23T00:00:00", "Asia/Kolkata")
    assert parsed == dt.datetime(2026, 11, 22, 18, 30, tzinfo=dt.timezone.utc)


def test_explicit_offset_is_authoritative():
    parsed = parse_datetime("2026-11-23T00:00:00+06:00", "America/New_York")
    assert parsed == dt.datetime(2026, 11, 22, 18, 0, tzinfo=dt.timezone.utc)


def test_date_only_input_uses_end_of_day_in_configured_timezone():
    parsed = parse_datetime("2026-11-23", "Asia/Dhaka")
    assert parsed == dt.datetime(
        2026, 11, 23, 17, 59, 59, 999999, tzinfo=dt.timezone.utc
    )


def test_date_only_filter_can_use_start_of_day():
    parsed = parse_datetime(
        "2026-11-23", "Asia/Dhaka", date_is_end_of_day=False
    )
    assert parsed == dt.datetime(2026, 11, 22, 18, 0, tzinfo=dt.timezone.utc)


def test_utc_is_stable_in_winter_and_summer():
    winter = parse_datetime("2026-01-15T12:00:00", "UTC")
    summer = parse_datetime("2026-07-01T12:00:00", "UTC")
    assert winter == dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.timezone.utc)
    assert summer == dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)


def test_daylight_saving_new_york_offsets():
    winter = parse_datetime("2026-01-15T12:00:00", "America/New_York")
    summer = parse_datetime("2026-07-01T12:00:00", "America/New_York")
    assert winter == dt.datetime(2026, 1, 15, 17, 0, tzinfo=dt.timezone.utc)
    assert summer == dt.datetime(2026, 7, 1, 16, 0, tzinfo=dt.timezone.utc)


def test_daylight_saving_london_offsets():
    winter = parse_datetime("2026-01-15T12:00:00", "Europe/London")
    summer = parse_datetime("2026-07-01T12:00:00", "Europe/London")
    assert winter == dt.datetime(2026, 1, 15, 12, 0, tzinfo=dt.timezone.utc)
    assert summer == dt.datetime(2026, 7, 1, 11, 0, tzinfo=dt.timezone.utc)


def test_nonexistent_dst_local_time_is_rejected():
    with pytest.raises(TimezoneError, match="daylight-saving"):
        parse_datetime("2026-03-08T02:30:00", "America/New_York")


def test_ambiguous_dst_local_time_resolves_to_first_occurrence():
    parsed = parse_datetime("2026-11-01T01:30:00", "America/New_York")
    # 01:30 EDT (UTC-4) is the first of the two 01:30 wall clocks.
    assert parsed == dt.datetime(2026, 11, 1, 5, 30, tzinfo=dt.timezone.utc)


def test_utc_offset_formatting():
    assert format_utc_offset(dt.timedelta(0)) == "UTC+00:00"
    assert format_utc_offset(dt.timedelta(hours=6)) == "UTC+06:00"
    assert format_utc_offset(dt.timedelta(hours=5, minutes=30)) == "UTC+05:30"
    assert format_utc_offset(dt.timedelta(hours=-8)) == "UTC-08:00"


# ---------------------------------------------------------------------------
# Request validation integration
# ---------------------------------------------------------------------------


def test_payload_datetime_uses_configured_timezone():
    payload = Payload({"value": "2026-11-23T00:00:00"})
    parsed = payload.datetime_utc("value", timezone="Asia/Dhaka")
    assert payload.errors == {}
    assert parsed == dt.datetime(2026, 11, 22, 18, 0, tzinfo=dt.timezone.utc)


def test_payload_datetime_reports_dst_gap_as_field_error():
    payload = Payload({"value": "2026-03-08T02:30:00"})
    parsed = payload.datetime_utc("value", timezone="America/New_York")
    assert parsed is None
    assert "daylight-saving" in payload.errors["value"]


def test_api_serializer_normalizes_aware_datetimes_to_utc():
    dhaka = dt.timezone(dt.timedelta(hours=6))
    value = dt.datetime(2026, 11, 23, 12, 0, tzinfo=dhaka)
    assert jsonable(value) == "2026-11-23T06:00:00+00:00"


# ---------------------------------------------------------------------------
# Frontend and migration static contracts
# ---------------------------------------------------------------------------


def test_frontend_service_uses_intl_timezone_and_central_conversion():
    assert 'from "./timezone.js"' in UI_JS
    assert "Intl.DateTimeFormat" in TIMEZONE_JS
    assert "timeZone" in TIMEZONE_JS
    assert "localInputToUtcIso" in TIMEZONE_JS
    assert "localDateEndOfDayToUtcIso" in TIMEZONE_JS
    assert "/api/v1/timezone" in TIMEZONE_JS


def test_platform_console_uses_shared_timezone_service():
    assert "initTimezone" in PLATFORM_JS
    assert "localInputToUtcIso" in PLATFORM_JS
    assert "data-searchable" in PLATFORM_JS
    assert "Restore UTC" in PLATFORM_JS
    assert "new Date(custom.value).toISOString()" not in PLATFORM_JS
    assert "new Date(expires.value).toISOString()" not in PLATFORM_JS


def test_classic_console_uses_shared_timezone_service():
    assert "initTimezone" in DASHBOARD_JS
    assert "localInputToUtcIso" in DASHBOARD_JS
    assert "localDateEndOfDayToUtcIso" in DASHBOARD_JS
    assert "new Date(customValue).toISOString()" not in DASHBOARD_JS
    assert "new Date(customInput.value).toISOString()" not in DASHBOARD_JS
    assert 'data-searchable="true"' in DASHBOARD_HTML
    assert 'id="timezone-preview"' in DASHBOARD_HTML
    assert 'id="reset-timezone-btn"' in DASHBOARD_HTML


def test_migration_seeds_only_missing_timezone_default():
    assert "nexora.display_timezone" in MIGRATION_012
    assert "'UTC'" in MIGRATION_012
    assert "ON CONFLICT (key) DO NOTHING" in MIGRATION_012
