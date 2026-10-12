import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { firefox } from "playwright";

const stylesheet = readFileSync(
  fileURLToPath(new URL("../app/realistic-review-ugc/BlueprintPreview.css", import.meta.url)),
  "utf8",
);
// Compare the full side embroidery with the magnifier's pre-zoom rendering.
test("Right Side magnifier preserves whole embroidery with the main cap's geometry", async () => {
  const browser = await firefox.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 850 } });
    const artwork = readFileSync(fileURLToPath(new URL("../public/rrugc/blueprint/roberts-reference.png", import.meta.url)));
    const pixel = "data:image/png;base64," + artwork.toString("base64");
    await page.setContent(`
      <div class="rrugc-blueprint-side-image is-ready" style="width:700px;height:430px;max-width:none;max-height:none">
        <img class="rrugc-blueprint-side-calibration-design" src="${pixel}" alt="">
        <div class="rrugc-blueprint-magnifier" style="left:0;top:0;opacity:1">
          <div class="rrugc-blueprint-magnifier-scene" style="width:700px;height:430px">
            <div class="rrugc-blueprint-magnifier-side-surface">
              <img class="rrugc-blueprint-side-calibration-design" src="${pixel}" alt="">
            </div>
          </div>
        </div>
      </div>
    `);
    await page.addStyleTag({ content: stylesheet });
    // Headless Firefox may report hover:none, hiding the magnifier entirely.
    // Force its visibility for this geometry-only test, without touching artwork rules.
    await page.addStyleTag({ content: ".rrugc-blueprint-magnifier{display:block!important}.rrugc-blueprint-side-calibration-design{transition:none!important}" });
    const rects = await page.evaluate(async () => {
      await Promise.all(Array.from(document.images).map(image => image.decode()));
      const cap = document.querySelector(".rrugc-blueprint-side-image");
      const main = cap.querySelector(":scope > img.rrugc-blueprint-side-calibration-design");
      const lens = cap.querySelector(".rrugc-blueprint-magnifier-side-surface > img.rrugc-blueprint-side-calibration-design");
      const geometry = image => {
        const box = image.getBoundingClientRect();
        const style = getComputedStyle(image);
        return { width: box.width, height: box.height, top: parseFloat(style.top),
          left: parseFloat(style.left), zIndex: style.zIndex,
          objectFit: style.objectFit, transform: style.transform, styleWidth: style.width, styleHeight: style.height };
      };
      return { main: geometry(main), lens: geometry(lens) };
    });
    assert.equal(rects.main.styleWidth, "134.4px", "main embroidery spans 19.2% of a 700px side tile");
    assert.equal(rects.lens.styleWidth, rects.main.styleWidth, "lens embroidery uses the same untransformed width");
    assert.ok(Math.abs(rects.lens.width - rects.main.width) <= .1,
      "magnifier embroidery must be same width before the 2.4x magnification");
    assert.ok(Math.abs(rects.lens.height - rects.main.height) <= .1,
      "magnifier must not stretch or crop the design");
    assert.ok(Math.abs(rects.lens.top - rects.main.top) <= .1);
    assert.ok(Math.abs(rects.lens.left - rects.main.left) <= .1);
    assert.equal(rects.lens.zIndex, rects.main.zIndex);
    assert.equal(rects.lens.transform, rects.main.transform);
  } finally {
    await browser.close();
  }
});
