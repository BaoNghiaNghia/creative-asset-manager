import { readFile } from "node:fs/promises";
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import { resolve, dirname } from "node:path";
import assert from "node:assert/strict";

const client = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sheets = ["EtsySpyPage.css", "EtsySpyEvo.css", "EtsySpyHeader.css",
  "EtsySpyDayCarousel.css", "EtsySpyWorkspaceV2.css"];
const css = (await Promise.all(sheets.map(name =>
  readFile(resolve(client, "app/etsy-spy", name), "utf8")))).join("\n");
const columns = ["", "Product / Etsy", "Design", "TikTok SEO", "Media", "Video UGC",
  "Keyword SMS", "Etsy Badge", "Bought / 24h", "Views / 24h", "Carts", "Observed", "Action"];
const cols = ["select","pageTitle","design","tiktokListing","listingImage","videoUgc",
  "keywordSms","badge","bought24h","views24h","inCarts","observedAt","action"];
const header = columns.map((name,i) => `<th data-etsy-col="${cols[i]}">${name}</th>`).join("");
const row = `<tr class="etsy-product-row">
<td class="etsy-select-cell"><input type="checkbox"></td>
<td class="etsy-title" data-etsy-col="pageTitle"><div class="etsy-product-identity"><span style="width:55px;height:55px;background:#d5dded;flex:0 0 55px;border-radius:6px"></span><div class="etsy-product-copy"><strong>Dinosaur Didn't Read Comfort Colors Shirt</strong><small>#123456789</small></div></div></td>
<td class="etsy-design-cell">Design</td><td class="etsy-tiktok-listing-cell">dinosaur shirt</td>
<td class="etsy-listing-image-cell">10 C1717</td><td class="etsy-video-ugc-cell">Video UGC</td>
<td class="etsy-sms-cell" data-etsy-col="keywordSms">dinosaur shirt</td>
<td data-etsy-col="badge">Bestseller</td><td class="etsy-num" data-etsy-col="bought24h">—</td><td class="etsy-num" data-etsy-col="views24h">35</td><td class="etsy-num" data-etsy-col="inCarts">20+</td><td class="etsy-time" data-etsy-col="observedAt">06:00</td>
<td class="etsy-action-cell"><div class="etsy-row-actions"><button class="etsy-v2-inspect-trigger">Inspect</button><button class="etsy-pick-button">Picked</button></div></td></tr>`;
const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{margin:0} ${css}</style></head>
<body><main class="etsy-spy-page" style="width:100%;max-width:100%;"><section class="etsy-stage-canvas" style="width:100%;max-width:100%;">
<header class="etsy-spy-topbar"><h1>Etsy Spy</h1><div>Agent online • Search</div></header>
<section class="etsy-stats">${["Eligible", "Picked", "Later", "Blacklist", "Win Keyword"].map(x => `<button class="etsy-stat"><span class="etsy-stat-heading">${x}</span><strong>353</strong></button>`).join("")}</section>
<section class="etsy-facet-explorer"><div id="etsy-facet-details" class="etsy-facet-details"><div class="etsy-observed-days"><div class="etsy-observed-day-carousel"><div class="etsy-observed-day-grid">${Array.from({length:16},(_,i)=>`<button class="etsy-observed-day" data-pick-state="eligible"><span class="etsy-observed-day-label">${20-i}/11</span><span class="etsy-observed-day-metrics"><strong>12 Picked</strong></span></button>`).join("")}</div></div></div></div></section>
<div class="etsy-v2-results-layout has-inspector">
 <div class="etsy-table-card etsy-v2-density-default"><div class="etsy-v2-results-toolbar"><div class="etsy-v2-scope-tabs"><button>Eligible</button><button class="active">Picked</button><button>Later</button></div><div class="etsy-v2-toolbar-actions"><button class="etsy-v2-control">Bộ lọc</button><button class="etsy-v2-control">Cột</button></div></div>
 <div class="etsy-table-wrap" style="overflow:auto"><table class="etsy-data-table is-products is-selectable is-picked-listings"><thead><tr>${header}</tr></thead><tbody>${row.repeat(3)}</tbody></table></div>
 <div class="etsy-pager">1–50 / 353</div></div>
 <div class="etsy-v2-inspector-layer"><button class="etsy-v2-inspector-scrim"></button><aside class="etsy-v2-inspector"><header class="etsy-v2-inspector-header"><strong>Quick Inspect</strong><button>×</button></header><div class="etsy-v2-inspector-scroll"><div class="etsy-v2-inspector-hero">Preview</div></div></aside></div>
</div></section></main></body></html>`;
const browser = await chromium.launch({
  headless: true,
  executablePath: "/opt/google/chrome/chrome",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
try {
  for (const width of [390, 768, 1024, 1440, 1536]) {
    const page = await browser.newPage({viewport:{width,height:900}, deviceScaleFactor:1});
    await page.setContent(html);
    const metrics = await page.evaluate(() => {
      const row = document.querySelector(".etsy-product-row");
      const inspector = document.querySelector(".etsy-v2-inspector-layer");
      const table = document.querySelector(".etsy-table-wrap");
      const results = document.querySelector(".etsy-v2-results-layout");
      const style = (selector) => {
        const node = document.querySelector(selector);
        const computed = getComputedStyle(node);
        return {
          background:computed.backgroundColor,
          radius:computed.borderTopLeftRadius,
          shadow:computed.boxShadow,
          borderTop:computed.borderTopWidth,
          borderLeft:computed.borderLeftWidth,
        };
      };
      return {
        surfaces:{
          page:style(".etsy-spy-page"),
          header:style(".etsy-spy-topbar"),
          metric:style(".etsy-stats .etsy-stat"),
          dateStrip:style(".etsy-facet-explorer"),
          date:style(".etsy-observed-day"),
          toolbar:style(".etsy-v2-results-toolbar"),
          table:style(".etsy-v2-results-layout > .etsy-table-card"),
          mobileRow:style(".etsy-product-row"),
        },
        docWidth: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
        rowDisplay: getComputedStyle(row).display,
        inspectorPosition: getComputedStyle(inspector).position,
        tableWidth: table.clientWidth,
        layoutWidth: results.getBoundingClientRect().width,
        tableOverflow: table.scrollWidth > table.clientWidth,
      };
    });
    assert(metrics.docWidth <= width + 2, `Unexpected horizontal page overflow at ${width}: ${JSON.stringify(metrics)}`);
    assert.equal(metrics.rowDisplay, width <= 680 ? "grid" : "table-row");
    assert.equal(metrics.inspectorPosition, width <= 1180 ? "fixed" : "static");
    assert(metrics.layoutWidth > 0 && metrics.tableWidth > 0);
    assert.equal(metrics.surfaces.page.background, "rgb(255, 255, 255)");
    for (const region of ["header", "metric", "dateStrip", "date", "toolbar", "table"]) {
      assert.equal(metrics.surfaces[region].radius, "0px", `${width}px: ${region} has card radius`);
      assert.equal(metrics.surfaces[region].shadow, "none", `${width}px: ${region} has card shadow`);
      assert.equal(metrics.surfaces[region].borderTop, "0px", `${width}px: ${region} has card border`);
      assert.equal(metrics.surfaces[region].borderLeft, "0px", `${width}px: ${region} has card border`);
    }
    if (width <= 680) {
      assert.equal(metrics.surfaces.mobileRow.radius, "0px");
      assert.equal(metrics.surfaces.mobileRow.shadow, "none");
    }
    console.log(`VIEWPORT PASS ${width}px ${JSON.stringify(metrics)}`);
    await page.close();
  }
} finally { await browser.close(); }
