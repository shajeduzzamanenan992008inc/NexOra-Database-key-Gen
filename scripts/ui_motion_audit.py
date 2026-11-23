"""Real-browser UI motion + key-creation audit for the NexOra Classic Platform.

Development verification script (not a pytest test). It starts the real Flask
application on a free loopback port against the local test database, signs in
through the real login form, and then verifies in an actual Chrome:

  * the SVG divider asset (HTTP status, MIME type, real rendering),
  * selection-box / input / dropdown / chip / tab / modal animation feedback,
  * the complete Classic key-creation lifecycle (single key, duplicate
    submission prevention, error display, list refresh, no plaintext leak),
  * reduced-motion behavior,
  * mobile responsiveness and broken-asset / console-error hygiene.

Fixture rows are removed afterwards. No production database, external
service, or deployment is touched.

Run: python scripts/ui_motion_audit.py
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
PASSWORD = "Nx!MotionAudit2026"
EMAIL = "ui-motion-audit@nexora.test"
PROJECT_PREFIX = "UI Motion Audit"
AUDIT_TYPE_ID = "ui_audit_free"


def _settings() -> Settings:
    return Settings(
        database=DatabaseSettings.from_dsn(DSN, pool_max=8),
        key_pepper="ui-motion-pepper-" + "0123456789abcdef" * 3,
        secret_key="ui-motion-secret-" + "fedcba9876543210" * 2,
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


def style(page, selector, prop):
    return page.evaluate(
        "(args) => { const node = document.querySelector(args[0]);"
        " return node ? getComputedStyle(node)[args[1]] : null; }",
        [selector, prop],
    )


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
                VALUES (%s, 'UI Motion Audit Owner', %s, 'owner')
                RETURNING id
                """,
                (EMAIL, digest),
            )
            admin_id = str(cursor.fetchone()[0])

    projects = ProjectService(db, settings)
    for stale in db.fetch_all(
        "SELECT id FROM nexora_projects WHERE project_name LIKE %s",
        (f"{PROJECT_PREFIX} %",),
    ):
        projects.hard_delete_project(
            str(stale["id"]),
            actor_id=admin_id,
            ip_address="127.0.0.1",
            user_agent="ui-motion-audit",
        )
    db.execute("DELETE FROM nexora_key_types WHERE id = %s AND is_builtin = false", (AUDIT_TYPE_ID,))

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server = create_server(app, host="127.0.0.1", port=port, threads=4)
    threading.Thread(target=server.run, daemon=True).start()
    _wait_ready(base + "/login")

    failures: list[str] = []
    console_errors: list[str] = []
    broken_assets: list[str] = []
    created_project_ids: list[str] = []

    def check(condition: bool, message: str) -> None:
        if condition:
            print(f"  ok    {message}")
        else:
            failures.append(message)
            print(f"  FAIL  {message}")

    # -- HTTP-level asset checks ------------------------------------------
    try:
        with urllib.request.urlopen(base + "/omega-assets/svg/divider.svg", timeout=10) as resp:
            svg_ok = resp.status == 200
            svg_mime = resp.headers.get("Content-Type", "").split(";")[0]
            svg_body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        svg_ok, svg_mime, svg_body = False, "", f"{exc}"
    check(svg_ok, "divider SVG responds 200")
    check(svg_mime == "image/svg+xml", f"divider SVG MIME is image/svg+xml ({svg_mime})")
    check('viewBox="0 0 800 20"' in svg_body, "divider SVG keeps its 800x20 viewBox")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on(
                "response",
                lambda response: broken_assets.append(response.url)
                if response.status >= 400
                and "api/v1/auth/session" not in response.url
                else None,
            )

            # Login page: brand divider must actually render.
            page.goto(base + "/login", wait_until="domcontentloaded")
            page.wait_for_selector(".omega-brand-divider", timeout=15000)
            brand_w = page.evaluate(
                "() => document.querySelector('.omega-brand-divider').naturalWidth"
            )
            check(brand_w and brand_w > 0, "login brand divider renders from SVG")

            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            page.click("#login-submit")
            # Client-side navigation to /dashboard — wait for network idle + URL
            page.wait_for_url("**/dashboard", timeout=25000, wait_until="networkidle")
            page.wait_for_selector("#boot-screen", state="hidden", timeout=10000)
            check(True, "login form authenticated")

            hero_w = page.evaluate("() => document.querySelector('.omega-hero-divider').naturalWidth")
            foot_w = page.evaluate("() => document.querySelector('.foot-divider').naturalWidth")
            check(hero_w and hero_w > 0 and foot_w and foot_w > 0, "dashboard hero + footer dividers render")

            # Navigate to generator view and wait for it to be fully active
            page.click('.nav-item[data-view="generator"]')
            page.wait_for_function(
                "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
            )
            page.wait_for_function(
                "() => document.querySelector('#gen-key-type-select')?.options.length > 1"
            )
            page.wait_for_timeout(400)

            # Ensure we are in "existing" mode (default after load)
            is_existing = page.evaluate(
                "() => document.querySelector('#gen-mode-existing')?.classList.contains('is-active')"
            )
            if not is_existing:
                page.click("#gen-mode-existing")
                page.wait_for_timeout(80)

            # -- Selects: focus animation + hover transition -----------------
            page.focus("#gen-expiration-select")
            page.wait_for_timeout(60)
            anim = style(page, "#gen-expiration-select", "animationName")
            trans = style(page, "#gen-expiration-select", "transitionProperty")
            check(anim and "control-focus" in anim, f"expiry select focus animation runs ({anim})")
            check(trans and "border-color" in trans, "expiry select has smooth border transition")

            # Project select is only visible in "existing" mode
            before = style(page, "#gen-project-select", "borderColor")
            page.hover("#gen-project-select")
            page.wait_for_timeout(320)
            after = style(page, "#gen-project-select", "borderColor")
            check(before != after, "project select border transitions on hover")

            # -- Scopes free input (reader type has no scope checklist) -------
            page.wait_for_selector("#gen-scopes-free:not([hidden])", timeout=10000)
            page.focus("#gen-scopes-free")
            page.wait_for_timeout(60)
            scopes_anim = style(page, "#gen-scopes-free", "animationName")
            scopes_trans = style(page, "#gen-scopes-free", "transitionProperty")
            check(
                scopes_anim and "control-focus" in scopes_anim,
                f"scopes input focus animation runs ({scopes_anim})",
            )
            check(scopes_trans and "border-color" in scopes_trans, "scopes input has smooth border transition")

            # -- Selection boxes (.check-option) -----------------------------
            page.hover(".check-option")
            page.wait_for_timeout(60)
            opt_trans = style(page, ".check-option", "transitionProperty")
            check(opt_trans and "border-color" in opt_trans, "selection box has smooth hover transition")

            page.click(".check-option input[type=checkbox]")
            page.wait_for_timeout(60)
            box_anim = style(page, ".check-option input[type=checkbox]", "animationName")
            label_anim = style(page, ".check-option", "animationName")
            check(box_anim and "check-pop" in box_anim, f"selection box checkbox pops when checked ({box_anim})")
            check(label_anim and "check-pop" in label_anim, f"selection box border animates when selected ({label_anim})")

            # -- Preset chips + expiry preview flash -------------------------
            page.select_option("#gen-key-type-select", "reader")
            page.wait_for_timeout(250)
            chip_trans = style(page, "#gen-expiration-presets .preset-chip", "transitionProperty")
            check(chip_trans and "border-color" in chip_trans, "preset chip transitions on hover")
            page.click('#gen-expiration-presets .preset-chip[data-preset="90_days"]')
            page.wait_for_timeout(60)
            preview_anim = style(page, "#gen-expiration-preview", "animationName")
            chip_anim = style(
                page, '#gen-expiration-presets .preset-chip[data-preset="90_days"]', "animationName"
            )
            check(preview_anim and "preview-flash" in preview_anim, f"expiry preview flashes on change ({preview_anim})")
            check(chip_anim and "preset-pop" in chip_anim, f"selected preset chip pops ({chip_anim})")
            check(
                page.input_value("#gen-expiration-select") == "90_days",
                "preset chip drives the expiration select",
            )

            # -- Key-type switch replays the summary animation ---------------
            page.select_option("#gen-key-type-select", "license")
            page.wait_for_timeout(60)
            summary_anim = style(page, "#gen-key-type-summary", "animationName")
            check(summary_anim and "type-pick" in summary_anim, f"key-type summary replays pick animation ({summary_anim})")

            # -- Segmented mode switch ----------------------------------------
            page.select_option("#gen-key-type-select", "reader")
            page.wait_for_timeout(120)
            page.click("#gen-mode-new")
            page.wait_for_timeout(60)
            mode_anim = style(page, "#gen-mode-new", "animationName")
            check(mode_anim and "preset-pop" in mode_anim, f"project workflow switch animates ({mode_anim})")
            page.click("#gen-mode-existing")
            page.wait_for_timeout(80)

            # -- Validation error keeps the form data -------------------------
            page.click("#generate-keys-btn")
            page.wait_for_timeout(120)
            error_text = page.inner_text("#generator-error")
            check("Select an existing project" in error_text, "empty project shows a clear validation message")
            check(
                "is-invalid" in (page.get_attribute("#gen-project-select", "class") or ""),
                "invalid project select shows the error state",
            )
            check(page.input_value("#gen-key-type-select") == "reader", "form data preserved after failed submit")

            # -- Real key creation (single, New Project mode) -----------------
            page.click("#gen-mode-new")
            page.wait_for_timeout(80)
            project_name = f"{PROJECT_PREFIX} Single {uuid.uuid4().hex[:8]}"
            page.fill("#gen-project-name-input", project_name)
            page.select_option("#gen-key-type-select", "reader")
            page.select_option("#gen-expiration-select", "30_days")
            page.click("#generate-keys-btn")
            busy = page.evaluate("() => document.querySelector('#generate-keys-btn').disabled")
            check(busy, "Generate button disables while the request is processing")
            # Wait longer for the key generation (includes DB transaction)
            page.wait_for_selector("#result-modal[open]", timeout=45000)
            full_key = page.evaluate(
                "() => document.querySelector('.key-plaintext')?.innerText.replace(/\\s+/g, '') || ''"
            )
            check(full_key.startswith("RDR-"), "generated key plaintext appears in the one-time reveal")
            page.click('#result-modal [data-close="result-modal"]')
            page.wait_for_function("() => !document.querySelector('#result-modal').open")
            check(True, "reveal modal closes")

            project_id = page.evaluate(
                """
                async (name) => {
                  const response = await fetch('/api/v1/projects');
                  const payload = await response.json();
                  const match = (payload.projects || []).find((p) => p.project_name === name);
                  return match ? match.id : null;
                }
                """,
                project_name,
            )
            created_project_ids.append(str(project_id)) if project_id else None
            check(bool(project_id), "created project is returned by the projects API")

            page.click('.nav-item[data-view="keys"]')
            page.wait_for_function(
                "() => document.querySelector('#view-keys')?.classList.contains('is-active')"
            )
            page.wait_for_function(
                "() => document.querySelector('#keys-body')?.innerText.length > 0"
            )
            keys_body = page.inner_text("#keys-body")
            check(project_name in keys_body, "generated key list refreshes with the new key")
            check(
                full_key not in keys_body.replace("\n", ""),
                "plaintext is not exposed in the key list",
            )

            # -- Duplicate submission prevention ------------------------------
            page.click('.nav-item[data-view="generator"]')
            page.wait_for_function(
                "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
            )
            page.wait_for_timeout(400)
            page.click("#gen-mode-new")
            page.wait_for_timeout(80)
            dup_name = f"{PROJECT_PREFIX} Double {uuid.uuid4().hex[:8]}"
            page.fill("#gen-project-name-input", dup_name)
            page.select_option("#gen-key-type-select", "reader")
            page.evaluate(
                """
                () => {
                  const button = document.querySelector('#generate-keys-btn');
                  button.click();
                  button.click();
                }
                """
            )
            page.wait_for_selector("#result-modal[open]", timeout=45000)
            page.wait_for_timeout(400)
            dup_count = page.evaluate(
                """
                async (name) => {
                  const response = await fetch('/api/v1/projects');
                  const payload = await response.json();
                  const match = (payload.projects || []).find((p) => p.project_name === name);
                  if (!match) return -1;
                  const keys = await fetch(
                    '/api/v1/universal/keys?project_id=' + encodeURIComponent(match.id)
                  ).then((r) => r.json());
                  return (keys.keys || keys.items || []).length;
                }
                """,
                dup_name,
            )
            check(dup_count == 1, f"double click creates exactly one key ({dup_count})")
            dup_id = page.evaluate(
                """
                async (name) => {
                  const response = await fetch('/api/v1/projects');
                  const payload = await response.json();
                  const match = (payload.projects || []).find((p) => p.project_name === name);
                  return match ? match.id : null;
                }
                """,
                dup_name,
            )
            if dup_id:
                created_project_ids.append(str(dup_id))
            page.click('#result-modal [data-close="result-modal"]')
            page.wait_for_function("() => !document.querySelector('#result-modal').open")

            # Return to generator for duplicate test
            is_generator = page.evaluate(
                "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
            )
            if not is_generator:
                page.wait_for_selector('.nav-item[data-view="generator"]', state="visible", timeout=10000)
                page.click('.nav-item[data-view="generator"]')
                page.wait_for_function(
                    "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
                )
            page.wait_for_timeout(400)

            # -- Mobile responsiveness ----------------------------------------
            page.set_viewport_size({"width": 390, "height": 844})
            # On mobile the sidebar is hidden; nav is accessed via hamburger
            # We test the generator is still usable by checking the view
            is_generator = page.evaluate(
                "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
            )
            if not is_generator:
                page.wait_for_selector('#mobile-nav-toggle', state="visible", timeout=10000)
                page.click('#mobile-nav-toggle')
                page.wait_for_timeout(300)
                page.wait_for_selector('.nav-item[data-view="generator"]', state="visible", timeout=10000)
                page.click('.nav-item[data-view="generator"]')
                page.wait_for_function(
                    "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
                )
            page.wait_for_timeout(400)
            overflow = page.evaluate(
                "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
            )
            check(overflow <= 0, f"no horizontal overflow at 390px ({overflow}px)")
            foot_box = page.evaluate(
                "() => { const n = document.querySelector('.foot-divider');"
                " const r = n.getBoundingClientRect(); return [r.width, window.innerWidth]; }"
            )
            check(
                foot_box and foot_box[0] <= foot_box[1],
                f"footer divider fits the mobile viewport ({foot_box[0]}px)",
            )
            page.click("#mobile-nav-toggle")
            page.wait_for_timeout(300)
            check(
                "is-open" in (page.get_attribute("#sidebar", "class") or ""),
                "mobile navigation drawer opens",
            )
            page.click("#sidebar-close")
            page.wait_for_timeout(200)

            # -- Reduced motion -------------------------------------------------
            reduced_context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                reduced_motion="reduce",
            )
            reduced_page = reduced_context.new_page()
            reduced_page.goto(base + "/login", wait_until="domcontentloaded")
            reduced_page.fill("#email", EMAIL)
            reduced_page.fill("#password", PASSWORD)
            reduced_page.click("#login-submit")
            reduced_page.wait_for_url("**/dashboard", timeout=20000)
            reduced_page.wait_for_selector("#boot-screen", state="hidden", timeout=10000)
            duration = reduced_page.evaluate(
                "() => parseFloat(getComputedStyle(document.querySelector('.stat-card')).animationDuration)"
            )
            check(duration <= 0.001, f"reduced motion clamps animation duration ({duration}s)")
            reduced_hero = reduced_page.evaluate(
                "() => document.querySelector('.omega-hero-divider').naturalWidth"
            )
            check(reduced_hero and reduced_hero > 0, "divider still renders under reduced motion")
            reduced_page.click('.nav-item[data-view="generator"]')
            reduced_page.wait_for_function(
                "() => document.querySelector('#view-generator')?.classList.contains('is-active')"
            )
            reduced_ok = reduced_page.evaluate(
                "() => document.querySelector('#generate-keys-btn') !== null"
            )
            check(reduced_ok, "generator remains fully usable under reduced motion")
            reduced_context.close()

            # -- Console / asset hygiene --------------------------------------
            unexpected = [
                error
                for error in console_errors
                if "Failed to load resource" not in error
            ]
            check(
                not unexpected,
                f"no page/script errors ({len(unexpected)} unexpected of {len(console_errors)})",
            )
            real_broken = [
                url
                for url in broken_assets
                if "/api/v1/" not in url
            ]
            check(
                not real_broken,
                f"no broken static assets ({len(real_broken)} of {len(broken_assets)} non-2xx responses)",
            )
            browser.close()
    finally:
        try:
            server.close()
        except Exception:
            pass
        try:
            for project_id in created_project_ids:
                projects.hard_delete_project(
                    project_id,
                    actor_id=admin_id,
                    ip_address="127.0.0.1",
                    user_agent="ui-motion-audit",
                )
            db.execute(
                "DELETE FROM nexora_key_types WHERE id = %s AND is_builtin = false",
                (AUDIT_TYPE_ID,),
            )
            db.execute("DELETE FROM admin_sessions WHERE admin_user_id = %s", (admin_id,))
            db.execute("DELETE FROM admin_users WHERE id = %s", (admin_id,))
        finally:
            db.close()

    if failures:
        print(f"\nFAILED: {len(failures)} UI motion audit check(s)")
        for failure in failures:
            print(f" - {failure}")
        if console_errors:
            print("\nConsole/page errors:")
            for error in console_errors[:20]:
                print(f" - {error}")
        if broken_assets:
            print("\nNon-2xx responses:")
            for url in broken_assets[:20]:
                print(f" - {url}")
        return 1
    print("\nPASSED: UI motion + key-creation browser audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
