/* Secure sign-out choreography.
 *
 * Order is deliberate:
 *   1. the server session is revoked by the caller before this module animates;
 *   2. local sensitive state is cleared;
 *   3. a bounded shutdown scene plays;
 *   4. navigation happens even if the scene fails or reduced motion is set.
 *
 * The animation never owns the logout result. A hard timer guarantees that a
 * broken image, a stalled frame, or a hidden document cannot trap the user.
 */

import { omegaAsset } from "./omega-assets.js";
import { prefersReducedMotion } from "./motion.js";

const SCENE_TIMEOUT_MS = 2600;
let playing = false;

function clearSensitiveState() {
  try {
    window.sessionStorage.clear();
  } catch {
    // Storage can be unavailable in hardened contexts.
  }
  try {
    for (const key of Object.keys(window.localStorage)) {
      if (/token|session|csrf|secret|credential/i.test(key) && !/remembered-email/i.test(key)) {
        window.localStorage.removeItem(key);
      }
    }
  } catch {
    // Ignore storage refusal.
  }
}

function buildScene() {
  const scene = document.createElement("div");
  scene.className = "shutdown-screen";
  scene.setAttribute("role", "status");
  scene.setAttribute("aria-live", "polite");
  scene.setAttribute("aria-label", "Secure shutdown");

  const core = document.createElement("div");
  core.className = "shutdown-core";
  core.setAttribute("aria-hidden", "true");

  const ringA = document.createElement("span");
  ringA.className = "shutdown-ring shutdown-ring-a";
  const ringB = document.createElement("span");
  ringB.className = "shutdown-ring shutdown-ring-b";
  const logo = document.createElement("img");
  logo.src = omegaAsset("logo", { animated: !prefersReducedMotion() });
  logo.alt = "";
  logo.width = 96;
  logo.height = 96;
  logo.decoding = "async";
  logo.addEventListener("error", () => {
    logo.hidden = true;
  });
  core.append(ringA, ringB, logo);

  const kicker = document.createElement("p");
  kicker.className = "shutdown-kicker";
  kicker.textContent = "OMEGA NEX-ORA / SECURE SHUTDOWN";

  const title = document.createElement("h2");
  title.className = "shutdown-title";
  title.textContent = "Session terminated";

  const status = document.createElement("p");
  status.className = "shutdown-status";
  status.textContent = "SERVER SESSION REVOKED · LOCAL STATE CLEARED · CORE CLOSING";

  const identity = document.createElement("div");
  identity.className = "shutdown-identity";
  const tier = document.createElement("span");
  tier.className = "founder-tier";
  tier.textContent = "Founder, Creator & Builder";
  const name = document.createElement("span");
  name.className = "founder-name";
  name.textContent = "Shajeduzzaman Enan";
  identity.append(tier, name);

  const progress = document.createElement("div");
  progress.className = "shutdown-progress";
  progress.setAttribute("aria-hidden", "true");
  progress.append(document.createElement("span"));

  scene.append(core, kicker, title, status, identity, progress);
  return scene;
}

export function runShutdownAnimation({ duration = 1500 } = {}) {
  if (playing) return Promise.resolve();
  playing = true;

  return new Promise((resolve) => {
    let settled = false;
    let scene = null;
    let timer = 0;

    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      try {
        if (scene) {
          scene.classList.add("is-finished");
          window.setTimeout(() => scene?.remove(), 260);
        }
      } catch {
        scene?.remove();
      }
      playing = false;
      resolve();
    };

    try {
      scene = buildScene();
      document.body.append(scene);
      const reduced =
        prefersReducedMotion() ||
        document.documentElement.classList.contains("is-lite");
      const total = reduced ? 420 : Math.max(600, Math.min(2200, duration));
      scene.style.setProperty("--shutdown-duration", `${total}ms`);
      requestAnimationFrame(() => scene.classList.add("is-active"));
      scene.addEventListener("click", finish, { once: true });
      timer = window.setTimeout(finish, reduced ? 700 : Math.max(total + 500, 1200));
    } catch {
      // A visual failure must never block navigation.
      finish();
    }
  });
}

export async function secureSignOut({
  logout,
  onRevoked,
  navigateTo = "/login",
  duration = 1500,
} = {}) {
  // Revocation is first and is never animated. If it fails, the caller keeps
  // the authenticated UI and reports the real error.
  await logout();
  try {
    onRevoked?.();
  } catch {
    // Local cleanup is best-effort; the server session is already gone.
  }
  clearSensitiveState();
  try {
    await runShutdownAnimation({ duration });
  } catch {
    // Navigation below still runs.
  }
  window.location.replace(navigateTo);
}

export { clearSensitiveState, SCENE_TIMEOUT_MS };
