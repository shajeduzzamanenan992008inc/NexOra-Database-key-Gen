"""Strict server-side validation helpers.

Every endpoint validates its own input. Nothing the browser sends is trusted,
including status, activation limits, timestamps, roles, and identifiers.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from uuid import UUID

from flask import Request

from .errors import ApiError, ValidationError
from .timezone import DEFAULT_TIMEZONE, TimezoneError, parse_datetime

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")


def require_json(request: Request) -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if payload is None:
        if request.data:
            raise ApiError(
                400,
                "invalid_json",
                "The request body must contain valid JSON.",
            )
        return {}
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_body", "The request body must be a JSON object.")
    return payload


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:45]
    return (request.remote_addr or "unknown")[:45]


def user_agent(request: Request) -> str:
    return (request.headers.get("User-Agent") or "")[:300]


def ensure_email(value: str) -> str:
    candidate = value.strip().lower()
    if len(candidate) > 254 or not _EMAIL_RE.match(candidate):
        raise ApiError(422, "validation_error", "A valid email address is required.")
    return candidate


class Payload:
    """Collect field errors and raise one validation error at the end."""

    def __init__(self, data: Any) -> None:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ApiError(
                400, "invalid_body", "The request body must be a JSON object."
            )
        self.data = data
        self.errors: dict[str, str] = {}

    def has(self, name: str) -> bool:
        return name in self.data

    def _valid(self, name: str) -> bool:
        return name not in self.errors

    def string(
        self,
        name: str,
        *,
        required: bool = False,
        default: str = "",
        min_length: int = 0,
        max_length: int = 500,
        pattern: str | None = None,
        pattern_message: str = "This value has an invalid format.",
        allow_null: bool = False,
        strip: bool = True,
        upper: bool = False,
    ) -> str | None:
        if name not in self.data:
            if required:
                self.errors[name] = "This field is required."
                return None
            return default
        value = self.data[name]
        if value is None:
            if allow_null:
                return None
            if required:
                self.errors[name] = "This field is required."
                return None
            return default
        if not isinstance(value, str):
            self.errors[name] = "This field must be a string."
            return None
        if strip:
            value = value.strip()
        if upper:
            value = value.upper()
        if len(value) < min_length:
            self.errors[name] = f"Must be at least {min_length} characters."
            return None
        if len(value) > max_length:
            self.errors[name] = f"Must be at most {max_length} characters."
            return None
        if pattern is not None and not re.fullmatch(pattern, value):
            self.errors[name] = pattern_message
            return None
        return value

    def email(self, name: str, *, required: bool = True) -> str | None:
        raw = self.string(name, required=required, max_length=254)
        if raw is None:
            return None
        candidate = raw.strip().lower()
        if len(candidate) > 254 or not _EMAIL_RE.match(candidate):
            self.errors[name] = "Enter a valid email address."
            return None
        return candidate

    def integer(
        self,
        name: str,
        *,
        default: int | None = None,
        required: bool = False,
        minimum: int = 0,
        maximum: int = 2_147_483_647,
    ) -> int | None:
        if name not in self.data:
            if required:
                self.errors[name] = "This field is required."
                return None
            return default
        value = self.data[name]
        if isinstance(value, bool):
            self.errors[name] = "This field must be an integer."
            return None
        if isinstance(value, float) and not value.is_integer():
            self.errors[name] = "This field must be an integer."
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            self.errors[name] = "This field must be an integer."
            return None
        if not minimum <= parsed <= maximum:
            self.errors[name] = f"Must be between {minimum} and {maximum}."
            return None
        return parsed

    def boolean(self, name: str, *, default: bool | None = None) -> bool | None:
        if name not in self.data:
            return default
        value = self.data[name]
        if isinstance(value, bool):
            return value
        self.errors[name] = "This field must be a boolean."
        return None

    def enum(
        self,
        name: str,
        *,
        allowed: Iterable[str],
        default: str | None = None,
        required: bool = False,
    ) -> str | None:
        allowed_set = set(allowed)
        if name not in self.data:
            if required:
                self.errors[name] = "This field is required."
                return None
            return default
        value = self.data[name]
        if not isinstance(value, str) or value not in allowed_set:
            self.errors[name] = "Unsupported value. Allowed: " + ", ".join(
                sorted(allowed_set)
            )
            return None
        return value

    def uuid(self, name: str, *, required: bool = False) -> str | None:
        if name not in self.data:
            if required:
                self.errors[name] = "This field is required."
            return None
        if self.data[name] is None:
            if required:
                self.errors[name] = "This field is required."
            return None
        raw = self.string(
            name, required=required, min_length=36, max_length=36
        )
        if raw is None or raw == "":
            return None
        try:
            return str(UUID(raw))
        except (ValueError, AttributeError):
            self.errors[name] = "Must be a valid UUID."
            return None

    def datetime_utc(
        self,
        name: str,
        *,
        required: bool = False,
        date_is_end_of_day: bool = True,
        allow_null: bool = False,
        timezone: str | None = None,
    ):
        if name not in self.data:
            if required:
                self.errors[name] = "This field is required."
                return None
            return None
        value = self.data[name]
        if value is None:
            if allow_null:
                return None
            self.errors[name] = "This field is required."
            return None
        if not isinstance(value, str) or not value.strip():
            self.errors[name] = "Must be an ISO-8601 date or timestamp."
            return None

        try:
            return parse_datetime(
                value,
                timezone or DEFAULT_TIMEZONE,
                date_is_end_of_day=date_is_end_of_day,
            )
        except TimezoneError as exc:
            self.errors[name] = str(exc)
            return None

    def reject_unknown(self, allowed: Iterable[str]) -> None:
        allowed_set = set(allowed)
        unexpected = sorted(set(self.data) - allowed_set)
        if unexpected:
            self.errors["_body"] = "Unexpected field(s): " + ", ".join(unexpected)

    def finish(self) -> None:
        if self.errors:
            raise ValidationError(self.errors)

    def error(self, name: str, message: str) -> None:
        self.errors[name] = message
