import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("./CapStackScoutStage.css", import.meta.url), "utf8");

test("Cap Stack feedback stays hidden on fine-pointer desktop until card hover or keyboard focus", () => {
  const start = css.indexOf("@media(hover:hover) and (pointer:fine){");
  assert.ok(start >= 0, "desktop hover-only rules must exist");
  const end = css.indexOf("\n}", start);
  assert.ok(end > start, "hover media query must close");
  const hoverStyles = css.slice(start, end);
  assert.match(hoverStyles, /\.rrugc-cap-stack-votes\.is-overlay\{opacity:0;visibility:hidden;pointer-events:none/);
  assert.match(hoverStyles, /\.rrugc-cap-stack-card:hover \.rrugc-cap-stack-votes\.is-overlay/);
  assert.match(hoverStyles, /\.rrugc-cap-stack-card:focus-within \.rrugc-cap-stack-votes\.is-overlay/);
  assert.match(hoverStyles, /opacity:1;visibility:visible;pointer-events:auto/);
  assert.ok(css.indexOf("opacity:0;visibility:hidden;pointer-events:none") > start,
    "the hidden state must not apply to touch-only devices");
});

test("masonry images show a reserved skeleton until the real lazy image is decoded", () => {
  assert.match(css, /\.rrugc-cap-stack-gallery\.view-masonry\{display:grid/);
  assert.match(css, /\.rrugc-cap-stack-gallery\.view-masonry \.rrugc-stable-masonry-column\{display:flex;flex-direction:column/);
  assert.match(css, /\.rrugc-cap-stack-skeleton\{position:absolute/);
  assert.match(css, /\.rrugc-cap-stack-media\.is-loaded \.rrugc-cap-stack-skeleton\{opacity:0;animation:none\}/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)/);
});
