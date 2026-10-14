import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";

const read = async file => fs.readFile(new URL("../app/realistic-review-ugc/" + file, import.meta.url), "utf8");

test("RRUGC Stage 1 DaFont toolbar has four real grid tracks", async () => {
  const css = await read("KeywordImageStage.css");
  assert.match(css, /\.rrugc-keyword-gen-toolbar\s*\{[^}]*grid-template-columns:\s*minmax\(200px,\s*1\.2fr\)\s+minmax\(185px,\s*\.65fr\)\s+minmax\(94px,\s*auto\)\s+32px/s);
  assert.match(css, /@media\s*\(max-width:\s*760px\)[\s\S]*?\.rrugc-keyword-gen-font-pool\s*\{[^}]*grid-row:\s*3/);
});

test("RRUGC data controls expose minimum touch targets across responsive overrides", async () => {
  const base = await read("ui-overhaul.css");
  const density = await read("tablet-mobile-density.css");
  const photo = await read("ProductsPhotoWorkflow.css");
  assert.match(base, /\.rrugc-source-ref-arrow\{[^}]*width:28px/);
  assert.match(base, /\.rrugc-stage2-ref-toggle\{[^}]*min-width:28px;height:28px/);
  assert.match(base, /\.rrugc-stage0-v2 \.rrugc-stage0-sort\{[^}]*min-height:28px/);
  assert.match(photo, /\.rrugc-products-photo-detail-toggle\{[^}]*min-height:28px/);
  assert.match(density, /\.rrugc-source-ref-arrow\{width:28px;height:62px/);
  assert.match(density, /\.rrugc-stage2-ref-toggle\{min-width:28px;height:28px/);
});
