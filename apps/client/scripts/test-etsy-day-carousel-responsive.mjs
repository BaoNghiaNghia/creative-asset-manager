import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

// A real Chromium check for the responsive day rail, not just jsdom state.
// Keep these widths explicit to cover every responsive card-count threshold.
const css = (await Promise.all([
  "EtsySpyPage.css", "EtsySpyEvo.css", "EtsySpyDayCarousel.css",
].map(file => readFile(new URL("../app/etsy-spy/" + file, import.meta.url), "utf8")))).join("\n");

const cards = Array.from({length: 28}, (_, index) => `
  <button type="button" class="etsy-observed-day ${index === 0 ? "is-empty" : index === 1 ? "is-loading" : index === 3 ? "active is-empty" : "has-activity"}" data-pick-state="eligible">
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
          emptyOpacity: Number(getComputedStyle(cardList[0]).opacity),
          loadingOpacity: Number(getComputedStyle(cardList[1]).opacity),
          activityOpacity: Number(getComputedStyle(cardList[2]).opacity),
          selectedEmptyOpacity: Number(getComputedStyle(cardList[3]).opacity),
          activityBackground: getComputedStyle(cardList[2]).backgroundImage,
          emptyLabelColor: getComputedStyle(cardList[0].querySelector(".etsy-observed-day-label")).color,
          activityStrongColor: getComputedStyle(cardList[2].querySelector("strong")).color,
          gaps: css.columnGap,
          cardWidth: cardList[0].getBoundingClientRect().width,
          noClippedText: cardList.every(card => [...card.querySelectorAll("small,strong")]
            .every(node => node.scrollWidth <= node.clientWidth + 1)),
        };
      });
      assert.ok(result.fullVisible >= 1 && result.fullVisible <= 28,
        `${width}px invalid visible card count: ${JSON.stringify(result)}`);
      assert.equal(result.railWidth, result.wrapperWidth, `${width}px rail is not full width`);
      assert.equal(result.scrollWidth > result.railWidth, result.fullVisible < 28,
        `${width}px unexpected scroll state`);
      assert.ok(result.cardWidth >= 140 && result.cardWidth < 160,
        `${width}px Eligible card is stretched: ${result.cardWidth}`);
      if (width === 2030) assert.ok(result.fullVisible >= 12,
        "wide desktop must show at least 12 Eligible cards");
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
      assert.ok(result.emptyOpacity < .75, `${width}px empty day did not fade`);
      assert.equal(result.loadingOpacity, 1, `${width}px pending day must not look empty`);
      assert.equal(result.activityOpacity, 1, `${width}px day with activity looks faded`);
      assert.equal(result.selectedEmptyOpacity, 1, `${width}px selected empty day looks faded`);
      assert.ok(result.activityBackground.includes("linear-gradient"),
        `${width}px active day lacks background differentiation`);
      assert.equal(result.emptyLabelColor, "rgb(135, 149, 170)");
      assert.equal(result.activityStrongColor, "rgb(37, 77, 128)");
      assert.ok(result.noClippedText, `${width}px card text clips`);
      const overlap = await page.locator(".etsy-observed-day").evaluateAll(cards => cards.some(card => {
        const eligible = card.querySelector(".etsy-observed-day-eligible").getBoundingClientRect();
        const picked = card.querySelector(".etsy-observed-day-pick").getBoundingClientRect();
        return eligible.right > picked.left - 1;
      }));
      assert.equal(overlap, false, `${width}px metrics overlap`);
      // Switch to Picked: one metric, so the card should shrink to its content
      // rather than retaining the width needed by a two-metric Eligible card.
      const picked = await page.evaluate(() => {
        const rail = document.querySelector(".etsy-observed-day-grid");
        const all = [...rail.querySelectorAll(".etsy-observed-day")];
        all.forEach(card => {
          card.dataset.pickState = "picked";
          card.querySelector(".etsy-observed-day-pick").remove();
          card.querySelector(".etsy-observed-day-eligible small").textContent = "Picked";
        });
        const rect = rail.getBoundingClientRect();
        return {
          firstWidth: all[0].getBoundingClientRect().width,
          lastWidth: all.at(-1).getBoundingClientRect().width,
          fullVisible: all.filter(card => card.getBoundingClientRect().right <= rect.right + 1).length,
          scrollWidth: rail.scrollWidth,
          clientWidth: rail.clientWidth,
          pageWidth: document.documentElement.scrollWidth,
          textFits: all.every(card =>
            [...card.querySelectorAll("small,strong")].every(text => text.scrollWidth <= text.clientWidth + 1)),
        };
      });
      assert.ok(picked.firstWidth >= (width <= 480 ? 90 : 94) &&
        picked.firstWidth <= 108, `${width}px Picked card is not content-sized: ${picked.firstWidth}`);
      assert.ok(picked.lastWidth < 126, `${width}px late Picked card too wide`);
      assert.ok(picked.fullVisible >= result.fullVisible,
        `${width}px shrinking card did not show more dates`);
      if (width === 2030) assert.ok(picked.fullVisible >= 17,
        `2030px should show at least 17 Picked cards, got ${picked.fullVisible}`);
      assert.equal(picked.scrollWidth > picked.clientWidth, picked.fullVisible < 28);
      assert.ok(picked.pageWidth <= width + 1, `${width}px Picked rail overflowed page`);
      assert.ok(picked.textFits, `${width}px Picked text clipped`);
      console.log(`PASS ${width}px: ${result.fullVisible} Eligible, ${picked.fullVisible} Picked; Picked card ${picked.firstWidth.toFixed(1)}px`);
    } finally { await page.close(); }
  }
} finally { await browser.close(); }
