/**
 * CustomSelect - Fully stylable dropdown replacement for native <select>
 *
 * Supports hover effects, open/close animation, keyboard navigation,
 * optional search, multi-select, live option synchronisation and a safe
 * native-select fallback when initialisation fails.
 *
 * The component styles live in /assets/custom-select.css. They are linked
 * from the page (or by injectCustomSelectStyles) instead of being injected as
 * an inline <style> block so the strict Content-Security-Policy
 * (`style-src 'self'`) is respected.
 */

const STYLE_HREF = "/assets/custom-select.css";
const INSTANCES = new Set();
let dynamicObserver = null;
let instanceCounter = 0;

function isSelectElement(node) {
  return Boolean(node) && node.tagName === "SELECT";
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function nextFrame(callback) {
  if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(callback);
  } else {
    callback();
  }
}

export class CustomSelect {
  constructor(element, options = {}) {
    const select = isSelectElement(element)
      ? element
      : element && typeof element.querySelector === "function"
        ? element.querySelector("select")
        : null;

    if (!select) {
      throw new Error("CustomSelect requires a <select> element.");
    }

    // Returning the existing instance makes direct construction idempotent as
    // well as initCustomSelects().
    if (select.customSelect instanceof CustomSelect) {
      return select.customSelect;
    }

    this.element = element;
    this.select = select;
    this.originalSelect = select;
    this.multi = options.multi ?? Boolean(select.multiple);
    this.options = {
      searchable:
        options.searchable ??
        (select.hasAttribute("data-searchable") ||
          select.dataset.searchable === "true"),
      multi: this.multi,
      placeholder:
        options.placeholder ??
        select.getAttribute("data-placeholder") ??
        "Select...",
      animate: options.animate ?? true,
      duration: options.duration ?? 200,
      easing: options.easing ?? "cubic-bezier(0.22, 1, 0.36, 1)",
      onChange: options.onChange ?? (() => {}),
      ...options,
    };
    this.options.multi = this.multi;

    this.id = `custom-select-${++instanceCounter}`;
    this.isOpen = false;
    this.focusedIndex = -1;
    this.filteredOptions = [];
    this.allOptions = [];
    this.selectedValues = new Set();
    this.destroyed = false;
    this.panelTimer = 0;
    this.pickTimer = 0;
    this.typeAheadBuffer = "";
    this.typeAheadTimer = 0;
    this.layerNode = null;

    // Set the reference before the <select> is moved into the wrapper. The
    // document-level observer then recognises the move as already initialised.
    select.customSelect = this;

    this.init();
  }

  /* ------------------------------------------------------------- lifecycle */

  init() {
    try {
      injectCustomSelectStyles();
      this.extractOptions();
      this.readSelectionFromOriginal();
      this.createCustomDropdown();
      this.bindEvents();
      this.attachValueInterceptor();
      this.hideOriginalSelect();
      this.observeOriginalSelect();
      this.updateDisabledState();
      this.syncValidationClasses();
      this.updateSelectedDisplay();

      this.wrapper.customSelect = this;
      this.select.customSelect = this;
      this.wrapper.dataset.customSelectReady = "true";
      this.select.dataset.customSelectReady = "true";
      INSTANCES.add(this);
    } catch (error) {
      this.restoreNativeSelect(error);
    }
  }

  attachValueInterceptor() {
    if (this._hasValueInterceptor || !this.select) return;
    const proto = HTMLSelectElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (!descriptor) return;

    this._hasValueInterceptor = true;
    const self = this;
    Object.defineProperty(this.select, "value", {
      configurable: true,
      enumerable: true,
      get() {
        return descriptor.get.call(this);
      },
      set(val) {
        descriptor.set.call(this, val);
        self.syncFromOriginal();
      },
    });
  }

  detachValueInterceptor() {
    if (this._hasValueInterceptor && this.select) {
      delete this.select.value;
      this._hasValueInterceptor = false;
    }
  }

  restoreNativeSelect(error) {
    this.destroyed = true;

    if (this.panelTimer) window.clearTimeout(this.panelTimer);
    if (this.pickTimer) window.clearTimeout(this.pickTimer);
    if (this.typeAheadTimer) window.clearTimeout(this.typeAheadTimer);

    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    this.detachValueInterceptor();
    this.unbindEvents();
    this.unraise();

    // Restore the native control next to (or in place of) the partial custom
    // markup. The user gets a working native select instead of a dead control.
    if (this.wrapper) {
      if (this.wrapper.parentNode) {
        this.wrapper.parentNode.insertBefore(this.select, this.wrapper);
      } else if (this.select.parentNode === this.wrapper) {
        this.wrapper.removeChild(this.select);
      }
      this.wrapper.remove();
    }
    this.select.hidden = false;
    this.select.removeAttribute("aria-hidden");
    this.select.removeAttribute("data-custom-select-ready");
    this.select.tabIndex = this.originalTabIndex ?? 0;
    this.select.style.removeProperty("display");
    delete this.select.customSelect;
    if (this.wrapper) delete this.wrapper.customSelect;
    INSTANCES.delete(this);

    this.failed = true;
    this.select.dataset.customSelectError = "true";
    // Log loudly enough to diagnose it, but keep the native select usable.
    console.error("CustomSelect could not initialise; native select retained.", error);
  }

  /* ---------------------------------------------------------------- options */

  extractOptions() {
    this.allOptions = Array.from(this.select.options).map((option, index) => ({
      value: option.value,
      label: (option.label || option.textContent || "").trim() || option.textContent || "",
      selected: option.selected,
      disabled: option.disabled,
      index,
    }));
  }

  readSelectionFromOriginal() {
    this.selectedValues.clear();

    const selected = Array.from(this.select.selectedOptions || []);
    const limit = this.multi ? selected.length : Math.min(selected.length, 1);
    for (let index = 0; index < limit; index += 1) {
      this.selectedValues.add(selected[index].value);
    }

    // Do not invent a selection when the native control has none. Browsers
    // normally auto-select the first option of a single <select>, but a
    // rebuilt select can briefly be genuinely empty and that must show the
    // placeholder rather than a stale or made-up label.
  }

  createCustomDropdown() {
    const select = this.select;
    const wrapper = document.createElement("div");
    wrapper.className = "custom-select-wrapper";
    wrapper.dataset.customSelectWrapper = "";
    wrapper.id = select.id ? `${select.id}-custom` : `${this.id}-wrapper`;
    if (select.className) {
      wrapper.classList.add(...String(select.className).split(/\s+/).filter(Boolean));
    }

    const triggerLabel = this.resolveAccessibleLabel();
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "custom-select-trigger";
    trigger.id = `${wrapper.id}-trigger`;
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${wrapper.id}-panel`);
    if (triggerLabel) trigger.setAttribute("aria-label", triggerLabel);
    trigger.tabIndex = 0;
    trigger.innerHTML = `
      <span class="custom-select-value"></span>
      <span class="custom-select-arrow" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M4 6L8 10L12 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </span>
    `;

    const panel = document.createElement("div");
    panel.className = "custom-select-panel";
    panel.id = `${wrapper.id}-panel`;
    panel.setAttribute("role", "listbox");
    panel.setAttribute("aria-label", triggerLabel || "Options");
    panel.setAttribute("aria-hidden", "true");
    panel.hidden = true;

    let searchInput = null;
    if (this.options.searchable) {
      const searchWrapper = document.createElement("div");
      searchWrapper.className = "custom-select-search-wrapper";
      searchInput = document.createElement("input");
      searchInput.type = "search";
      searchInput.className = "custom-select-search";
      searchInput.placeholder = "Search options...";
      searchInput.setAttribute("aria-label", "Search options");
      searchWrapper.appendChild(searchInput);
      panel.appendChild(searchWrapper);
    }

    const list = document.createElement("ul");
    list.className = "custom-select-options";
    list.setAttribute("role", "presentation");
    panel.appendChild(list);

    wrapper.appendChild(trigger);
    wrapper.appendChild(panel);

    select.parentNode.insertBefore(wrapper, select);
    // Keeping the native select inside the wrapper makes destroy(), duplicate
    // initialisation checks and outside-click checks unambiguous.
    wrapper.appendChild(select);

    this.wrapper = wrapper;
    this.trigger = trigger;
    this.panel = panel;
    this.list = list;
    this.searchInput = searchInput;
    this.valueDisplay = trigger.querySelector(".custom-select-value");

    this.renderOptions();
  }

  resolveAccessibleLabel() {
    const explicit = this.select.getAttribute("aria-label");
    if (explicit) return explicit;

    const labels = this.select.labels ? Array.from(this.select.labels) : [];
    for (const label of labels) {
      const named = label.querySelector(".field-label, .sr-only");
      if (named && named.textContent.trim()) return named.textContent.trim();
    }
    if (labels.length && labels[0].textContent.trim()) {
      return labels[0].textContent.trim().replace(/\s+/g, " ").slice(0, 120);
    }
    return this.options.placeholder || "Options";
  }

  renderOptions(filter = this.searchFilter || "") {
    if (!this.list) return;
    this.searchFilter = filter;

    const needle = String(filter || "").trim().toLowerCase();
    this.filteredOptions = this.allOptions.filter((option) => {
      if (!needle) return true;
      return option.label.toLowerCase().includes(needle);
    });

    this.list.innerHTML = "";

    if (this.filteredOptions.length === 0) {
      const empty = document.createElement("li");
      empty.className = "custom-select-empty";
      empty.setAttribute("role", "presentation");
      empty.textContent = "No options found";
      this.list.appendChild(empty);
      this.focusedIndex = -1;
      this.focusedOption = null;
      this.updateActiveDescendant(null);
      return;
    }

    this.filteredOptions.forEach((option, index) => {
      const item = document.createElement("li");
      item.className = "custom-select-option";
      item.id = `${this.id}-option-${index}`;
      item.dataset.value = option.value;
      item.dataset.index = String(index);
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(this.selectedValues.has(option.value)));
      if (option.disabled) {
        item.classList.add("disabled");
        item.setAttribute("aria-disabled", "true");
      }
      if (this.selectedValues.has(option.value)) {
        item.classList.add("selected");
        item.appendChild(this.createCheckIcon());
      }

      const label = document.createElement("span");
      label.className = "custom-select-option-label";
      label.textContent = option.label;
      label.title = option.label;
      item.appendChild(label);

      this.list.appendChild(item);
    });

    this.focusedIndex = -1;
    this.focusedOption = null;
    this.updateActiveDescendant(null);

    if (this.isOpen) this.focusSelectedOption();
  }

  createCheckIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "custom-select-check");
    svg.setAttribute("width", "14");
    svg.setAttribute("height", "14");
    svg.setAttribute("viewBox", "0 0 14 14");
    svg.setAttribute("fill", "none");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M1 6.5L5.5 11L13 2");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    return svg;
  }

  refresh() {
    if (this.destroyed || !this.wrapper) return;
    const filter = this.searchInput ? this.searchInput.value : this.searchFilter || "";
    this.extractOptions();
    this.readSelectionFromOriginal();
    this.updateDisabledState();
    this.renderOptions(filter);
    this.updateSelectedDisplay();
  }

  /* ---------------------------------------------------------------- events */

  bindEvents() {
    this.onTriggerClick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.toggle();
    };
    this.onTriggerKeydown = (event) => this.handleTriggerKeydown(event);
    this.onListClick = (event) => {
      // The custom control often lives inside a <label>. Stop the label's
      // default activation from forwarding the click to the hidden native
      // select (which can try to open an unstyleable picker).
      event.preventDefault();
      const option = event.target.closest(".custom-select-option");
      if (option && !option.classList.contains("disabled")) {
        this.selectOption(option.dataset.value, option);
      }
    };
    this.onPanelClick = (event) => {
      if (event.target.closest(".custom-select-search")) return;
      event.preventDefault();
    };
    this.onListMouseover = (event) => {
      const option = event.target.closest(".custom-select-option");
      if (option && !option.classList.contains("disabled")) {
        this.setFocusedOption(option);
      }
    };
    this.onDocumentClick = (event) => {
      if (this.isOpen && !this.wrapper.contains(event.target)) {
        this.close({ returnFocus: false });
      }
    };
    this.onFormReset = () => nextFrame(() => this.refresh());
    this.onSelectChange = () => this.syncFromOriginal();
    this.onSelectInput = () => this.syncFromOriginal();
    this.onSelectFocus = () => {
      // The visible trigger must be the focus target, never the hidden select.
      if (document.activeElement === this.select) this.trigger.focus({ preventScroll: true });
    };
    this.onFocus = () => this.wrapper.classList.add("focused");
    this.onBlur = () => this.wrapper.classList.remove("focused");

    this.trigger.addEventListener("click", this.onTriggerClick);
    this.trigger.addEventListener("keydown", this.onTriggerKeydown);
    this.trigger.addEventListener("focus", this.onFocus);
    this.trigger.addEventListener("blur", this.onBlur);
    this.list.addEventListener("click", this.onListClick);
    this.list.addEventListener("mouseover", this.onListMouseover);
    this.panel.addEventListener("click", this.onPanelClick);
    document.addEventListener("click", this.onDocumentClick);
    this.select.addEventListener("change", this.onSelectChange);
    this.select.addEventListener("input", this.onSelectInput);
    this.select.addEventListener("focus", this.onSelectFocus);

    const form = this.select.form || this.select.closest("form");
    if (form) {
      this.form = form;
      form.addEventListener("reset", this.onFormReset);
    }

    if (this.searchInput) {
      this.onSearchInput = (event) => {
        this.renderOptions(event.target.value);
        if (this.isOpen) this.focusFirstOption();
      };
      this.onSearchKeydown = (event) => this.handleSearchKeydown(event);
      this.searchInput.addEventListener("input", this.onSearchInput);
      this.searchInput.addEventListener("keydown", this.onSearchKeydown);
      this.searchInput.addEventListener("click", (event) => event.stopPropagation());
    }
  }

  unbindEvents() {
    if (this.trigger) {
      this.trigger.removeEventListener("click", this.onTriggerClick);
      this.trigger.removeEventListener("keydown", this.onTriggerKeydown);
      this.trigger.removeEventListener("focus", this.onFocus);
      this.trigger.removeEventListener("blur", this.onBlur);
    }
    if (this.list) {
      this.list.removeEventListener("click", this.onListClick);
      this.list.removeEventListener("mouseover", this.onListMouseover);
    }
    if (this.panel) {
      this.panel.removeEventListener("click", this.onPanelClick);
    }
    document.removeEventListener("click", this.onDocumentClick);
    if (this.select) {
      this.select.removeEventListener("change", this.onSelectChange);
      this.select.removeEventListener("input", this.onSelectInput);
      this.select.removeEventListener("focus", this.onSelectFocus);
    }
    if (this.form) {
      this.form.removeEventListener("reset", this.onFormReset);
    }
    if (this.searchInput) {
      this.searchInput.removeEventListener("input", this.onSearchInput);
      this.searchInput.removeEventListener("keydown", this.onSearchKeydown);
    }
  }

  observeOriginalSelect() {
    if (typeof MutationObserver !== "function") return;
    this.observer = new MutationObserver((mutations) => {
      let needsRefresh = false;
      let checkClasses = false;

      mutations.forEach((record) => {
        if (record.type === "childList" || record.type === "characterData") {
          needsRefresh = true;
        } else if (record.type === "attributes") {
          if (record.attributeName === "class") {
            checkClasses = true;
          } else {
            needsRefresh = true;
          }
        }
      });

      if (checkClasses) {
        this.syncValidationClasses();
      }
      if (needsRefresh) {
        this.refresh();
      }
    });
    this.observer.observe(this.select, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      // Attributes that change the rendered control or validation state
      attributeFilter: ["disabled", "multiple", "required", "class"],
    });
  }

  syncValidationClasses() {
    if (!this.wrapper || !this.trigger || !this.select) return;
    const isInvalid = this.select.classList.contains("is-invalid");
    this.wrapper.classList.toggle("is-invalid", isInvalid);
    this.trigger.classList.toggle("is-invalid", isInvalid);
    this.trigger.setAttribute("aria-invalid", String(isInvalid));
  }

  /* ------------------------------------------------------------- keyboard */

  handleTriggerKeydown(event) {
    switch (event.key) {
      case "Enter":
      case " ":
        event.preventDefault();
        if (this.isOpen && this.focusedOption && !this.focusedOption.classList.contains("disabled")) {
          this.selectOption(this.focusedOption.dataset.value, this.focusedOption);
        } else {
          this.toggle();
        }
        break;
      case "Escape":
        if (this.isOpen) {
          event.preventDefault();
          this.close({ returnFocus: true });
        }
        break;
      case "ArrowDown":
        event.preventDefault();
        if (!this.isOpen) {
          this.open();
        } else {
          this.focusNext();
        }
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!this.isOpen) {
          this.open({ focusLast: true });
        } else {
          this.focusPrevious();
        }
        break;
      case "Home":
        if (this.isOpen) {
          event.preventDefault();
          this.focusFirstOption();
        }
        break;
      case "End":
        if (this.isOpen) {
          event.preventDefault();
          this.focusLastOption();
        }
        break;
      case "Tab":
        this.close({ returnFocus: false });
        break;
      default:
        if (
          event.key.length === 1 &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.altKey
        ) {
          this.typeAheadSearch(event.key);
        }
    }
  }

  handleSearchKeydown(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      this.close({ returnFocus: true });
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      this.focusNext();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      this.focusPrevious();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (this.focusedOption) {
        this.selectOption(this.focusedOption.dataset.value, this.focusedOption);
      }
    }
  }

  typeAheadSearch(char) {
    const now = Date.now();
    if (now - (this.typeAheadAt || 0) > 700) this.typeAheadBuffer = "";
    this.typeAheadAt = now;
    this.typeAheadBuffer += char.toLowerCase();

    const options = Array.from(
      this.list.querySelectorAll(".custom-select-option:not(.disabled)")
    );
    const match = options.find((option) => {
      const label = option.querySelector(".custom-select-option-label");
      return (label ? label.textContent : "").toLowerCase().startsWith(this.typeAheadBuffer);
    });
    if (!match) return;

    if (!this.isOpen) this.open();
    this.setFocusedOption(match);
  }

  setFocusedOption(option) {
    if (!option) return;
    this.list
      .querySelectorAll(".custom-select-option.focused")
      .forEach((element) => element.classList.remove("focused"));
    option.classList.add("focused");
    this.focusedOption = option;
    const enabled = this.enabledOptions();
    this.focusedIndex = enabled.indexOf(option);
    this.updateActiveDescendant(option);

    // Scroll only the options list. scrollIntoView() also scrolls the page,
    // which made the trigger jump out of view the moment a dropdown opened.
    if (this.list) {
      const listRect = this.list.getBoundingClientRect();
      const optionRect = option.getBoundingClientRect();
      if (optionRect.top < listRect.top) {
        this.list.scrollTop -= listRect.top - optionRect.top;
      } else if (optionRect.bottom > listRect.bottom) {
        this.list.scrollTop += optionRect.bottom - listRect.bottom;
      }
    }
  }

  updateActiveDescendant(option) {
    if (!this.trigger) return;
    if (option && option.id) {
      this.trigger.setAttribute("aria-activedescendant", option.id);
    } else {
      this.trigger.removeAttribute("aria-activedescendant");
    }
  }

  enabledOptions() {
    if (!this.list) return [];
    return Array.from(
      this.list.querySelectorAll(".custom-select-option:not(.disabled)")
    );
  }

  focusFirstOption() {
    const options = this.enabledOptions();
    if (options.length) this.setFocusedOption(options[0]);
  }

  focusLastOption() {
    const options = this.enabledOptions();
    if (options.length) this.setFocusedOption(options[options.length - 1]);
  }

  focusSelectedOption() {
    const selected = this.list
      ? this.list.querySelector(".custom-select-option.selected:not(.disabled)")
      : null;
    if (selected) {
      this.setFocusedOption(selected);
    } else {
      this.focusFirstOption();
    }
  }

  focusNext() {
    const options = this.enabledOptions();
    if (!options.length) return;
    const current = this.focusedOption ? options.indexOf(this.focusedOption) : -1;
    this.setFocusedOption(options[(current + 1) % options.length]);
  }

  focusPrevious() {
    const options = this.enabledOptions();
    if (!options.length) return;
    const current = this.focusedOption ? options.indexOf(this.focusedOption) : options.length;
    this.setFocusedOption(options[(current - 1 + options.length) % options.length]);
  }

  /* ----------------------------------------------------------- open/close */

  toggle() {
    if (this.isOpen) this.close({ returnFocus: true });
    else this.open();
  }

  open({ focusLast = false } = {}) {
    if (this.destroyed || !this.wrapper || this.wrapper.classList.contains("disabled")) {
      return;
    }

    // Close every other dropdown through the shared registry. Querying wrapper
    // elements alone missed instances because the reference lives on the
    // original select.
    INSTANCES.forEach((instance) => {
      if (instance !== this && instance.isOpen) {
        instance.close({ returnFocus: false });
      }
    });

    this.isOpen = true;
    this.trigger.setAttribute("aria-expanded", "true");
    this.panel.hidden = false;
    this.panel.setAttribute("aria-hidden", "false");
    this.wrapper.classList.remove("drop-up");

    this.raise();

    // Force a layout read while the panel is in its closed visual state. The
    // following class then animates instead of snapping into place.
    void this.panel.offsetHeight;
    this.positionPanel();
    nextFrame(() => {
      if (!this.isOpen) return;
      this.wrapper.classList.add("open");
      if (focusLast) this.focusLastOption();
      else this.focusSelectedOption();
      if (this.searchInput) {
        this.searchInput.focus({ preventScroll: true });
      }
    });
  }

  close({ returnFocus = false, immediate = false } = {}) {
    if (this.panelTimer) {
      window.clearTimeout(this.panelTimer);
      this.panelTimer = 0;
    }

    if (!this.isOpen && this.panel.hidden) {
      this.unraise();
      return;
    }

    this.isOpen = false;
    if (this.wrapper) {
      this.wrapper.classList.remove("open");
      this.wrapper.classList.remove("drop-up");
    }
    this.trigger.setAttribute("aria-expanded", "false");
    this.panel.setAttribute("aria-hidden", "true");
    this.updateActiveDescendant(null);
    this.unraise();

    if (this.searchInput) {
      this.searchInput.value = "";
      this.searchFilter = "";
    }

    const finish = () => {
      this.panelTimer = 0;
      if (!this.isOpen) {
        this.panel.hidden = true;
        // Re-render with the full option list after a search was cleared.
        if (this.searchInput) this.renderOptions("");
      }
    };

    if (immediate || this.options.animate === false || prefersReducedMotion()) {
      finish();
    } else {
      this.panelTimer = window.setTimeout(finish, Math.max(0, this.options.duration));
    }

    if (returnFocus) this.trigger.focus({ preventScroll: true });
  }

  positionPanel() {
    if (!this.trigger || !this.panel) return;
    const triggerRect = this.trigger.getBoundingClientRect();
    // offsetHeight is the rendered (max-height constrained) panel height,
    // which is what actually has to fit below the trigger.
    const panelHeight = Math.min(
      this.panel.offsetHeight || this.panel.scrollHeight || 0,
      window.innerHeight - 24
    );
    const spaceBelow = window.innerHeight - triggerRect.bottom - 10;
    const spaceAbove = triggerRect.top - 10;

    if (panelHeight > spaceBelow && spaceAbove > spaceBelow) {
      this.wrapper.classList.add("drop-up");
    } else {
      this.wrapper.classList.remove("drop-up");
    }
  }

  raise() {
    if (!this.wrapper) return;
    let node = this.wrapper;
    while (
      node.parentElement &&
      !node.parentElement.classList.contains("glass") &&
      node.parentElement !== document.body
    ) {
      node = node.parentElement;
    }
    this.layerNode = node;
    node.classList.add("custom-select-layer");
  }

  unraise() {
    if (this.layerNode) {
      this.layerNode.classList.remove("custom-select-layer");
      this.layerNode = null;
    }
  }

  /* ------------------------------------------------------------- selection */

  selectOption(value, optionElement = null) {
    const option = this.allOptions.find((entry) => entry.value === value);
    if (!option || option.disabled) return;

    if (this.multi) {
      if (this.selectedValues.has(value)) this.selectedValues.delete(value);
      else this.selectedValues.add(value);
    } else {
      this.selectedValues.clear();
      this.selectedValues.add(value);
    }

    this.updateSelectedDisplay();
    this.updateOptionStates();

    if (!this.multi) {
      this.close({ returnFocus: true });
      this.playSelectionFeedback();
    }

    this.syncToOriginal();
    this.options.onChange(Array.from(this.selectedValues), option);
  }

  updateOptionStates() {
    if (!this.list) return;
    this.list.querySelectorAll(".custom-select-option").forEach((element) => {
      const value = element.dataset.value;
      const isSelected = this.selectedValues.has(value);
      element.classList.toggle("selected", isSelected);
      element.setAttribute("aria-selected", String(isSelected));

      const check = element.querySelector(".custom-select-check");
      if (isSelected && !check) {
        element.appendChild(this.createCheckIcon());
      } else if (!isSelected && check) {
        check.remove();
      }
    });
  }

  updateSelectedDisplay() {
    if (!this.valueDisplay) return;

    let label = "";
    if (this.multi) {
      label = this.allOptions
        .filter((option) => this.selectedValues.has(option.value))
        .map((option) => option.label)
        .join(", ");
    } else {
      const selected = this.allOptions.find((option) =>
        this.selectedValues.has(option.value)
      );
      label = selected ? selected.label : "";
    }

    if (!label) {
      this.valueDisplay.textContent = this.options.placeholder;
      this.valueDisplay.classList.add("placeholder");
    } else {
      this.valueDisplay.textContent = label;
      this.valueDisplay.classList.remove("placeholder");
    }
  }

  updateDisabledState() {
    if (!this.wrapper) return;
    const disabled = Boolean(this.select.disabled);
    this.wrapper.classList.toggle("disabled", disabled);
    this.trigger.disabled = disabled;
    this.trigger.setAttribute("aria-disabled", String(disabled));
    if (disabled && this.isOpen) this.close({ returnFocus: false });
  }

  playSelectionFeedback() {
    if (!this.trigger) return;
    if (this.pickTimer) window.clearTimeout(this.pickTimer);
    this.trigger.classList.remove("is-picked");
    void this.trigger.offsetWidth;
    this.trigger.classList.add("is-picked");
    this.pickTimer = window.setTimeout(() => {
      this.trigger.classList.remove("is-picked");
      this.pickTimer = 0;
    }, 380);
  }

  syncFromOriginal() {
    if (!this.originalSelect || !this.wrapper) return;
    this.readSelectionFromOriginal();
    this.updateSelectedDisplay();
    this.updateOptionStates();
  }

  writeToOriginal() {
    if (!this.originalSelect) return;
    Array.from(this.originalSelect.options).forEach((option) => {
      option.selected = this.selectedValues.has(option.value);
    });
  }

  syncToOriginal() {
    if (!this.originalSelect) return;
    this.writeToOriginal();
    // Native selects fire input then change on user selection. Preserve both
    // so existing dashboard handlers and form integrations keep working.
    this.originalSelect.dispatchEvent(new Event("input", { bubbles: true }));
    this.originalSelect.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /* ---------------------------------------------------------- public API */

  setValue(value, { emit = true } = {}) {
    this.selectedValues.clear();
    if (Array.isArray(value)) {
      value.forEach((entry) => this.selectedValues.add(entry));
    } else if (value !== undefined && value !== null) {
      this.selectedValues.add(value);
    }

    this.updateSelectedDisplay();
    this.updateOptionStates();
    if (emit) this.syncToOriginal();
    else this.writeToOriginal();
  }

  getValue() {
    return this.multi
      ? Array.from(this.selectedValues)
      : this.selectedValues.values().next().value;
  }

  addOption(value, label, selected = false) {
    const option = new Option(label, value, selected, selected);
    this.select.add(option);
    this.refresh();
  }

  removeOption(value) {
    const option = Array.from(this.select.options).find(
      (entry) => entry.value === value
    );
    if (option) option.remove();
    this.refresh();
  }

  disable() {
    this.select.disabled = true;
    this.updateDisabledState();
  }

  enable() {
    this.select.disabled = false;
    this.updateDisabledState();
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.close({ returnFocus: false, immediate: true });
    if (this.panelTimer) window.clearTimeout(this.panelTimer);
    if (this.pickTimer) window.clearTimeout(this.pickTimer);
    if (this.typeAheadTimer) window.clearTimeout(this.typeAheadTimer);
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }

    this.detachValueInterceptor();
    this.unbindEvents();
    this.unraise();

    if (this.wrapper) {
      if (this.wrapper.parentNode) {
        this.wrapper.parentNode.insertBefore(this.select, this.wrapper);
      } else if (this.select.parentNode === this.wrapper) {
        this.wrapper.removeChild(this.select);
      }
      this.wrapper.remove();
    }
    this.select.hidden = false;
    this.select.removeAttribute("aria-hidden");
    this.select.tabIndex = this.originalTabIndex ?? 0;
    this.select.style.removeProperty("display");
    delete this.select.customSelect;
    if (this.wrapper) delete this.wrapper.customSelect;
    this.select.removeAttribute("data-custom-select-ready");
    this.wrapper = null;
    INSTANCES.delete(this);
  }

  /* ----------------------------------------------------------- native hide */

  hideOriginalSelect() {
    if (!this.originalSelect) return;
    this.originalTabIndex = this.originalSelect.tabIndex;
    this.originalSelect.hidden = true;
    this.originalSelect.setAttribute("aria-hidden", "true");
    this.originalSelect.tabIndex = -1;
    this.originalSelect.style.setProperty("display", "none", "important");
  }
}

/* ------------------------------------------------------------------ init */

function safeInit(select) {
  if (!isSelectElement(select)) return null;
  if (select.customSelect instanceof CustomSelect) return select.customSelect;
  if (select.dataset.customSelectError === "true") return null;
  try {
    return new CustomSelect(select);
  } catch (error) {
    select.dataset.customSelectError = "true";
    console.error("CustomSelect initialisation failed:", error);
    return null;
  }
}

function ensureDynamicObserver() {
  if (dynamicObserver || typeof MutationObserver !== "function") return;
  if (typeof document === "undefined" || !document.body) return;

  dynamicObserver = new MutationObserver((records) => {
    const removed = [];

    records.forEach((record) => {
      record.addedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.matches && node.matches("select[data-custom-select]")) {
          safeInit(node);
        }
        if (node.querySelectorAll) {
          node
            .querySelectorAll("select[data-custom-select]")
            .forEach((select) => safeInit(select));
        }
      });

      record.removedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.matches && node.matches("select[data-custom-select]")) {
          removed.push(node);
        }
        if (node.querySelectorAll) {
          node
            .querySelectorAll("select[data-custom-select]")
            .forEach((select) => removed.push(select));
        }
      });
    });

    removed.forEach((select) => {
      const instance = select.customSelect;
      // A node can be moved within the same mutation batch; only tear down
      // instances that really left the document.
      if (instance instanceof CustomSelect && !select.isConnected) {
        instance.destroy();
      }
    });
  });
  dynamicObserver.observe(document.body, { childList: true, subtree: true });
}

export function initCustomSelects(root = document) {
  if (!root) return [];
  injectCustomSelectStyles();
  const scope = root;
  const selects = [];

  if (scope.matches && scope.matches("select[data-custom-select]")) {
    selects.push(scope);
  }
  if (scope.querySelectorAll) {
    scope
      .querySelectorAll("select[data-custom-select]")
      .forEach((select) => selects.push(select));
  }

  const instances = [];
  selects.forEach((select) => {
    const instance = safeInit(select);
    if (instance) instances.push(instance);
  });

  ensureDynamicObserver();
  return Array.from(new Set(instances));
}

export function refreshCustomSelects(root = document) {
  if (!root) return;
  const scope = root;
  INSTANCES.forEach((instance) => {
    if (scope === document || scope.contains(instance.select) || scope === instance.select) {
      instance.refresh();
    }
  });
}

export function closeCustomSelects({ except = null, returnFocus = false } = {}) {
  INSTANCES.forEach((instance) => {
    if (instance !== except && instance.isOpen) {
      instance.close({ returnFocus });
    }
  });
}

export function injectCustomSelectStyles() {
  if (typeof document === "undefined") return null;
  const existing =
    document.getElementById("custom-select-styles") ||
    document.querySelector('link[rel="stylesheet"][href*="custom-select.css"]');
  if (existing) {
    if (!existing.id) existing.id = "custom-select-styles";
    return existing;
  }

  const link = document.createElement("link");
  link.id = "custom-select-styles";
  link.rel = "stylesheet";
  link.href = STYLE_HREF;
  link.dataset.customSelectStyles = "true";
  document.head.appendChild(link);
  return link;
}

// CommonJS compatibility for isolated component tests/tools.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    CustomSelect,
    initCustomSelects,
    refreshCustomSelects,
    closeCustomSelects,
    injectCustomSelectStyles,
  };
}
