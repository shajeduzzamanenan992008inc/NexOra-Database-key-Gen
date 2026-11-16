"""Typed API errors and uniform JSON error responses."""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .db import DatabaseUnavailable
from .serialization import jsonable

log = logging.getLogger("nexora.errors")

GENERIC_MESSAGE = "An unexpected error occurred. The request was not completed."


class ApiError(Exception):
    """An error that is safe to return to the caller."""

    def __init__(
        self,
        status: int = 400,
        code: str = "bad_request",
        message: str = "",
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        if status_code is not None:
            status = status_code
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


class ValidationError(ApiError):
    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__(
            422,
            "validation_error",
            "One or more fields are invalid.",
            {"fields": fields},
        )


def error_payload(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    from flask import g

    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(g, "request_id", None),
        }
    }
    if details:
        payload["error"]["details"] = jsonable(details)
    return payload


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        response = jsonify(error_payload(exc.code, exc.message, exc.details))
        response.status_code = exc.status
        for key, value in exc.headers.items():
            response.headers[key] = value
        return response

    @app.errorhandler(DatabaseUnavailable)
    def handle_database_unavailable(exc: DatabaseUnavailable):
        log.error("database unavailable: %s", exc)
        response = jsonify(
            error_payload(
                "database_unavailable",
                "The database is temporarily unavailable. Try again shortly.",
            )
        )
        response.status_code = 503
        response.headers["Retry-After"] = "5"
        return response

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if not request.path.startswith("/api/"):
            return exc

        code = (exc.name or "http_error").lower().replace(" ", "_")
        message = exc.description or GENERIC_MESSAGE
        if exc.code == 404:
            code = "not_found"
            message = "The requested resource was not found."
        elif exc.code == 405:
            code = "method_not_allowed"
            message = "This HTTP method is not allowed for the endpoint."
        elif exc.code == 413:
            code = "payload_too_large"
            message = "The request body is too large."
        elif exc.code == 429:
            code = "rate_limited"
        response = jsonify(error_payload(code, message))
        response.status_code = exc.code or 500
        for key, value in getattr(exc, "headers", {}) or {}:
            response.headers[key] = value
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        if isinstance(exc, HTTPException):
            return handle_http_error(exc)
        if isinstance(exc, DatabaseUnavailable):
            return handle_database_unavailable(exc)
        if isinstance(exc, ApiError):
            return handle_api_error(exc)
        log.exception(
            "unhandled error",
            extra={"request_id": getattr(request, "request_id", None)},
        )
        response = jsonify(error_payload("internal_error", GENERIC_MESSAGE))
        response.status_code = 500
        return response
