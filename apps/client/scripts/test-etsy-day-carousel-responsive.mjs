import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

// A real Chromium check for the responsive day rail, not just jsdom state.
// Keep these widths explicit to cover every responsive card-count threshold.
const css = (await Promise.all([
  "EtsySpyPage.css", "EtsySpyEvo.css", "EtsySpyDayCarousel.css",
].map(file => readFile(new URL("../app/etsy-spy/" + file, import.meta.url), "utf8")))).join("\n");

const cards = Array.from({length: 10}, (_, index) => `
  <button type="button" class="etsy-observed-day">
    <span class="etsy-observed-day-label">${index === 0 ? "Hôm nay" : index === 1 ? "Hôm qua" : (17-index) + "/11"}</span>
    <span class="etsy-observed-day-metrics">
      <span class="etsy-observed-day-eligible"><strong>${index * 99}</strong><small>Picked</small></span>
    </span>
  </button>`).join("");

const scenarios = [
  [320, 2], [375, 2], [480, 2], [481, 3], [600, 3],
  [640, 3], [641, 4], [768, 4], [861, 5], [1024, 5],
  [1025, 6], [1180, 6], [1181, 7], [1360, 7], [1361, 8],
  [1600, 8], [1800, 9], [1920, 9],
];
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  for (const [width, visible] of scenarios) {
    const page = await browser.newPage({viewport:{width,height:580}});
    try {
      await page.setContent(`<!doctype html><html lang="vi"><head>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <style>
          *,*::before,*::after{box-sizing:border-box}
          body{margin:0;padding:12px;font:13px Arial,sans-serif}
          ${css}
        </style>
      </head><body><main class="etsy-spy-page">
        <section class="etsy-observed-days">
          <div class="etsy-observed-day-carousel">
            <div class="etsy-observed-day-grid">${cards}</div>
            <button class="etsy-observed-day-nav is-prev" aria-label="Trước">
              <svg viewBox="0 0 24 24"><path d="M14.5 5 7.5 12l7 7"/></svg>
            </button>
            <button class="etsy-observed-day-nav is-next" aria-label="Tiếp">
              <svg viewBox="0 0 24 24"><path d="m9.5 5 7 7-7 7"/></svg>
            </button>
          </div>
        </section>
      </main></body></html>`);
      const result = await page.evaluate(() => {
        const rail = document.querySelector(".etsy-observed-day-grid");
        const wrapper = document.querySelector(".etsy-observed-day-carousel");
        const cardList = [...document.querySelectorAll(".etsy-observed-day")];
        const next = document.querySelector(".etsy-observed-day-nav.is-next");
        const rect = rail.getBoundingClientRect();
        const fullVisible = cardList.filter(card => card.getBoundingClientRect().right <= rect.right + 1).length;
        const css = getComputedStyle(rail);
        const navStyle = getComputedStyle(next);
        return {
          fullVisible, railWidth: rail.clientWidth, wrapperWidth: wrapper.clientWidth,
          scrollWidth: rail.scrollWidth, scrollbar: css.scrollbarWidth,
          documentWidth: document.documentElement.scrollWidth,
          navBackground: navStyle.backgroundColor, navBorder: navStyle.borderTopWidth,
          navShadow: navStyle.boxShadow, navColor: navStyle.color,
          gaps: css.columnGap,
          cardWidth: cardList[0].getBoundingClientRect().width,
          noClippedText: cardList.every(card => [...card.querySelectorAll("small,strong")]
            .every(node => node.scrollWidth <= node.clientWidth + 1)),
        };
      });
      assert.equal(result.fullVisible, visible, `${width}px card count: ${JSON.stringify(result)}`);
      assert.equal(result.railWidth, result.wrapperWidth, `${width}px rail is not full width`);
      assert.ok(result.scrollWidth > result.railWidth, `${width}px rail does not scroll`);
      assert.equal(result.scrollbar, "none", `${width}px scrollbar showing`);
      assert.ok(result.documentWidth <= width + 1, `${width}px document overflow`);
      assert.equal(result.navBackground, "rgba(0, 0, 0, 0)", "Navigation has a background");
      assert.equal(result.navBorder, "0px", "Navigation has a border");
      assert.equal(result.navShadow, "none", "Navigation has a shadow");
      assert.equal(result.navColor, "rgb(117, 130, 153)", "Chevron is not gray");
      assert.ok(result.noClippedText, `${width}px card text clips`);
      console.log(`PASS ${width}px: ${visible} full cards, ${result.cardWidth.toFixed(1)}px each, no visible scrollbar`);
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
