import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

// A real Chromium check for the responsive day rail, not just jsdom state.
// Keep these widths explicit to cover every responsive card-count threshold.
const css = (await Promise.all([
  "EtsySpyPage.css", "EtsySpyEvo.css", "EtsySpyDayCarousel.css",
].map(file => readFile(new URL("../app/etsy-spy/" + file, import.meta.url), "utf8")))).join("\n");

const cards = Array.from({length: 14}, (_, index) => `
  <button type="button" class="etsy-observed-day">
    <span class="etsy-observed-day-label">${index === 0 ? "Hôm nay" : index === 1 ? "Hôm qua" : (17-index) + "/11"}</span>
    <span class="etsy-observed-day-metrics">
      <span class="etsy-observed-day-eligible"><strong>${index * 99}</strong><small>Khả dụng</small></span>
      <span class="etsy-observed-day-pick"><strong>${index * 13}</strong><small>Picked</small></span>
    </span>
  </button>`).join("");

const scenarios = [
  320, 360, 361, 375, 480, 481, 600, 601, 640, 641, 680, 681, 720, 721, 768,
  861, 1024, 1025, 1180, 1181, 1360, 1361, 1600, 1800, 1920, 2030, 2560,
];
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  for (const width of scenarios) {
    const cardTarget = width <= 360 ? 136 : width <= 600 ? 140 :
      width <= 720 ? 144 : 152;
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
        const prev = document.querySelector(".etsy-observed-day-nav.is-prev");
        const rect = rail.getBoundingClientRect();
        const fullVisible = cardList.filter(card => card.getBoundingClientRect().right <= rect.right + 1).length;
        const css = getComputedStyle(rail);
        const navStyle = getComputedStyle(next);
        const prevStyle = getComputedStyle(prev);
        const nextRect = next.getBoundingClientRect();
        const prevRect = prev.getBoundingClientRect();
        return {
          fullVisible, railWidth: rail.clientWidth, wrapperWidth: wrapper.clientWidth,
          scrollWidth: rail.scrollWidth, scrollbar: css.scrollbarWidth,
          documentWidth: document.documentElement.scrollWidth,
          nextBackground: navStyle.backgroundImage, prevBackground: prevStyle.backgroundImage,
          navBorder: navStyle.borderTopWidth,
          navShadow: navStyle.boxShadow, navColor: navStyle.color,
          navWidth: nextRect.width, navHeight: nextRect.height,
          prevX: prevRect.left, nextRight: nextRect.right,
          leftEdge: rect.left, rightEdge: rect.right,
          navVisible: navStyle.visibility, navPointer: navStyle.pointerEvents,
          navOpacity: Number(navStyle.opacity),
          gaps: css.columnGap,
          cardWidth: cardList[0].getBoundingClientRect().width,
          noClippedText: cardList.every(card => [...card.querySelectorAll("small,strong")]
            .every(node => node.scrollWidth <= node.clientWidth + 1)),
        };
      });
      const visible = Math.min(14, Math.floor((result.railWidth + 8) / (cardTarget + 8)));
      assert.equal(result.fullVisible, visible, `${width}px card count: ${JSON.stringify(result)}`);
      assert.equal(result.railWidth, result.wrapperWidth, `${width}px rail is not full width`);
      assert.equal(result.scrollWidth > result.railWidth, visible < 14, `${width}px unexpected scroll state`);
      assert.equal(Math.round(result.cardWidth), cardTarget, `${width}px card stretched`);
      if (width === 2030) assert.equal(visible, 12, "wide desktop must show 12 compact cards");
      assert.equal(result.scrollbar, "none", `${width}px scrollbar showing`);
      assert.ok(result.documentWidth <= width + 1, `${width}px document overflow`);
      assert.ok(result.prevBackground.startsWith("linear-gradient(90deg"),
        `${width}px missing left-side gradient`);
      assert.ok(result.nextBackground.startsWith("linear-gradient(270deg"),
        `${width}px missing right-side gradient`);
      assert.equal(result.navBorder, "0px", "Navigation has a border");
      assert.equal(result.navShadow, "none", "Navigation has a shadow");
      assert.equal(result.navColor, "rgb(98, 114, 137)", "Chevron is not gray");
      assert.ok(result.navWidth >= 52 && result.navHeight >= 44,
        `${width}px arrow hit area is too small: ${result.navWidth}x${result.navHeight}`);
      assert.ok(Math.abs(result.prevX - result.leftEdge) < 1,
        `${width}px left arrow is misplaced`);
      assert.ok(Math.abs(result.nextRight - result.rightEdge) < 1,
        `${width}px right arrow is misplaced`);
      assert.equal(result.navVisible, "visible", `${width}px arrow hidden without hover`);
      assert.equal(result.navPointer, "auto", `${width}px arrow isn't clickable`);
      assert.ok(result.navOpacity >= .9, `${width}px arrow too faint`);
      assert.ok(result.noClippedText, `${width}px card text clips`);
      const overlap = await page.locator(".etsy-observed-day").evaluateAll(cards => cards.some(card => {
        const eligible = card.querySelector(".etsy-observed-day-eligible").getBoundingClientRect();
        const picked = card.querySelector(".etsy-observed-day-pick").getBoundingClientRect();
        return eligible.right > picked.left - 1;
      }));
      assert.equal(overlap, false, `${width}px metrics overlap`);
      console.log(`PASS ${width}px: ${visible} full cards, ${result.cardWidth.toFixed(1)}px each, no visible scrollbar`);
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
