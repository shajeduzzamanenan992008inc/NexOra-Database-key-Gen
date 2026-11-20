"""Static responsive and one-time-key display contracts for the admin UI.

These checks are intentionally deterministic and do not need a database: they
guard the CSS/JS invariants that keep every requested viewport free of page
level horizontal overflow and keep the generated Reader Key fully readable.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
CSS = (FRONTEND / "assets" / "app.css").read_text(encoding="utf-8")
LOGIN = (FRONTEND / "index.html").read_text(encoding="utf-8")
DASHBOARD = (FRONTEND / "dashboard.html").read_text(encoding="utf-8")
DASHBOARD_JS = (FRONTEND / "assets" / "js" / "dashboard.js").read_text(encoding="utf-8")

REQUESTED_WIDTHS = (320, 360, 375, 390, 430, 480, 768, 1024, 1280, 1440, 1920)

# The widest media query tier that still applies at each requested viewport.
EXPECTED_TIER = {
    320: 380,
    360: 380,
    375: 380,
    390: 430,
    430: 430,
    480: 720,
    768: 980,
    1024: 1120,
    1280: 1360,
    1440: None,
    1920: None,
}


def media_max_widths() -> set[int]:
    return {int(value) for value in re.findall(r"@media \(max-width: (\d+)px\)", CSS)}


def test_every_requested_viewport_has_a_responsive_tier():
    available = media_max_widths()
    for width in REQUESTED_WIDTHS:
        expected = EXPECTED_TIER[width]
        if expected is None:
            # Wide desktop widths are served by the fluid base layout.
            assert width > max(available)
            continue
        assert expected in available, (width, expected, sorted(available))
        assert expected >= min(w for w in available if w >= width)


def test_page_level_overflow_is_suppressed_while_content_wraps():
    assert "body {" in CSS
    assert "overflow-x: hidden" in CSS
    assert "max-width: 100%" in CSS
    # Long project names, emails, errors and IDs must break inside containers.
    assert "overflow-wrap: anywhere" in CSS
    assert "min-width: 0" in CSS
    assert ".main" in CSS


def test_mobile_navigation_uses_an_off_canvas_drawer():
    for element_id in ("sidebar", "sidebar-close", "sidebar-backdrop", "mobile-nav-toggle"):
        assert f'id="{element_id}"' in DASHBOARD
    assert ".sidebar.is-open" in CSS
    assert "body.nav-open { overflow: hidden; }" in CSS
    assert "aria-expanded" in DASHBOARD_JS
    assert 'aria-controls="sidebar"' in DASHBOARD
    assert 'aria-expanded="false"' in DASHBOARD


def test_tables_switch_to_cards_at_the_table_breakpoint():
    assert "responsive-table" in DASHBOARD
    assert "@media (max-width: 1180px)" in CSS
    assert "attr(data-label)" in CSS
    assert "data-label" in DASHBOARD_JS
    assert DASHBOARD.count("responsive-table") >= 5


def test_one_time_key_is_segmented_and_never_ellipsized():
    assert "buildKeySegments" in DASHBOARD_JS
    assert "key-segment" in DASHBOARD_JS
    assert "plaintext_key" in DASHBOARD_JS
    for rule in (".key-plaintext {", ".key-segment {"):
        assert rule in CSS
    key_block = CSS.split(".key-plaintext {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in key_block
    assert "max-width: 100%" in key_block
    assert "text-overflow: ellipsis" not in key_block
    assert "white-space: nowrap" not in key_block
    assert "Copy key" in DASHBOARD_JS
    assert "state.generatedKeys" in DASHBOARD_JS
    assert "purgeGeneratedKeys" in DASHBOARD_JS
    assert "addEventListener" in DASHBOARD_JS


def test_result_modal_purges_plaintext_when_closed():
    assert 'id="result-modal"' in DASHBOARD
    assert 'id="generated-list"' in DASHBOARD
    assert 'id="copy-all"' in DASHBOARD
    assert 'id="download-keys-csv"' in DASHBOARD
    assert 'resultModal.addEventListener("close", purgeGeneratedKeys)' in DASHBOARD_JS
    assert "pagehide" in DASHBOARD_JS


def test_button_shimmer_cannot_extend_horizontal_scroll():
    # The decorative ::before sweep translates +65% on hover. Without clipping,
    # a full-width mobile button widens the document by ~65% of its own width.
    btn_block = CSS.split(".btn {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in btn_block
    assert ".btn:hover:not(:disabled)::before" in CSS
    assert "transform: translateX(65%)" in CSS


def test_modals_are_viewport_bounded_and_scrollable():
    assert ".modal {" in CSS
    modal_block = CSS.split(".modal {", 1)[1].split("}", 1)[0]
    assert "max-width" in modal_block
    assert "max-height" in modal_block
    assert "overflow" in modal_block
    assert "@media (max-width: 980px)" in CSS
    assert ".modal { width: calc(100vw - 24px); }" in CSS


def test_reduced_motion_and_keyboard_accessibility_are_preserved():
    assert "prefers-reduced-motion" in CSS
    assert ":focus-visible" in CSS
    assert ".sr-only" in CSS
    assert 'id="live-region"' in DASHBOARD
    assert 'role="status"' in DASHBOARD
    assert 'class="skip-link"' in DASHBOARD
    assert 'event.key === "Escape"' in DASHBOARD_JS


def test_login_and_dashboard_shells_are_viewport_ready():
    for page in (LOGIN, DASHBOARD):
        assert 'name="viewport"' in page
        assert "width=device-width" in page
        assert "viewport-fit=cover" in page
    assert "boot-screen" in LOGIN and "boot-screen" in DASHBOARD
    assert "leave-screen" in LOGIN and "leave-screen" in DASHBOARD
    assert "ambient-scene" in LOGIN and "ambient-scene" in DASHBOARD
    assert "toast-stack" in (FRONTEND / "assets" / "js" / "ui.js").read_text(encoding="utf-8")


def test_low_power_profile_and_visibility_pause_exist():
    motion = (FRONTEND / "assets" / "js" / "motion.js").read_text(encoding="utf-8")
    assert "html.is-lite" in CSS
    assert "html.is-paused" in CSS
    assert "pointer: coarse" in CSS
    assert "prefers-contrast: more" in CSS
    assert "saveData" in motion
    assert "deviceMemory" in motion
    assert "hardwareConcurrency" in motion
    assert "setLiteMode" in motion
    assert 'classList.toggle("is-paused"' in motion
    assert 'id="setting-lite-mode"' in DASHBOARD
    assert 'id="motion-profile"' in DASHBOARD
    assert "nexora:litechange" in DASHBOARD_JS


def test_tooltips_are_viewport_clamped_and_accessible():
    ui = (FRONTEND / "assets" / "js" / "ui.js").read_text(encoding="utf-8")
    assert ".tooltip-pop" in CSS
    assert "opacity: 0" in CSS.split(".tooltip-pop {", 1)[1].split("}", 1)[0]
    assert 'role: "tooltip"' in ui
    assert "data-tip" in ui
    assert 'trigger.getAttribute("data-tip")' in ui
    assert "window.innerWidth" in ui
    assert "Math.min" in ui and "Math.max" in ui
    assert "aria-describedby" in ui
    assert 'tabindex="0"' in DASHBOARD
    assert "data-tip=" in DASHBOARD


def test_key_reveal_is_tap_selectable_without_truncation():
    key_block = CSS.split(".key-plaintext {", 1)[1].split("}", 1)[0]
    assert "user-select: all" in key_block
    assert "overflow-wrap: anywhere" in key_block


def test_project_detail_lists_each_project_key():
    assert "renderProjectKeyList" in DASHBOARD_JS
    assert "Reader Keys for this Project" in DASHBOARD_JS
    assert "project_id: project.id" in DASHBOARD_JS
    assert "onDone: () => renderProjectKeyList" in DASHBOARD_JS


def test_generated_key_result_states_expiration_setting_and_date():
    assert "Expiration setting: " in DASHBOARD_JS
    assert "Resulting expiration: " in DASHBOARD_JS
    assert "Never (explicit)" in DASHBOARD_JS


def test_table_rows_use_the_coordinated_entrance_animation():
    assert "@keyframes row-in" in CSS
    assert "animation: row-in 0.34s" in CSS
    assert "animateTableRows" in DASHBOARD_JS


def test_expired_status_badge_has_a_gentle_expiration_animation():
    assert "@keyframes expiry-breathe" in CSS
    assert '.badge[data-status="expired"]' in CSS
    # Lite mode must switch the loop off for low-power hardware.
    assert 'html.is-lite .badge[data-status="expired"]' in CSS
