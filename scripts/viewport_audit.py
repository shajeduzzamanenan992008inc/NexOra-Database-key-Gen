"""Real-browser responsive audit for the NexOra admin frontend.

Serves the real frontend files with the production Content-Security-Policy and
stubs the authenticated JSON API so the console can render deterministically.
It then drives Chrome (Playwright channel="chrome") at every requested viewport
and checks:

* no page-level or element-level horizontal overflow in every view,
* the one-time Reader Key is fully visible and never truncated,
* the reveal modal fits the viewport and purges plaintext when closed,
* clipboard feedback works,
* the mobile navigation drawer opens/closes without overflow,
* login -> dashboard works without console/CSP errors,
* reduced-motion and lite-mode profiles stay interactive.

This is a development audit, not a pytest test: it needs Chrome and Playwright.
Run: python scripts/viewport_audit.py
"""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

WIDTHS = (320, 360, 375, 390, 430, 480, 768, 1024, 1280, 1440, 1920)
HEIGHT = 900
VIEWS = (
    "overview",
    "projects",
    "generator",
    "keys",
    "installations",
    "security",
    "logs",
    "settings",
)
KEY = "NXR-9F4K-2M7Q-XB8T-L3WZ"
ISO = "2026-11-17T09:00:00Z"

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


# --------------------------------------------------------------------- server


class FrontendHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):  # keep the audit output clean
        return

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/login"):
            target = FRONTEND / "index.html"
        elif path == "/dashboard":
            target = FRONTEND / "dashboard.html"
        elif path.startswith("/assets/"):
            target = FRONTEND / path.lstrip("/")
        else:
            target = None

        if target is None or not target.is_file():
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"not found")
            return

        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript"
        elif target.suffix == ".svg":
            content_type = "image/svg+xml"

        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


# ---------------------------------------------------------------------- data


STATS = {
    "total_projects": 12,
    "active_projects": 9,
    "scheduled_projects": 2,
    "due_projects": 1,
    "total_keys": 128,
    "unused_keys": 74,
    "used_keys": 31,
    "expired_keys": 14,
    "revoked_keys": 9,
    "active_installations": 7,
    "database_status": "connected",
    "backend_health": "ok",
    "recent_audit": [
        {
            "action": "reader_key.generate",
            "actor_email": "admin@nexora.io",
            "created_at": ISO,
            "outcome": "success",
            "project_name": "Orion Core",
        },
        {
            "action": "auth.login",
            "actor_email": "admin@nexora.io",
            "created_at": ISO,
            "outcome": "success",
        },
        {
            "action": "reader_installation.revoke",
            "actor_email": "admin@nexora.io",
            "created_at": ISO,
            "outcome": "failure",
            "project_name": "Extremely Long Production Cluster Name That Must Wrap",
        },
    ],
}

PROJECT = {
    "id": "prj_01HZX9ORIONCORE0000000000",
    "project_name": "Extremely Long Production Cluster Name That Must Wrap Without Overflow Alpha",
    "status": "active",
    "reader_key_status": "generated",
    "reader_key_hint": "NXR-****-****-****-9F4K",
    "created_at": ISO,
    "auto_delete_at": "2026-12-31T23:59:59Z",
    "days_remaining": 44,
    "active_installation_id": "ins_01HZX9ORIONCORE000000001",
    "active_device_id": "NexOra-Reader-7c2f9a",
}

PROJECTS = [
    PROJECT,
    {
        "id": "prj_01HZXBETACLUSTER000000000",
        "project_name": "Beta Cluster",
        "status": "active",
        "reader_key_status": "used",
        "reader_key_hint": "NXR-****-****-****-7Q2M",
        "created_at": ISO,
        "auto_delete_at": None,
        "days_remaining": None,
        "active_installation_id": "ins_02",
        "active_device_id": "NexOra-Reader-2f71b0",
    },
    {
        "id": "prj_01HZXDELTALAB00000000000",
        "project_name": "Delta Lab",
        "status": "inactive",
        "reader_key_status": "revoked",
        "reader_key_hint": "NXR-****-****-****-L3WZ",
        "created_at": ISO,
        "auto_delete_at": None,
        "days_remaining": None,
        "active_installation_id": None,
        "active_device_id": None,
    },
]

KEYS = [
    {
        "id": "key_01HZX9ORIONCORE0000000000",
        "key_hint": "NXR-****-****-****-9F4K",
        "project_name": PROJECT["project_name"],
        "label": "Primary reader deployment",
        "status": "generated",
        "is_permanent": False,
        "expires_at": "2027-01-17T09:00:00Z",
        "used_at": None,
        "created_at": ISO,
    },
    {
        "id": "key_02",
        "key_hint": "NXR-****-****-****-7Q2M",
        "project_name": "Beta Cluster",
        "label": None,
        "status": "used",
        "is_permanent": True,
        "expires_at": None,
        "used_at": ISO,
        "created_at": ISO,
    },
    {
        "id": "key_03",
        "key_hint": "NXR-****-****-****-L3WZ",
        "project_name": "Delta Lab",
        "label": "Revoked after migration",
        "status": "revoked",
        "is_permanent": False,
        "expires_at": None,
        "used_at": None,
        "created_at": ISO,
    },
]

INSTALLATIONS = [
    {
        "id": "ins_01HZX9ORIONCORE000000001",
        "project_name": PROJECT["project_name"],
        "key_hint": "NXR-****-****-****-9F4K",
        "device_id": "NexOra-Reader-7c2f9a",
        "status": "active",
        "activated_at": ISO,
        "last_seen_at": ISO,
        "ip_address": "203.0.113.24",
    },
    {
        "id": "ins_02",
        "project_name": "Beta Cluster",
        "key_hint": "NXR-****-****-****-7Q2M",
        "device_id": "NexOra-Reader-2f71b0",
        "status": "revoked",
        "activated_at": ISO,
        "last_seen_at": ISO,
        "ip_address": "198.51.100.9",
    },
]

AUDIT = [
    {
        "action": "reader_key.generate",
        "created_at": ISO,
        "target_type": "reader_key",
        "target_id": "key_01HZX9ORIONCORE0000000000",
        "outcome": "success",
        "actor_email": "admin@nexora.io",
        "ip_address": "203.0.113.24",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "details": {"project_name": PROJECT["project_name"]},
    },
    {
        "action": "auth.login",
        "created_at": ISO,
        "target_type": "admin",
        "target_id": "adm_01",
        "outcome": "success",
        "actor_email": "admin@nexora.io",
        "ip_address": "203.0.113.24",
        "details": {},
    },
]

SECURITY = {
    "auth": {
        "admin_email": "admin@nexora.io",
        "admin_role": "owner",
        "failed_login_attempts": 2,
    },
    "session": {"cookie_name": "nexora_session", "session_ttl_seconds": 28800},
    "database": {"status": "connected", "ssl_mode": "require"},
    "recent_events": [
        {
            "created_at": ISO,
            "action": "auth.login",
            "outcome": "success",
            "target_type": "admin",
            "ip_address": "203.0.113.24",
        }
    ],
}

SETTINGS = {
    "key_prefix": "NXR",
    "default_key_expiry_days": 30,
    "max_bulk_keys": 500,
    "heartbeat_interval_seconds": 300,
    "api_rate_limits": "60/min",
    "display_timezone": "UTC",
    "maintenance_mode": False,
    "auto_delete_days": 0,
}


def pagination(page: int = 1, size: int = 25, total: int | None = None) -> dict:
    total = total if total is not None else 0
    return {
        "page": page,
        "page_size": size,
        "total": total,
        "total_pages": max(1, (total + size - 1) // size),
    }


def make_api_handler(authenticated: dict):
    def handler(route):
        request = route.request
        path = urllib.parse.urlparse(request.url).path
        method = request.method

        def send(payload, status=200):
            route.fulfill(
                status=status,
                content_type="application/json",
                headers={"Cache-Control": "no-store"},
                body=json.dumps(payload),
            )

        if path == "/api/v1/auth/session":
            if authenticated["value"]:
                return send(
                    {
                        "user": {
                            "email": "admin@nexora.io",
                            "full_name": "NexOra Operator",
                            "role": "owner",
                        },
                        "csrf_token": "audit-csrf-token",
                    }
                )
            return send(
                {
                    "error": {
                        "message": "Authentication required.",
                        "code": "UNAUTHENTICATED",
                        "details": {},
                    }
                },
                401,
            )

        if path == "/api/v1/auth/login" and method == "POST":
            authenticated["value"] = True
            return send({"csrf_token": "audit-csrf-token"})

        if path == "/api/v1/auth/logout" and method == "POST":
            authenticated["value"] = False
            return send({})

        if path == "/api/v1/dashboard/stats":
            return send(STATS)

        if path == "/api/v1/health":
            return send({"status": "ok"})

        if path == "/api/v1/projects/generate-key" and method == "POST":
            return send(
                {
                    "project": {
                        "id": PROJECT["id"],
                        "project_name": "Orion Core",
                        "status": "active",
                    },
                    "reader_key": {
                        "id": "key_generated",
                        "project_name": "Orion Core",
                        "plaintext_key": KEY,
                        "key_hint": "NXR-****-****-****-L3WZ",
                        "status": "generated",
                        "is_permanent": False,
                        "expires_at": "2027-01-17T09:00:00Z",
                        "created_at": ISO,
                        "label": "Audit deployment",
                    },
                },
                201,
            )

        if path == "/api/v1/projects":
            if method == "DELETE":
                return send({"deleted_count": 12})
            return send({"projects": PROJECTS})

        if path.startswith("/api/v1/projects/"):
            suffix = path.rsplit("/", 1)[-1]
            if suffix == "status" or suffix == "auto-delete":
                return send({"project": PROJECT})
            return send({"project": PROJECT})

        if path == "/api/v1/keys":
            return send({"items": KEYS, "pagination": pagination(total=3)})

        if path.startswith("/api/v1/keys/"):
            return send({"key": KEYS[0]})

        if path == "/api/v1/installations":
            return send({"items": INSTALLATIONS, "pagination": pagination(total=2)})

        if path.startswith("/api/v1/installations/"):
            return send({"installation": INSTALLATIONS[0]})

        if path == "/api/v1/security/status":
            return send(SECURITY)

        if path == "/api/v1/audit-logs":
            return send({"items": AUDIT, "pagination": pagination(total=2)})

        if path == "/api/v1/settings":
            if method == "PATCH":
                return send({"ok": True})
            return send(SETTINGS)

        return send(
            {
                "error": {
                    "message": f"Unhandled audit route: {method} {path}",
                    "code": "NOT_FOUND",
                    "details": {},
                }
            },
            404,
        )

    return handler


# ------------------------------------------------------------------ browser JS


OVERFLOW_JS = """
() => {
  const vw = window.innerWidth;
  const excluded = [
    ".ambient-scene", ".sidebar", ".sidebar-backdrop", ".skip-link",
    ".scroll-progress", ".boot-screen", ".leave-screen", ".toast-stack",
    ".tooltip-pop", ".busy-overlay", "[hidden]"
  ].join(",");
  const docWidth = Math.max(
    document.documentElement.scrollWidth,
    document.body.scrollWidth
  );
  const offenders = [];

  for (const node of document.body.querySelectorAll("*")) {
    if (node.closest(excluded)) continue;
    if (node.closest("[aria-hidden='true']")) continue;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") continue;
    if (style.position === "fixed") continue;

    // Internal scrollers (wide desktop tables, code blocks) are allowed to
    // scroll inside their own box; they must not widen the page.
    let scroller = node.parentElement;
    let insideInternalScroller = false;
    while (scroller && scroller !== document.body) {
      const s = getComputedStyle(scroller);
      if (["auto", "scroll"].includes(s.overflowX) && scroller.getBoundingClientRect().width <= vw + 1) {
        insideInternalScroller = true;
        break;
      }
      scroller = scroller.parentElement;
    }
    if (insideInternalScroller) continue;

    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (rect.right > vw + 1 || rect.left < -1) {
      const classText =
        node.className && node.className.baseVal !== undefined
          ? node.className.baseVal
          : String(node.className || "");
      offenders.push(
        `${node.tagName.toLowerCase()}.${classText.trim().slice(0, 60)} [${Math.round(rect.left)}..${Math.round(rect.right)}]`
      );
      if (offenders.length >= 8) break;
    }
  }

  return {
    vw,
    docWidth,
    docOverflow: docWidth - vw,
    offenders,
  };
}
"""

MODAL_JS = """
() => {
  const modal = document.querySelector("#result-modal");
  const card = modal.querySelector(".modal-card");
  const key = modal.querySelector(".key-plaintext");
  const rect = card.getBoundingClientRect();
  const keyRect = key.getBoundingClientRect();
  const storage = JSON.stringify({
    local: { ...localStorage },
    session: { ...sessionStorage },
  });
  return {
    open: modal.open,
    card: {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
    },
    key: {
      left: keyRect.left,
      right: keyRect.right,
      width: keyRect.width,
      scrollWidth: key.scrollWidth,
      clientWidth: key.clientWidth,
      text: key.textContent.trim(),
    },
    segments: [...modal.querySelectorAll(".key-segment")].map((s) => s.textContent),
    vw: window.innerWidth,
    vh: window.innerHeight,
    docOverflow:
      Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) -
      window.innerWidth,
    storageHasPlaintext: storage.includes("NXR-9F4K-2M7Q-XB8T-L3WZ"),
  };
}
"""


class Audit:
    def __init__(self):
        self.failures: list[str] = []
        self.checks = 0

    def check(self, condition, message):
        self.checks += 1
        if not condition:
            self.failures.append(message)
            print(f"  FAIL  {message}")
        return condition

    def note(self, message):
        print(f"  ok    {message}")


def check_overflow(audit: Audit, page, label: str):
    result = page.evaluate(OVERFLOW_JS)
    ok = audit.check(
        result["docOverflow"] <= 1 and not result["offenders"],
        f"{label}: horizontal overflow {result['docOverflow']}px; offenders={result['offenders']}",
    )
    if ok:
        audit.note(f"{label}: no horizontal overflow")
    return ok


def wait_for_console_clean(page, errors: list[str]):
    def on_console(msg):
        if msg.type != "error":
            return
        url = (msg.location or {}).get("url", "")
        errors.append(f"console[{url}]: {msg.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))


def unexpected_errors(errors: list[str]) -> list[str]:
    """The login page intentionally probes auth/session and receives a 401,
    which Chrome reports as a failed resource. That is the documented
    unauthenticated state, not an application or CSP error."""
    return [
        error
        for error in errors
        if not (
            "/api/v1/auth/session" in error
            and "401" in error
        )
    ]


def force_full_effects(context):
    """Pin the visual/profile contexts to FULL so overflow and key checks are
    measured against the cinematic baseline; the LITE toggle has its own test."""
    context.add_init_script(
        "try { localStorage.setItem('nexora:lite-mode', '0'); } catch (error) {}"
    )


def dashboard_ready(page):
    page.wait_for_selector("#view-overview.is-active", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('[data-stat=\"total_projects\"]')?.textContent.trim() !== '—'",
        timeout=15000,
    )


def run_width(audit: Audit, browser, base: str, width: int):
    print(f"\n[{width}px]")
    context = browser.new_context(
        viewport={"width": width, "height": HEIGHT},
        reduced_motion="no-preference",
    )
    force_full_effects(context)
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
    authenticated = {"value": True}
    context.route("**/api/v1/**", make_api_handler(authenticated))

    page = context.new_page()
    errors: list[str] = []
    wait_for_console_clean(page, errors)

    page.goto(f"{base}/dashboard#overview", wait_until="domcontentloaded")
    dashboard_ready(page)
    page.wait_for_timeout(500)

    if width == 390:
        page.hover("#db-health-chip")
        page.wait_for_timeout(280)
        tooltip = page.evaluate(
            """() => {
              const tip = document.querySelector('#nx-tooltip');
              const rect = tip.getBoundingClientRect();
              return {
                visible: !tip.hidden && tip.classList.contains('is-visible'),
                text: tip.textContent,
                left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
                vw: innerWidth, vh: innerHeight,
              };
            }"""
        )
        audit.check(
            tooltip["visible"] and bool(tooltip["text"]),
            f"390px: tooltip did not appear: {tooltip}",
        )
        audit.check(
            tooltip["left"] >= -1
            and tooltip["right"] <= tooltip["vw"] + 1
            and tooltip["top"] >= -1
            and tooltip["bottom"] <= tooltip["vh"] + 1,
            f"390px: tooltip outside viewport: {tooltip}",
        )
        page.focus("#db-health-chip")
        page.wait_for_timeout(180)
        audit.check(
            "nx-tooltip"
            in (
                page.get_attribute("#db-health-chip", "aria-describedby") or ""
            ),
            "390px: focused tooltip trigger is not described by the tooltip",
        )
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        audit.check(
            page.evaluate("() => document.querySelector('#nx-tooltip').hidden"),
            "390px: Escape did not dismiss the tooltip",
        )
        audit.note("390px: tooltip clamped, focus-linked and Escape-dismissed")

    for view in VIEWS:
        page.evaluate("(view) => { window.location.hash = view; }", view)
        page.wait_for_selector(f"#view-{view}.is-active", timeout=10000)
        page.wait_for_timeout(320)
        check_overflow(audit, page, f"{width}px/{view}")

    # One-time Reader Key reveal at this viewport.
    page.evaluate("() => { window.location.hash = 'generator'; }")
    page.wait_for_selector("#view-generator.is-active")
    page.click("#gen-mode-new")
    page.wait_for_selector("#gen-new-field:not([hidden])")
    page.fill("#gen-project-name-input", "Orion Core")
    page.click("#generate-keys-btn")
    page.wait_for_selector("#result-modal[open]", timeout=15000)
    page.wait_for_selector(".key-plaintext")
    page.wait_for_timeout(350)

    result = page.evaluate(MODAL_JS)
    card = result["card"]
    key = result["key"]
    audit.check(result["open"], f"{width}px: result modal did not open")
    audit.check(
        card["left"] >= -1
        and card["right"] <= result["vw"] + 1
        and card["top"] >= -1
        and card["bottom"] <= result["vh"] + 1,
        f"{width}px: reveal modal outside viewport: {card}",
    )
    audit.check(
        key["left"] >= -1 and key["right"] <= result["vw"] + 1,
        f"{width}px: key outside viewport: {key}",
    )
    audit.check(
        key["scrollWidth"] <= key["clientWidth"] + 1,
        f"{width}px: key clipped horizontally: {key}",
    )
    audit.check(
        key["text"] == KEY,
        f"{width}px: key not fully rendered: {key['text']!r}",
    )
    audit.check(
        result["docOverflow"] <= 1,
        f"{width}px: page overflow with reveal modal open: {result['docOverflow']}px",
    )
    audit.check(
        not result["storageHasPlaintext"],
        f"{width}px: plaintext key leaked into localStorage/sessionStorage",
    )
    audit.note(f"{width}px: key fully visible ({len(result['segments'])} segments)")

    page.click(".key-copy")
    page.wait_for_timeout(450)
    audit.check(
        "is-copied" in page.get_attribute(".key-copy", "class"),
        f"{width}px: copy button did not confirm",
    )
    try:
        clipboard = page.evaluate("() => navigator.clipboard.readText()")
        audit.check(clipboard == KEY, f"{width}px: clipboard mismatch")
    except Exception as exc:  # clipboard is permission-dependent in headless
        audit.note(f"{width}px: clipboard read skipped ({exc.__class__.__name__})")

    page.click('[data-close="result-modal"]')
    # The dialog runs a 175 ms close transition before dispatching `close`.
    # Wait for the purge itself instead of racing a fixed sleep under load.
    purged = True
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#generated-list *').length === 0",
            timeout=5000,
        )
    except Exception:
        purged = False
    audit.check(
        purged,
        f"{width}px: plaintext key not purged after closing the modal",
    )

    # Project detail modal fits.
    page.evaluate("() => { window.location.hash = 'projects'; }")
    page.wait_for_selector("#view-projects.is-active")
    page.wait_for_timeout(350)
    page.click("#projects-table-body .table-link")
    page.wait_for_selector("#project-detail-modal[open]")
    page.wait_for_timeout(250)
    bounds = page.evaluate(
        """() => {
          const card = document.querySelector('#project-detail-modal .modal-card');
          const r = card.getBoundingClientRect();
          return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,vw:innerWidth,vh:innerHeight};
        }"""
    )
    audit.check(
        bounds["left"] >= -1
        and bounds["right"] <= bounds["vw"] + 1
        and bounds["top"] >= -1
        and bounds["bottom"] <= bounds["vh"] + 1,
        f"{width}px: project modal outside viewport: {bounds}",
    )
    page.click('[data-close="project-detail-modal"]')
    page.wait_for_timeout(300)

    # Mobile navigation drawer.
    if width <= 980:
        page.click("#mobile-nav-toggle")
        page.wait_for_timeout(450)
        audit.check(
            page.evaluate("() => document.querySelector('#sidebar').classList.contains('is-open')"),
            f"{width}px: mobile nav did not open",
        )
        check_overflow(audit, page, f"{width}px/mobile-nav-open")
        page.keyboard.press("Escape")
        page.wait_for_timeout(450)
        audit.check(
            page.evaluate(
                "() => !document.querySelector('#sidebar').classList.contains('is-open') && document.querySelector('#mobile-nav-toggle').getAttribute('aria-expanded') === 'false'"
            ),
            f"{width}px: mobile nav did not close with Escape",
        )

    audit.check(
        not unexpected_errors(errors),
        f"{width}px: console/CSP errors: {unexpected_errors(errors)[:5]}",
    )
    context.close()

    # The sign-in page is part of the same responsive contract.
    login_context = browser.new_context(viewport={"width": width, "height": HEIGHT})
    force_full_effects(login_context)
    login_context.route("**/api/v1/**", make_api_handler({"value": False}))
    login_page = login_context.new_page()
    login_errors: list[str] = []
    wait_for_console_clean(login_page, login_errors)
    login_page.goto(base + "/", wait_until="domcontentloaded")
    login_page.wait_for_selector("#login-form")
    login_page.wait_for_timeout(900)
    check_overflow(audit, login_page, f"{width}px/login")
    audit.check(
        not unexpected_errors(login_errors),
        f"{width}px: login console/CSP errors: {unexpected_errors(login_errors)[:5]}",
    )
    login_context.close()


def run_login_and_profiles(audit: Audit, browser, base: str):
    print("\n[login flow]")
    context = browser.new_context(viewport={"width": 375, "height": 812})
    force_full_effects(context)
    authenticated = {"value": False}
    context.route("**/api/v1/**", make_api_handler(authenticated))
    page = context.new_page()
    errors: list[str] = []
    wait_for_console_clean(page, errors)

    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#login-form")
    page.fill("#email", "admin@nexora.io")
    page.fill("#password", "correct horse battery staple")
    page.click("#login-submit")
    page.wait_for_url("**/dashboard", timeout=15000)
    dashboard_ready(page)
    audit.note("login -> dashboard succeeded")
    check_overflow(audit, page, "375px/login-dashboard")
    audit.check(
        not unexpected_errors(errors),
        f"login flow console errors: {unexpected_errors(errors)[:5]}",
    )
    context.close()

    print("\n[reduced motion]")
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    authenticated = {"value": True}
    context.route("**/api/v1/**", make_api_handler(authenticated))
    page = context.new_page()
    errors = []
    wait_for_console_clean(page, errors)
    page.goto(f"{base}/dashboard#generator", wait_until="domcontentloaded")
    page.wait_for_selector("#view-generator.is-active", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#user-name')?.textContent.trim() !== 'Loading…'",
        timeout=15000,
    )
    page.wait_for_timeout(1600)
    audit.check(
        page.evaluate(
            "() => window.matchMedia('(prefers-reduced-motion: reduce)').matches"
        ),
        "reduced-motion emulation was not applied",
    )
    duration = page.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('.aurora')).animationDuration)"
    )
    audit.check(
        duration <= 0.0001,
        f"reduced motion did not suppress ambient animation (duration={duration}s)",
    )
    page.evaluate("() => { window.location.hash = 'generator'; }")
    page.wait_for_selector("#view-generator.is-active")
    page.click("#gen-mode-new")
    page.wait_for_selector("#gen-new-field:not([hidden])")
    page.fill("#gen-project-name-input", "Reduced Motion Project")
    page.click("#generate-keys-btn")
    page.wait_for_selector("#result-modal[open]", timeout=15000)
    page.wait_for_selector(".key-plaintext")
    reduced_modal = page.evaluate(MODAL_JS)
    audit.check(
        reduced_modal["key"]["text"] == KEY,
        "reduced motion: key not fully rendered",
    )
    audit.check(
        reduced_modal["docOverflow"] <= 1,
        f"reduced motion: overflow {reduced_modal['docOverflow']}px",
    )
    page.click('[data-close="result-modal"]')
    page.wait_for_timeout(300)
    audit.check(
        not unexpected_errors(errors),
        f"reduced-motion console errors: {unexpected_errors(errors)[:5]}",
    )
    context.close()

    print("\n[lite mode toggle]")
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    force_full_effects(context)
    authenticated = {"value": True}
    context.route("**/api/v1/**", make_api_handler(authenticated))
    page = context.new_page()
    page.goto(f"{base}/dashboard#settings", wait_until="domcontentloaded")
    page.wait_for_selector("#view-settings.is-active", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#user-name')?.textContent.trim() !== 'Loading…'",
        timeout=15000,
    )
    page.wait_for_timeout(600)
    audit.check(
        "badge" in (page.get_attribute("#motion-profile", "class") or ""),
        "motion profile badge missing",
    )
    audit.check(
        page.text_content("#motion-profile").strip() == "FULL",
        "forced full profile did not start in FULL",
    )
    page.click("#setting-lite-mode")
    page.wait_for_timeout(300)
    audit.check(
        page.evaluate("() => document.documentElement.classList.contains('is-lite')"),
        "lite mode class was not applied",
    )
    audit.check(
        page.text_content("#motion-profile").strip() == "LITE",
        "motion profile did not switch to LITE",
    )
    audit.check(
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.glass')).backdropFilter"
        )
        in ("none", ""),
        "lite mode kept backdrop blur",
    )
    audit.check(
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.aurora')).animationName"
        )
        == "none",
        "lite mode kept ambient animation",
    )
    page.click("#setting-lite-mode")
    page.wait_for_timeout(300)
    audit.check(
        not page.evaluate("() => document.documentElement.classList.contains('is-lite')"),
        "lite mode class was not removed",
    )
    audit.check(
        page.text_content("#motion-profile").strip() == "FULL",
        "motion profile did not return to FULL",
    )
    context.close()


def main():
    server, base = start_server()
    audit = Audit()
    print(f"Serving {FRONTEND} at {base}")
    print(f"Chrome viewports: {', '.join(str(w) for w in WIDTHS)}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width in WIDTHS:
                    run_width(audit, browser, base, width)
                run_login_and_profiles(audit, browser, base)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()

    print("\n" + "=" * 70)
    if audit.failures:
        print(f"FAILED: {len(audit.failures)} of {audit.checks} checks")
        for failure in audit.failures:
            print(f" - {failure}")
        raise SystemExit(1)

    print(f"PASSED: all {audit.checks} browser checks across {len(WIDTHS)} viewports")


if __name__ == "__main__":
    main()
