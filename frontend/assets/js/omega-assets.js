/* Central manager for the OMEGA animation assets.
 *
 * Static WebP is the baseline. Animated WebP is selected only when the user
 * has not requested reduced motion and the device is not in the lightweight
 * mode chosen by the motion layer. No assets are bundled into JavaScript.
 *
 * The approved NexOra brand source lives outside frontend/assets: the Flask
 * app serves assets/effect/ at /effect-assets/. Both the logo and the hero
 * banner are referenced from there so there is exactly one original copy.
 */

const BASE = "/omega-assets";
const EFFECT_BASE = "/effect-assets";

export const EFFECT_LOGO = `${EFFECT_BASE}/ultra-universal-keygen-logo-v3.svg`;
export const EFFECT_BANNER = `${EFFECT_BASE}/ultra-universal-keygen.svg`;

const KINDS = new Set([
  "logo",
  "logo-mark",
  "divider",
  "database-key",
  "reader-key",
  "license-key",
  "other-key",
  "key-generating",
  "key-verified",
  "favicon",
  "readme-icon",
]);

export function motionAllowed() {
  return (
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
    !document.documentElement.classList.contains("is-lite")
  );
}

export function effectAsset(filename) {
  return `${EFFECT_BASE}/${String(filename).replace(/^\/+/, "")}`;
}

export function omegaAsset(kind, { animated = motionAllowed(), webp = true } = {}) {
  const safeKind = KINDS.has(kind) ? kind : "other-key";
  if (safeKind === "favicon") return `${BASE}/webp/favicon.webp`;
  if (!webp) return `${BASE}/svg/${safeKind}.svg`;
  const suffix = animated ? "-animated" : "";
  return `${BASE}/webp/${safeKind}${suffix}.webp`;
}

export function hydrateOmegaImages(root = document) {
  root.querySelectorAll("[data-omega]").forEach((node) => {
    const kind = node.dataset.omega;

    // A <picture> lets the browser choose the animated source before any
    // request starts. Reduced motion and Lite mode disable that source so the
    // static fallback is the only artwork downloaded.
    if (node.tagName === "PICTURE") {
      const source = node.querySelector("source");
      if (source) {
        source.media = motionAllowed()
          ? "(prefers-reduced-motion: no-preference)"
          : "not all";
      }
      const img = node.querySelector("img");
      if (img) {
        img.loading = img.loading || "lazy";
        img.decoding = "async";
      }
      return;
    }

    node.src = omegaAsset(kind);
    node.loading = node.loading || "lazy";
    node.decoding = "async";
  });
}

export function refreshOmegaImages() {
  hydrateOmegaImages(document);
}

export function omegaPicture(kind, { className = "", alt = "", width, height } = {}) {
  const picture = document.createElement("picture");
  const source = document.createElement("source");
  source.type = "image/webp";
  source.srcset = omegaAsset(kind, { animated: true });
  const img = document.createElement("img");
  img.src = omegaAsset(kind, { animated: false, webp: true });
  img.alt = alt;
  img.className = className;
  if (width) img.width = width;
  if (height) img.height = height;
  img.loading = "lazy";
  img.decoding = "async";
  picture.append(source, img);
  return picture;
}

document.addEventListener("nexora:litechange", () => {
  refreshOmegaImages();
});

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
if (reduced.addEventListener) {
  reduced.addEventListener("change", () => refreshOmegaImages());
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => hydrateOmegaImages());
} else {
  hydrateOmegaImages();
}
