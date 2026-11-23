"""Static contracts for the Classic shell and its Universal key generator."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_HTML = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
DASHBOARD_JS = (ROOT / "frontend" / "assets" / "js" / "dashboard.js").read_text(
    encoding="utf-8"
)
API_JS = (ROOT / "frontend" / "assets" / "js" / "api.js").read_text(
    encoding="utf-8"
)
APP_PY = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
LOGIN_JS = (ROOT / "frontend" / "assets" / "js" / "login.js").read_text(
    encoding="utf-8"
)
APP_CSS = (ROOT / "frontend" / "assets" / "app.css").read_text(encoding="utf-8")


def test_classic_dashboard_is_the_primary_shell():
    assert 'return _page("dashboard.html")' in APP_PY
    assert 'redirect(url_for("dashboard_page"))' in APP_PY
    assert 'window.location.assign("/dashboard")' in LOGIN_JS
    assert 'id="view-overview"' in DASHBOARD_HTML
    assert 'id="key-generator-form"' in DASHBOARD_HTML
    assert "Universal Platform" not in DASHBOARD_HTML
    assert 'href="/platform"' not in DASHBOARD_HTML


def test_generator_has_one_inline_type_aware_flow():
    for token in (
        'id="gen-key-type-select"',
        'id="gen-key-type-summary"',
        'id="gen-type-config"',
        'id="gen-permissions-grid"',
        'id="gen-scopes-grid"',
        'id="gen-metadata-fields"',
        'id="gen-project-expiration-select"',
        'id="gen-project-expiration-custom"',
        'id="gen-custom-expiration-field"',
        'id="gen-expiration-custom"',
    ):
        assert token in DASHBOARD_HTML, token
    for token in (
        "api.keyTypes",
        "api.generateUniversalKeys",
        "keyTypePresets",
        "defaultKeyTypePreset",
        "readMetadataValues",
        "readScopeValues",
        "data-permission",
        "data-scope",
        "api.createProject",
        "expiration_preset: expiration",
        "key_type_id: type.id",
    ):
        assert token in DASHBOARD_JS, token


def test_dividers_use_the_original_svg_asset():
    assert DASHBOARD_HTML.count('src="/omega-assets/svg/divider.svg"') >= 2
    assert "/omega-assets/webp/divider" not in DASHBOARD_HTML
    assert "/omega-assets/webp/divider-animated.webp" not in DASHBOARD_HTML


def test_selector_changes_replay_the_selection_animation_system():
    assert 'updateGeneratorType({ animate: true })' in DASHBOARD_JS
    assert 'replayMotion(select, "is-picked", 340)' in DASHBOARD_JS
    assert 'replayMotion(summary, "is-picked", 420)' in DASHBOARD_JS
    assert 'replayMotion(generatorProject, "is-picked", 340)' in DASHBOARD_JS
    assert 'replayMotion(expirationSelect, "is-picked", 340)' in DASHBOARD_JS
    assert ".field select.is-picked" in APP_CSS
    assert ".key-type-summary.is-picked" in APP_CSS


def test_key_type_builder_and_safe_delete_are_in_the_classic_shell():
    for token in (
        'id="view-key-types"',
        'data-view="key-types"',
        'id="new-key-type-btn"',
        'id="key-types-table-body"',
        'id="type-builder-panel"',
        'id="key-type-form"',
        'id="type-id-input"',
        'id="type-permissions-grid"',
        'id="type-scopes-input"',
        'id="type-metadata-input"',
        'id="type-validation-input"',
        'id="type-expiry-presets"',
        'id="type-default-expiry"',
    ):
        assert token in DASHBOARD_HTML, token
    for token in (
        "loadKeyTypeCatalogue",
        "renderKeyTypeCatalogue",
        "openTypeBuilder",
        "handleKeyTypeSubmit",
        "syncTypeDefaultExpiry",
        "deleteKeyTypePrompt",
        "api.deleteKeyType",
        "api.createKeyType",
        "api.updateKeyType",
        "is_builtin",
        "key_count",
    ):
        assert token in DASHBOARD_JS, token
    assert DASHBOARD_HTML.count('data-view="key-types"') == 1
    assert "deleteKeyType: (id)" in API_JS


def test_remaining_universal_controls_live_in_one_classic_operations_view():
    for token in (
        'id="view-operations"',
        'data-view="operations"',
        'id="operations-tabs"',
        'id="operations-content"',
        'data-ops-tab="devices"',
        'data-ops-tab="api-usage"',
        'data-ops-tab="webhooks"',
        'data-ops-tab="inbox"',
        'data-ops-tab="data"',
        'data-ops-tab="automation"',
        'data-ops-tab="access-control"',
        'data-ops-tab="search"',
        'data-ops-tab="docs"',
    ):
        assert token in DASHBOARD_HTML, token
    assert DASHBOARD_HTML.count('data-view="operations"') == 1
    operations = (ROOT / "frontend" / "assets" / "js" / "operations.js").read_text(
        encoding="utf-8"
    )
    for token in (
        "renderDevices",
        "renderApiUsage",
        "renderWebhooks",
        "renderInbox",
        "renderData",
        "renderAutomation",
        "renderAccessControl",
        "renderSearch",
        "renderDocs",
        "api.createWebhook",
        "api.universalImportPreview",
        "api.universalProjectImportPreview",
        "api.importedKeyReplacementPreview",
        "api.schedulerStatus",
        "api.universalRoles",
        "api.universalAdmins",
        "api.universalOpenapi",
    ):
        assert token in operations, token
    assert "renderOperations" in DASHBOARD_JS


def test_generator_partial_failure_reuses_one_batch_and_project():
    for token in (
        'id="generator-recovery"',
        'id="generator-retry-btn"',
        'id="generator-recovery-view-btn"',
        'id="generator-recovery-dismiss"',
    ):
        assert token in DASHBOARD_HTML, token
    for token in (
        "createGeneratorBatchId",
        "state.generatorBatchId",
        "batch_id: batchId",
        "keyRequestStarted",
        "BATCH_ALREADY_PROCESSED",
        "showGeneratorRecovery",
        "hideGeneratorRecovery",
        "The project was created, but the key could not be generated.",
    ):
        assert token in DASHBOARD_JS, token
    assert "createProject" in DASHBOARD_JS


def test_custom_project_and_key_expiry_are_in_the_classic_forms():
    for token in (
        "PROJECT_EXPIRY_PRESETS",
        "saveProjectExpiration",
        "api.updateUniversalProject",
        "project-expiry-select",
        "project-expiry-custom",
        "buildKeyEditSection",
        "saveKeyEdits",
        "api.updateUniversalKey",
        "key-expiry-select",
        "Never expires",
    ):
        assert token in DASHBOARD_JS, token
    assert '"never"' in DASHBOARD_JS
    assert '"custom"' in DASHBOARD_JS


def test_universal_api_client_contracts_exist():
    for token in (
        "keyTypes: (params)",
        "generateUniversalKeys: (body)",
        "updateUniversalKey: (id, body)",
        "updateUniversalProject: (id, body)",
        "createProject: (projectName, options = {})",
    ):
        assert token in API_JS, token


def test_no_second_dashboard_or_six_step_wizard_was_added():
    assert "platform-view" not in DASHBOARD_HTML
    assert "formStep(" not in DASHBOARD_JS
    assert "Universal Mode" not in DASHBOARD_JS
