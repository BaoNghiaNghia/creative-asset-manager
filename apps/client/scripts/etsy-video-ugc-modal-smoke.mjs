import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const css = await readFile(resolve(root, "app/etsy-spy/VideoUGCCell.css"), "utf8");
const videoCard = (ratio, orientation) => `
  <article class="etsy-video-ugc-player is-${orientation} ${orientation === "vertical" ? "is-active" : ""}">
    <div class="etsy-video-ugc-player-head">
      <div class="etsy-video-ugc-player-identity"><strong>Video ${orientation === "vertical" ? "dọc" : "ngang"}</strong><span class="etsy-video-ugc-ratio">${ratio}</span></div>
      <span class="etsy-video-ugc-qa is-pending"><span class="etsy-video-ugc-qa-dot"></span>Chờ QA nội dung</span>
    </div>
    <div class="etsy-video-ugc-stage"><video controls playsinline preload="none" class="${orientation}" aria-label="Video ${ratio}"></video></div>
    <div class="etsy-video-ugc-player-foot">
      <span class="etsy-video-ugc-file-status">Đã lưu Drive</span>
      <a href="#" target="_blank" rel="noopener noreferrer">Mở file gốc</a>
    </div>
  </article>`;
const html = `<!doctype html><html lang="vi"><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>
* {box-sizing:border-box;} body{margin:0;font-family:Arial,sans-serif;background:#fafbfc;}
${css}
</style></head><body>
<div class="etsy-video-ugc-backdrop">
<section class="etsy-video-ugc-modal" role="dialog" aria-modal="true" aria-labelledby="ugc-title">
<header class="etsy-video-ugc-modal-head">
<div class="etsy-video-ugc-modal-identity"><span class="etsy-video-ugc-modal-hero-icon">▣</span>
<div class="etsy-video-ugc-modal-titles"><span class="etsy-video-ugc-kicker">VIDEO LIBRARY</span><h2 id="ugc-title">Video UGC</h2>
<p>Listing 4456979256 · Đã lưu trên Drive</p></div></div>
<div class="etsy-video-ugc-modal-head-actions"><span class="etsy-video-ugc-modal-count">2/2 video</span><button class="etsy-video-ugc-close" aria-label="Đóng video UGC">✕</button></div>
</header>
<div class="etsy-video-ugc-modal-content">
<div class="etsy-video-ugc-mobile-tabs" role="tablist"><button role="tab" aria-selected="true" class="is-active">Dọc <span>9:16</span></button>
<button role="tab" aria-selected="false">Ngang <span>16:9</span></button></div>
<div class="etsy-video-ugc-players">${videoCard("9:16", "vertical")}${videoCard("16:9", "horizontal")}</div>
</div>
<footer class="etsy-video-ugc-modal-footnote">Trạng thái lưu Drive và đánh giá QA nội dung là hai bước riêng biệt.</footer>
</section></div></body></html>`;

const browser = await chromium.launch({
  headless: true, executablePath: "/opt/google/chrome/chrome",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
try {
  for (const [width, height] of [
    [390, 844], [414, 896], [680, 840], [768, 1024],
    [930, 948], [1280, 800], [1440, 900], [2048, 1100], [844, 390],
  ]) {
    const page = await browser.newPage({viewport: {width, height}});
    await page.setContent(html);
    const m = await page.evaluate(() => {
      const modal = document.querySelector(".etsy-video-ugc-modal");
      const cards = Array.from(document.querySelectorAll(".etsy-video-ugc-player"));
      const stages = cards.map(x => x.querySelector(".etsy-video-ugc-stage"));
      const rect = n => n.getBoundingClientRect();
      const modalRect = rect(modal);
      return {
        viewportWidth: window.innerWidth,
        docWidth: document.documentElement.scrollWidth,
        modalWidth: Math.round(modalRect.width),
        modalHeight: Math.round(modalRect.height),
        maxHeight: getComputedStyle(modal).maxHeight,
        cardDisplays: cards.map(n=>getComputedStyle(n).display),
        cardTops: cards.map(n=>Math.round(rect(n).top)),
        cardHeights: cards.map(n=>Math.round(rect(n).height)),
        stageHeights: stages.map(n=>Math.round(rect(n).height)),
        tabsDisplay: getComputedStyle(document.querySelector(".etsy-video-ugc-mobile-tabs")).display,
        contentOverflowY: getComputedStyle(document.querySelector(".etsy-video-ugc-modal-content")).overflowY,
      };
    });
    assert(m.docWidth <= width + 1, `${width}px unexpected horizontal overflow: ${JSON.stringify(m)}`);
    assert(m.modalWidth <= width - 18, `${width}px modal too wide: ${JSON.stringify(m)}`);
    assert(m.modalHeight <= height + 1, `${width}px modal outside screen: ${JSON.stringify(m)}`);
    if (width > 720) {
      assert.equal(m.tabsDisplay, "none");
      assert.deepEqual(m.cardDisplays, ["grid", "grid"]);
      assert(Math.abs(m.cardTops[0] - m.cardTops[1]) < 2, "Desktop cards misaligned");
      assert(Math.abs(m.stageHeights[0] - m.stageHeights[1]) < 2,
        `Desktop video stages not balanced: ${JSON.stringify(m)}`);
      assert(m.modalHeight <= 790, `Desktop modal unnecessarily large: ${JSON.stringify(m)}`);
    } else {
      assert.equal(m.tabsDisplay, "grid");
      assert.deepEqual(m.cardDisplays, ["grid", "none"]);
      assert(m.stageHeights[0] > 0);
    }
    console.log(`UGC_MODAL_VIEWPORT_PASS ${width}x${height} ${JSON.stringify({
      modal: [m.modalWidth, m.modalHeight], stages: m.stageHeights, tabs: m.tabsDisplay,
    })}`);
    await page.close();
  }
} finally {
  await browser.close();
}
