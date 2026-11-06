// Browser regression check: the global "nav" selector adds padding that must
// never inset the Etsy Spy Stage 0 tab strip.
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const assetsDir = join(root, "dist", "assets");
const builtCss = (await readdir(assetsDir)).find((name) => name.startsWith("index-") && name.endsWith(".css"));
assert.ok(builtCss, "Build the frontend before running this browser check.");
const globalCss = await readFile(join(assetsDir, builtCss), "utf8");
const etsyCss = await readFile(join(root, "app", "etsy-spy", "EtsySpyPage.css"), "utf8");
const titles = ["Products", "Keyword Tree", "Trademark", "System Logs"];
const html = `<main class="etsy-spy-page">
  <header class="workspace-page-header">
    <div class="workspace-page-header-copy">
      <small class="workspace-page-header-eyebrow">ETSY MARKET INTELLIGENCE</small>
      <div class="workspace-page-header-title-row"><h1>Etsy Spy</h1></div>
      <p class="workspace-page-header-description">Eligible apparel demand, keyword discovery, trademark evidence, and Windows sync diagnostics.</p>
    </div>
  </header>
  <div class="etsy-stage-nav">
    <nav role="tablist" class="etsy-tabs">
      ${titles.map((title, index) => `<button role="tab" class="${index === 0 ? "active" : ""}">
        <span class="etsy-tab-number">${index + 1}</span>
        <span class="etsy-tab-copy"><strong>${title}</strong><small>Section description</small></span>
      </button>`).join("")}
    </nav>
  </div>
  <section class="etsy-stage-canvas"></section>
</main>`;

const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
try {
  for (const width of [390, 682, 1024, 1920]) {
    const page = await browser.newPage({ viewport: { width, height: 780 } });
    try {
      // Disable the 140ms button background transition for deterministic QA.
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setContent(html);
      await page.addStyleTag({ content: globalCss });
      await page.addStyleTag({ content: etsyCss });
      // The global stylesheet contains imported resources. Let CSS settle
      // before asserting the final computed layout.
      await page.waitForFunction(() => getComputedStyle(document.querySelector(".etsy-tabs")).paddingTop === "0px", undefined, { timeout: 5000 });
      const layout = await page.evaluate(() => {
        const header = document.querySelector(".workspace-page-header");
        const nav = document.querySelector(".etsy-stage-nav");
        const tabs = document.querySelector(".etsy-tabs");
        const buttons = [...tabs.querySelectorAll("button")];
        const headerCopy = document.querySelector(".workspace-page-header-copy");
        const headerBounds = header.getBoundingClientRect();
        const navBounds = nav.getBoundingClientRect();
        const tabBounds = tabs.getBoundingClientRect();
        const firstBounds = buttons[0].getBoundingClientRect();
        const active = getComputedStyle(buttons[0]);
        const tabStyle = getComputedStyle(tabs);
        return {
          headerBottom: headerBounds.bottom,
          navTop: navBounds.top,
          tabTop: tabBounds.top,
          navHeight: navBounds.height,
          tabsHeight: tabBounds.height,
          tabLeft: firstBounds.left,
          titleLeft: headerCopy.getBoundingClientRect().left,
          tabPaddingTop: tabStyle.paddingTop,
          tabPaddingLeft: tabStyle.paddingLeft,
          widths: buttons.map((button) => button.getBoundingClientRect().width),
          activeBackground: active.backgroundColor,
          underline: active.borderBottomColor,
          fullPageWidth: document.documentElement.scrollWidth,
          tabsScrollWidth: tabs.scrollWidth,
        };
      });
      assert.equal(layout.tabPaddingTop, "0px", `${width}: nav top padding must be reset`);
      assert.equal(layout.tabPaddingLeft, "0px", `${width}: nav left padding must be reset`);
      assert.ok(Math.abs(layout.headerBottom - layout.navTop) <= 1, `${width}: tabs must directly follow header`);
      assert.ok(Math.abs(layout.tabTop - layout.navTop) <= 1, `${width}: no blank strip above tabs`);
      assert.ok(Math.abs(layout.tabLeft - layout.titleLeft) <= 1, `${width}: tab must align with header title`);
      assert.ok(layout.widths.every((value) => Math.abs(value - 140) <= 1), `${width}: each tab should be 140px`);
      assert.equal(layout.activeBackground, "rgb(243, 245, 248)", `${width}: active tab background`);
      assert.equal(layout.underline, "rgb(76, 115, 211)", `${width}: active tab underline`);
      assert.ok(layout.fullPageWidth <= width, `${width}: do not overflow the page`);
      console.log(`PASS ${width}px: title/tab aligned, no extra nav padding, four 140px tabs, no body overflow`);
    } finally {
      await page.close();
    }
  }
} finally {
  await browser.close();
}
