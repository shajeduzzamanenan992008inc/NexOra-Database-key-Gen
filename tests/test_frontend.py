from __future__ import annotations

import re


def test_login_and_dashboard_pages_are_served(app, owner):
    client = app.test_client()
    login = client.get("/")
    assert login.status_code == 200
    assert "text/html" in login.headers["Content-Type"]
    assert "NexOra" in login.get_data(as_text=True)
    assert 'id="login-form"' in login.get_data(as_text=True)

    # The console is server-rendered markup, so it must never reach anonymous
    # visitors; only an authenticated session may load it.
    anonymous = client.get("/dashboard")
    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].endswith("/login")
    assert 'id="view-overview"' not in anonymous.get_data(as_text=True)

    dashboard = owner.client.get("/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.get_data(as_text=True)
    assert 'id="view-overview"' in body
    assert 'id="keys-body"' in body
    assert 'id="gen-project-name-input"' in body
    assert 'id="delete-all-modal"' in body
    assert 'id="detail-modal"' in body
    assert 'id="project-modal"' not in body
    assert 'id="generate-modal"' not in body


def test_all_local_assets_resolve(app, owner):
    pages = {"/": app.test_client(), "/dashboard": owner.client}
    for path, client in pages.items():
        html = client.get(path).get_data(as_text=True)
        references = re.findall(r'(?:src|href)="(/[^"]+)"', html)
        assert references
        for reference in references:
            response = client.get(reference)
            assert response.status_code == 200, (path, reference)


def test_pages_are_csp_compatible_without_inline_code(app, owner):
    pages = {"/": app.test_client(), "/dashboard": owner.client}
    for path, client in pages.items():
        html = client.get(path).get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", html, re.IGNORECASE)
        assert not re.search(r"\sstyle=", html, re.IGNORECASE)
        assert "onclick=" not in html.lower()


def test_stylesheet_is_responsive_and_accessible(app):
    css = app.test_client().get("/assets/app.css").get_data(as_text=True)
    assert "@media (max-width: 980px)" in css
    assert "@media (max-width: 720px)" in css
    assert "@media (max-width: 430px)" in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
    assert ".sr-only" in css


def test_javascript_uses_safe_dom_apis(app):
    client = app.test_client()
    dashboard = client.get("/assets/js/dashboard.js").get_data(as_text=True)
    assert "textContent" in dashboard
    assert "innerHTML" not in dashboard
    assert "eval(" not in dashboard
    uijs = client.get("/assets/js/ui.js").get_data(as_text=True)
    assert "textContent" in uijs


def test_dashboard_generate_key_uses_canonical_endpoint_and_fields(app):
    client = app.test_client()
    api_js = client.get("/assets/js/api.js").get_data(as_text=True)
    dashboard_js = client.get("/assets/js/dashboard.js").get_data(as_text=True)

    assert '"/api/v1/projects/generate-key"' in api_js
    assert "api.generateProjectKey" in dashboard_js
    assert "project_name: projectName" in dashboard_js
    assert "label," in dashboard_js


OLD_BRAND = re.compile(r"RH E-?SPORTS|rhesports|rh-esports|rh_keygen|rh_session|RHX", re.IGNORECASE)


def test_branding_is_nexora_everywhere(app, owner):
    pages = {"/": app.test_client(), "/dashboard": owner.client}
    for path, client in pages.items():
        body = client.get(path).get_data(as_text=True)
        assert "NexOra" in body, path
        assert "NexOra-Database-key-Gen" in body, path
        assert "NexOra Database Key Generator" in body, path
        assert not OLD_BRAND.search(body), path

    for asset in (
        "/assets/app.css",
        "/assets/js/dashboard.js",
        "/assets/js/ui.js",
        "/assets/logo.svg",
        "/assets/favicon.svg",
    ):
        body = app.test_client().get(asset).get_data(as_text=True)
        assert not OLD_BRAND.search(body), asset


def test_no_html_secrets_or_source_maps(app, owner):
    pages = {"/": app.test_client(), "/dashboard": owner.client}
    for path, client in pages.items():
        body = client.get(path).get_data(as_text=True)
        assert "DATABASE_URL" not in body
        assert "KEY_PEPPER" not in body
        assert "service_role" not in body
        assert "sourceMappingURL" not in body
