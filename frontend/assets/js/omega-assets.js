/* Central manager for the OMEGA animation assets.
 *
 * Static WebP is the baseline. Animated WebP is selected only when the user
 * has not requested reduced motion and the device is not in the lightweight
 * mode chosen by the motion layer. No assets are bundled into JavaScript.
 */

const BASE = "/omega-assets";
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

export function omegaAsset(kind, { animated = motionAllowed(), webp = true } = {}) {
  const safeKind = KINDS.has(kind) ? kind : "other-key";
  if (safeKind === "favicon") return `${BASE}/favicon-32.png`;
  if (!webp) return `${BASE}/svg/${safeKind}.svg`;
  const suffix = animated ? "-animated" : "";
  return `${BASE}/webp/${safeKind}${suffix}.webp`;
}

export function hydrateOmegaImages(root = document) {
  root.querySelectorAll("[data-omega]").forEach((img) => {
    const kind = img.dataset.omega;
    img.src = omegaAsset(kind);
    img.loading = img.loading || "lazy";
    img.decoding = "async";
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
