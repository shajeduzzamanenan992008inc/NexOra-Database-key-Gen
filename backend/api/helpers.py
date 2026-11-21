"""Shared helpers for API blueprints."""

from __future__ import annotations

from typing import Any

from flask import Response, current_app, request

from ..errors import ApiError
from ..serialization import isoformat
from ..services.auth_service import AuthService
from ..services.license_service import LicenseService
from ..services.project_service import ProjectService
from ..services.project_transfer_service import ProjectTransferService
from ..services.role_service import RoleService
from ..services.scheduler_service import SchedulerService
from ..services.universal_service import UniversalService
from ..services.webhook_service import WebhookService
from ..validation import Payload, client_ip, user_agent


def settings() -> Any:
    return current_app.extensions["settings"]


def license_service() -> LicenseService:
    return current_app.extensions["license_service"]


def project_service() -> ProjectService:
    if "project_service" not in current_app.extensions:
        current_app.extensions["project_service"] = ProjectService(
            current_app.extensions["db"], current_app.extensions["settings"]
        )
    return current_app.extensions["project_service"]


def auth_service() -> AuthService:
    return current_app.extensions["auth_service"]


def universal_service() -> UniversalService:
    if "universal_service" not in current_app.extensions:
        current_app.extensions["universal_service"] = UniversalService(
            current_app.extensions["db"], current_app.extensions["settings"]
        )
    return current_app.extensions["universal_service"]


def webhook_service() -> WebhookService:
    if "webhook_service" not in current_app.extensions:
        current_app.extensions["webhook_service"] = WebhookService(
            current_app.extensions["db"], current_app.extensions["settings"]
        )
    return current_app.extensions["webhook_service"]


def transfer_service() -> ProjectTransferService:
    if "transfer_service" not in current_app.extensions:
        current_app.extensions["transfer_service"] = ProjectTransferService(
            current_app.extensions["db"], current_app.extensions["settings"]
        )
    return current_app.extensions["transfer_service"]


def role_service() -> RoleService:
    if "role_service" not in current_app.extensions:
        current_app.extensions["role_service"] = RoleService(
            current_app.extensions["db"], current_app.extensions["settings"]
        )
    return current_app.extensions["role_service"]


def scheduler_service() -> SchedulerService:
    if "scheduler_service" not in current_app.extensions:
        current_app.extensions["scheduler_service"] = SchedulerService(
            current_app.extensions["db"], current_app.extensions["settings"]
        )
    return current_app.extensions["scheduler_service"]


def request_meta() -> tuple[str, str]:
    cfg = settings()
    return client_ip(request, cfg.trust_proxy), user_agent(request)


def attach_session_cookie(response: Response, token: str, max_age: int) -> None:
    cfg = settings()
    response.set_cookie(
        cfg.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="Strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    cfg = settings()
    response.delete_cookie(
        cfg.session_cookie_name,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="Strict",
        path="/",
    )


def pagination_args() -> tuple[int, int]:
    raw_page = request.args.get("page", "1")
    raw_size = request.args.get("page_size", "25")
    errors: dict[str, str] = {}
    try:
        page = int(raw_page)
        if page < 1:
            raise ValueError
    except (TypeError, ValueError):
        errors["page"] = "Must be an integer greater than or equal to 1."
        page = 1
    try:
        page_size = int(raw_size)
        if not 1 <= page_size <= 100:
            raise ValueError
    except (TypeError, ValueError):
        errors["page_size"] = "Must be an integer between 1 and 100."
        page_size = 25
    if errors:
        from ..errors import ValidationError

        raise ValidationError(errors)
    return page, page_size


def optional_datetime(name: str):
    if name not in request.args or request.args.get(name, "") == "":
        return None
    from ..errors import ValidationError

    payload = Payload({"value": request.args.get(name)})
    parsed = payload.datetime_utc("value", required=True)
    if parsed is None:
        raise ValidationError({name: payload.errors.get("value", "Invalid date.")})
    return parsed


def to_iso(value: Any) -> str | None:
    return isoformat(value) if value is not None else None
