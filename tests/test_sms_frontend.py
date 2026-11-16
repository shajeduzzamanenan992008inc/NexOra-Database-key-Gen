from __future__ import annotations

import re

from conftest import SMS_PEPPER, TEST_PEPPER


def test_sms_login_page_is_served_with_branding_and_no_inline_script(sms_app):
    response = sms_app.test_client().get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
    body = response.get_data(as_text=True)
    assert "NexOra" in body
    assert "SMS Reader" in body
    assert 'id="login-form"' in body
    assert 'id="email"' in body
    assert 'id="password"' in body
    assert "/sms-assets/js/login.js" in body
    assert SMS_PEPPER not in body
    assert TEST_PEPPER not in body
    assert "DATABASE_URL" not in body
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", body, re.I)
    response.close()


def test_sms_dashboard_requires_its_own_session(sms_app, sms_owner):
    anonymous = sms_app.test_client().get("/dashboard")
    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].endswith("/login")
    assert 'id="view-overview"' not in anonymous.get_data(as_text=True)
    anonymous.close()

    dashboard = sms_owner.get("/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.get_data(as_text=True)
    for element_id in (
        "view-overview",
        "view-generate",
        "view-bulk",
        "view-keys",
        "keys-body",
        "result-modal",
        "detail-modal",
        "recent-events",
        "reader-preview-body",
        "status-filter",
        "key-search",
    ):
        assert f'id="{element_id}"' in body
    assert "/sms-assets/sms.css" in body
    assert "/sms-assets/js/dashboard.js" in body
    assert "NEXORA-XXXX-XXXX-XXXX-XXXX" in body
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", body, re.I)
    assert SMS_PEPPER not in body
    assert "SUPABASE_SERVICE_ROLE_KEY" not in body
    dashboard.close()


def test_sms_assets_resolve(sms_app):
    client = sms_app.test_client()
    for path, content_type in (
        ("/assets/app.css", "text/css"),
        ("/assets/js/ui.js", "javascript"),
        ("/assets/logo.svg", "svg"),
        ("/assets/favicon.svg", "svg"),
        ("/sms-assets/sms.css", "text/css"),
        ("/sms-assets/js/api.js", "javascript"),
        ("/sms-assets/js/login.js", "javascript"),
        ("/sms-assets/js/dashboard.js", "javascript"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert content_type in response.headers["Content-Type"], path
        response.close()


def test_sms_security_headers_and_health(sms_app):
    client = sms_app.test_client()
    page = client.get("/")
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]

    api = client.get("/api/health")
    assert api.status_code == 200
    payload = api.get_json()
    assert payload["status"] == "ok"
    assert payload["service"] == "nexora-sms-reader"
    assert payload["database"] == "connected"
    assert payload["supabase_rest_configured"] is False
    assert api.headers["Cache-Control"] == "no-store"
    assert "/api/" not in str(payload)
    page.close()
    api.close()


def test_sms_stylesheet_is_responsive_for_mobile_widths(sms_app, sms_owner):
    css = sms_app.test_client().get("/sms-assets/sms.css").get_data(as_text=True)
    for breakpoint in (1120, 980, 720, 430):
        assert f"@media (max-width: {breakpoint}px)" in css
    # The sidebar becomes a static, stacked block below the desktop breakpoint
    # and the key table keeps a minimum width inside its scroll container.
    assert ".sms-app .sidebar" in css
    assert "position: static" in css
    assert ".sms-keys-table" in css
    assert "min-width: 860px" in css
    for path, client in (("/", sms_app.test_client()), ("/dashboard", sms_owner.client)):
        html = client.get(path).get_data(as_text=True)
        assert 'name="viewport"' in html, path
        assert "width=device-width" in html, path


def test_sms_javascript_uses_only_relative_imports_and_no_secret_names(sms_app):
    client = sms_app.test_client()
    for path in (
        "/sms-assets/js/api.js",
        "/sms-assets/js/login.js",
        "/sms-assets/js/dashboard.js",
    ):
        response = client.get(path)
        body = response.get_data(as_text=True)
        response.close()
        for forbidden in (
            "SUPABASE_SERVICE_ROLE_KEY",
            "DATABASE_URL",
            "SERVICE_ROLE_KEY",
            "KEY_PEPPER",
            SMS_PEPPER,
        ):
            assert forbidden not in body, path
        # Only same-origin relative or absolute paths are fetched.
        assert "https://" not in body, path
        assert "http://" not in body, path
