/**
 * CustomDatePicker - NexOra Cyberpunk Calendar & DateTime Picker
 *
 * Replaces native browser date & datetime-local inputs with a fully-stylable,
 * dark-glass, animated calendar and time picker matching NexOra's design system.
 *
 * Supports:
 *  - Month/Year grid navigation with smooth transitions
 *  - NexOra hover effects, today indicator, active date glow
 *  - Hours & Minutes time picker with AM/PM toggle & quick presets
 *  - Live synchronization with native <input type="datetime-local"> or <input type="date">
 *  - Value interceptor to capture programmatic .value = assignments
 *  - Keyboard navigation (Escape, Enter) and outside-click detection
 *  - Content-Security-Policy compliant (uses static /assets/custom-date-picker.css)
 *  - Safe fallback to native input if initialization encounters an error
 */

const STYLE_HREF = "/assets/custom-date-picker.css";
const INSTANCES = new Set();
let dynamicObserver = null;
let instanceCounter = 0;

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

function isDateInputElement(node) {
  if (!node || node.tagName !== "INPUT") return false;
  const type = node.type ? node.type.toLowerCase() : "";
  return type === "date" || type === "datetime-local" || node.hasAttribute("data-custom-date");
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function pad(num) {
  return String(num).padStart(2, "0");
}

export function injectCustomDatePickerStyles() {
  if (typeof document === "undefined") return;
  if (document.getElementById("custom-date-picker-styles")) return;
  if (document.querySelector(`link[href*="custom-date-picker.css"]`)) return;

  const link = document.createElement("link");
  link.id = "custom-date-picker-styles";
  link.rel = "stylesheet";
  link.href = STYLE_HREF;
  document.head.appendChild(link);
}

export class CustomDatePicker {
  constructor(element, options = {}) {
    const input = isDateInputElement(element)
      ? element
      : element && typeof element.querySelector === "function"
        ? element.querySelector('input[type="date"], input[type="datetime-local"], input[data-custom-date]')
        : null;

    if (!input) {
      throw new Error("CustomDatePicker requires a date or datetime-local <input> element.");
    }

    if (input.customDatePicker instanceof CustomDatePicker) {
      return input.customDatePicker;
    }

    this.input = input;
    this.originalInput = input;
    this.isDateTime = input.type === "datetime-local" || options.time === true;
    this.options = {
      placeholder: options.placeholder || (this.isDateTime ? "Select date & time..." : "Select date..."),
      min: options.min || input.min || null,
      max: options.max || input.max || null,
      onChange: options.onChange || (() => {}),
      ...options,
    };

    this.id = `custom-date-picker-${++instanceCounter}`;
    this.isOpen = false;

    // Internal date state
    const now = new Date();
    this.viewYear = now.getFullYear();
    this.viewMonth = now.getMonth(); // 0-indexed
    this.selectedDate = null;
    this.selectedHours = now.getHours();
    this.selectedMinutes = Math.floor(now.getMinutes() / 5) * 5;

    input.customDatePicker = this;
    this.init();
  }

  /* ------------------------------------------------------------- lifecycle */

  init() {
    try {
      injectCustomDatePickerStyles();
      this.readFromOriginalInput();
      this.createDom();
      this.bindEvents();
      this.attachValueInterceptor();
      this.hideOriginalInput();
      this.observeOriginalInput();
      this.syncValidationClasses();
      this.render();

      this.wrapper.customDatePicker = this;
      this.input.customDatePicker = this;
      this.wrapper.dataset.customDateReady = "true";
      this.input.dataset.customDateReady = "true";
      INSTANCES.add(this);
    } catch (error) {
      this.restoreNativeInput(error);
    }
  }

  readFromOriginalInput() {
    const val = (this.input.value || "").trim();
    if (!val) return;

    const parsed = new Date(val);
    if (!Number.isNaN(parsed.getTime())) {
      this.selectedDate = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
      this.viewYear = parsed.getFullYear();
      this.viewMonth = parsed.getMonth();
      if (this.isDateTime) {
        this.selectedHours = parsed.getHours();
        this.selectedMinutes = parsed.getMinutes();
      }
    }
  }

  attachValueInterceptor() {
    if (this._hasValueInterceptor || !this.input) return;
    const proto = HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (!descriptor) return;

    this._hasValueInterceptor = true;
    const self = this;
    Object.defineProperty(this.input, "value", {
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
    if (this._hasValueInterceptor && this.input) {
      delete this.input.value;
      this._hasValueInterceptor = false;
    }
  }

  syncFromOriginal() {
    this.readFromOriginalInput();
    this.render();
  }

  hideOriginalInput() {
    this.originalTabIndex = this.input.tabIndex;
    this.input.tabIndex = -1;
    this.input.hidden = true;
    this.input.setAttribute("aria-hidden", "true");
    this.input.style.setProperty("display", "none", "important");
  }

  observeOriginalInput() {
    if (typeof MutationObserver !== "function") return;
    this.observer = new MutationObserver((mutations) => {
      mutations.forEach((record) => {
        if (record.type === "attributes") {
          if (record.attributeName === "class") {
            this.syncValidationClasses();
          } else if (record.attributeName === "disabled") {
            this.updateDisabledState();
          }
        }
      });
    });
    this.observer.observe(this.input, { attributes: true });
  }

  syncValidationClasses() {
    if (!this.wrapper || !this.trigger || !this.input) return;
    const isInvalid = this.input.classList.contains("is-invalid");
    this.wrapper.classList.toggle("is-invalid", isInvalid);
    this.trigger.classList.toggle("is-invalid", isInvalid);
  }

  updateDisabledState() {
    const disabled = Boolean(this.input.disabled);
    this.wrapper.classList.toggle("disabled", disabled);
    this.trigger.disabled = disabled;
    if (disabled && this.isOpen) this.close();
  }

  restoreNativeInput(error) {
    this.destroyed = true;
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
    this.detachValueInterceptor();
    this.unbindEvents();

    if (this.wrapper) {
      if (this.wrapper.parentNode) {
        this.wrapper.parentNode.insertBefore(this.input, this.wrapper);
      }
      this.wrapper.remove();
    }

    this.input.hidden = false;
    this.input.removeAttribute("aria-hidden");
    this.input.removeAttribute("data-custom-date-ready");
    this.input.tabIndex = this.originalTabIndex ?? 0;
    this.input.style.removeProperty("display");
    delete this.input.customDatePicker;
    INSTANCES.delete(this);

    console.warn("CustomDatePicker failed to initialize; native input retained.", error);
  }

  /* ------------------------------------------------------------- DOM creation */

  createDom() {
    const input = this.input;
    const wrapper = document.createElement("div");
    wrapper.className = "custom-date-picker-wrapper";
    wrapper.id = input.id ? `${input.id}-custom` : `${this.id}-wrapper`;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "custom-date-picker-trigger";
    trigger.id = `${wrapper.id}-trigger`;
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    trigger.innerHTML = `
      <span class="custom-date-picker-value placeholder">${this.options.placeholder}</span>
      <span class="custom-date-picker-icon" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
          <line x1="16" y1="2" x2="16" y2="6"/>
          <line x1="8" y1="2" x2="8" y2="6"/>
          <line x1="3" y1="10" x2="21" y2="10"/>
        </svg>
      </span>
    `;

    const panel = document.createElement("div");
    panel.className = "custom-date-picker-panel";
    panel.id = `${wrapper.id}-panel`;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Choose date and time");
    panel.hidden = true;

    // Assemble structure
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(trigger);
    wrapper.appendChild(panel);
    wrapper.appendChild(input);

    this.wrapper = wrapper;
    this.trigger = trigger;
    this.panel = panel;
    this.valueDisplay = trigger.querySelector(".custom-date-picker-value");
  }

  /* ---------------------------------------------------------------- render */

  render() {
    this.updateTriggerDisplay();
    this.renderPanel();
  }

  updateTriggerDisplay() {
    if (!this.selectedDate) {
      this.valueDisplay.textContent = this.options.placeholder;
      this.valueDisplay.classList.add("placeholder");
      return;
    }

    const d = this.selectedDate;
    const monthStr = MONTH_NAMES[d.getMonth()].slice(0, 3);
    const dayStr = d.getDate();
    const yearStr = d.getFullYear();

    if (this.isDateTime) {
      const h = this.selectedHours;
      const m = pad(this.selectedMinutes);
      const isPm = h >= 12;
      const h12 = h % 12 || 12;
      const ampm = isPm ? "PM" : "AM";
      this.valueDisplay.textContent = `${monthStr} ${dayStr}, ${yearStr} · ${pad(h12)}:${m} ${ampm}`;
    } else {
      this.valueDisplay.textContent = `${monthStr} ${dayStr}, ${yearStr}`;
    }
    this.valueDisplay.classList.remove("placeholder");
  }

  renderPanel() {
    const monthName = MONTH_NAMES[this.viewMonth];
    const year = this.viewYear;

    let timeSectionHtml = "";
    if (this.isDateTime) {
      const h = this.selectedHours;
      const m = this.selectedMinutes;
      const isPm = h >= 12;
      const h12 = h % 12 || 12;

      timeSectionHtml = `
        <div class="cdp-time-section">
          <div class="cdp-time-header">
            <span class="cdp-time-title">Time</span>
          </div>
          <div class="cdp-time-picker">
            <div class="cdp-time-box">
              <button type="button" class="cdp-time-btn" data-action="hour-up" aria-label="Increase hour">▲</button>
              <input type="text" class="cdp-time-input" id="${this.id}-hour" value="${pad(h12)}" maxlength="2" aria-label="Hour">
              <button type="button" class="cdp-time-btn" data-action="hour-down" aria-label="Decrease hour">▼</button>
            </div>
            <span class="cdp-time-colon">:</span>
            <div class="cdp-time-box">
              <button type="button" class="cdp-time-btn" data-action="min-up" aria-label="Increase minute">▲</button>
              <input type="text" class="cdp-time-input" id="${this.id}-min" value="${pad(m)}" maxlength="2" aria-label="Minute">
              <button type="button" class="cdp-time-btn" data-action="min-down" aria-label="Decrease minute">▼</button>
            </div>
            <div class="cdp-ampm-toggle">
              <button type="button" class="cdp-ampm-btn ${!isPm ? 'active' : ''}" data-action="set-am">AM</button>
              <button type="button" class="cdp-ampm-btn ${isPm ? 'active' : ''}" data-action="set-pm">PM</button>
            </div>
          </div>
          <div class="cdp-time-presets">
            <button type="button" class="cdp-time-chip" data-preset-time="09:00">09:00 AM</button>
            <button type="button" class="cdp-time-chip" data-preset-time="12:00">12:00 PM</button>
            <button type="button" class="cdp-time-chip" data-preset-time="17:00">05:00 PM</button>
            <button type="button" class="cdp-time-chip" data-preset-time="23:59">11:59 PM</button>
          </div>
        </div>
      `;
    }

    this.panel.innerHTML = `
      <div class="cdp-header">
        <button type="button" class="cdp-month-year-btn" id="${this.id}-my-btn">
          ${monthName} ${year}
        </button>
        <div class="cdp-nav-btns">
          <button type="button" class="cdp-nav-btn" data-action="prev-month" aria-label="Previous month">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M10 12L6 8L10 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <button type="button" class="cdp-nav-btn" data-action="next-month" aria-label="Next month">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M6 4L10 8L6 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
      </div>
      <div class="cdp-calendar-body">
        <div class="cdp-weekdays">
          ${WEEKDAYS.map((w) => `<span class="cdp-weekday">${w}</span>`).join("")}
        </div>
        <div class="cdp-days" id="${this.id}-days">
          ${this.buildDaysHtml()}
        </div>
      </div>
      ${timeSectionHtml}
      <div class="cdp-footer">
        <button type="button" class="cdp-footer-btn" data-action="clear">Clear</button>
        <button type="button" class="cdp-footer-btn" data-action="today">Now</button>
        <button type="button" class="cdp-footer-btn-primary" data-action="apply">Set</button>
      </div>
    `;

    this.bindPanelEvents();
  }

  buildDaysHtml() {
    const year = this.viewYear;
    const month = this.viewMonth;
    const firstDayIndex = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const daysInPrevMonth = new Date(year, month, 0).getDate();

    const today = new Date();
    const isThisMonth = today.getFullYear() === year && today.getMonth() === month;
    const todayDate = today.getDate();

    let html = "";

    // Trailing days of previous month
    for (let i = firstDayIndex - 1; i >= 0; i--) {
      const dayNum = daysInPrevMonth - i;
      html += `<button type="button" class="cdp-day outside" data-action="prev-month-day" data-day="${dayNum}">${dayNum}</button>`;
    }

    // Days in current month
    for (let d = 1; d <= daysInMonth; d++) {
      const isToday = isThisMonth && d === todayDate;
      const isSelected = this.selectedDate &&
        this.selectedDate.getFullYear() === year &&
        this.selectedDate.getMonth() === month &&
        this.selectedDate.getDate() === d;

      const classes = [
        "cdp-day",
        isToday ? "today" : "",
        isSelected ? "selected" : ""
      ].filter(Boolean).join(" ");

      html += `<button type="button" class="${classes}" data-day="${d}">${d}</button>`;
    }

    // Leading days of next month (fill remaining up to 42 cells)
    const totalFilled = firstDayIndex + daysInMonth;
    const remaining = 42 - totalFilled;
    for (let n = 1; n <= remaining; n++) {
      html += `<button type="button" class="cdp-day outside" data-action="next-month-day" data-day="${n}">${n}</button>`;
    }

    return html;
  }

  /* ------------------------------------------------------------- event binding */

  bindEvents() {
    this._onTriggerClick = (e) => {
      e.preventDefault();
      this.toggle();
    };

    this._onOutsideClick = (e) => {
      if (!this.isOpen) return;
      if (!this.wrapper.contains(e.target)) {
        this.close();
      }
    };

    this._onKeyDown = (e) => {
      if (!this.isOpen) return;
      if (e.key === "Escape") {
        this.close();
        this.trigger.focus();
      } else if (e.key === "Enter") {
        e.preventDefault();
        this.applyValue();
        this.close();
      }
    };

    this.trigger.addEventListener("click", this._onTriggerClick);
    document.addEventListener("mousedown", this._onOutsideClick);
    document.addEventListener("keydown", this._onKeyDown);
  }

  bindPanelEvents() {
    // Navigation buttons
    const prevBtn = this.panel.querySelector('[data-action="prev-month"]');
    const nextBtn = this.panel.querySelector('[data-action="next-month"]');
    if (prevBtn) prevBtn.addEventListener("click", () => this.prevMonth());
    if (nextBtn) nextBtn.addEventListener("click", () => this.nextMonth());

    // Day buttons click
    const daysContainer = this.panel.querySelector(`#${this.id}-days`);
    if (daysContainer) {
      daysContainer.addEventListener("click", (e) => {
        const btn = e.target.closest(".cdp-day");
        if (!btn || btn.classList.contains("disabled")) return;

        const action = btn.dataset.action;
        const day = parseInt(btn.dataset.day, 10);

        if (action === "prev-month-day") {
          this.prevMonth();
          this.selectDay(day);
        } else if (action === "next-month-day") {
          this.nextMonth();
          this.selectDay(day);
        } else {
          this.selectDay(day);
        }
      });
    }

    // Time buttons & inputs
    if (this.isDateTime) {
      const hourInput = this.panel.querySelector(`#${this.id}-hour`);
      const minInput = this.panel.querySelector(`#${this.id}-min`);

      const hourUp = this.panel.querySelector('[data-action="hour-up"]');
      const hourDown = this.panel.querySelector('[data-action="hour-down"]');
      const minUp = this.panel.querySelector('[data-action="min-up"]');
      const minDown = this.panel.querySelector('[data-action="min-down"]');
      const setAm = this.panel.querySelector('[data-action="set-am"]');
      const setPm = this.panel.querySelector('[data-action="set-pm"]');

      if (hourUp) hourUp.addEventListener("click", () => this.adjustHour(1));
      if (hourDown) hourDown.addEventListener("click", () => this.adjustHour(-1));
      if (minUp) minUp.addEventListener("click", () => this.adjustMinute(5));
      if (minDown) minDown.addEventListener("click", () => this.adjustMinute(-5));

      if (setAm) setAm.addEventListener("click", () => this.setMeridiem(false));
      if (setPm) setPm.addEventListener("click", () => this.setMeridiem(true));

      if (hourInput) {
        hourInput.addEventListener("change", () => {
          let h12 = parseInt(hourInput.value, 10) || 12;
          h12 = Math.max(1, Math.min(12, h12));
          const isPm = this.selectedHours >= 12;
          this.selectedHours = isPm ? (h12 % 12) + 12 : (h12 % 12);
          this.applyValue();
          this.render();
        });
      }

      if (minInput) {
        minInput.addEventListener("change", () => {
          let m = parseInt(minInput.value, 10) || 0;
          m = Math.max(0, Math.min(59, m));
          this.selectedMinutes = m;
          this.applyValue();
          this.render();
        });
      }

      // Time preset chips
      this.panel.querySelectorAll(".cdp-time-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const [hh, mm] = chip.dataset.presetTime.split(":").map(Number);
          this.selectedHours = hh;
          this.selectedMinutes = mm;
          this.applyValue();
          this.render();
        });
      });
    }

    // Footer actions
    const clearBtn = this.panel.querySelector('[data-action="clear"]');
    const todayBtn = this.panel.querySelector('[data-action="today"]');
    const applyBtn = this.panel.querySelector('[data-action="apply"]');

    if (clearBtn) clearBtn.addEventListener("click", () => this.clear());
    if (todayBtn) todayBtn.addEventListener("click", () => this.setNow());
    if (applyBtn) {
      applyBtn.addEventListener("click", () => {
        if (!this.selectedDate) this.setNow();
        else this.applyValue();
        this.close();
      });
    }
  }

  unbindEvents() {
    if (this.trigger && this._onTriggerClick) {
      this.trigger.removeEventListener("click", this._onTriggerClick);
    }
    if (this._onOutsideClick) {
      document.removeEventListener("mousedown", this._onOutsideClick);
    }
    if (this._onKeyDown) {
      document.removeEventListener("keydown", this._onKeyDown);
    }
  }

  /* ----------------------------------------------------------- interactions */

  open() {
    if (this.isOpen || this.input.disabled) return;
    this.isOpen = true;
    this.wrapper.classList.add("open");
    this.panel.hidden = false;
    this.trigger.setAttribute("aria-expanded", "true");

    // Position check for drop-up
    const rect = this.wrapper.getBoundingClientRect();
    const panelHeight = 420;
    const spaceBelow = window.innerHeight - rect.bottom;
    const shouldDropUp = spaceBelow < panelHeight && rect.top > panelHeight;
    this.wrapper.classList.toggle("drop-up", shouldDropUp);

    this.render();
  }

  close() {
    if (!this.isOpen) return;
    this.isOpen = false;
    this.wrapper.classList.remove("open");
    this.panel.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }

  prevMonth() {
    this.viewMonth -= 1;
    if (this.viewMonth < 0) {
      this.viewMonth = 11;
      this.viewYear -= 1;
    }
    this.render();
  }

  nextMonth() {
    this.viewMonth += 1;
    if (this.viewMonth > 11) {
      this.viewMonth = 0;
      this.viewYear += 1;
    }
    this.render();
  }

  selectDay(day) {
    this.selectedDate = new Date(this.viewYear, this.viewMonth, day);
    this.applyValue();
    this.render();

    // If date-only, close immediately on day click
    if (!this.isDateTime) {
      setTimeout(() => this.close(), 160);
    }
  }

  adjustHour(delta) {
    let h = this.selectedHours + delta;
    if (h < 0) h = 23;
    else if (h > 23) h = 0;
    this.selectedHours = h;
    this.applyValue();
    this.render();
  }

  adjustMinute(delta) {
    let m = this.selectedMinutes + delta;
    if (m < 0) m = 55;
    else if (m > 59) m = 0;
    this.selectedMinutes = m;
    this.applyValue();
    this.render();
  }

  setMeridiem(isPm) {
    const currentIsPm = this.selectedHours >= 12;
    if (isPm && !currentIsPm) {
      this.selectedHours += 12;
    } else if (!isPm && currentIsPm) {
      this.selectedHours -= 12;
    }
    this.applyValue();
    this.render();
  }

  setNow() {
    const now = new Date();
    this.selectedDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    this.viewYear = now.getFullYear();
    this.viewMonth = now.getMonth();
    this.selectedHours = now.getHours();
    this.selectedMinutes = now.getMinutes();
    this.applyValue();
    this.render();
    this.close();
  }

  clear() {
    this.selectedDate = null;
    this.input.value = "";
    this.dispatchChange();
    this.render();
    this.close();
  }

  applyValue() {
    if (!this.selectedDate) return;
    const y = this.selectedDate.getFullYear();
    const m = pad(this.selectedDate.getMonth() + 1);
    const d = pad(this.selectedDate.getDate());

    let formatted = `${y}-${m}-${d}`;
    if (this.isDateTime) {
      const hh = pad(this.selectedHours);
      const mm = pad(this.selectedMinutes);
      formatted += `T${hh}:${mm}`;
    }

    this.input.value = formatted;
    this.dispatchChange();
  }

  dispatchChange() {
    this.input.dispatchEvent(new Event("input", { bubbles: true }));
    this.input.dispatchEvent(new Event("change", { bubbles: true }));
    if (typeof this.options.onChange === "function") {
      this.options.onChange(this.input.value);
    }
  }

  destroy() {
    this.restoreNativeInput();
  }
}

/* ----------------------------------------------------------------- observers */

function ensureDynamicDatePickerObserver() {
  if (dynamicObserver || typeof MutationObserver !== "function") return;
  if (typeof document === "undefined" || !document.body) return;

  dynamicObserver = new MutationObserver((records) => {
    records.forEach((record) => {
      record.addedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.matches && isDateInputElement(node)) {
          safeInit(node);
        }
        if (node.querySelectorAll) {
          node.querySelectorAll('input[type="date"], input[type="datetime-local"], input[data-custom-date]').forEach(safeInit);
        }
      });
    });
  });

  dynamicObserver.observe(document.body, { childList: true, subtree: true });
}

function safeInit(input) {
  if (!input || input.customDatePicker || input.dataset.noCustom !== undefined) return null;
  try {
    return new CustomDatePicker(input);
  } catch (err) {
    console.warn("Could not auto-initialize CustomDatePicker on input:", input, err);
    return null;
  }
}

export function initCustomDatePickers(root = document) {
  if (!root) return [];
  injectCustomDatePickerStyles();
  const instances = [];

  const inputs = [];
  if (root.matches && isDateInputElement(root)) {
    inputs.push(root);
  }
  if (root.querySelectorAll) {
    root.querySelectorAll('input[type="date"], input[type="datetime-local"], input[data-custom-date]').forEach((el) => {
      inputs.push(el);
    });
  }

  inputs.forEach((input) => {
    const inst = safeInit(input);
    if (inst) instances.push(inst);
  });

  ensureDynamicDatePickerObserver();
  return Array.from(new Set(instances));
}
