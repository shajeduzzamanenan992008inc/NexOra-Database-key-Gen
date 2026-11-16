"""Administrative CLI.

Examples:
    python -m backend.cli hash-password --email admin@example.com
    python -m backend.cli bootstrap-admin
    python -m backend.cli migrate
    python -m backend.cli migration-status
    python -m backend.cli set-password --email admin@example.com
    python -m backend.cli expire
    python -m backend.cli list-admins
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import ConfigError, Settings
from .db import Database, DatabaseUnavailable
from .errors import ApiError
from .migrations import apply_migrations, inspect_migrations
from .security.passwords import hash_password, password_problems
from .services.admin_bootstrap import ensure_initial_admin
from .services.auth_service import AuthService
from .services.license_service import LicenseService
from .services.project_service import ProjectService

ROOT = Path(__file__).resolve().parent.parent


def _settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings.from_env()


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise SystemExit("No password received on stdin.")
        return password
    if not sys.stdin.isatty():
        raise SystemExit(
            "Refusing to read a password from a non-interactive terminal. "
            "Use --password-stdin."
        )
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    return first


def cmd_hash_password(args: argparse.Namespace) -> int:
    """Print an Argon2id hash suitable for ADMIN_PASSWORD_HASH."""
    password = _read_password(args)
    problems = password_problems(password, args.email or "")
    if problems:
        print("The password does not meet the administrator policy:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    # The hash is not a credential by itself, but print nothing else so the
    # value can be redirected straight into a secret manager or .env file.
    print(hash_password(password))
    return 0


def cmd_bootstrap_admin(_args: argparse.Namespace) -> int:
    settings = _settings()
    db = Database(settings.database)
    try:
        result = ensure_initial_admin(db, settings)
        if result["created"]:
            print(
                f"Created initial {result['role']} account: "
                f"{result['email']} ({result['id']})"
            )
        else:
            print(
                f"Administrator already exists: {result['email']} "
                f"({result['role']}); password left unchanged."
            )
        return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except DatabaseUnavailable as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()


def cmd_create_admin(args: argparse.Namespace) -> int:
    settings = _settings()
    db = Database(settings.database)
    try:
        service = AuthService(db, settings)
        password = _read_password(args)
        admin = service.create_admin(
            email=args.email,
            password=password,
            full_name=args.name,
            role=args.role,
        )
        print(f"Created {admin['role']} account: {admin['email']} ({admin['id']})")
        return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.details:
            print(exc.details, file=sys.stderr)
        return 1
    except DatabaseUnavailable as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()


def cmd_set_password(args: argparse.Namespace) -> int:
    """Explicit password change. Never called automatically at start-up."""
    settings = _settings()
    db = Database(settings.database)
    try:
        password = _read_password(args)
        admin = AuthService(db, settings).change_password(
            email=args.email,
            password=password,
        )
        print(
            f"Password updated for {admin['email']}. "
            "All active sessions for the account were revoked."
        )
        return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.details:
            print(exc.details, file=sys.stderr)
        return 1
    except DatabaseUnavailable as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()


def cmd_migrate(_args: argparse.Namespace) -> int:
    settings = _settings()
    db = Database(settings.database)
    try:
        applied = apply_migrations(db, ROOT / "db" / "migrations")
        print(
            "Applied: " + ", ".join(applied) if applied else "Database is up to date."
        )
        return 0
    except DatabaseUnavailable as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 3
    finally:
        db.close()


def cmd_migration_status(_args: argparse.Namespace) -> int:
    """Read-only migration and Migration 007 schema readiness check."""
    settings = _settings()
    db = Database(settings.database)
    try:
        readiness = inspect_migrations(db, ROOT / "db" / "migrations")
        print(
            "Target: "
            f"{settings.database.host}:{settings.database.port}/"
            f"{settings.database.database} "
            f"sslmode={settings.database.sslmode}"
        )
        applied = [row["filename"] for row in readiness["applied"]]
        print("Applied: " + (", ".join(applied) if applied else "none"))
        pending = readiness["pending"]
        print("Pending: " + (", ".join(pending) if pending else "none"))
        if readiness["checksum_mismatches"]:
            print("Checksum mismatches:", file=sys.stderr)
            for item in readiness["checksum_mismatches"]:
                print(
                    f"  - {item['filename']}: local={item['local'][:12]} "
                    f"database={item['database'][:12]}",
                    file=sys.stderr,
                )
        migration = readiness["migration_007"]
        print(
            "007:"
            f" present={migration['present']}"
            f" recorded={migration['applied']}"
            f" checksum={str(migration['checksum'])[:12]}"
        )
        for name, present in readiness["objects"].items():
            print(f"  {name}: {'present' if present else 'MISSING'}")
        for issue in readiness.get("verification_errors", []):
            print(f"Verification unavailable: {issue}", file=sys.stderr)
        for check in readiness.get("checks", []):
            if not check["ok"]:
                detail = f": {check['detail']}" if check["detail"] else ""
                print(f"Check failed: {check['name']}{detail}", file=sys.stderr)
        print("READY" if readiness["ready"] else "NOT READY")
        return 0 if readiness["ready"] else 1
    except DatabaseUnavailable:
        print(
            "Database error: unable to connect or query the configured target; "
            "no credentials were displayed.",
            file=sys.stderr,
        )
        return 3
    finally:
        db.close()


def cmd_purge_projects(args: argparse.Namespace) -> int:
    """Permanently delete projects whose configured auto-delete date is due.

    Run this from the platform scheduler (cron, systemd timer, Windows Task
    Scheduler, or a scheduled job) after the administrator has configured a
    retention period. It never deletes a project without an explicit
    auto_delete_at date.
    """
    settings = _settings()
    db = Database(settings.database)
    try:
        result = ProjectService(db, settings).purge_due_projects(limit=args.limit)
        if result["deleted_count"]:
            print(
                f"Permanently deleted {result['deleted_count']} due project(s):"
            )
            for name in result["project_names"]:
                print(f"  - {name}")
        else:
            print("No projects are due for automatic deletion.")
        return 0
    finally:
        db.close()


def cmd_expire(_args: argparse.Namespace) -> int:
    settings = _settings()
    db = Database(settings.database)
    try:
        expired = LicenseService(db, settings).expire_overdue()
        # Sessions are rejected once expired, but the rows would otherwise
        # accumulate forever; keep a day of history for support questions.
        pruned = int(
            db.fetch_value(
                """
                WITH gone AS (
                    DELETE FROM admin_sessions
                     WHERE expires_at < now() - interval '1 day'
                        OR revoked_at < now() - interval '1 day'
                    RETURNING 1
                )
                SELECT count(*) FROM gone
                """
            )
            or 0
        )
        print(f"Marked {expired} key(s) as expired.")
        print(f"Pruned {pruned} expired or revoked session(s).")
        return 0
    finally:
        db.close()


def cmd_list_admins(_args: argparse.Namespace) -> int:
    settings = _settings()
    db = Database(settings.database)
    try:
        rows = db.fetch_all(
            """
            SELECT id, email, full_name, role, is_active, last_login_at
              FROM admin_users ORDER BY created_at
            """
        )
        for row in rows:
            state = "active" if row["is_active"] else "disabled"
            print(f"{row['id']}  {row['email']:<40} {row['role']:<8} {state}")
        if not rows:
            print("No administrator accounts.")
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli",
        description="NexOra Database Key Generator administration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    hash_cmd = sub.add_parser(
        "hash-password",
        help="Hash a password for ADMIN_PASSWORD_HASH (never prints the password).",
    )
    hash_cmd.add_argument(
        "--email",
        default="",
        help="Email of the administrator, used for policy checks.",
    )
    hash_cmd.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin.",
    )
    hash_cmd.set_defaults(func=cmd_hash_password)

    bootstrap = sub.add_parser(
        "bootstrap-admin",
        help="Create the initial administrator from ADMIN_EMAIL/ADMIN_PASSWORD_HASH.",
    )
    bootstrap.set_defaults(func=cmd_bootstrap_admin)

    create = sub.add_parser("create-admin", help="Create an administrator account.")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="")
    create.add_argument(
        "--role", choices=("owner", "admin", "viewer"), default="admin"
    )
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin.",
    )
    create.set_defaults(func=cmd_create_admin)

    set_password = sub.add_parser(
        "set-password",
        help="Explicitly change an existing administrator password.",
    )
    set_password.add_argument("--email", required=True)
    set_password.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of stdin.",
    )
    set_password.set_defaults(func=cmd_set_password)

    migrate = sub.add_parser("migrate", help="Apply database migrations.")
    migrate.set_defaults(func=cmd_migrate)

    migration_status = sub.add_parser(
        "migration-status",
        help="Read-only migration and Migration 007 schema readiness check.",
    )
    migration_status.set_defaults(func=cmd_migration_status)

    expire = sub.add_parser("expire", help="Mark overdue keys as expired.")
    expire.set_defaults(func=cmd_expire)

    purge_projects = sub.add_parser(
        "purge-expired-projects",
        help="Permanently delete projects whose auto-delete date is due.",
    )
    purge_projects.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum projects to process in one run (default 100).",
    )
    purge_projects.set_defaults(func=cmd_purge_projects)

    listing = sub.add_parser("list-admins", help="List administrator accounts.")
    listing.set_defaults(func=cmd_list_admins)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
