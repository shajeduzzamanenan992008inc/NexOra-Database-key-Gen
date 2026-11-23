"""Static regression contracts for the NexOra CustomDatePicker component.

These tests pin the structural, CSS, and JavaScript invariants that
ensure the custom calendar & time picker replace the browser-native
date/datetime picker with NexOra's cyberpunk design system.

They are deterministic and need no database, browser, or network.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

CUSTOM_DATE_PICKER_JS = (
    FRONTEND / "assets" / "js" / "components" / "CustomDatePicker.js"
).read_text(encoding="utf-8")

CUSTOM_DATE_PICKER_CSS = (
    FRONTEND / "assets" / "custom-date-picker.css"
).read_text(encoding="utf-8")

DASHBOARD_JS = (FRONTEND / "assets" / "js" / "dashboard.js").read_text(
    encoding="utf-8"
)
OPERATIONS_JS = (FRONTEND / "assets" / "js" / "operations.js").read_text(
    encoding="utf-8"
)
PLATFORM_JS = (FRONTEND / "assets" / "js" / "platform.js").read_text(
    encoding="utf-8"
)

DASHBOARD_HTML = (FRONTEND / "dashboard.html").read_text(encoding="utf-8")
PLATFORM_HTML = (FRONTEND / "platform.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. HTML structure and CSS link contracts
# ---------------------------------------------------------------------------


def test_dashboard_html_links_custom_date_picker_css():
    """dashboard.html must link custom-date-picker.css."""
    assert "custom-date-picker.css" in DASHBOARD_HTML, (
        "dashboard.html must have a <link> for custom-date-picker.css"
    )


def test_platform_html_links_custom_date_picker_css():
    """platform.html must link custom-date-picker.css."""
    assert "custom-date-picker.css" in PLATFORM_HTML, (
        "platform.html must have a <link> for custom-date-picker.css"
    )


def test_dashboard_html_has_data_custom_date_on_custom_expiration():
    """gen-expiration-custom input must opt-in via data-custom-date."""
    assert 'id="gen-expiration-custom"' in DASHBOARD_HTML
    assert 'data-custom-date' in DASHBOARD_HTML, (
        "gen-expiration-custom should carry data-custom-date attribute"
    )


# ---------------------------------------------------------------------------
# 2. CustomDatePicker.js — Exports and Core API Surface
# ---------------------------------------------------------------------------


def test_custom_date_picker_js_exports_class():
    """CustomDatePicker class must be exported."""
    assert "export class CustomDatePicker" in CUSTOM_DATE_PICKER_JS, (
        "CustomDatePicker.js must export class CustomDatePicker"
    )


def test_custom_date_picker_js_exports_init_function():
    """initCustomDatePickers() must be exported."""
    assert "export function initCustomDatePickers" in CUSTOM_DATE_PICKER_JS, (
        "CustomDatePicker.js must export initCustomDatePickers()"
    )


def test_custom_date_picker_js_exports_inject_styles():
    """injectCustomDatePickerStyles() must be exported."""
    assert "export function injectCustomDatePickerStyles" in CUSTOM_DATE_PICKER_JS, (
        "CustomDatePicker.js must export injectCustomDatePickerStyles()"
    )


def test_custom_date_picker_js_has_idempotency_guard():
    """Constructor must return existing instance if already initialised."""
    assert "instanceof CustomDatePicker" in CUSTOM_DATE_PICKER_JS, (
        "CustomDatePicker constructor must have an idempotency guard"
    )


def test_custom_date_picker_js_hides_native_input():
    """hideOriginalInput() must hide the native input with display: none !important."""
    assert "hideOriginalInput" in CUSTOM_DATE_PICKER_JS
    assert "display" in CUSTOM_DATE_PICKER_JS and "important" in CUSTOM_DATE_PICKER_JS, (
        "hideOriginalInput must set display: none !important on the native input"
    )


def test_custom_date_picker_js_creates_wrapper_and_trigger():
    """createDom() must create .custom-date-picker-wrapper and trigger button."""
    assert "custom-date-picker-wrapper" in CUSTOM_DATE_PICKER_JS
    assert "custom-date-picker-trigger" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_has_calendar_icon():
    """Trigger must contain a calendar SVG icon."""
    assert "custom-date-picker-icon" in CUSTOM_DATE_PICKER_JS
    assert "<svg" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_creates_panel():
    """Custom panel must carry role=dialog."""
    assert "custom-date-picker-panel" in CUSTOM_DATE_PICKER_JS
    assert 'role", "dialog"' in CUSTOM_DATE_PICKER_JS or "role', 'dialog'" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_has_time_picker():
    """CustomDatePicker must support time selection for datetime-local."""
    assert "cdp-time-picker" in CUSTOM_DATE_PICKER_JS
    assert "cdp-ampm-btn" in CUSTOM_DATE_PICKER_JS
    assert "cdp-time-chip" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_has_month_navigation():
    """Must support prevMonth() and nextMonth()."""
    assert "prevMonth" in CUSTOM_DATE_PICKER_JS
    assert "nextMonth" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_has_value_interceptor():
    """attachValueInterceptor() must intercept programmatic input.value assignments."""
    assert "attachValueInterceptor" in CUSTOM_DATE_PICKER_JS
    assert "Object.defineProperty" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_dispatches_input_and_change_events():
    """applyValue() / dispatchChange() must dispatch input and change events."""
    assert "dispatchChange" in CUSTOM_DATE_PICKER_JS
    assert '"input"' in CUSTOM_DATE_PICKER_JS or "'input'" in CUSTOM_DATE_PICKER_JS
    assert '"change"' in CUSTOM_DATE_PICKER_JS or "'change'" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_has_mutation_observer():
    """Must use MutationObserver for dynamic inputs and validation classes."""
    assert "MutationObserver" in CUSTOM_DATE_PICKER_JS
    assert "ensureDynamicDatePickerObserver" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_has_fallback_on_error():
    """restoreNativeInput() must recover the native input on error."""
    assert "restoreNativeInput" in CUSTOM_DATE_PICKER_JS
    assert "try" in CUSTOM_DATE_PICKER_JS and "catch" in CUSTOM_DATE_PICKER_JS


def test_custom_date_picker_js_respects_reduced_motion():
    """prefersReducedMotion() guard must be present."""
    assert "prefersReducedMotion" in CUSTOM_DATE_PICKER_JS
    assert "prefers-reduced-motion" in CUSTOM_DATE_PICKER_JS


# ---------------------------------------------------------------------------
# 3. custom-date-picker.css — Visual Design Contracts
# ---------------------------------------------------------------------------


def test_custom_date_picker_css_has_balanced_braces():
    """Stylesheet must have balanced curly braces."""
    assert CUSTOM_DATE_PICKER_CSS.count("{") == CUSTOM_DATE_PICKER_CSS.count("}"), (
        "custom-date-picker.css has unbalanced braces"
    )


def test_custom_date_picker_css_defines_wrapper_and_panel():
    """.custom-date-picker-wrapper and .custom-date-picker-panel must be defined."""
    assert ".custom-date-picker-wrapper" in CUSTOM_DATE_PICKER_CSS
    assert ".custom-date-picker-panel" in CUSTOM_DATE_PICKER_CSS


def test_custom_date_picker_css_has_open_state():
    """.open selector must transition panel opacity and transform."""
    assert ".custom-date-picker-wrapper.open" in CUSTOM_DATE_PICKER_CSS


def test_custom_date_picker_css_has_day_styling():
    """.cdp-day must have hover, today, and selected states."""
    assert ".cdp-day" in CUSTOM_DATE_PICKER_CSS
    assert ".cdp-day:hover" in CUSTOM_DATE_PICKER_CSS
    assert ".cdp-day.today" in CUSTOM_DATE_PICKER_CSS
    assert ".cdp-day.selected" in CUSTOM_DATE_PICKER_CSS


def test_custom_date_picker_css_has_time_picker_styling():
    """Time selector styles must be present."""
    assert ".cdp-time-picker" in CUSTOM_DATE_PICKER_CSS
    assert ".cdp-ampm-btn" in CUSTOM_DATE_PICKER_CSS
    assert ".cdp-time-chip" in CUSTOM_DATE_PICKER_CSS


def test_custom_date_picker_css_has_validation_error_state():
    """.is-invalid styling must be present."""
    assert "is-invalid" in CUSTOM_DATE_PICKER_CSS


def test_custom_date_picker_css_has_reduced_motion_guard():
    """prefers-reduced-motion media query must be present."""
    assert "prefers-reduced-motion" in CUSTOM_DATE_PICKER_CSS


# ---------------------------------------------------------------------------
# 4. Integration with dashboard.js, operations.js, platform.js
# ---------------------------------------------------------------------------


def test_dashboard_js_imports_custom_date_picker():
    """dashboard.js must import CustomDatePicker functions."""
    assert "initCustomDatePickers" in DASHBOARD_JS


def test_dashboard_js_calls_init_custom_date_pickers():
    """dashboard.js must call initCustomDatePickers() on boot."""
    assert "initCustomDatePickers()" in DASHBOARD_JS


def test_operations_js_calls_init_custom_date_pickers():
    """operations.js must call initCustomDatePickers(root)."""
    assert "initCustomDatePickers(root)" in OPERATIONS_JS


def test_platform_js_calls_init_custom_date_pickers():
    """platform.js must call initCustomDatePickers()."""
    assert "initCustomDatePickers" in PLATFORM_JS
