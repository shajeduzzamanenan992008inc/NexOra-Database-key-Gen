"""Static animation-regression contracts for the NexOra Classic Platform.

These checks pin the interaction-motion invariants that a refactor must never
remove: animated selection boxes, focus/hover feedback on every control
(including inputs rendered outside ``.field`` wrappers), the SVG divider
asset, dropdown/pick feedback, smooth preview updates, and reduced-motion /
lite-mode safety. They are deterministic and need no database.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
CSS = (FRONTEND / "assets" / "app.css").read_text(encoding="utf-8")
DASHBOARD_JS = (FRONTEND / "assets" / "js" / "dashboard.js").read_text(encoding="utf-8")
DASHBOARD_HTML = (FRONTEND / "dashboard.html").read_text(encoding="utf-8")
INDEX_HTML = (FRONTEND / "index.html").read_text(encoding="utf-8")
DIVIDER_SVG = (ROOT / "assets" / "omega-key-assets" / "svg" / "divider.svg").read_text(
    encoding="utf-8"
)


# ---------------------------------------------------------------------------
# Divider asset
# ---------------------------------------------------------------------------


def test_divider_svg_is_the_only_divider_across_the_classic_shell():
    for html in (DASHBOARD_HTML, INDEX_HTML):
        assert "/omega-assets/svg/divider.svg" in html
        assert "/omega-assets/webp/divider" not in html
        assert "divider.png" not in html
    assert "/omega-assets/webp/divider" not in CSS
    assert "divider.png" not in CSS


def test_divider_svg_keeps_its_original_shape_and_reduced_motion_guard():
    assert 'viewBox="0 0 800 20"' in DIVIDER_SVG
    assert "prefers-reduced-motion" in DIVIDER_SVG
    # The asset itself is never rewritten to a different artwork.
    assert "linearGradient" in DIVIDER_SVG


# ---------------------------------------------------------------------------
# Selection boxes
# ---------------------------------------------------------------------------


def test_selection_boxes_have_hover_focus_and_checked_feedback():
    # The check-option boxes used by permission/scope/expiry grids and the
    # type builder must keep animated hover, focus-within and checked states.
    for token in (
        ".check-option:hover",
        ".check-option:focus-within",
        ".check-option:has(input:checked)",
        ".check-option input[type=\"checkbox\"]:checked",
        ".check-option input[type=\"checkbox\"]:focus-visible",
        "@keyframes check-pop",
    ):
        assert token in CSS, token
    block = CSS.split(".check-option {", 1)[1].split("}", 1)[0]
    assert "transition:" in block
    assert "border-color" in block


def test_inputs_outside_field_wrappers_keep_focus_and_hover_motion():
    # #gen-scopes-free lives inside .generator-config-section, not a .field
    # label, so the shared control motion rules must cover it explicitly.
    for token in (
        ".generator-config-section > input:hover",
        ".generator-config-section > input:focus",
        ".generator-config-section > input.is-invalid",
        ".generator-config-section > input::placeholder",
    ):
        assert token in CSS, token


def test_expiration_previews_transition_and_flash_on_change():
    for token in (
        ".expiration-preview {",
        ".expiration-preview.is-flash",
        "@keyframes preview-flash",
        ".expiration-preview strong {",
    ):
        assert token in CSS, token
    block = CSS.split(".expiration-preview {", 1)[1].split("}", 1)[0]
    assert "transition:" in block


# ---------------------------------------------------------------------------
# Generator interaction wiring
# ---------------------------------------------------------------------------


def test_generator_selection_animations_are_wired():
    for token in (
        'replayMotion(select, "is-picked", 340)',
        'replayMotion(summary, "is-picked", 420)',
        'replayMotion(generatorProject, "is-picked", 340)',
        'replayMotion(expirationSelect, "is-picked", 340)',
        'replayMotion(projectExpirySelect, "is-picked", 340)',
        "updateProjectExpirationPreview({ animate: true })",
        'setGeneratorMode("existing", { animate: true })',
        'setGeneratorMode("new", { animate: true })',
    ):
        assert token in DASHBOARD_JS, token


def test_remaining_platform_selects_replay_the_shared_pick_animation():
    # A delegated listener keeps filters, the type builder and settings
    # selects animated without double-firing the four wired generator ones.
    assert "replayHandledIds" in DASHBOARD_JS
    assert 'target instanceof HTMLSelectElement' in DASHBOARD_JS
    assert 'replayMotion(target, "is-picked", 340)' in DASHBOARD_JS
    assert '"gen-expiration-select"' in DASHBOARD_JS


# ---------------------------------------------------------------------------
# Interaction states that must stay animated
# ---------------------------------------------------------------------------


def test_buttons_and_tabs_keep_hover_active_and_loading_motion():
    for token in (
        ".btn:active",
        ".btn.is-busy",
        "@keyframes button-energy",
        ".segmented-btn:hover",
        ".segmented-btn.is-active",
        ".preset-chip:hover",
        ".preset-chip:focus-visible",
        ".ops-tab:hover",
        "@keyframes spin",
    ):
        assert token in CSS, token


def test_overlays_keep_entrance_exit_and_tooltip_motion():
    for token in (
        ".modal[open] .modal-card",
        "@keyframes modal-in",
        "@keyframes modal-out",
        "@keyframes toast-in",
        "@keyframes toast-out",
        ".tooltip-pop",
        ".tooltip-pop.is-visible",
    ):
        assert token in CSS, token


def test_nav_cards_tables_and_mobile_drawer_keep_motion():
    for token in (
        ".nav-item:hover",
        ".nav-item.is-active::before",
        "@keyframes nav-item-in",
        ".stat-card:hover",
        ".data-table tbody tr {",
        ".responsive-table tbody tr:hover",
        ".sidebar.is-open",
        ".mobile-nav-toggle[aria-expanded=\"true\"]",
        "@keyframes card-rise",
        "@keyframes fade-up",
    ):
        assert token in CSS, token


# ---------------------------------------------------------------------------
# Accessibility: reduced motion and keyboard focus
# ---------------------------------------------------------------------------


def test_reduced_motion_shortens_decorative_animations_preserves_interactive_transitions():
    # The global reduced-motion block disables decorative/ambient animations
    # but PRESERVES interactive transitions (hover, focus, active) for accessibility.
    assert "animation-duration: 0.001ms !important" in CSS
    assert "animation-iteration-count: 1 !important" in CSS
    assert "scroll-behavior: auto" in CSS  # Without !important is fine

    # Verify interactive elements keep their transitions in reduced-motion
    # Check that the reduced-motion block has explicit rules keeping transitions
    assert "transition-duration: var(--motion-fast) !important" in CSS
    # Verify transform animations are disabled on hover for reduced motion
    assert "transform: none !important" in CSS


def test_lite_mode_disables_ambient_loops():
    assert "html.is-lite" in CSS
    assert "html.is-paused" in CSS
    assert "animation-play-state: paused !important" in CSS


def test_focus_visible_indicators_are_preserved():
    assert ":focus-visible {" in CSS
    assert ".segmented-btn:focus-visible" in CSS
    assert ".preset-chip:focus-visible" in CSS
    assert ".ops-tab:focus-visible" in CSS
