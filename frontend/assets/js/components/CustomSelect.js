/**
 * CustomSelect - Fully stylable dropdown replacement for native <select>
 * Supports: hover effects, animations, transitions, keyboard navigation, search, multi-select
 */

export class CustomSelect {
  constructor(element, options = {}) {
    this.element = element;
    this.select = element.querySelector('select') || element;
    this.options = {
      searchable: options.searchable ?? false,
      multi: options.multi ?? false,
      placeholder: options.placeholder ?? 'Select...',
      animate: options.animate ?? true,
      duration: options.duration ?? 200,
      easing: options.easing ?? 'cubic-bezier(0.22, 1, 0.36, 1)',
      onChange: options.onChange ?? (() => {}),
      ...options
    };

    this.isOpen = false;
    this.focusedIndex = -1;
    this.selectedValues = new Set();
    this.filteredOptions = [];
    this.allOptions = [];

    this.init();
  }

  init() {
    // Store original select reference
    this.originalSelect = this.select;
    if (this.select.tagName === 'SELECT') {
      this.extractOptions();
      this.createCustomDropdown();
      this.hideOriginalSelect();
    } else {
      // Already a custom dropdown container
      this.dropdown = this.select;
      this.parseExistingOptions();
    }

    this.bindEvents();
    this.updateSelectedDisplay();
  }

  extractOptions() {
    const select = this.select;
    this.allOptions = Array.from(select.options).map((opt, index) => ({
      value: opt.value,
      label: opt.textContent,
      selected: opt.selected,
      disabled: opt.disabled,
      index
    }));

    // Set initial selected values
    this.allOptions.forEach(opt => {
      if (opt.selected) this.selectedValues.add(opt.value);
    });
  }

  parseExistingOptions() {
    // Parse from existing custom dropdown structure
    const items = this.dropdown.querySelectorAll('.custom-select-option');
    this.allOptions = Array.from(items).map((item, index) => ({
      value: item.dataset.value,
      label: item.textContent.trim(),
      selected: item.classList.contains('selected'),
      disabled: item.classList.contains('disabled'),
      index
    }));

    this.allOptions.forEach(opt => {
      if (opt.selected) this.selectedValues.add(opt.value);
    });
  }

  createCustomDropdown() {
    const select = this.select;
    const wrapper = document.createElement('div');
    wrapper.className = 'custom-select-wrapper';
    wrapper.dataset.customSelect = '';

    // Copy relevant attributes
    if (select.id) wrapper.id = `${select.id}-custom`;
    if (select.className) wrapper.classList.add(...select.className.split(' ').filter(c => c));
    if (select.disabled) wrapper.classList.add('disabled');

    // Create trigger button
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'custom-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('tabindex', '0');
    trigger.innerHTML = `
      <span class="custom-select-value"></span>
      <span class="custom-select-arrow" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M4 6L8 10L12 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </span>
    `;

    // Create dropdown panel
    const panel = document.createElement('div');
    panel.className = 'custom-select-panel';
    panel.setAttribute('role', 'listbox');
    panel.setAttribute('aria-label', select.getAttribute('aria-label') || 'Options');
    panel.hidden = true;

    // Search input (if searchable)
    let searchInput = null;
    if (this.options.searchable) {
      const searchWrapper = document.createElement('div');
      searchWrapper.className = 'custom-select-search-wrapper';
      searchInput = document.createElement('input');
      searchInput.type = 'search';
      searchInput.className = 'custom-select-search';
      searchInput.placeholder = 'Search options...';
      searchInput.setAttribute('aria-label', 'Search options');
      searchWrapper.appendChild(searchInput);
      panel.appendChild(searchWrapper);
    }

    // Options list
    const list = document.createElement('ul');
    list.className = 'custom-select-options';
    list.setAttribute('role', 'presentation');
    panel.appendChild(list);

    wrapper.appendChild(trigger);
    wrapper.appendChild(panel);

    // Insert before original select
    select.parentNode.insertBefore(wrapper, select);

    this.wrapper = wrapper;
    this.trigger = trigger;
    this.panel = panel;
    this.list = list;
    this.searchInput = searchInput;
    this.valueDisplay = trigger.querySelector('.custom-select-value');

    this.renderOptions();
  }

  renderOptions(filter = '') {
    this.filteredOptions = this.allOptions.filter(opt => {
      if (opt.disabled) return false;
      if (!filter) return true;
      return opt.label.toLowerCase().includes(filter.toLowerCase());
    });

    this.list.innerHTML = '';

    if (this.filteredOptions.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'custom-select-empty';
      empty.textContent = 'No options found';
      this.list.appendChild(empty);
      return;
    }

    this.filteredOptions.forEach((opt, index) => {
      const li = document.createElement('li');
      li.className = 'custom-select-option';
      li.dataset.value = opt.value;
      li.dataset.index = index;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', this.selectedValues.has(opt.value).toString());
      if (opt.disabled) li.classList.add('disabled');
      if (this.selectedValues.has(opt.value)) li.classList.add('selected');

      li.innerHTML = `
        <span class="custom-select-option-label">${this.escapeHtml(opt.label)}</span>
        ${this.options.multi && this.selectedValues.has(opt.value) ? `
          <svg class="custom-select-check" width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M1 6.5L5.5 11L13 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        ` : ''}
      `;

      this.list.appendChild(li);
    });

    this.focusedIndex = -1;
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  bindEvents() {
    // Trigger click/toggle
    this.trigger.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.toggle();
    });

    // Keyboard navigation on trigger
    this.trigger.addEventListener('keydown', (e) => this.handleTriggerKeydown(e));

    // Option clicks
    this.list.addEventListener('click', (e) => {
      const option = e.target.closest('.custom-select-option');
      if (option && !option.classList.contains('disabled')) {
        this.selectOption(option.dataset.value, option);
      }
    });

    // Option hover for keyboard navigation highlight
    this.list.addEventListener('mouseover', (e) => {
      const option = e.target.closest('.custom-select-option');
      if (option && !option.classList.contains('disabled')) {
        this.setFocusedOption(option);
      }
    });

    // Search input
    if (this.searchInput) {
      this.searchInput.addEventListener('input', (e) => {
        this.renderOptions(e.target.value);
      });
      this.searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          this.close();
        }
      });
      // Prevent search input click from closing dropdown
      this.searchInput.addEventListener('click', (e) => e.stopPropagation());
    }

    // Click outside to close
    document.addEventListener('click', (e) => {
      if (!this.wrapper.contains(e.target)) {
        this.close();
      }
    });

    // Focus management
    this.trigger.addEventListener('focus', () => this.wrapper.classList.add('focused'));
    this.trigger.addEventListener('blur', () => this.wrapper.classList.remove('focused'));

    // Sync with original select if it exists
    if (this.originalSelect && this.originalSelect.tagName === 'SELECT') {
      this.originalSelect.addEventListener('change', () => {
        this.syncFromOriginal();
      });
    }
  }

  handleTriggerKeydown(e) {
    switch (e.key) {
      case 'Enter':
      case ' ':
        e.preventDefault();
        this.toggle();
        break;
      case 'Escape':
        this.close();
        break;
      case 'ArrowDown':
        e.preventDefault();
        if (!this.isOpen) {
          this.open();
        } else {
          this.focusNext();
        }
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (this.isOpen) {
          this.focusPrevious();
        }
        break;
      case 'Tab':
        this.close();
        break;
      default:
        // Type-ahead search
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          this.typeAheadSearch(e.key);
        }
    }
  }

  typeAheadSearch(char) {
    // Simple type-ahead: find first option starting with typed character
    const options = this.list.querySelectorAll('.custom-select-option:not(.disabled)');
    const lowerChar = char.toLowerCase();
    for (const option of options) {
      const label = option.querySelector('.custom-select-option-label')?.textContent?.toLowerCase() || '';
      if (label.startsWith(lowerChar)) {
        this.setFocusedOption(option);
        option.scrollIntoView({ block: 'nearest' });
        break;
      }
    }
  }

  setFocusedOption(option) {
    // Remove previous focus
    this.list.querySelectorAll('.custom-select-option.focused').forEach(el => {
      el.classList.remove('focused');
    });
    // Add focus to new option
    option.classList.add('focused');
    this.focusedIndex = this.getOptionIndex(option);
    option.scrollIntoView({ block: 'nearest' });
  }

  getOptionIndex(option) {
    return Array.from(this.list.children).indexOf(option);
  }

  focusNext() {
    const options = Array.from(this.list.querySelectorAll('.custom-select-option:not(.disabled)'));
    if (options.length === 0) return;
    this.focusedIndex = (this.focusedIndex + 1) % options.length;
    this.setFocusedOption(options[this.focusedIndex]);
  }

  focusPrevious() {
    const options = Array.from(this.list.querySelectorAll('.custom-select-option:not(.disabled)'));
    if (options.length === 0) return;
    this.focusedIndex = (this.focusedIndex - 1 + options.length) % options.length;
    this.setFocusedOption(options[this.focusedIndex]);
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }

  open() {
    if (this.wrapper.classList.contains('disabled')) return;

    this.isOpen = true;
    this.wrapper.classList.add('open');
    this.trigger.setAttribute('aria-expanded', 'true');
    this.panel.hidden = false;

    // Animate in
    if (this.options.animate) {
      requestAnimationFrame(() => {
        this.panel.style.opacity = '1';
        this.panel.style.transform = 'translateY(0)';
      });
    }

    // Focus search input if searchable
    if (this.searchInput) {
      setTimeout(() => this.searchInput.focus(), 50);
    } else if (this.focusedIndex >= 0) {
      const options = this.list.querySelectorAll('.custom-select-option:not(.disabled)');
      if (options[this.focusedIndex]) {
        this.setFocusedOption(options[this.focusedIndex]);
      }
    }

    // Close other dropdowns
    document.querySelectorAll('.custom-select-wrapper.open').forEach(w => {
      if (w !== this.wrapper) {
        const cs = w.customSelect;
        if (cs && cs.close) cs.close();
      }
    });
  }

  close() {
    if (!this.isOpen) return;

    this.isOpen = false;
    this.wrapper.classList.remove('open');
    this.trigger.setAttribute('aria-expanded', 'false');

    if (this.options.animate) {
      this.panel.style.opacity = '0';
      this.panel.style.transform = 'translateY(-8px)';
      setTimeout(() => {
        if (!this.isOpen) this.panel.hidden = true;
      }, this.options.duration);
    } else {
      this.panel.hidden = true;
    }

    // Clear search
    if (this.searchInput) {
      this.searchInput.value = '';
      this.renderOptions('');
    }

    // Return focus to trigger
    this.trigger.focus();
  }

  selectOption(value, optionElement = null) {
    const opt = this.allOptions.find(o => o.value === value);
    if (!opt || opt.disabled) return;

    if (this.options.multi) {
      if (this.selectedValues.has(value)) {
        this.selectedValues.delete(value);
      } else {
        this.selectedValues.add(value);
      }
    } else {
      // Single select: replace selection
      this.selectedValues.clear();
      this.selectedValues.add(value);
      this.close();
    }

    this.updateSelectedDisplay();
    this.updateOptionStates();

    // Fire change event on original select
    if (this.originalSelect && this.originalSelect.tagName === 'SELECT') {
      this.syncToOriginal();
    }

    // Trigger callback
    this.options.onChange(Array.from(this.selectedValues), opt);
  }

  updateOptionStates() {
    this.list.querySelectorAll('.custom-select-option').forEach(option => {
      const value = option.dataset.value;
      const isSelected = this.selectedValues.has(value);
      option.classList.toggle('selected', isSelected);
      option.setAttribute('aria-selected', isSelected.toString());

      // Update checkmark for multi-select
      const check = option.querySelector('.custom-select-check');
      if (this.options.multi) {
        if (isSelected && !check) {
          const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          svg.className = 'custom-select-check';
          svg.setAttribute('width', '14');
          svg.setAttribute('height', '14');
          svg.setAttribute('viewBox', '0 0 14 14');
          svg.setAttribute('fill', 'none');
          svg.innerHTML = '<path d="M1 6.5L5.5 11L13 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
          option.appendChild(svg);
        } else if (!isSelected && check) {
          check.remove();
        }
      }
    });
  }

  updateSelectedDisplay() {
    if (!this.valueDisplay) return;

    if (this.selectedValues.size === 0) {
      this.valueDisplay.textContent = this.options.placeholder;
      this.valueDisplay.classList.add('placeholder');
    } else if (this.options.multi) {
      const labels = this.allOptions
        .filter(opt => this.selectedValues.has(opt.value))
        .map(opt => opt.label);
      this.valueDisplay.textContent = labels.join(', ');
      this.valueDisplay.classList.remove('placeholder');
    } else {
      const selected = this.allOptions.find(opt => this.selectedValues.has(opt.value));
      this.valueDisplay.textContent = selected ? selected.label : this.options.placeholder;
      this.valueDisplay.classList.remove('placeholder');
    }
  }

  syncFromOriginal() {
    if (!this.originalSelect || this.originalSelect.tagName !== 'SELECT') return;
    this.selectedValues.clear();
    Array.from(this.originalSelect.selectedOptions).forEach(opt => {
      this.selectedValues.add(opt.value);
    });
    this.updateSelectedDisplay();
    this.updateOptionStates();
  }

  syncToOriginal() {
    if (!this.originalSelect || this.originalSelect.tagName !== 'SELECT') return;

    Array.from(this.originalSelect.options).forEach(opt => {
      opt.selected = this.selectedValues.has(opt.value);
    });

    // Dispatch change event
    this.originalSelect.dispatchEvent(new Event('change', { bubbles: true }));
  }

  hideOriginalSelect() {
    if (this.originalSelect && this.originalSelect.tagName === 'SELECT') {
      this.originalSelect.style.display = 'none';
      this.originalSelect.setAttribute('aria-hidden', 'true');
      this.originalSelect.tabIndex = -1;
    }
  }

  // Public API
  setValue(value) {
    if (Array.isArray(value)) {
      this.selectedValues.clear();
      value.forEach(v => this.selectedValues.add(v));
    } else {
      this.selectedValues.clear();
      this.selectedValues.add(value);
    }
    this.updateSelectedDisplay();
    this.updateOptionStates();
    this.syncToOriginal();
  }

  getValue() {
    return this.options.multi ? Array.from(this.selectedValues) : this.selectedValues.values().next().value;
  }

  addOption(value, label, selected = false) {
    this.allOptions.push({ value, label, selected, disabled: false, index: this.allOptions.length });
    if (selected) this.selectedValues.add(value);
    this.renderOptions(this.searchInput?.value || '');
    this.updateSelectedDisplay();
  }

  removeOption(value) {
    this.allOptions = this.allOptions.filter(opt => opt.value !== value);
    this.selectedValues.delete(value);
    this.renderOptions(this.searchInput?.value || '');
    this.updateSelectedDisplay();
  }

  disable() {
    this.wrapper.classList.add('disabled');
    this.trigger.disabled = true;
    this.trigger.setAttribute('aria-disabled', 'true');
    this.close();
  }

  enable() {
    this.wrapper.classList.remove('disabled');
    this.trigger.disabled = false;
    this.trigger.removeAttribute('aria-disabled');
  }

  destroy() {
    document.removeEventListener('click', this.close);
    if (this.wrapper && this.wrapper.parentNode) {
      this.wrapper.parentNode.insertBefore(this.originalSelect, this.wrapper);
      this.wrapper.remove();
    }
    this.originalSelect.style.display = '';
    this.originalSelect.removeAttribute('aria-hidden');
    this.originalSelect.tabIndex = 0;
  }
}

// Auto-initialize on data-custom-select attribute
export function initCustomSelects(root = document) {
  root.querySelectorAll('select[data-custom-select], .custom-select-wrapper[data-custom-select]').forEach(el => {
    if (!el.customSelect) {
      el.customSelect = new CustomSelect(el);
    }
  });
  return Array.from(root.querySelectorAll('[data-custom-select]')).map(el => el.customSelect);
}

// CSS injection for component styles
export function injectCustomSelectStyles() {
  if (document.getElementById('custom-select-styles')) return;

  const style = document.createElement('style');
  style.id = 'custom-select-styles';
  style.textContent = `
/* ==========================================================================
   CustomSelect Component Styles
   ========================================================================== */

.custom-select-wrapper {
  position: relative;
  display: inline-block;
  width: 100%;
  max-width: 100%;
  font-family: inherit;
  font-size: 0.86rem;
  line-height: 1.5;
}

.custom-select-wrapper.disabled {
  opacity: 0.55;
  pointer-events: none;
}

.custom-select-trigger {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 46px;
  padding: 11px 14px 11px 40px;
  border-radius: 12px;
  border: 1px solid rgba(120, 170, 255, 0.16);
  background: rgba(3, 8, 17, 0.72);
  color: var(--text);
  font: inherit;
  font-size: 0.86rem;
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    background 0.18s ease;
}

.custom-select-wrapper:not(.disabled) .custom-select-trigger:hover {
  border-color: rgba(55, 226, 255, 0.3);
}

.custom-select-wrapper.focused .custom-select-trigger,
.custom-select-wrapper.open .custom-select-trigger {
  outline: none;
  border-color: rgba(55, 226, 255, 0.55);
  box-shadow: 0 0 0 3px rgba(55, 226, 255, 0.12), 0 0 26px rgba(55, 226, 255, 0.07);
  background: rgba(4, 12, 24, 0.92);
}

.custom-select-value {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-dim);
}

.custom-select-value.placeholder {
  color: var(--muted);
}

.custom-select-arrow {
  flex: none;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  color: var(--muted);
  transition: transform 0.2s var(--ease-out);
}

.custom-select-wrapper.open .custom-select-arrow {
  transform: rotate(180deg);
}

.custom-select-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: 1000;
  border-radius: 12px;
  border: 1px solid rgba(120, 170, 255, 0.18);
  background: rgba(3, 8, 17, 0.96);
  backdrop-filter: blur(14px) saturate(120%);
  -webkit-backdrop-filter: blur(14px) saturate(120%);
  box-shadow:
    0 20px 54px rgba(0, 0, 0, 0.62),
    0 0 24px rgba(55, 226, 255, 0.08);
  overflow: hidden;
  opacity: 0;
  transform: translateY(-8px);
  transition:
    opacity 0.2s var(--ease-out),
    transform 0.2s var(--ease-out);
}

.custom-select-wrapper.open .custom-select-panel {
  opacity: 1;
  transform: translateY(0);
}

.custom-select-search-wrapper {
  padding: 8px;
  border-bottom: 1px solid rgba(112, 168, 255, 0.12);
}

.custom-select-search {
  width: 100%;
  min-height: 38px;
  padding: 8px 12px;
  border-radius: 10px;
  border: 1px solid rgba(120, 170, 255, 0.16);
  background: rgba(3, 8, 17, 0.72);
  color: var(--text);
  font: inherit;
  font-size: 0.8rem;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.custom-select-search:focus {
  outline: none;
  border-color: rgba(55, 226, 255, 0.55);
  box-shadow: 0 0 0 3px rgba(55, 226, 255, 0.12);
}

.custom-select-options {
  list-style: none;
  margin: 0;
  padding: 6px;
  max-height: 320px;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: rgba(55, 226, 255, 0.3) transparent;
}

.custom-select-options::-webkit-scrollbar { width: 6px; }
.custom-select-options::-webkit-scrollbar-thumb {
  background: rgba(55, 226, 255, 0.24);
  border-radius: 999px;
}

.custom-select-option {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 9px;
  color: var(--text);
  font-size: 0.82rem;
  cursor: pointer;
  transition:
    background 0.12s ease,
    color 0.12s ease;
}

.custom-select-option:hover:not(.disabled),
.custom-select-option.focused:not(.disabled) {
  background: rgba(55, 226, 255, 0.07);
  color: var(--cyan);
}

.custom-select-option:active:not(.disabled) {
  background: rgba(55, 226, 255, 0.12);
}

.custom-select-option.disabled {
  opacity: 0.4;
  cursor: not-allowed;
  color: var(--muted);
}

.custom-select-option.selected {
  background: rgba(55, 226, 255, 0.09);
}

.custom-select-option-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.custom-select-check {
  flex: none;
  width: 14px;
  height: 14px;
  color: var(--cyan);
}

.custom-select-empty {
  padding: 16px 12px;
  text-align: center;
  color: var(--muted);
  font-size: 0.78rem;
}

.custom-select-wrapper.disabled .custom-select-trigger {
  cursor: not-allowed;
}

/* Animation keyframes */
@keyframes custom-select-option-in {
  from { opacity: 0; transform: translateY(4px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes custom-select-panel-in {
  from { opacity: 0; transform: translateY(-8px) scale(0.99); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.custom-select-wrapper.open .custom-select-option {
  animation: custom-select-option-in 0.2s var(--ease-out) both;
}

.custom-select-wrapper.open .custom-select-option:nth-child(1) { animation-delay: 0ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(2) { animation-delay: 20ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(3) { animation-delay: 40ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(4) { animation-delay: 60ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(5) { animation-delay: 80ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(6) { animation-delay: 100ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(7) { animation-delay: 120ms; }
.custom-select-wrapper.open .custom-select-option:nth-child(8) { animation-delay: 140ms; }

/* Reduced motion support */
@media (prefers-reduced-motion: reduce) {
  .custom-select-panel,
  .custom-select-trigger,
  .custom-select-option {
    transition-duration: 0.001ms !important;
    animation-duration: 0.001ms !important;
  }
}

/* Lite mode */
html.is-lite .custom-select-panel {
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  background: rgba(3, 8, 17, 1);
}

/* Focus visible */
.custom-select-trigger:focus-visible {
  outline: 2px solid var(--cyan);
  outline-offset: 2px;
}
  `;
  document.head.appendChild(style);
}

// Export for module use
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CustomSelect, initCustomSelects, injectCustomSelectStyles };
}