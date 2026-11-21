"""Local development server for the SMS Reader console (default port 5100).

Same interface as ``run.py``. Every status line comes from a real check.
This console is isolated from the main NexOra app, so it does NOT inherit the
generic HOST / PORT variables: a shared .env with PORT=5000 used to make both
servers fight over one port, and HOST=0.0.0.0 would silently expose the
console to the network.

Usage:
    python run_sms.py [--host HOST] [--port PORT] [--debug] [--no-reload] [--no-anim]

Environment variables:
    SMS_HOST / SMS_PORT      bind address (default 127.0.0.1:5100)
    SMS_DEBUG=1              enable the Flask debugger
    NEXORA_RELOADER=0        disable the Werkzeug reloader when debugging
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
from typing import Optional, Sequence

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
TITLE = "SMS Reader console"
TAGLINE = ("isolated service", "local development")

_app = None


def _build_app():
    """Create the SMS console app once (shared by main() and ``run_sms.app``)."""
    global _app
    if _app is None:
        from sms_reader import create_sms_app

        _app = create_sms_app()
    return _app


def __getattr__(name: str):
    # Keeps ``flask --app run_sms`` and ``from run_sms import app`` working
    # without building the app at import time.
    if name == "app":
        return _build_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NexOra SMS Reader console - development server (use wsgi_sms.py in production).",
        epilog="Environment variables:" + __doc__.split("Environment variables:")[1].rstrip() if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", help="bind address (default: $SMS_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="bind port (default: $SMS_PORT or 5100)")
    parser.add_argument("--debug", action="store_true", help="enable the Flask debugger")
    parser.add_argument("--no-reload", action="store_true", help="disable the reloader while debugging")
    parser.add_argument("--no-anim", action="store_true", help="disable animation")
    return parser.parse_args(argv)


def _describe(app) -> str:
    """Real facts about the built app, or a plain 'ready' if it is not a Flask app."""
    try:
        routes = sum(1 for rule in app.url_map.iter_rules() if rule.endpoint != "static")
        blueprints = len(app.blueprints)
    except AttributeError:
        return "ready"
    return f"{routes} route{'' if routes == 1 else 's'}, {blueprints} blueprint{'' if blueprints == 1 else 's'}"


def _run(ui: Console, args: argparse.Namespace, *, debug: bool, use_reloader: bool, quiet_child: bool) -> int:
    if load_dotenv is None:
        ui.fatal("Dependencies", "python-dotenv is not installed", hint="pip install python-dotenv")
        return 1

    host = args.host or os.getenv("SMS_HOST") or "127.0.0.1"
    try:
        port = parse_port(args.port if args.port is not None else os.getenv("SMS_PORT", "5100"))
    except ValueError as exc:
        ui.fatal("Configuration", str(exc), hint="set SMS_PORT (or pass --port) to a number from 1 to 65535")
        return 1
    sep = ui.g["sep"]

    # The logo reveal plays while the platform modules import, so the two overlap.
    banner = ui.banner(TITLE, TAGLINE, background=True)
    import_error: Optional[ImportError] = None
    started = time.perf_counter()
    try:
        import sms_reader  # noqa: F401
    except ImportError as exc:
        import_error = exc
    import_ms = (time.perf_counter() - started) * 1000
    ui.wait(banner)

    ui.section("System check")
    ui.status("ok", "Runtime", ui.join(f"Python {platform.python_version()}", f"{platform.system()} {platform.release()}"))
    if import_error is not None:
        ui.fatal("Modules", f"{type(import_error).__name__}: {import_error}", hint=import_hint(import_error, ROOT))
        return 1
    ui.status("ok", "Modules", f"sms_reader loaded {sep} {import_ms:.0f} ms")

    with ui.task("Application", "building SMS console") as task:
        try:
            app = _build_app()
        except Exception as exc:
            task.fail(f"{type(exc).__name__}: {exc}")
            if debug or env_flag("NEXORA_TRACEBACK", False):
                ui.show_traceback()
            else:
                ui.hint("re-run with --debug (or NEXORA_TRACEBACK=1) for the full traceback")
            return 1
        task.ok(_describe(app), timing=True)

    db = getattr(app, "extensions", {}).get("db")
    if not quiet_child and db is not None and hasattr(db, "ping"):
        with ui.task("Database", "connecting") as task:
            started = time.perf_counter()
            try:
                connected = bool(db.ping())
            except Exception:
                connected = False
            elapsed_ms = (time.perf_counter() - started) * 1000
            if connected:
                task.ok(f"connected {sep} {elapsed_ms:.0f} ms")
            else:
                task.warn("unreachable - check the database settings of the SMS console")

    url = format_url(host, port, "/")
    bind = f"{format_host(host)}:{port}"
    rows = [
        ("Console", url, "cyan"),
        ("Bind", bind if use_reloader else ui.join(bind, f"pid {os.getpid()}")),
        ("Mode", ui.join("development", f"debug {'on' if debug else 'off'}", f"reloader {'on' if use_reloader else 'off'}")),
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
        open_browser=False,
        window_title=f"NexOra {sep} {TITLE} {sep} :{port}",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.no_anim:
        os.environ["NEXORA_ANIMATE"] = "0"

    debug = args.debug or env_flag("SMS_DEBUG", False)
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
