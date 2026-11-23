"""Centralized IANA timezone handling for NexOra.

Every timestamp is stored in UTC. This module is the only place that turns a
wall-clock value into UTC (and back), so expiration enforcement, date filters
and API metadata cannot drift apart.

The configured timezone is an administrator preference stored in
``system_settings`` under ``nexora.display_timezone``. It defaults to UTC and
never changes historical timestamps: it only changes how local input is
interpreted and how the console renders instants.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_TIMEZONE = "UTC"
TIMEZONE_SETTING_KEY = "nexora.display_timezone"
UTC = dt.timezone.utc


class TimezoneError(ValueError):
    """A timezone identifier or local wall-clock value cannot be used safely."""


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    try:
        zones = set(available_timezones())
    except Exception:  # pragma: no cover - depends on host tzdata
        zones = set()
    zones.add(DEFAULT_TIMEZONE)
    return frozenset(zones)


def validate_timezone(value: Any) -> str:
    """Return a canonical IANA identifier or raise :class:`TimezoneError`.

    Only identifiers from the installed IANA database are accepted. Fixed
    offsets such as ``+06:00`` are not timezones and are rejected, as are
    malformed or path-like values.
    """

    if not isinstance(value, str):
        raise TimezoneError("Choose a valid IANA timezone identifier.")
    candidate = value.strip()
    if not candidate:
        raise TimezoneError("Choose a valid IANA timezone identifier.")
    if len(candidate) > 64:
        raise TimezoneError("Timezone identifiers must be 64 characters or fewer.")
    if candidate.upper() == "UTC":
        return DEFAULT_TIMEZONE
    if (
        candidate.startswith(("/", "\\", "."))
        or ".." in candidate
        or "\\" in candidate
        or "\x00" in candidate
    ):
        raise TimezoneError("Choose a valid IANA timezone identifier.")
    # A bare numeric offset is not an IANA identifier. Accepting it would make
    # DST behavior dependent on the administrator's current offset.
    if candidate[0] in "+-" or ":" in candidate:
        raise TimezoneError(
            "Use an IANA timezone identifier (for example Asia/Dhaka), not a UTC offset."
        )
    if candidate not in _known_timezones():
        raise TimezoneError(
            "Unknown timezone. Use a valid IANA identifier such as Asia/Dhaka or America/New_York."
        )
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise TimezoneError(
            "Unknown timezone. Use a valid IANA identifier such as Asia/Dhaka or America/New_York."
        ) from None
    return candidate


def is_valid_timezone(value: Any) -> bool:
    try:
        validate_timezone(value)
    except TimezoneError:
        return False
    return True


def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(validate_timezone(name))


def _localize(naive: dt.datetime, name: str) -> dt.datetime:
    """Attach ``name`` to a naive wall-clock value, handling DST edges.

    A nonexistent local time (the spring-forward gap) is rejected rather than
    silently shifted. An ambiguous time (the fall-back overlap) resolves to the
    first occurrence deterministically, which matches the default ``fold=0``
    behavior of the IANA database.
    """

    zone = _zone(name)
    first = naive.replace(tzinfo=zone, fold=0)
    roundtrip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if roundtrip != naive:
        raise TimezoneError(
            f"{naive.strftime('%Y-%m-%d %H:%M')} does not exist in {name} "
            "because of a daylight-saving transition. Choose another local time."
        )
    return first.astimezone(UTC)


def parse_datetime(
    value: Any,
    timezone: str = DEFAULT_TIMEZONE,
    *,
    date_is_end_of_day: bool = True,
) -> dt.datetime | None:
    """Parse an ISO date or timestamp into an aware UTC datetime.

    * An explicit offset (``...Z`` or ``...+06:00``) is authoritative.
    * A naive timestamp is interpreted in the configured timezone.
    * A date-only value becomes the start or end of that day in the configured
      timezone, then converts to UTC.

    ``None`` is returned only for ``None``. Malformed values and DST gaps raise
    :class:`TimezoneError`.
    """

    if value is None:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        if not isinstance(value, str) or not value.strip():
            raise TimezoneError("Use an ISO-8601 date or timestamp.")
        text = value.strip()
        try:
            if len(text) == 10 and text[4] == "-" and text[7] == "-":
                date = dt.date.fromisoformat(text)
                time = (
                    dt.time(23, 59, 59, 999999)
                    if date_is_end_of_day
                    else dt.time(0, 0, 0)
                )
                return _localize(dt.datetime.combine(date, time), timezone)
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise TimezoneError("Use an ISO-8601 date or timestamp.") from None

    if parsed.tzinfo is None:
        return _localize(parsed, timezone)
    return parsed.astimezone(UTC)


def to_local(value: dt.datetime, timezone: str) -> dt.datetime:
    """Convert an aware instant to the configured timezone."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_zone(timezone))


def format_utc_offset(offset: dt.timedelta | None) -> str:
    total = int((offset or dt.timedelta(0)).total_seconds())
    sign = "+" if total >= 0 else "-"
    hours, remainder = divmod(abs(total), 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


@dataclass(frozen=True)
class TimezoneState:
    """The effective timezone and the raw persisted setting behind it."""

    name: str = DEFAULT_TIMEZONE
    configured_value: str = ""
    configured: bool = False
    valid: bool = True


def resolve_timezone_setting(db: Any | None) -> TimezoneState:
    """Read the persisted timezone, falling back to UTC when missing/invalid.

    A database failure is not hidden from the caller's main operation, but this
    helper is deliberately best-effort: an unreadable setting must never make a
    timestamp unrenderable or a key expire at the wrong moment. The effective
    fallback is always UTC.
    """

    if db is None:
        return TimezoneState()
    try:
        row = db.fetch_one(
            "SELECT value FROM system_settings WHERE key = %s",
            (TIMEZONE_SETTING_KEY,),
        )
    except Exception:
        return TimezoneState()
    raw = str((row or {}).get("value") or "").strip()
    if not raw:
        return TimezoneState()
    try:
        name = validate_timezone(raw)
    except TimezoneError:
        return TimezoneState(
            name=DEFAULT_TIMEZONE,
            configured_value=raw,
            configured=True,
            valid=False,
        )
    return TimezoneState(
        name=name,
        configured_value=raw,
        configured=True,
        valid=True,
    )


def get_configured_timezone(db: Any | None) -> str:
    return resolve_timezone_setting(db).name


def timezone_payload(
    db: Any | None, *, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Describe the effective timezone for API clients and the settings UI."""

    state = resolve_timezone_setting(db)
    instant = now or dt.datetime.now(UTC)
    local = to_local(instant, state.name)
    offset = local.utcoffset() or dt.timedelta(0)
    return {
        "timezone": state.name,
        "default_timezone": DEFAULT_TIMEZONE,
        "is_default": state.name == DEFAULT_TIMEZONE,
        "configured": state.configured,
        "configured_value": state.configured_value,
        "valid": state.valid,
        "utc_offset": format_utc_offset(offset),
        "utc_offset_seconds": int(offset.total_seconds()),
        "abbreviation": local.tzname() or state.name,
        "local_time": local.isoformat(),
        "utc_time": instant.astimezone(UTC).isoformat(),
    }


_OPTIONS_CACHE_SECONDS = 300


@lru_cache(maxsize=4)
def _timezone_options_for_bucket(bucket: int) -> tuple[dict[str, Any], ...]:
    """Build the IANA catalog once per five-minute offset bucket.

    Reading every zone on Windows can take several seconds on the first call;
    the settings page requests this catalog on each render. Bucketing keeps the
    list current across DST transitions without rebuilding it on every view.
    """

    instant = dt.datetime.fromtimestamp(bucket * _OPTIONS_CACHE_SECONDS, tz=UTC)
    options: list[dict[str, Any]] = []
    for name in sorted(
        _known_timezones(), key=lambda item: (item != DEFAULT_TIMEZONE, item)
    ):
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
        local = instant.astimezone(zone)
        offset = local.utcoffset() or dt.timedelta(0)
        options.append(
            {
                "id": name,
                "name": name,
                "offset": format_utc_offset(offset),
                "offset_seconds": int(offset.total_seconds()),
                "abbreviation": local.tzname() or name,
            }
        )
    return tuple(options)


def timezone_options(*, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Return every installed IANA timezone with its current offset."""

    instant = now or dt.datetime.now(UTC)
    bucket = int(instant.timestamp() // _OPTIONS_CACHE_SECONDS)
    return [dict(option) for option in _timezone_options_for_bucket(bucket)]
