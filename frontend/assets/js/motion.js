/* Shared motion layer: boot sequence, ambient pointer light, scroll progress,
   number counters, and the secure leave transition. CSS drives the visuals;
   this module only orchestrates state and respects reduced-motion choices. */

const numberFormatter = new Intl.NumberFormat();

let initialized = false;
let bootFallback = 0;
let leaveTimer = 0;

const LITE_STORAGE_KEY = "nexora:lite-mode";

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function isLiteMode() {
  return document.documentElement.classList.contains("is-lite");
}

export function detectsLowPowerDevice() {
  const connection =
    navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (connection && connection.saveData === true) return true;

  // Only genuinely constrained devices opt out automatically. A 4-core
  // laptop is common; treating it as low power silently removed the motion
  // system for most users.
  const memory = Number(navigator.deviceMemory);
  if (Number.isFinite(memory) && memory > 0 && memory <= 2) return true;

  const cores = Number(navigator.hardwareConcurrency);
  if (Number.isFinite(cores) && cores > 0 && cores <= 2) return true;

  return false;
}

export function getLitePreference() {
  try {
    const stored = window.localStorage.getItem(LITE_STORAGE_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
  } catch {
    // Storage can be unavailable in hardened/private contexts.
  }
  return null;
}

export function setLiteMode(enabled, { persist = true } = {}) {
  const active = Boolean(enabled);
  document.documentElement.classList.toggle("is-lite", active);

  if (persist) {
    try {
      window.localStorage.setItem(LITE_STORAGE_KEY, active ? "1" : "0");
    } catch {
      // The visual preference still applies for this page view.
    }
  }

  window.dispatchEvent(
    new CustomEvent("nexora:litechange", { detail: { lite: active } })
  );
  return active;
}

export function applyLitePreference() {
  const stored = getLitePreference();
  return setLiteMode(stored === null ? detectsLowPowerDevice() : stored, {
    persist: false,
  });
}

export function initMotion() {
  if (initialized) return;
  initialized = true;

  applyLitePreference();
  document.documentElement.classList.add("motion-ready");
  requestAnimationFrame(() => document.documentElement.classList.add("app-ready"));

  initCursorGlow();
  initScrollProgress();
  initAmbientPause();
  initMagneticButtons();

  bootFallback = window.setTimeout(dismissBootScreen, 2000);
}

export function dismissBootScreen() {
  window.clearTimeout(bootFallback);
  const boot = document.getElementById("boot-screen");
  if (!boot || boot.classList.contains("is-done")) return;

  boot.classList.add("is-done");
  boot.setAttribute("aria-hidden", "true");
  window.setTimeout(() => {
    if (boot.classList.contains("is-done")) boot.hidden = true;
  }, 700);
}

export function playLeaveScreen({ kicker, title, status, duration = 720 } = {}) {
  const screen = document.getElementById("leave-screen");
  if (!screen) return Promise.resolve();

  if (kicker) {
    const node = screen.querySelector(".leave-kicker");
    if (node) node.textContent = kicker;
  }
  if (title) {
    const node = screen.querySelector(".leave-title");
    if (node) node.textContent = title;
  }
  if (status) {
    const node = screen.querySelector(".leave-status");
    if (node) node.textContent = status;
  }

  screen.hidden = false;
  void screen.offsetWidth;
  screen.classList.add("is-active");

  window.clearTimeout(leaveTimer);
  return new Promise((resolve) => {
    leaveTimer = window.setTimeout(resolve, prefersReducedMotion() ? 80 : duration);
  });
}

export function hideLeaveScreen() {
  const screen = document.getElementById("leave-screen");
  if (!screen) return;
  screen.classList.remove("is-active");
  screen.hidden = true;
}

export function animateNumber(node, target, { duration = 850, formatter = numberFormatter } = {}) {
  if (!node) return;
  const value = Number(target);

  if (!Number.isFinite(value)) {
    node.textContent = "—";
    return;
  }

  if (prefersReducedMotion()) {
    node.textContent = formatter.format(value);
    node.dataset.countValue = String(value);
    return;
  }

  const previous = Number(node.dataset.countValue);
  const start = Number.isFinite(previous) ? previous : 0;
  node.dataset.countValue = String(value);

  if (node._countFrame) window.cancelAnimationFrame(node._countFrame);

  const startedAt = performance.now();
  const span = Math.max(220, duration);

  const step = (now) => {
    const progress = Math.min(1, (now - startedAt) / span);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.round(start + (value - start) * eased);
    node.textContent = formatter.format(current);

    if (progress < 1) {
      node._countFrame = window.requestAnimationFrame(step);
    } else {
      node.textContent = formatter.format(value);
      node._countFrame = 0;
    }
  };

  node._countFrame = window.requestAnimationFrame(step);
}

export function pulseElement(node, className = "is-pulse") {
  if (!node) return;
  node.classList.remove(className);
  void node.offsetWidth;
  node.classList.add(className);
  window.setTimeout(() => node.classList.remove(className), 900);
}

export function animateTableRows(tbody, limit = 12) {
  if (!tbody) return;
  Array.from(tbody.children).slice(0, limit).forEach((row, index) => {
    row.style.setProperty("animation-delay", `${Math.min(index * 32, 260)}ms`);
  });
}

function initScrollProgress() {
  const bar = document.getElementById("scroll-progress");
  if (!bar) return;

  let ticking = false;

  const update = () => {
    ticking = false;
    const root = document.documentElement;
    const max = Math.max(1, root.scrollHeight - window.innerHeight);
    const ratio = Math.min(1, Math.max(0, window.scrollY / max));
    bar.style.setProperty("--scroll-progress", ratio.toFixed(4));
  };

  const requestUpdate = () => {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(update);
  };

  window.addEventListener("scroll", requestUpdate, { passive: true });
  window.addEventListener("resize", requestUpdate, { passive: true });
  update();
}

function initAmbientPause() {
  const root = document.documentElement;

  const sync = () => {
    root.classList.toggle("is-paused", document.visibilityState === "hidden");
  };

  document.addEventListener("visibilitychange", sync);
  sync();
}

function initMagneticButtons() {
  if (!window.matchMedia("(pointer: fine)").matches) return;

  let active = null;
  let point = null;
  let frame = 0;

  const reset = (node) => {
    if (!node) return;
    node.style.removeProperty("--mag-x");
    node.style.removeProperty("--mag-y");
  };

  const paint = () => {
    frame = 0;
    if (!active || !point) return;
    if (prefersReducedMotion() || isLiteMode()) {
      reset(active);
      return;
    }

    const rect = active.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    const x = ((point.clientX - rect.left) / rect.width - 0.5) * 5;
    const y = ((point.clientY - rect.top) / rect.height - 0.5) * 4;
    active.style.setProperty("--mag-x", `${x.toFixed(2)}px`);
    active.style.setProperty("--mag-y", `${y.toFixed(2)}px`);
  };

  document.addEventListener(
    "pointermove",
    (event) => {
      if (isLiteMode() || prefersReducedMotion()) return;
      const node = event.target instanceof Element ? event.target.closest(".btn") : null;

      if (active && active !== node) {
        reset(active);
        active = null;
      }
      if (!node || node.disabled) return;

      active = node;
      point = event;
      if (!frame) frame = window.requestAnimationFrame(paint);
    },
    { passive: true }
  );

  document.addEventListener(
    "pointerout",
    (event) => {
      if (!active) return;
      if (event.relatedTarget instanceof Node && active.contains(event.relatedTarget)) {
        return;
      }
      if (event.target instanceof Element && event.target.closest(".btn") === active) {
        reset(active);
        active = null;
      }
    },
    { passive: true }
  );

  window.addEventListener("blur", () => {
    reset(active);
    active = null;
  });
}

function initCursorGlow() {
  const glow = document.querySelector(".cursor-glow");
  if (!glow) return;
  if (!window.matchMedia("(pointer: fine)").matches) {
    glow.style.display = "none";
    return;
  }

  let frame = 0;
  let x = window.innerWidth * 0.5;
  let y = window.innerHeight * 0.28;

  const paint = () => {
    frame = 0;
    if (isLiteMode() || prefersReducedMotion()) return;
    glow.style.setProperty("--glow-x", `${x}px`);
    glow.style.setProperty("--glow-y", `${y}px`);
  };

  window.addEventListener(
    "pointermove",
    (event) => {
      if (event.pointerType && event.pointerType !== "mouse" && event.pointerType !== "pen") {
        return;
      }
      x = event.clientX;
      y = event.clientY;
      if (!frame) frame = window.requestAnimationFrame(paint);
    },
    { passive: true }
  );

  document.addEventListener("pointerleave", () => {
    glow.style.opacity = "0.16";
  });
  document.addEventListener("pointerenter", () => {
    glow.style.opacity = "";
  });
}
