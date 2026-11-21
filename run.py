"""NexOra development launcher.

Every status line printed by the preflight is derived from a real
configuration value or database query - nothing is simulated. Animation is
cosmetic only: a spinner runs while a real check runs, and every timing shown
is measured. Production deployments must use a WSGI server (see ``wsgi.py``);
this module is the developer entry point only.

Usage:
    python run.py [--host HOST] [--port PORT] [--debug] [--no-reload]
                  [--no-browser] [--no-anim]

Environment variables:
    HOST / PORT              bind address (default 127.0.0.1:5000)
    NEXORA_DEBUG=1           enable the Flask debugger (blocked in production)
    NEXORA_RELOADER=0        disable the Werkzeug reloader when debugging
    NEXORA_OPEN_BROWSER=0    do not open the local login page automatically
    NEXORA_ANIMATE=0         disable animation (also off in CI and pipes)
    NEXORA_ASCII=1           force plain ASCII output
    NEXORA_ART=0             hide the wolf mascot in the banner
    NEXORA_TRACEBACK=1       print the full traceback when startup fails
    NEXORA_LOG_QUERY=1       show query strings in the live request log
    NO_COLOR=1               disable colours
"""

from __future__ import annotations

import argparse
import os
import platform
import time
from typing import Optional, Sequence, Tuple

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # reported by main() once the console is ready
    load_dotenv = None  # type: ignore[assignment]
else:
    if not load_dotenv():  # .env next to this file first, then the current folder
        load_dotenv(find_dotenv(usecwd=True))

from nexora_ui import (  # noqa: E402
    Console,
    env_flag,
    format_host,
    format_url,
    import_hint,
    parse_port,
    serve,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
TITLE = "Database key generator"
TAGLINE = ("secure key generation", "license control", "audit trail")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NexOra development launcher (use wsgi.py in production).",
        epilog="Environment variables:" + __doc__.split("Environment variables:")[1].rstrip() if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", help="bind address (default: $HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="bind port (default: $PORT or 5000)")
    parser.add_argument("--debug", action="store_true", help="enable the Flask debugger")
    parser.add_argument("--no-reload", action="store_true", help="disable the reloader while debugging")
    parser.add_argument("--no-browser", action="store_true", help="do not open the login page")
    parser.add_argument("--no-anim", action="store_true", help="disable animation")
    return parser.parse_args(argv)


def _preflight(ui: Console, app, settings) -> Tuple[bool, int]:
    """Real startup checks. Returns (can_serve, warning_count)."""
    sep = ui.g["sep"]
    db = app.extensions.get("db")
    warnings = 0

    with ui.task("Database", "connecting") as task:
        started = time.perf_counter()
        try:
            connected = bool(db and db.ping())
        except Exception:
            connected = False
        elapsed_ms = (time.perf_counter() - started) * 1000
        if connected:
            task.ok(f"connected {sep} {elapsed_ms:.0f} ms")
        else:
            task.fail("unreachable - check DATABASE_URL and that PostgreSQL is running")

    if connected:
        with ui.task("Schema", "checking migrations") as task:
            try:
                applied = db.fetch_value("SELECT count(*) FROM schema_migrations")
                applied = int(applied) if applied is not None else 0
                task.ok(f"{applied} migrations applied")
            except Exception:
                task.warn("schema_migrations missing - run: python -m backend.cli db upgrade")
            warnings += 1 if task.kind == "warn" else 0

        with ui.task("Administrator", "checking accounts") as task:
            try:
                admins = db.fetch_value("SELECT count(*) FROM admin_users")
                admins = int(admins) if admins is not None else 0
                if admins:
                    task.ok(f"{admins} account(s) provisioned")
                else:
                    task.warn("no administrator account - set ADMIN_EMAIL and ADMIN_PASSWORD_HASH")
            except Exception:
                task.warn("could not verify admin_users")
            warnings += 1 if task.kind == "warn" else 0

    rate = "rate limiting on" if settings.rate_limit_enabled else "rate limiting off"
    cookies = "secure cookies on" if settings.cookie_secure else "secure cookies off"
    if settings.is_production and not (settings.rate_limit_enabled and settings.cookie_secure):
        ui.status("warn", "Security", f"{rate} {sep} {cookies} - review before going live")
        warnings += 1
    elif not settings.cookie_secure:
        ui.status("ok", "Security", f"{rate} {sep} {cookies} (dev only)")
    else:
        ui.status("ok", "Security", f"{rate} {sep} {cookies}")

    if not connected:
        ui.line()
        ui.fatal(
            "Startup",
            "halted - the database connection is required before serving",
            hint="start PostgreSQL and check DATABASE_URL, then run this launcher again",
        )
        return False, warnings
    return True, warnings


def _run(ui: Console, args: argparse.Namespace, *, debug: bool, use_reloader: bool, quiet_child: bool) -> int:
    if load_dotenv is None:
        ui.fatal("Dependencies", "python-dotenv is not installed", hint="pip install python-dotenv")
        return 1

    host = args.host or os.getenv("HOST", "127.0.0.1")
    try:
        port = parse_port(args.port if args.port is not None else os.getenv("PORT", "5000"))
    except ValueError as exc:
        ui.fatal("Configuration", str(exc), hint="set PORT (or pass --port) to a number from 1 to 65535")
        return 1
    sep = ui.g["sep"]

    # The logo reveal plays while the platform modules import, so the two overlap.
    banner = ui.banner(TITLE, TAGLINE, background=True)
    import_error: Optional[ImportError] = None
    started = time.perf_counter()
    try:
        from backend.app import create_app
        from backend.config import ConfigError, Settings
    except ImportError as exc:
        import_error = exc
    import_ms = (time.perf_counter() - started) * 1000
    ui.wait(banner)

    ui.section("System check")
    ui.status("ok", "Runtime", ui.join(f"Python {platform.python_version()}", f"{platform.system()} {platform.release()}"))
    if import_error is not None:
        ui.fatal("Modules", f"{type(import_error).__name__}: {import_error}", hint=import_hint(import_error, ROOT))
        return 1
    ui.status("ok", "Modules", f"backend loaded {sep} {import_ms:.0f} ms")

    with ui.task("Configuration", "reading environment") as task:
        try:
            settings = Settings.from_env()
        except ConfigError as exc:
            task.fail(str(exc))
            ui.hint("check the values in your .env file")
            return 1
        mode = "production" if settings.is_production else "development"
        task.ok(ui.join(mode, f"v{settings.version}"))

    if debug and settings.is_production:
        ui.fatal(
            "Security",
            "debug mode is blocked in production (remote code execution risk)",
            hint="unset NEXORA_DEBUG / FLASK_DEBUG, or switch to a development configuration",
        )
        return 1

    with ui.task("Application", "building application") as task:
        try:
            app = create_app(settings)
        except Exception as exc:  # database/bootstrap failures
            task.fail(f"{type(exc).__name__}: {exc}")
            if debug or env_flag("NEXORA_TRACEBACK", False):
                ui.show_traceback()
            else:
                ui.hint("re-run with --debug (or NEXORA_TRACEBACK=1) for the full traceback")
            return 1
        task.ok("application factory ready", timing=True)

    warnings = 0
    if not quiet_child:
        can_serve, warnings = _preflight(ui, app, settings)
        if not can_serve:
            return 1

    url = format_url(host, port, "/login")
    open_browser = (
        env_flag("NEXORA_OPEN_BROWSER", True)
        and not args.no_browser
        and not settings.is_production
        and not quiet_child
    )
    bind = f"{format_host(host)}:{port}"
    rows = [
        ("Console", url, "cyan"),
        ("Bind", bind if use_reloader else ui.join(bind, f"pid {os.getpid()}")),
        ("Mode", ui.join(mode, f"debug {'on' if debug else 'off'}", f"reloader {'on' if use_reloader else 'off'}")),
        ("Runtime", ui.join(f"Python {platform.python_version()}", platform.system())),
    ]
    return serve(
        ui,
        app,
        host=host,
        port=port,
        debug=debug,
        reloader=use_reloader,
        url=url,
        rows=rows,
        quiet_child=quiet_child,
        open_browser=open_browser,
        window_title=f"NexOra {sep} {TITLE} {sep} :{port}",
        warnings=warnings,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.no_anim:
        os.environ["NEXORA_ANIMATE"] = "0"

    debug = args.debug or env_flag("NEXORA_DEBUG", False) or env_flag("FLASK_DEBUG", False)
    use_reloader = debug and not args.no_reload and env_flag("NEXORA_RELOADER", True)
    reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    quiet_child = use_reloader and reloader_child

    ui = Console(quiet=quiet_child)
    try:
        return _run(ui, args, debug=debug, use_reloader=use_reloader, quiet_child=quiet_child)
    except KeyboardInterrupt:
        ui.stop()
        ui.line()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
