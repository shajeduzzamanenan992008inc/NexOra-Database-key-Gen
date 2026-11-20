"""NexOra development launcher.

Every status line printed by the preflight is derived from a real
configuration value or database query - nothing is simulated. Production
deployments must use a WSGI server (see ``wsgi.py``); this module is the
developer entry point only.

Environment variables:
    HOST / PORT              bind address (default 127.0.0.1:5000)
    NEXORA_DEBUG=1           enable the Flask debugger
    NEXORA_RELOADER=0        disable the Werkzeug reloader when debugging
    NEXORA_OPEN_BROWSER=0    do not open the local login page automatically
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

from dotenv import load_dotenv

load_dotenv()

from backend.app import create_app  # noqa: E402
from backend.config import ConfigError, Settings  # noqa: E402

BANNER = r"""
  ╔══════════════════════════════════════════════════════════════╗
  ║                                                              ║
  ║    ╭──────────────────────────────╮                           ║
  ║    │  ◈  ◈  ◈  ◈  ◈  ◈  ◈  ◈  │  NEXORA                    ║
  ║    │  ◈  ◈  ◈  ◈  ◈  ◈  ◈  ◈  │  OMEGA                      ║
  ║    │  ◈  ◈  ◈  ◈  ◈  ◈  ◈  ◈  │  COMMAND CENTER             ║
  ║    ╰──────────────────────────────╯                           ║
  ║                                                              ║
  ╚══════════════════════════════════════════════════════════════╝
"""

FOUNDER_REVEAL = r"""
  ┌──────────────────────────────────────────────────────────────┐
  │                                                              │
  │              ◈  F O U N D E R  ◈                           │
  │              ◈  C R E A T O R  ◈                           │
  │              ◈  B U I L D E R  ◈                           │
  │                                                              │
  │              ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─                         │
  │                                                              │
  │              S H A J E D U Z Z A M A N                      │
  │                    E N A N                                  │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
"""

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
AMBER = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
FOUNDER_CYAN = "\033[38;5;51m"
FOUNDER_VIOLET = "\033[38;5;129m"
FOUNDER_GOLD = "\033[38;5;214m"
FOUNDER_WHITE = "\033[38;5;231m"


def _supports_color() -> bool:
    if os.getenv("NO_COLOR") or os.getenv("NEXORA_NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Best effort: enable VT processing on Windows 10+ consoles.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


COLOR = _supports_color()


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if COLOR else text


def _status(tag: str, label: str, detail: str, color: str) -> None:
    badge = _paint(f"[{tag}]", color)
    print(f"  {badge} {label:<15} {_paint(detail, DIM if COLOR else '')}", flush=True)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _probe_host(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _preflight(app, settings: Settings) -> bool:
    """Print honest startup checks. Returns False when the app cannot serve."""

    print(BANNER, flush=True)
    _status(
        " OK ",
        "Configuration",
        f"{'production' if settings.is_production else 'development'} · v{settings.version}",
        GREEN,
    )

    db = app.extensions.get("db")
    started = time.perf_counter()
    try:
        connected = bool(db and db.ping())
    except Exception:
        connected = False
    elapsed_ms = (time.perf_counter() - started) * 1000

    if connected:
        _status(" OK ", "Database", f"connected · {elapsed_ms:.0f} ms", GREEN)
    else:
        _status(
            "FAIL",
            "Database",
            "unreachable - check DATABASE_URL and that PostgreSQL is running",
            RED,
        )

    if connected:
        try:
            applied = db.fetch_value("SELECT count(*) FROM schema_migrations")
            applied = int(applied) if applied is not None else 0
            _status(" OK ", "Schema", f"{applied} migrations applied", GREEN)
        except Exception:
            _status(
                "WARN",
                "Schema",
                "schema_migrations missing - run: python -m backend.cli db upgrade",
                AMBER,
            )
        try:
            admins = db.fetch_value("SELECT count(*) FROM admin_users")
            admins = int(admins) if admins is not None else 0
            if admins:
                _status(" OK ", "Administrator", f"{admins} account(s) provisioned", GREEN)
            else:
                _status(
                    "WARN",
                    "Administrator",
                    "no administrator account - set ADMIN_EMAIL and ADMIN_PASSWORD_HASH",
                    AMBER,
                )
        except Exception:
            _status("WARN", "Administrator", "could not verify admin_users", AMBER)

    rate = "rate limiting on" if settings.rate_limit_enabled else "rate limiting off"
    cookies = "secure cookies on" if settings.cookie_secure else "secure cookies off (dev only)"
    _status(" OK ", "Security", f"{rate} · {cookies}", GREEN)

    if not connected:
        print(
            "  "
            + _paint(
                "Startup halted: the database connection is required before serving.",
                RED,
            ),
            flush=True,
        )
        return False

    return True


def _reveal_founder() -> None:
    """Print the cinematic founder identity reveal to the terminal."""
    if not COLOR:
        print(FOUNDER_REVEAL.replace("◈", "*"), flush=True)
        return
    lines = FOUNDER_REVEAL.splitlines()
    for line in lines:
        if "F O U N D E R" in line:
            print(_paint(line, FOUNDER_CYAN), flush=True)
        elif "C R E A T O R" in line:
            print(_paint(line, FOUNDER_VIOLET), flush=True)
        elif "B U I L D E R" in line:
            print(_paint(line, FOUNDER_GOLD), flush=True)
        elif "S H A J E D U Z Z A M A N" in line:
            print(_paint(line, FOUNDER_WHITE), flush=True)
        elif "E N A N" in line:
            print(_paint(line, FOUNDER_WHITE), flush=True)
        else:
            print(_paint(line, DIM), flush=True)
        time.sleep(0.18)


def _announce_ready(host: str, port: int, url: str, *, open_browser: bool) -> None:
    probe = _probe_host(host)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if _port_open(probe, port):
            break
        time.sleep(0.15)
    else:
        _status("WARN", "Server", f"did not accept connections on {probe}:{port}", AMBER)
        return

    _status(" OK ", "Server", f"running at {url}", GREEN)
    _reveal_founder()
    print(_paint(
        "  NexOra OMEGA ready — open console at: " + url,
        CYAN,
    ), flush=True)
    if open_browser:
        try:
            webbrowser.open(url, new=0, autoraise=True)
        except Exception:
            _status("WARN", "Browser", "could not launch the default browser", AMBER)


def main() -> int:
    host = os.getenv("HOST", "127.0.0.1")
    try:
        port = int(os.getenv("PORT", "5000"))
    except ValueError:
        print(_paint("PORT must be an integer.", RED), flush=True)
        return 1

    debug = _env_flag("NEXORA_DEBUG", False) or _env_flag("FLASK_DEBUG", False)
    use_reloader = debug and _env_flag("NEXORA_RELOADER", True)
    reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    quiet_child = use_reloader and reloader_child

    try:
        settings = Settings.from_env()
        app = create_app(settings)
    except ConfigError as exc:
        print(_paint(f"[FAIL] Configuration: {exc}", RED), flush=True)
        return 1
    except Exception as exc:  # database/bootstrap failures
        print(_paint(f"[FAIL] Startup: {type(exc).__name__}: {exc}", RED), flush=True)
        return 1

    url = f"http://{_probe_host(host)}:{port}/login"
    open_browser = (
        _env_flag("NEXORA_OPEN_BROWSER", True)
        and not settings.is_production
        and not quiet_child
    )

    if not quiet_child:
        if not _preflight(app, settings):
            return 1
        if _port_open(_probe_host(host), port):
            _status(
                "WARN",
                "Server",
                f"port {port} is already in use - browser will not be opened",
                AMBER,
            )
            open_browser = False
        else:
            _status(" OK ", "Server", f"starting on {url}", CYAN)
            threading.Thread(
                target=_announce_ready,
                args=(host, port, url),
                kwargs={"open_browser": open_browser},
                daemon=True,
                name="nexora-ready",
            ).start()

    try:
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=use_reloader,
            threaded=True,
        )
    except KeyboardInterrupt:
        print()
        print(_paint("  ┌──────────────────────────────────────────────────────────────┐", DIM), flush=True)
        print(_paint("  │                                                              │", DIM), flush=True)
        print(_paint("  │         ◈  S E C U R I N G   S E S S I O N  ◈              │", CYAN), flush=True)
        print(_paint("  │                                                              │", DIM), flush=True)
        print(_paint("  └──────────────────────────────────────────────────────────────┘", DIM), flush=True)
        time.sleep(0.25)
        print(_paint("  ┌──────────────────────────────────────────────────────────────┐", DIM), flush=True)
        print(_paint("  │                                                              │", DIM), flush=True)
        print(_paint("  │              S H A J E D U Z Z A M A N                      │", FOUNDER_WHITE), flush=True)
        print(_paint("  │                    E N A N                                  │", FOUNDER_WHITE), flush=True)
        print(_paint("  │              Founder · Creator · Builder                     │", FOUNDER_CYAN), flush=True)
        print(_paint("  │                                                              │", DIM), flush=True)
        print(_paint("  └──────────────────────────────────────────────────────────────┘", DIM), flush=True)
        time.sleep(0.3)
        print(_paint("\n  NexOra OMEGA shut down gracefully. Until next time.", DIM), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
