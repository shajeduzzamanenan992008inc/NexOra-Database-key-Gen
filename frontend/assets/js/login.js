import { api, ApiProblem, setCsrfToken } from "./api.js";
import { $, announce, setButtonBusy } from "./ui.js";
import { dismissBootScreen, initMotion, prefersReducedMotion } from "./motion.js";

const REMEMBER_KEY = "nexora:remembered-email";

const form = $("#login-form");
const card = $(".omega-card");
const emailInput = $("#email");
const passwordInput = $("#password");
const message = $("#login-message");
const submit = $("#login-submit");
const toggle = $("#toggle-password");
const remember = $("#remember-me");
const recovery = $("#session-recovery");
const recoveryEmail = $("#session-email");
const resumeButton = $("#resume-session");
const signOutButton = $("#session-signout");

let submitting = false;
let redirecting = false;
let existingSession = false;

/* Boot is decorative only. It is dismissed on the first frame and never gates
   the form, the session probe, or navigation. */
initMotion();
dismissBootScreen();

/* --------------------------------------------------------------- storage */

function storageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Hardened/private contexts may refuse storage; the form still works.
  }
}

function storageRemove(key) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore.
  }
}

/* ------------------------------------------------------------- messaging */

function showMessage(text, tone = "error") {
  if (!message) return;
  message.textContent = text || "";
  if (text) {
    message.dataset.tone = tone;
    announce(text);
  } else {
    delete message.dataset.tone;
  }
}

function markInvalid(input, invalid) {
  if (!input) return;
  input.classList.toggle("is-invalid", invalid);
  if (invalid) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
}

function shakeCard() {
  if (!card) return;
  card.classList.remove("is-shaking");
  void card.offsetWidth;
  card.classList.add("is-shaking");
  window.setTimeout(() => card.classList.remove("is-shaking"), 520);
}

function setLoading(active) {
  if (submit) setButtonBusy(submit, active, "Verifying…");
  if (form) form.classList.toggle("is-submitting", active);
  if (card) card.classList.toggle("is-loading", active);
  if (form) form.setAttribute("aria-busy", active ? "true" : "false");
}

/* ------------------------------------------------------ session recovery */

function hideRecovery() {
  existingSession = false;
  if (recovery) recovery.hidden = true;
}

function showRecovery(user) {
  if (!recovery) return;
  existingSession = true;
  if (recoveryEmail) {
    const label = (user && (user.full_name || user.email)) || "Verified administrator";
    recoveryEmail.textContent = `${label} · session verified by the server`;
  }
  recovery.hidden = false;
}

async function probeSession() {
  try {
    const session = await api.session({ timeoutMs: 7000 });
    if (!session || !session.user) return;
    setCsrfToken(session.csrf_token);
    showRecovery(session.user);
  } catch (error) {
    // 401 is the expected signed-out state. A timeout or network failure also
    // must not block or bypass the login form.
    if (error instanceof ApiProblem && error.status === 401) {
      hideRecovery();
      return;
    }
    hideRecovery();
  }
}

function enterConsole() {
  if (redirecting) return;
  redirecting = true;
  window.location.assign("/dashboard");
}

if (resumeButton) {
  resumeButton.addEventListener("click", () => {
    setButtonBusy(resumeButton, true, "Opening…");
    enterConsole();
  });
}

if (signOutButton) {
  signOutButton.addEventListener("click", async () => {
    if (signOutButton.disabled) return;
    setButtonBusy(signOutButton, true, "Signing out…");
    try {
      await api.logoutSafe();
      setCsrfToken("");
      hideRecovery();
      showMessage("Session terminated. Sign in to continue.", "success");
      if (emailInput) emailInput.focus({ preventScroll: true });
    } catch (error) {
      showMessage(
        error && error.message
          ? error.message
          : "Sign-out failed. The previous session may still be active."
      );
    } finally {
      setButtonBusy(signOutButton, false);
    }
  });
}

/* --------------------------------------------------------- password eye */

if (toggle && passwordInput) {
  toggle.addEventListener("click", () => {
    const showing = passwordInput.type === "text";
    passwordInput.type = showing ? "password" : "text";
    toggle.textContent = showing ? "Show" : "Hide";
    toggle.setAttribute("aria-pressed", String(!showing));
    toggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    passwordInput.focus({ preventScroll: true });
  });
}

[emailInput, passwordInput].filter(Boolean).forEach((input) => {
  input.addEventListener("input", () => {
    markInvalid(input, false);
    if (message && message.dataset.tone === "error") showMessage("");
  });
});

/* ------------------------------------------------------- remembered email */

(function initRememberedEmail() {
  const saved = storageGet(REMEMBER_KEY);
  if (!saved) return;
  if (emailInput && !emailInput.value) emailInput.value = saved;
  if (remember) remember.checked = true;
})();

if (remember) {
  remember.addEventListener("change", () => {
    if (remember.checked) {
      const value = emailInput ? emailInput.value.trim() : "";
      if (value) storageSet(REMEMBER_KEY, value);
    } else {
      storageRemove(REMEMBER_KEY);
    }
  });
}

/* ----------------------------------------------------------------- login */

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || redirecting) return;

    const email = emailInput ? emailInput.value.trim() : "";
    const password = passwordInput ? passwordInput.value : "";

    markInvalid(emailInput, false);
    markInvalid(passwordInput, false);
    showMessage("");

    if (!email || !password) {
      if (!email) markInvalid(emailInput, true);
      if (!password) markInvalid(passwordInput, true);
      shakeCard();
      showMessage("Enter your email and password.");
      if (!email && emailInput) emailInput.focus();
      else if (passwordInput) passwordInput.focus();
      return;
    }

    submitting = true;
    setLoading(true);

    try {
      const result = await api.login(email, password);
      setCsrfToken(result && result.csrf_token);

      if (remember && remember.checked) storageSet(REMEMBER_KEY, email);
      if (passwordInput) passwordInput.value = "";

      form.classList.add("is-success");
      if (card) card.classList.add("is-success");
      hideRecovery();
      showMessage("Access granted. Opening your control center…", "success");

      // Navigation is scheduled, never awaited behind an animation. Reduced
      // motion and slow devices reach the dashboard at the same speed.
      window.setTimeout(enterConsole, prefersReducedMotion() ? 0 : 120);
    } catch (error) {
      const retryAfter =
        error instanceof ApiProblem &&
        error.details &&
        error.details.retry_after_seconds
          ? ` Try again in ${error.details.retry_after_seconds}s.`
          : "";

      showMessage(
        `${error && error.message ? error.message : "Sign-in failed."}${retryAfter}`
      );
      markInvalid(passwordInput, true);
      shakeCard();
      if (passwordInput) {
        passwordInput.value = "";
        passwordInput.focus();
      }
      setLoading(false);
      submitting = false;
    }
  });
}

/* A bfcache restore could show a stale authenticated shell. Reload so the
   server decides the truth. */
window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});

// Probe once, after the first paint. No automatic redirect and no automatic
// credential submission ever happen here.
window.requestAnimationFrame(() => {
  probeSession();
});
