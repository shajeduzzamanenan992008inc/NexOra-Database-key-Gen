"""REST API blueprints."""

from .auth import bp as auth_bp
from .dashboard import bp as dashboard_bp
from .keys import bp as keys_bp
from .logs import bp as logs_bp

BLUEPRINTS = (auth_bp, keys_bp, dashboard_bp, logs_bp)

__all__ = ["BLUEPRINTS"]
