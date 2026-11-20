"""NexOra Version 1 REST API blueprints."""

from __future__ import annotations

from flask import Blueprint

from .audit import bp as audit_bp
from .auth import bp as auth_bp
from .dashboard import bp as dashboard_bp
from .health import bp as health_bp
from .installations import bp as installations_bp
from .keys import bp as keys_bp
from .projects import bp as projects_bp
from .reader import bp as reader_bp
from .security import bp as security_bp
from .settings import bp as settings_bp
from .universal import bp as universal_bp

v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
v1_bp.register_blueprint(health_bp)
v1_bp.register_blueprint(projects_bp)
v1_bp.register_blueprint(reader_bp)
v1_bp.register_blueprint(installations_bp)
v1_bp.register_blueprint(dashboard_bp)
v1_bp.register_blueprint(keys_bp)
v1_bp.register_blueprint(security_bp)
v1_bp.register_blueprint(audit_bp)
v1_bp.register_blueprint(settings_bp)
v1_bp.register_blueprint(auth_bp)
v1_bp.register_blueprint(universal_bp)

V1_BLUEPRINTS = (v1_bp,)

__all__ = ["v1_bp", "V1_BLUEPRINTS"]
