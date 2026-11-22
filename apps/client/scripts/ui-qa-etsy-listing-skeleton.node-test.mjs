import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// CSS regression: an unanchored absolute loading skeleton fills the page,
// covering all product metadata while image thumbnails paint above it.
const listingCss = readFileSync(
  new URL("../app/etsy-spy/ListingImageCell.css", import.meta.url), "utf8",
);
const imageCss = readFileSync(
  new URL("../app/etsy-spy/EtsySpyImages.css", import.meta.url), "utf8",
);

test("Main Listing Image thumbnail contains its own absolute loading skeleton", () => {
  const rule = listingCss.match(/\.etsy-listing-image-main\s*\{([^}]*)\}/)?.[1];
  assert.ok(rule, "Main thumbnail CSS selector must exist");
  assert.match(rule, /position:\s*relative/, "skeleton must anchor to the compact thumbnail button");
  assert.match(rule, /isolation:\s*isolate/, "loading stack cannot cover other cells");
  assert.match(rule, /overflow:\s*hidden/, "no skeleton overflow beyond Main");
  assert.match(rule, /width:\s*72px;height:\s*72px/, "thumbnail geometry stays stable");
});

test("shared Etsy image skeleton styles also anchor Listing Image Main", () => {
  const anchor = imageCss.match(
    /\.etsy-spy-page\s+:is\(([^)]+)\)\s*,\s*\.etsy-design-gallery/s,
  )?.[1];
  assert.ok(anchor, "shared positioned-image selector must exist");
  assert.match(anchor, /\.etsy-listing-image-main\b/);
  assert.match(imageCss, /\.etsy-image-skeleton\s*,[\s\S]*?position:\s*absolute/);
});
