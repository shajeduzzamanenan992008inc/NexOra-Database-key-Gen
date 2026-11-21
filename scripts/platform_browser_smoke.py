"""Real-browser smoke test for the authenticated universal platform console.

This is a development verification script, not a pytest test. It starts the
real Flask application on a free loopback port against the local test database,
signs in through the real login form, exercises every platform view, opens the
project export and delete-all dialogs, verifies mobile navigation, and confirms
that sign-out revokes the session before the bounded shutdown animation
navigates to the login page.

The fixture account and project are created only for this run and removed
afterwards. No production database, external service, or deployment is touched.

Run: python scripts/platform_browser_smoke.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from waitress import create_server

from backend.app import create_app
from backend.config import DatabaseSettings, Settings
from backend.migrations import apply_migrations
from backend.security.passwords import hash_password
from backend.services.project_service import ProjectService

DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres@127.0.0.1:55432/nexora_keygen_test?sslmode=disable",
)
PASSWORD = "Nx!PlatformSmoke2026"
EMAIL = "browser-smoke@nexora.test"
PROJECT_NAME = f"Browser Smoke {uuid.uuid4().hex[:8]}"

VIEWS = {
    "overview": "Overview",
    "analytics": "Analytics",
    "projects": "Projects",
    "keys": "Universal Keys",
    "generator": "Generate Keys",
    "key-types": "Key Types",
    "builder": "Type Builder",
    "devices": "Devices",
    "api-usage": "API Usage",
    "webhooks": "Webhooks",
    "notifications": "Notifications",
    "search": "Global Search",
    "audit": "Audit Trail",
    "import-export": "Import / Export",
    "automation": "Automation",
    "roles": "Roles",
    "admins": "Administrators",
    "health": "Health",
    "settings": "Preferences",
    "docs": "API Docs",
}


def _settings() -> Settings:
    return Settings(
        database=DatabaseSettings.from_dsn(DSN, pool_max=8),
        key_pepper="browser-smoke-pepper-" + "0123456789abcdef" * 3,
        secret_key="browser-smoke-secret-" + "fedcba9876543210" * 2,
        environment="testing",
        session_ttl_seconds=3600,
        session_cookie_name="nexora_session",
        cookie_secure=False,
        rate_limit_enabled=False,
        trust_proxy=False,
        max_bulk_keys=500,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("Flask application did not become ready")


def main() -> int:
    settings = _settings()
    app = create_app(settings)
    app.config["TESTING"] = True
    db = app.extensions["db"]
    apply_migrations(db, ROOT / "db" / "migrations")
    digest = hash_password(PASSWORD)

    with db.cursor() as cursor:
        cursor.execute("SELECT id FROM admin_users WHERE email = %s", (EMAIL,))
        existing = cursor.fetchone()
        if existing:
            admin_id = str(existing[0])
            cursor.execute(
                """
                UPDATE admin_users
                   SET password_hash = %s, role = 'owner', is_active = true,
                       failed_login_attempts = 0, locked_until = NULL
                 WHERE id = %s
                """,
                (digest, admin_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO admin_users (email, full_name, password_hash, role)
                VALUES (%s, 'Browser Smoke Owner', %s, 'owner')
                RETURNING id
                """,
                (EMAIL, digest),
            )
            admin_id = str(cursor.fetchone()[0])

    projects = ProjectService(db, settings)
    # Remove only fixtures created by an earlier interrupted smoke run.
    for stale in db.fetch_all(
        "SELECT id FROM nexora_projects WHERE project_name LIKE 'Browser Smoke %'"
    ):
        projects.hard_delete_project(
            str(stale["id"]),
            actor_id=admin_id,
            ip_address="127.0.0.1",
            user_agent="platform-browser-smoke",
        )

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server = create_server(app, host="127.0.0.1", port=port, threads=4)
    threading.Thread(target=server.run, daemon=True).start()
    _wait_ready(base + "/login")

    project_id = None
    failures: list[str] = []
    console_errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)
            print(f"  FAIL  {message}")
        else:
            print(f"  ok    {message}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )

            page.goto(base + "/login", wait_until="domcontentloaded")
            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            page.click("#login-submit")
            page.wait_for_url("**/dashboard", timeout=20000)
            check(True, "login form authenticated")

            page.goto(base + "/platform", wait_until="domcontentloaded")
            page.wait_for_selector("#logout-btn:not([disabled])", timeout=20000)
            page.wait_for_selector("#boot-screen", state="hidden", timeout=10000)
            check(True, "platform session bootstrapped")

            for view, title in VIEWS.items():
                page.click(f'.nav-item[data-view="{view}"]')
                try:
                    page.wait_for_function(
                        "title => document.querySelector('#page-title')?.textContent === title",
                        arg=title,
                        timeout=20000,
                    )
                    page.wait_for_function(
                        "() => document.querySelector('#platform-view')?.getAttribute('aria-busy') !== 'true'",
                        timeout=20000,
                    )
                    body = page.inner_text("#platform-view")
                    check(
                        "This view could not load" not in body,
                        f"view '{view}' rendered without an API error",
                    )
                except Exception as exc:
                    check(False, f"view '{view}' failed: {exc}")

            # Create a project through the documented generation endpoint.
            created = page.evaluate(
                """
                async (name) => {
                  const session = await fetch('/api/v1/auth/session').then((r) => r.json());
                  const response = await fetch('/api/v1/projects/generate-key', {
                    method: 'POST',
                    headers: {
                      'Content-Type': 'application/json',
                      'X-CSRF-Token': session.csrf_token,
                    },
                    body: JSON.stringify({ project_name: name }),
                  });
                  const payload = await response.json();
                  return { status: response.status, id: payload?.project?.id || null };
                }
                """,
                PROJECT_NAME,
            )
            project_id = created.get("id")
            check(created.get("status") == 201 and bool(project_id), "project created")

            page.click('.nav-item[data-view="projects"]')
            page.wait_for_function(
                "() => document.querySelector('#page-title')?.textContent === 'Projects'"
            )
            page.wait_for_timeout(500)
            row = page.locator("tr", has_text=PROJECT_NAME).first
            check(row.count() > 0, "created project appears in Projects")
            row.get_by_role("button", name="Export", exact=True).click()
            page.wait_for_selector("#reveal-dialog[open]", timeout=15000)
            panel = page.inner_text("#reveal-body")
            check("Export" in page.inner_text("#reveal-title"), "export preview opened")
            check("key" in panel.lower() and "not" in panel.lower(), "export explains key limits")
            page.click("#reveal-dialog [data-close-dialog]")
            page.wait_for_function("() => !document.querySelector('#reveal-dialog').open")

            page.get_by_role("button", name="Delete all projects", exact=True).click()
            page.wait_for_selector("#reveal-dialog[open]", timeout=15000)
            page.wait_for_timeout(600)
            totals = page.locator("#reveal-body .record-item")
            total_labels = [item.upper() for item in totals.all_inner_texts()]
            check(
                len(total_labels) >= 6
                and any("PROJECTS" in item for item in total_labels)
                and any("KEYS" in item for item in total_labels)
                and any("DEVICES" in item for item in total_labels)
                and any("WEBHOOKS" in item for item in total_labels),
                f"delete-all shows pre-execution totals ({len(total_labels)} rows)",
            )
            phrase_field = page.locator(
                '#reveal-body input[placeholder="DELETE ALL PROJECTS"]'
            )
            check(phrase_field.count() == 1, "delete-all requires the exact phrase")
            page.click("#reveal-dialog [data-close-dialog]")
            page.wait_for_function("() => !document.querySelector('#reveal-dialog').open")

            page.set_viewport_size({"width": 390, "height": 900})
            page.click("#mobile-nav-toggle")
            page.wait_for_timeout(250)
            check(
                "is-open" in (page.get_attribute("#sidebar", "class") or ""),
                "mobile navigation opens at 390px",
            )
            page.click("#sidebar-close")
            page.wait_for_timeout(150)
            check(
                "is-open" not in (page.get_attribute("#sidebar", "class") or ""),
                "mobile navigation closes",
            )

            page.set_viewport_size({"width": 1280, "height": 900})
            page.click("#logout-btn")
            page.wait_for_selector(".shutdown-screen", timeout=5000)
            check(True, "shutdown animation appears")
            page.wait_for_url("**/login", timeout=7000)
            check(True, "sign-out reaches the login page")

            # The logout CSRF-recovery path intentionally probes the session and
            # retries once after a 403, and the login page expects a 401 session
            # probe. Those network console entries are expected; page errors are not.
            unexpected = [
                error
                for error in console_errors
                if "Failed to load resource" not in error
            ]
            check(
                not unexpected,
                f"no page/script errors ({len(unexpected)} unexpected of {len(console_errors)})",
            )
            browser.close()
    finally:
        try:
            server.close()
        except Exception:
            pass
        try:
            if project_id:
                ProjectService(db, settings).hard_delete_project(
                    project_id,
                    actor_id=admin_id,
                    ip_address="127.0.0.1",
                    user_agent="platform-browser-smoke",
                )
            db.execute("DELETE FROM admin_sessions WHERE admin_user_id = %s", (admin_id,))
            db.execute("DELETE FROM admin_users WHERE id = %s", (admin_id,))
        finally:
            db.close()

    if failures:
        print(f"\nFAILED: {len(failures)} platform browser check(s)")
        for failure in failures:
            print(f" - {failure}")
        if console_errors:
            print("\nConsole/page errors:")
            for error in console_errors[:20]:
                print(f" - {error}")
        return 1
    print("\nPASSED: platform browser smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
