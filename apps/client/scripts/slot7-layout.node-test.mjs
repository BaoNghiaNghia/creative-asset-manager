import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { chromium } from "playwright";

const base = new URL("../app/realistic-review-ugc/", import.meta.url);
const css = ["ProductsPhotoWorkflow.css", "ProductsPhotoWorkflowTwoColumns.css"]
  .map(name => readFileSync(new URL(name, base), "utf8")).join("\n");
const hatData = "data:image/jpeg;base64," + readFileSync(
  new URL("../public/rrugc/blueprint/stock/natural-black/front.jpg", import.meta.url),
).toString("base64");

function fixture() {
  const sample = Array.from({ length: 6 }, (_, index) =>
    `<div class="rrugc-products-photo-stage-row">
      <div class="rrugc-products-photo-stage-title">
        <span class="rrugc-products-photo-number">${index + 1}</span>
        <strong>Ecommerce</strong>
      </div>
      <div class="rrugc-products-photo-skill">Skill ${index + 1}</div>
      <div class="rrugc-products-photo-stage-controls">Done</div>
      <div class="rrugc-products-photo-output-strip">
        <button type="button" class="rrugc-products-photo-empty-shot is-completed"><img src="${hatData}" alt="Output"></button>
      </div>
    </div>`).join("");
  const colors = Array.from({ length: 13 }, (_, index) =>
    `<button type="button" class="rrugc-colorway-slot is-completed" aria-label="Color ${index + 1}">
      <img alt="Color ${index + 1}" src="${hatData}">
    </button>`).join("");
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="box-sizing:border-box;margin:0;padding:20px">
    <main class="rrugc-products-photo-pipeline">
      <div class="rrugc-products-photo-stage-list is-two-column">${sample}
      <div class="rrugc-products-photo-stage-row is-colorway" data-skill="colorway">
        <div class="rrugc-products-photo-stage-title">
          <span class="rrugc-products-photo-number">7</span>
          <div><strong>13 Hat Colors</strong><small>13/13 images</small></div>
        </div>
        <div class="rrugc-products-photo-output-strip is-colors">${colors}</div>
        <div class="rrugc-products-photo-skill"><span>SKILL 7</span><strong>s7-8869-scale-12-color</strong></div>
        <div class="rrugc-products-photo-stage-controls">
          <time class="rrugc-products-photo-updated">10/23/2026</time>
          <span class="rrugc-products-photo-duration">25m 47s</span>
          <button class="rrugc-products-photo-run-button is-completed">Done</button>
          <button class="rrugc-products-photo-detail-toggle">Details</button>
        </div>
      </div>
      </div>
    </main>
    </body></html>`;
}

const cases = [
  {viewport:1920, count:8, minTileWidth:100, minStripWidth:800, twoColumns:true},
  {viewport:1200, count:6, minTileWidth:75, minStripWidth:500, twoColumns:true},
  {viewport:750, count:6, minTileWidth:90, minStripWidth:600, twoColumns:false},
  {viewport:390, count:4, minTileWidth:68, minStripWidth:300, twoColumns:false},
];

test("Stage 2 Slot 7 enlarges previews, maintains overflow, and reflows without clipping", async () => {
  const executablePath = [process.env.CHROME_BIN, chromium.executablePath(), "/usr/bin/google-chrome"].find(path => path && existsSync(path));
  assert.ok(executablePath, "A Chromium or Chrome browser must be available for layout checks");
  const browser = await chromium.launch({ headless:true, executablePath });
  try {
    for (const c of cases) {
      const page = await browser.newPage({viewport:{width:c.viewport,height:900}});
      try {
        await page.setContent(fixture());
        await page.addStyleTag({content:css});
        const sizes = await page.evaluate(() => {
          const list = document.querySelector(".rrugc-products-photo-stage-list");
          const card = document.querySelector('[data-skill="colorway"]');
          const strip = card.querySelector(".rrugc-products-photo-output-strip.is-colors");
          const tiles = [...card.querySelectorAll(".rrugc-colorway-slot")];
          const title = card.querySelector(".rrugc-products-photo-stage-title");
          const controls = card.querySelector(".rrugc-products-photo-stage-controls");
          const rect = element => {
            const r = element.getBoundingClientRect();
            return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};
          };
          return {
            columns:getComputedStyle(list).gridTemplateColumns.split(" ").length,
            card:rect(card), title:rect(title), controls:rect(controls),
            strip:rect(strip), tiles:tiles.map(rect),
            overflow:strip.scrollWidth-strip.clientWidth,
            bodyOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
          };
        });
        assert.equal(sizes.columns, c.twoColumns ? 2 : 1, `columns @${c.viewport}`);
        assert.ok(sizes.strip.width >= c.minStripWidth, `strip width @${c.viewport}: ${sizes.strip.width}`);
        assert.ok(sizes.tiles[0].width >= c.minTileWidth, `tile width @${c.viewport}: ${sizes.tiles[0].width}`);
        assert.ok(sizes.tiles[0].height >= 105, `tile height @${c.viewport}: ${sizes.tiles[0].height}`);
        assert.ok(sizes.strip.y >= sizes.title.bottom - 1, `gallery after header @${c.viewport}`);
        assert.ok(sizes.tiles[c.count-1].right <= sizes.strip.right+1, `last visible color @${c.viewport}`);
        assert.ok(sizes.tiles[c.count].right > sizes.strip.right+1, `remaining colors must scroll @${c.viewport}`);
        assert.ok(sizes.overflow > 100, `horizontal scroll @${c.viewport}`);
        assert.ok(sizes.bodyOverflow < 2, `document must not overflow @${c.viewport}`);
        console.log(`Slot 7 @${c.viewport}px: ${c.count} visible, tile ${Math.round(sizes.tiles[0].width)}x${Math.round(sizes.tiles[0].height)}px, strip ${Math.round(sizes.strip.width)}px`);
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
});
