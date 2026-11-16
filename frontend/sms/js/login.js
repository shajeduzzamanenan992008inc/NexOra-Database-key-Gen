import { api, ApiProblem, setCsrfToken } from "./api.js";
import { $, setButtonBusy } from "/assets/js/ui.js";

const form = $("#login-form");
const emailInput = $("#email");
const passwordInput = $("#password");
const message = $("#login-message");
const submit = $("#login-submit");
const toggle = $("#toggle-password");

function showMessage(text, tone = "error") {
  message.textContent = text;
  message.dataset.tone = tone;
}

async function redirectIfSignedIn() {
  try {
    const session = await api.session();
    if (session && session.user) {
      setCsrfToken(session.csrf_token);
      window.location.replace("/dashboard");
    }
  } catch {
    // 401 is the expected state on the login page.
  }
}

toggle.addEventListener("click", () => {
  const showing = passwordInput.type === "text";
  passwordInput.type = showing ? "password" : "text";
  toggle.textContent = showing ? "Show" : "Hide";
  toggle.setAttribute("aria-pressed", String(!showing));
  toggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
  passwordInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMessage("");
  const email = emailInput.value.trim();
  const password = passwordInput.value;

  if (!email || !password) {
    showMessage("Enter your email and password.");
    if (!email) emailInput.focus();
    else passwordInput.focus();
    return;
  }

  setButtonBusy(submit, true, "Verifying…");
  try {
    const result = await api.login(email, password);
    setCsrfToken(result.csrf_token);
    showMessage("Access granted. Opening the SMS console…", "success");
    window.location.assign("/dashboard");
  } catch (error) {
    const retry =
      error instanceof ApiProblem &&
      error.details &&
      error.details.retry_after_seconds
        ? ` Try again in ${error.details.retry_after_seconds}s.`
        : "";
    showMessage(`${error.message || "Sign-in failed."}${retry}`);
    passwordInput.value = "";
    passwordInput.focus();
  } finally {
    setButtonBusy(submit, false);
  }
});

redirectIfSignedIn();
