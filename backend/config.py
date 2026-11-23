"""Environment configuration with fail-fast validation.

Secrets are read from the process environment only. Nothing in this module is
importable by the browser bundle: the frontend is served as static files and
never receives database credentials or the key pepper.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_VALID_SSL_MODES = frozenset({"disable", "require", "verify-ca", "verify-full"})
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
_ADMIN_ROLES = frozenset({"owner", "admin"})
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "replace-me",
    "replace_me",
    "replace_with",
    "replace_",
    "your-",
    "<",
    "placeholder",
    "example",
)


class ConfigError(RuntimeError):
    """Configuration is missing or unsafe; the application must not start."""


def _string_env(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


def _bool_env(name: str, default: bool) -> bool:
    raw = _string_env(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value (true/false).")


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = _string_env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer.") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = _string_env(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number.") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class DatabaseSettings:
    """A parsed PostgreSQL/Supabase connection description.

    ``password`` is excluded from ``repr`` so a stray log statement cannot leak
    it. Use ``dsn_summary`` for diagnostics.
    """

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str = "postgres"
    sslmode: str = "require"
    sslrootcert: str | None = None
    connect_timeout: int = 10
    pool_max: int = 10
    pool_acquire_timeout: int = 15

    @property
    def uses_ssl(self) -> bool:
        return self.sslmode != "disable"

    @property
    def dsn_summary(self) -> str:
        return (
            f"postgresql://{self.user}@{self.host}:{self.port}/"
            f"{self.database}?sslmode={self.sslmode}"
        )

    @classmethod
    def from_dsn(
        cls,
        dsn: str | None,
        *,
        pool_max: int = 10,
        connect_timeout: int = 10,
    ) -> "DatabaseSettings":
        if not dsn:
            raise ConfigError(
                "DATABASE_URL is required. Use the Supabase PostgreSQL connection string."
            )
        try:
            parsed = urlparse(dsn)
            port = parsed.port
        except ValueError as exc:
            raise ConfigError(f"DATABASE_URL is malformed: {exc}") from None

        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ConfigError("DATABASE_URL must use postgresql:// or postgres://.")
        if not parsed.hostname:
            raise ConfigError("DATABASE_URL must include a host.")

        query = parse_qs(parsed.query)
        sslmode = (
            query.get("sslmode", [None])[0]
            or _string_env("DATABASE_SSLMODE")
            or "require"
        ).lower()
        if sslmode not in _VALID_SSL_MODES:
            raise ConfigError(
                "DATABASE_URL sslmode must be one of: "
                + ", ".join(sorted(_VALID_SSL_MODES))
            )

        sslrootcert = _validate_sslrootcert(
            query.get("sslrootcert", [None])[0]
            or _string_env("DATABASE_SSLROOTCERT")
        )

        timeout = connect_timeout
        if "connect_timeout" in query:
            try:
                timeout = int(query["connect_timeout"][0])
            except (TypeError, ValueError):
                raise ConfigError("connect_timeout must be an integer.") from None
            if not 1 <= timeout <= 60:
                raise ConfigError("connect_timeout must be between 1 and 60 seconds.")

        return cls(
            host=parsed.hostname,
            port=port or 5432,
            user=unquote(parsed.username) if parsed.username else "postgres",
            password=unquote(parsed.password) if parsed.password else "",
            database=unquote(parsed.path.lstrip("/")) or "postgres",
            sslmode=sslmode,
            sslrootcert=sslrootcert,
            connect_timeout=timeout,
            pool_max=pool_max,
        )


def _validate_sslrootcert(value: str | None) -> str | None:
    if not value:
        return None
    candidate = Path(os.path.expanduser(value.strip()))
    if not candidate.is_absolute():
        candidate = _PROJECT_ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise ConfigError(
            "DATABASE_SSLROOTCERT is set but no certificate bundle exists at "
            f"{candidate}. Download your provider's CA certificate (for Supabase: "
            "Database -> SSL Configuration -> prod-ca-2021.crt)."
        )
    return str(candidate)


def _validate_pepper(value: str | None) -> str:
    if not value:
        raise ConfigError(
            "KEY_PEPPER is required. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if len(value) < 32:
        raise ConfigError("KEY_PEPPER must be at least 32 characters long.")
    lowered = value.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            raise ConfigError(
                "KEY_PEPPER still contains a placeholder value; generate a random secret."
            )
    return value


def _validate_secret_key(value: str | None) -> str:
    if not value:
        raise ConfigError(
            "SECRET_KEY is required. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if len(value) < 32:
        raise ConfigError("SECRET_KEY must be at least 32 characters long.")
    lowered = value.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            raise ConfigError(
                "SECRET_KEY still contains a placeholder value; generate a random secret."
            )
    return value


def _validate_admin_email(value: str | None) -> str:
    if not value:
        raise ConfigError("ADMIN_EMAIL is required for initial administrator setup.")
    candidate = value.strip().lower()
    if len(candidate) > 254 or not _EMAIL_RE.match(candidate):
        raise ConfigError("ADMIN_EMAIL must be a valid email address.")
    return candidate


def _validate_admin_password_hash(value: str | None) -> str:
    if not value:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH is required. Generate one with: "
            "python -m backend.cli hash-password --email admin@example.com"
        )
    # Imported lazily to keep config.py free of a package-level cycle
    # (security/__init__ imports sessions, which imports db, which imports us).
    from .security.passwords import validate_password_hash

    try:
        validate_password_hash(value)
    except ValueError as exc:
        raise ConfigError(
            "ADMIN_PASSWORD_HASH is invalid: "
            f"{exc}. Generate a new hash with: "
            "python -m backend.cli hash-password --email admin@example.com"
        ) from None
    return value


def _validate_admin_role(value: str | None) -> str:
    role = (value or "owner").strip().lower()
    if role not in _ADMIN_ROLES:
        raise ConfigError("ADMIN_ROLE must be either 'owner' or 'admin'.")
    return role


def _validate_supabase_url(value: str | None) -> str:
    """Validate SUPABASE_URL — optional, but must be a real Supabase project URL if set."""
    if not value:
        return ""
    value = value.strip().rstrip("/")
    for marker in _PLACEHOLDER_MARKERS:
        if marker in value.lower():
            raise ConfigError("SUPABASE_URL still contains a placeholder value.")
    if not value.startswith("https://"):
        raise ConfigError("SUPABASE_URL must start with https://")
    if ".supabase.co" not in value:
        raise ConfigError(
            "SUPABASE_URL must be a valid Supabase project URL (*.supabase.co)"
        )
    return value


def _validate_supabase_service_role_key(key: str | None, url: str) -> str:
    """Validate SUPABASE_SERVICE_ROLE_KEY when configured."""
    if not key:
        if url:
            raise ConfigError(
                "SUPABASE_SERVICE_ROLE_KEY is required when SUPABASE_URL is configured."
            )
        return ""
    if not url:
        raise ConfigError(
            "SUPABASE_URL is required when SUPABASE_SERVICE_ROLE_KEY is configured."
        )
    value = key.strip()
    lowered = value.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            raise ConfigError(
                "SUPABASE_SERVICE_ROLE_KEY still contains a placeholder value."
            )
    if len(value) < 20:
        raise ConfigError("SUPABASE_SERVICE_ROLE_KEY appears too short to be a valid JWT.")
    return value


@dataclass(frozen=True)
class Settings:
    """Validated application settings.

    ``secret_key``, ``key_pepper``, ``admin_password_hash`` and
    ``supabase_service_role_key`` are excluded from ``repr`` so a stray log
    statement cannot leak a credential.
    """

    database: DatabaseSettings
    key_pepper: str = field(repr=False)
    secret_key: str = field(default="", repr=False)
    admin_email: str = ""
    admin_password_hash: str = field(default="", repr=False)
    admin_role: str = "owner"
    environment: str = "production"
    session_ttl_seconds: int = 8 * 3600
    session_cookie_name: str = "nexora_session"
    # Sessions are scoped to one application surface. The SMS Reader console
    # uses ``sms_reader`` so a license-console cookie cannot be replayed there.
    session_audience: str = "keygen"
    cookie_secure: bool = True
    csrf_header: str = "X-CSRF-Token"
    rate_limit_enabled: bool = True
    trust_proxy: bool = False
    max_bulk_keys: int = 500
    brand_name: str = "NexOra-Database-key-Gen"
    version: str = "1.0.0"
    # ---- NexOra Phase 1: API versioning ----
    api_version: str = "v1"
    # ---- NexOra Phase 1: Supabase integration ----
    # Optional — validated only when set. Never written to logs.
    supabase_url: str = ""
    supabase_service_role_key: str = field(default="", repr=False)
    # ---- NexOra Phase 1: Reader installation limit ----
    # Only one active reader installation is permitted per project.
    max_reader_installations_per_project: int = 1
    # ---- Webhook delivery bounds (SSRF-safe transport) ----
    webhook_timeout_seconds: float = 5.0
    webhook_max_response_bytes: int = 64 * 1024
    webhook_max_request_bytes: int = 64 * 1024
    # ---- Standalone scheduler ----
    scheduler_batch_size: int = 100
    scheduler_interval_seconds: int = 60
    # Run the scheduler loop inside the application process so expiration
    # cleanup needs no external cron. Set SCHEDULER_EMBEDDED=0 only when a
    # dedicated worker is deployed; the advisory lock still serializes them.
    scheduler_embedded: bool = True
    # ---- Project transfer ----
    project_import_max_bytes: int = 8 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @classmethod
    def from_env(cls) -> "Settings":
        environment = (
            _string_env("APP_ENV") or _string_env("FLASK_ENV") or "production"
        ).lower()
        database = DatabaseSettings.from_dsn(
            _string_env("DATABASE_URL"),
            pool_max=_int_env("DB_POOL_MAX", 10, minimum=1, maximum=50),
            connect_timeout=_int_env(
                "DB_CONNECT_TIMEOUT", 10, minimum=1, maximum=60
            ),
        )

        supabase_url = _validate_supabase_url(_string_env("SUPABASE_URL"))
        supabase_key = _validate_supabase_service_role_key(
            _string_env("SUPABASE_SERVICE_ROLE_KEY"), supabase_url
        )

        return cls(
            database=database,
            key_pepper=_validate_pepper(_string_env("KEY_PEPPER")),
            secret_key=_validate_secret_key(_string_env("SECRET_KEY")),
            admin_email=_validate_admin_email(_string_env("ADMIN_EMAIL")),
            admin_password_hash=_validate_admin_password_hash(
                _string_env("ADMIN_PASSWORD_HASH")
            ),
            admin_role=_validate_admin_role(_string_env("ADMIN_ROLE")),
            environment=environment,
            session_ttl_seconds=_int_env(
                "SESSION_TTL_HOURS", 8, minimum=1, maximum=720
            )
            * 3600,
            session_cookie_name=_string_env(
                "SESSION_COOKIE_NAME", "nexora_session"
            )
            or "nexora_session",
            cookie_secure=_bool_env("COOKIE_SECURE", environment == "production"),
            rate_limit_enabled=_bool_env("RATE_LIMIT_ENABLED", True),
            trust_proxy=_bool_env("TRUST_PROXY", False),
            max_bulk_keys=_int_env("MAX_BULK_KEYS", 500, minimum=1, maximum=2000),
            webhook_timeout_seconds=_float_env(
                "WEBHOOK_TIMEOUT_SECONDS", 5.0, minimum=0.5, maximum=30.0
            ),
            webhook_max_response_bytes=_int_env(
                "WEBHOOK_MAX_RESPONSE_BYTES",
                64 * 1024,
                minimum=1024,
                maximum=1024 * 1024,
            ),
            webhook_max_request_bytes=_int_env(
                "WEBHOOK_MAX_REQUEST_BYTES",
                64 * 1024,
                minimum=1024,
                maximum=1024 * 1024,
            ),
            scheduler_batch_size=_int_env(
                "SCHEDULER_BATCH_SIZE", 100, minimum=1, maximum=1000
            ),
            scheduler_interval_seconds=_int_env(
                "SCHEDULER_INTERVAL_SECONDS", 60, minimum=5, maximum=86400
            ),
            scheduler_embedded=_bool_env("SCHEDULER_EMBEDDED", True),
            project_import_max_bytes=_int_env(
                "PROJECT_IMPORT_MAX_BYTES",
                8 * 1024 * 1024,
                minimum=64 * 1024,
                maximum=32 * 1024 * 1024,
            ),
            # NexOra Phase 1: Supabase credentials (validated together)
            supabase_url=supabase_url,
            supabase_service_role_key=supabase_key,
        )
