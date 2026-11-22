"""Flask application factory."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from flask import Flask, g, jsonify, redirect, request, send_from_directory, url_for

from .api import BLUEPRINTS
from .config import ConfigError, Settings
from .db import Database
from .errors import register_error_handlers
from .security.rate_limit import SlidingWindowLimiter, enforce
from .security.sessions import find_session
from .services import AuthService, LicenseService, ensure_initial_admin

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
EFFECT_ASSETS_DIR = PROJECT_ROOT / "assets" / "effect"
PRIMARY_LOGO_FILE = "ultra-universal-keygen-logo-v3.svg"
OMEGA_ASSETS_DIR = PROJECT_ROOT / "assets" / "omega-key-assets"

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

access_log = logging.getLogger("nexora.access")


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app(settings: Settings | None = None) -> Flask:
    _configure_logging()
    settings = settings or Settings.from_env()

    app = Flask(__name__, static_folder=None)
    # Project bundle uploads are validated separately (8 MiB JSON/ZIP with a
    # stricter parser). The Flask ceiling only needs to admit the multipart
    # envelope; it is not a trust boundary for bundle contents.
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    app.config["SETTINGS"] = settings
    app.secret_key = settings.secret_key
    app.json.sort_keys = False

    db = Database(settings.database)
    app.extensions["settings"] = settings
    app.extensions["db"] = db
    app.extensions["limiter"] = SlidingWindowLimiter()
    app.extensions["auth_service"] = AuthService(db, settings)
    app.extensions["license_service"] = LicenseService(db, settings)

    # Provision the initial administrator from the environment on first boot.
    # The operation is idempotent and never updates an existing account. A
    # missing or malformed credential fails application start-up. Direct
    # construction of Settings cannot bypass this in production.
    if settings.is_production and not settings.secret_key:
        raise ConfigError("SECRET_KEY is required in production.")
    if settings.is_production and not (
        settings.admin_email and settings.admin_password_hash
    ):
        raise ConfigError(
            "ADMIN_EMAIL and ADMIN_PASSWORD_HASH are required in production."
        )
    if settings.admin_email or settings.admin_password_hash:
        bootstrap = ensure_initial_admin(db, settings)
        if bootstrap["created"]:
            access_log.info(
                "initial administrator account created (role=%s)", bootstrap["role"]
            )
        else:
            access_log.info(
                "initial administrator account already exists; credentials preserved"
            )

    register_error_handlers(app)

    @app.before_request
    def _before_request():
        g.request_id = uuid4().hex
        g.request_started = time.perf_counter()
        enforce(
            request,
            limiter=app.extensions["limiter"],
            enabled=settings.rate_limit_enabled,
            trust_proxy=settings.trust_proxy,
        )

    @app.after_request
    def _security_headers(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

        if settings.is_production and settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"

        elapsed_ms = (
            time.perf_counter() - getattr(g, "request_started", time.perf_counter())
        ) * 1000
        access_log.info(
            "%s %s %s %.1fms request_id=%s",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
            getattr(g, "request_id", "-"),
        )
        return response

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.get("/api/health")
    def health():
        database_ok = app.extensions["db"].ping()
        return (
            jsonify(
                {
                    "status": "ok" if database_ok else "degraded",
                    "database": "connected" if database_ok else "unavailable",
                    "version": settings.version,
                    "service": "nexora-keygen",
                }
            ),
            200 if database_ok else 503,
        )

    def _page(filename: str, status: int = 200):
        response = send_from_directory(FRONTEND_DIR, filename, max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.status_code = status
        return response

    @app.get("/")
    @app.get("/login")
    def login_page():
        return _page("index.html")

    @app.get("/dashboard")
    def dashboard_page():
        # The Classic console is the primary application shell. The SPA shell
        # is only served to a valid session; the browser-side guard remains as
        # a second layer for expired sessions.
        token = request.cookies.get(settings.session_cookie_name)
        if (
            not token
            or find_session(
                app.extensions["db"],
                token,
                audience=settings.session_audience,
            )
            is None
        ):
            return redirect(url_for("login_page"))
        return _page("dashboard.html")

    @app.get("/platform")
    def platform_page():
        # The former Universal Platform is no longer a second interface. Keep
        # the URL as a compatibility redirect for bookmarks and old links.
        token = request.cookies.get(settings.session_cookie_name)
        if (
            not token
            or find_session(
                app.extensions["db"],
                token,
                audience=settings.session_audience,
            )
            is None
        ):
            return redirect(url_for("login_page"))
        return redirect(url_for("dashboard_page"))

    @app.get("/omega-assets/<path:filename>")
    def omega_assets(filename: str):
        if not OMEGA_ASSETS_DIR.is_dir():
            return jsonify({"error": {"code": "not_found"}}), 404
        return send_from_directory(OMEGA_ASSETS_DIR, filename, max_age=86400)

    @app.get("/effect-assets/<path:filename>")
    def effect_assets(filename: str):
        # Approved NexOra effect artwork (primary logo + hero banner).
        if not EFFECT_ASSETS_DIR.is_dir():
            return jsonify({"error": {"code": "not_found"}}), 404
        return send_from_directory(EFFECT_ASSETS_DIR, filename, max_age=86400)

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        # Revalidate on every load (304 via ETag) so a deploy never serves a
        # stale console to an administrator with a warm cache.
        return send_from_directory(ASSETS_DIR, filename, max_age=0)

    @app.get("/favicon.svg")
    def favicon_svg():
        # The approved primary logo is the favicon source of truth.
        return send_from_directory(
            EFFECT_ASSETS_DIR,
            PRIMARY_LOGO_FILE,
            max_age=86400,
            mimetype="image/svg+xml",
        )

    @app.get("/favicon.ico")
    def favicon():
        # Legacy browsers request /favicon.ico directly; the SVG is the real
        # icon, so send them to it instead of a second stale image.
        return redirect(url_for("favicon_svg"))

    return app
