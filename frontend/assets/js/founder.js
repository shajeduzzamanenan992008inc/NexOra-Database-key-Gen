/**
 * Founder identity reveal — shows the cinematic FOUNDER/CREATOR/BUILDER
 * typography after the boot screen finishes transitioning out.
 *
 * This module is page-agnostic: it finds the founder-reveal element and
 * wires it to the boot-screen transitionend event. Both index.html and
 * dashboard.html include it after their boot screen markup.
 */

(function () {
  "use strict";

  var bootScreen = document.getElementById("boot-screen");
  var founderReveal = document.getElementById("founder-reveal");
  if (!bootScreen || !founderReveal) return;

  function showFounder() {
    // Defer to the next frame so the boot-screen transition has started.
    requestAnimationFrame(function () {
      founderReveal.classList.add("is-visible");
      founderReveal.removeAttribute("aria-hidden");
    });
  }

  if (bootScreen.classList.contains("is-done")) {
    showFounder();
  } else {
    var handled = false;
    bootScreen.addEventListener(
      "transitionend",
      function handler() {
        if (handled) return;
        if (bootScreen.classList.contains("is-done")) {
          handled = true;
          bootScreen.removeEventListener("transitionend", handler);
          showFounder();
        }
      }
    );
    // Fallback: if transitionend never fires (e.g. reduced motion makes
    // the transition instant), show after a short delay.
    window.setTimeout(function () {
      if (!handled) {
        handled = true;
        showFounder();
      }
    }, 800);
  }
})();
