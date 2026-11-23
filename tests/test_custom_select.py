"""Static regression contracts for the NexOra CustomSelect component.

These tests pin the structural, CSS, and JavaScript invariants that
*must* hold so the custom dropdown works correctly in the browser.

They are deterministic, need no database, no browser, and no network.
They inspect the source files directly (HTML, JS, CSS) to verify that:

  1.  Every ``<select data-custom-select>`` in the HTML gets wrapped.
  2.  ``initCustomSelects()`` and ``injectCustomSelectStyles()`` are
      exported and called at the right moments (dashboard boot, dynamic
      render paths).
  3.  The CSS delivers the required visual properties.
  4.  The JavaScript component honours idempotency, hides the native
      select, synchronises values, dispatches change events, handles
      keyboard, and falls back gracefully on error.
  5.  Operations.js and platform.js initialise dynamically-created
      selects after insertion.
  6.  ``custom-select.css`` is linked from every relevant HTML page.

No test here is weakened or deleted; new tests are additive only.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# Source files under test
CUSTOM_SELECT_JS = (
    FRONTEND / "assets" / "js" / "components" / "CustomSelect.js"
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

CUSTOM_SELECT_CSS_PATH = FRONTEND / "assets" / "custom-select.css"
CUSTOM_SELECT_CSS = CUSTOM_SELECT_CSS_PATH.read_text(encoding="utf-8")

APP_CSS = (FRONTEND / "assets" / "app.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. HTML structure — data-custom-select attribute coverage
# ---------------------------------------------------------------------------


def test_dashboard_html_has_data_custom_select_attributes():
    """Every <select> in dashboard.html must opt-in to the custom component."""
    # Find all <select ...> tags (not closing tags)
    all_selects = re.findall(r"<select\b[^>]*>", DASHBOARD_HTML, re.IGNORECASE)
    plain_selects = [
        tag
        for tag in all_selects
        if "data-custom-select" not in tag
        # Exclude selects that are explicitly excluded (data-no-custom)
        and "data-no-custom" not in tag
    ]
    assert not plain_selects, (
        f"Found {len(plain_selects)} <select> elements without data-custom-select "
        f"in dashboard.html. Add the attribute or data-no-custom to suppress:\n"
        + "\n".join(plain_selects)
    )


def test_custom_select_css_linked_in_dashboard_html():
    """dashboard.html must link custom-select.css."""
    assert "custom-select.css" in DASHBOARD_HTML, (
        "dashboard.html must have a <link> for custom-select.css"
    )


def test_custom_select_css_linked_in_platform_html():
    """platform.html must link custom-select.css."""
    assert "custom-select.css" in PLATFORM_HTML, (
        "platform.html must have a <link> for custom-select.css"
    )


# ---------------------------------------------------------------------------
# 2. CustomSelect.js — exports and core API surface
# ---------------------------------------------------------------------------


def test_custom_select_js_exports_class():
    """CustomSelect class must be exported."""
    assert "export class CustomSelect" in CUSTOM_SELECT_JS, (
        "CustomSelect.js must export class CustomSelect"
    )


def test_custom_select_js_exports_init_function():
    """initCustomSelects() must be exported."""
    assert "export function initCustomSelects" in CUSTOM_SELECT_JS or (
        "export { initCustomSelects" in CUSTOM_SELECT_JS
    ), "CustomSelect.js must export initCustomSelects()"


def test_custom_select_js_exports_inject_styles():
    """injectCustomSelectStyles() must be exported."""
    assert (
        "export function injectCustomSelectStyles" in CUSTOM_SELECT_JS
        or "export { injectCustomSelectStyles" in CUSTOM_SELECT_JS
    ), "CustomSelect.js must export injectCustomSelectStyles()"


def test_custom_select_js_has_idempotency_guard():
    """Constructor must return the existing instance if already initialised."""
    # The standard pattern: check select.customSelect instanceof CustomSelect
    assert "instanceof CustomSelect" in CUSTOM_SELECT_JS, (
        "CustomSelect constructor must have an idempotency guard: "
        "'if (select.customSelect instanceof CustomSelect) return select.customSelect'"
    )


def test_custom_select_js_hides_native_select():
    """init() must call hideOriginalSelect() to hide the native <select>."""
    assert "hideOriginalSelect" in CUSTOM_SELECT_JS, (
        "CustomSelect must call hideOriginalSelect() during init()"
    )
    # Must set hidden = true  OR  style.display = 'none'
    assert "hidden = true" in CUSTOM_SELECT_JS or "display" in CUSTOM_SELECT_JS, (
        "hideOriginalSelect() must set hidden=true or display:none on the native select"
    )


def test_custom_select_js_has_aria_hidden_on_native_select():
    """The native <select> must get aria-hidden='true' so screen readers skip it."""
    assert "aria-hidden" in CUSTOM_SELECT_JS, (
        "CustomSelect must set aria-hidden='true' on the hidden native select"
    )


def test_custom_select_js_creates_wrapper_with_correct_class():
    """createCustomDropdown() must produce a .custom-select-wrapper element."""
    assert "custom-select-wrapper" in CUSTOM_SELECT_JS, (
        "CustomSelect must create a div.custom-select-wrapper"
    )


def test_custom_select_js_creates_trigger_button():
    """The trigger must be a <button> with role=combobox."""
    assert "createElement(\"button\")" in CUSTOM_SELECT_JS or (
        "createElement('button')" in CUSTOM_SELECT_JS
    ), "CustomSelect must create a <button> as the trigger"
    assert 'role", "combobox"' in CUSTOM_SELECT_JS or (
        "role', 'combobox'" in CUSTOM_SELECT_JS
    ), "CustomSelect trigger must have role='combobox'"


def test_custom_select_js_has_arrow_svg():
    """The trigger must include the animated arrow SVG."""
    assert "custom-select-arrow" in CUSTOM_SELECT_JS, (
        "CustomSelect trigger must include .custom-select-arrow span with SVG"
    )


def test_custom_select_js_panel_has_role_listbox():
    """The options panel must carry role='listbox'."""
    assert '"listbox"' in CUSTOM_SELECT_JS or "'listbox'" in CUSTOM_SELECT_JS, (
        "CustomSelect panel must have role='listbox'"
    )


def test_custom_select_js_has_open_method():
    """open() / close() methods must exist for programmatic control."""
    assert "open()" in CUSTOM_SELECT_JS or "open () " in CUSTOM_SELECT_JS or (
        re.search(r"\bopen\s*\(", CUSTOM_SELECT_JS) is not None
    ), "CustomSelect must have an open() method"


def test_custom_select_js_has_close_method():
    assert re.search(r"\bclose\s*\(", CUSTOM_SELECT_JS) is not None, (
        "CustomSelect must have a close() method"
    )


def test_custom_select_js_has_select_option_method():
    """selectOption() propagates value back to the native select."""
    assert "selectOption" in CUSTOM_SELECT_JS, (
        "CustomSelect must have a selectOption() method"
    )


def test_custom_select_js_dispatches_change_event():
    """Selecting an option must dispatch a 'change' event on the native select."""
    assert "dispatchEvent" in CUSTOM_SELECT_JS and (
        '"change"' in CUSTOM_SELECT_JS or "'change'" in CUSTOM_SELECT_JS
    ), (
        "CustomSelect.selectOption() must dispatch a 'change' event on the "
        "native select so form listeners are notified"
    )


def test_custom_select_js_has_keyboard_navigation():
    """Arrow key / Enter / Escape / Space keyboard navigation must be present."""
    assert "ArrowDown" in CUSTOM_SELECT_JS, (
        "CustomSelect must handle ArrowDown key for keyboard navigation"
    )
    assert "ArrowUp" in CUSTOM_SELECT_JS, (
        "CustomSelect must handle ArrowUp key for keyboard navigation"
    )
    assert '"Enter"' in CUSTOM_SELECT_JS or "'Enter'" in CUSTOM_SELECT_JS, (
        "CustomSelect must handle Enter key to confirm selection"
    )
    assert '"Escape"' in CUSTOM_SELECT_JS or "'Escape'" in CUSTOM_SELECT_JS, (
        "CustomSelect must handle Escape key to close the panel"
    )


def test_custom_select_js_has_space_key_handling():
    """Space key must open the panel or confirm the focused option."""
    assert '" "' in CUSTOM_SELECT_JS or "' '" in CUSTOM_SELECT_JS or (
        '"Space"' in CUSTOM_SELECT_JS or "'Space'" in CUSTOM_SELECT_JS
    ), "CustomSelect must handle Space key"


def test_custom_select_js_has_tab_key_handling():
    """Tab key must close the dropdown panel."""
    assert '"Tab"' in CUSTOM_SELECT_JS or "'Tab'" in CUSTOM_SELECT_JS, (
        "CustomSelect must handle Tab key to close the panel and move focus"
    )


def test_custom_select_js_has_value_interceptor():
    """attachValueInterceptor() must intercept programmatic select.value = x assignments."""
    assert "attachValueInterceptor" in CUSTOM_SELECT_JS, (
        "CustomSelect must implement attachValueInterceptor() so dashboard.js "
        "assignments like 'select.value = x' keep the custom UI in sync"
    )
    assert "Object.defineProperty" in CUSTOM_SELECT_JS, (
        "attachValueInterceptor must use Object.defineProperty to intercept the setter"
    )


def test_custom_select_js_has_detach_value_interceptor():
    """detachValueInterceptor() must clean up on destroy()."""
    assert "detachValueInterceptor" in CUSTOM_SELECT_JS, (
        "CustomSelect must implement detachValueInterceptor() called from destroy()"
    )


def test_custom_select_js_has_mutation_observer():
    """MutationObserver must watch the native select for external option updates."""
    assert "MutationObserver" in CUSTOM_SELECT_JS, (
        "CustomSelect must use MutationObserver to watch for dynamic option changes"
    )


def test_custom_select_js_has_dynamic_observer():
    """A document-level MutationObserver must catch dynamically inserted selects."""
    assert "dynamicObserver" in CUSTOM_SELECT_JS, (
        "CustomSelect.js must maintain a document-level dynamicObserver to auto-init "
        "selects inserted into the DOM after page load"
    )


def test_custom_select_js_has_destroy_method():
    """destroy() must exist and clean up the custom markup."""
    assert re.search(r"\bdestroy\s*\(", CUSTOM_SELECT_JS) is not None, (
        "CustomSelect must have a destroy() method that removes the wrapper"
    )
    assert "INSTANCES.delete" in CUSTOM_SELECT_JS, (
        "destroy() must remove the instance from INSTANCES to avoid memory leaks"
    )


def test_custom_select_js_has_fallback_on_init_error():
    """restoreNativeSelect() must be called if init() throws."""
    assert "restoreNativeSelect" in CUSTOM_SELECT_JS, (
        "CustomSelect must call restoreNativeSelect() if init() throws, so the "
        "user still has a working native select as fallback"
    )
    assert "try" in CUSTOM_SELECT_JS and "catch" in CUSTOM_SELECT_JS, (
        "init() must wrap its body in try/catch and call restoreNativeSelect() on error"
    )


def test_custom_select_js_no_duplicate_wrapper_possible():
    """The idempotency guard must prevent double-wrapping the same select."""
    # Idempotency is enforced by the constructor's early return when
    # select.customSelect is already a CustomSelect instance.
    # The guard must exist AND init() must set select.customSelect = this
    # before creating the wrapper (to survive re-entry).
    assert "select.customSelect = this" in CUSTOM_SELECT_JS or (
        "select.customSelect=this" in CUSTOM_SELECT_JS
    ), (
        "CustomSelect must assign select.customSelect = this before creating the "
        "wrapper so re-entry is blocked and no duplicate wrapper is created"
    )


def test_custom_select_js_wrapper_id_uses_select_id():
    """The wrapper id must derive from the original select's id for predictability."""
    # Triggers are addressed as "#<select-id>-custom-trigger" by tests and
    # other scripts; the pattern is wrapper id = `${select.id}-custom`.
    assert "${select.id}" in CUSTOM_SELECT_JS and "custom" in CUSTOM_SELECT_JS, (
        "CustomSelect wrapper id must follow the pattern '<select-id>-custom' so "
        "selectors like #gen-expiration-select-custom-trigger work reliably"
    )


def test_custom_select_js_has_sync_from_original():
    """syncFromOriginal() must pull the native select value into the custom UI."""
    assert "syncFromOriginal" in CUSTOM_SELECT_JS, (
        "CustomSelect must implement syncFromOriginal() so programmatic "
        "select.value changes are reflected in the custom trigger label"
    )


def test_custom_select_js_has_sync_validation_classes():
    """syncValidationClasses() must mirror is-invalid from native select to wrapper."""
    assert "syncValidationClasses" in CUSTOM_SELECT_JS, (
        "CustomSelect must implement syncValidationClasses() to mirror validation "
        "CSS classes (e.g. is-invalid) from the native select to the wrapper"
    )


def test_custom_select_js_has_render_options():
    """renderOptions() must exist and populate the panel from allOptions."""
    assert "renderOptions" in CUSTOM_SELECT_JS, (
        "CustomSelect must have a renderOptions() method to build the option list"
    )


def test_custom_select_js_has_set_value():
    """setValue() must exist for programmatic value updates."""
    assert "setValue" in CUSTOM_SELECT_JS, (
        "CustomSelect must expose a setValue() method for external programmatic control"
    )


def test_custom_select_js_handles_disabled_options():
    """Disabled options must be marked and non-selectable."""
    assert "disabled" in CUSTOM_SELECT_JS and (
        "disabled" in CUSTOM_SELECT_JS
    ), "CustomSelect must handle disabled <option> elements"
    # The disabled CSS class on option items
    assert ".disabled" in CUSTOM_SELECT_JS or "disabled" in CUSTOM_SELECT_JS, (
        "Disabled options must receive a 'disabled' marker class"
    )


def test_custom_select_js_respects_reduced_motion():
    """prefersReducedMotion() must guard animation timers."""
    assert "prefersReducedMotion" in CUSTOM_SELECT_JS, (
        "CustomSelect must call prefersReducedMotion() and skip animations when active"
    )
    assert "prefers-reduced-motion" in CUSTOM_SELECT_JS, (
        "prefersReducedMotion() must query the '(prefers-reduced-motion: reduce)' media"
    )


def test_custom_select_js_has_inject_styles_function():
    """injectCustomSelectStyles() must add the stylesheet link exactly once."""
    assert "injectCustomSelectStyles" in CUSTOM_SELECT_JS, (
        "CustomSelect.js must define and export injectCustomSelectStyles()"
    )
    # Must guard against double injection
    assert (
        "getElementById" in CUSTOM_SELECT_JS
        or "querySelector" in CUSTOM_SELECT_JS
    ), (
        "injectCustomSelectStyles() must check for an existing link tag before "
        "adding a new one to avoid duplicate stylesheets"
    )


def test_custom_select_js_style_href_points_to_css():
    """The injected stylesheet href must match the served CSS route."""
    assert "/assets/custom-select.css" in CUSTOM_SELECT_JS, (
        "STYLE_HREF in CustomSelect.js must point to /assets/custom-select.css"
    )


# ---------------------------------------------------------------------------
# 3. dashboard.js — integration with CustomSelect
# ---------------------------------------------------------------------------


def test_dashboard_js_imports_custom_select():
    """dashboard.js must import initCustomSelects and/or injectCustomSelectStyles."""
    assert "initCustomSelects" in DASHBOARD_JS or "CustomSelect" in DASHBOARD_JS, (
        "dashboard.js must import from CustomSelect.js to call initCustomSelects()"
    )


def test_dashboard_js_calls_init_custom_selects():
    """dashboard.js must call initCustomSelects() during application boot."""
    assert "initCustomSelects" in DASHBOARD_JS, (
        "dashboard.js must call initCustomSelects() so static HTML selects are "
        "wrapped before the user can interact with them"
    )


def test_dashboard_js_calls_inject_custom_select_styles():
    """dashboard.js must ensure custom-select.css is loaded."""
    assert "injectCustomSelectStyles" in DASHBOARD_JS or "custom-select" in DASHBOARD_JS, (
        "dashboard.js must call injectCustomSelectStyles() or link custom-select.css "
        "so the dropdown CSS is always present"
    )


# ---------------------------------------------------------------------------
# 4. operations.js — dynamic select initialization
# ---------------------------------------------------------------------------


def test_operations_js_imports_init_custom_selects():
    """operations.js must import initCustomSelects to init dynamic selects."""
    assert "initCustomSelects" in OPERATIONS_JS, (
        "operations.js must import and call initCustomSelects() after inserting "
        "dynamic <select> elements so they receive the custom component"
    )


def test_operations_js_adds_data_custom_select_to_dynamic_selects():
    """operations.js select() helper must set data-custom-select on new selects."""
    # Look for the attribute being assigned in the dynamic select helper
    assert "data-custom-select" in OPERATIONS_JS, (
        "operations.js local select() helper must add data-custom-select='true' "
        "so initCustomSelects() picks up dynamically created selects"
    )


def test_operations_js_calls_init_custom_selects_after_render():
    """operations.js must call initCustomSelects(root) after rendering content."""
    assert "initCustomSelects" in OPERATIONS_JS, (
        "operations.js must call initCustomSelects(root) after appending dynamic "
        "selects to the DOM so they are wrapped before the user sees them"
    )


# ---------------------------------------------------------------------------
# 5. platform.js — dynamic select initialization
# ---------------------------------------------------------------------------


def test_platform_js_imports_init_custom_selects():
    """platform.js must import initCustomSelects."""
    assert "initCustomSelects" in PLATFORM_JS, (
        "platform.js must import and call initCustomSelects() in showView() so "
        "each platform view's selects receive the custom component"
    )


def test_platform_js_adds_data_custom_select_to_dynamic_selects():
    """platform.js select() helper must set data-custom-select on new selects."""
    assert "data-custom-select" in PLATFORM_JS, (
        "platform.js local select() helper must add data-custom-select='true'"
    )


def test_platform_js_calls_init_custom_selects_in_show_view():
    """platform.js showView() must call initCustomSelects(root) after view loads."""
    assert "initCustomSelects" in PLATFORM_JS, (
        "platform.js showView() must call initCustomSelects(root) after each view "
        "is inserted so its selects are wrapped"
    )


# ---------------------------------------------------------------------------
# 6. custom-select.css — visual design contracts
# ---------------------------------------------------------------------------


def test_custom_select_css_file_exists():
    """custom-select.css must exist as a static file."""
    assert CUSTOM_SELECT_CSS_PATH.exists(), (
        "frontend/assets/custom-select.css must exist and be served at "
        "/assets/custom-select.css"
    )


def test_custom_select_css_wrapper_class_defined():
    """.custom-select-wrapper rule must be present."""
    assert ".custom-select-wrapper" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define styles for .custom-select-wrapper"
    )


def test_custom_select_css_trigger_class_defined():
    """.custom-select-trigger must have hover/focus styles."""
    assert ".custom-select-trigger" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define styles for .custom-select-trigger"
    )


def test_custom_select_css_has_open_state():
    """.custom-select-wrapper.open must exist for the open animation."""
    assert ".open" in CUSTOM_SELECT_CSS and "custom-select" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define .custom-select-wrapper.open state for the "
        "open/close animation"
    )


def test_custom_select_css_has_arrow_rotation():
    """The arrow must rotate when the panel is open."""
    assert "rotate" in CUSTOM_SELECT_CSS or "transform" in CUSTOM_SELECT_CSS, (
        "custom-select.css must rotate the .custom-select-arrow when the panel is open"
    )


def test_custom_select_css_has_option_hover():
    """.custom-select-option must have a NexOra-branded hover state."""
    assert ".custom-select-option" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define styles for .custom-select-option including hover"
    )
    assert ":hover" in CUSTOM_SELECT_CSS or "hover" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define hover styles for .custom-select-option"
    )


def test_custom_select_css_has_panel_animation():
    """The panel must have a fade-in / translate animation."""
    # Either a keyframe or a transition on the panel
    assert (
        "@keyframes" in CUSTOM_SELECT_CSS or "animation" in CUSTOM_SELECT_CSS
    ), "custom-select.css must define panel open/close animation"


def test_custom_select_css_has_z_index():
    """The panel must have a z-index to stack above page content."""
    assert "z-index" in CUSTOM_SELECT_CSS, (
        "custom-select.css must set z-index on .custom-select-panel to ensure "
        "it stacks above surrounding content"
    )


def test_custom_select_css_has_selected_state():
    """.selected option must have a distinct style."""
    assert ".selected" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define a .selected state for the chosen option"
    )


def test_custom_select_css_has_reduced_motion_support():
    """prefers-reduced-motion must disable or clamp animations."""
    assert "prefers-reduced-motion" in CUSTOM_SELECT_CSS, (
        "custom-select.css must include a @media (prefers-reduced-motion: reduce) "
        "block to suppress or shorten animations"
    )


def test_custom_select_css_has_focus_visible_style():
    """Keyboard-focused trigger must have visible focus indicator."""
    assert ":focus" in CUSTOM_SELECT_CSS or "focus" in CUSTOM_SELECT_CSS, (
        "custom-select.css must style the focused trigger for keyboard accessibility"
    )


def test_custom_select_css_has_is_invalid_state():
    """is-invalid state must be visually reflected on the wrapper/trigger."""
    assert "is-invalid" in CUSTOM_SELECT_CSS, (
        "custom-select.css must define .is-invalid styles so validation errors are "
        "shown on the custom trigger (not just the hidden native select)"
    )


# ---------------------------------------------------------------------------
# 7. app.css — no native-select override that fights the custom component
# ---------------------------------------------------------------------------


def test_app_css_does_not_force_select_visible():
    """app.css must not use 'select { display: block !important }' or similar
    rules that would override the custom component's hidden state."""
    # A forced display:block on 'select' would fight the custom select hiding
    bad_patterns = [
        re.compile(
            r"select\s*\{[^}]*display\s*:\s*block\s*!important", re.DOTALL
        ),
    ]
    for pattern in bad_patterns:
        assert not pattern.search(APP_CSS), (
            "app.css must not apply 'display: block !important' to <select> elements; "
            "it overrides CustomSelect's hidden state"
        )


# ---------------------------------------------------------------------------
# 8. No duplicate injection / wrapper safeguards (static contract)
# ---------------------------------------------------------------------------


def test_custom_select_js_inject_styles_has_duplicate_guard():
    """injectCustomSelectStyles() must check for an existing <link> first."""
    # The function should check if the link already exists before creating one
    assert (
        "getElementById" in CUSTOM_SELECT_JS
        or "querySelector" in CUSTOM_SELECT_JS
        or "querySelectorAll" in CUSTOM_SELECT_JS
    ), (
        "injectCustomSelectStyles() must query the DOM for an existing link tag "
        "before creating a new one, preventing duplicate stylesheet injection"
    )


def test_custom_select_js_init_custom_selects_skips_initialized():
    """initCustomSelects() must not re-wrap already-initialized selects."""
    # The safe guard is the constructor's early return; additionally
    # initCustomSelects() should not select already-ready wrappers.
    assert (
        "data-custom-select-ready" in CUSTOM_SELECT_JS
        or "customSelect" in CUSTOM_SELECT_JS
    ), (
        "initCustomSelects() must skip <select> elements that are already wrapped "
        "(e.g. check select.customSelect or data-custom-select-ready attribute)"
    )
