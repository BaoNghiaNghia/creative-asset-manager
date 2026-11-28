import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const client = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// Include the real global shell styles to catch inherited white aside surfaces.
const globalCss = await readFile(resolve(client, "styles/global.css"), "utf8");
const css = globalCss + "\n" + await readFile(resolve(client, "app/etsy-spy/VideoUGCCell.css"), "utf8");
const capture = process.env.CAM_UGC_QA_SCREENSHOTS === "1";
const evidenceDir = resolve(client, ".ui-qa/ugc-orientation");
if (capture) await mkdir(evidenceDir, {recursive:true});
const markup = (aspect) => {
  const vertical = aspect === "9:16";
  const other = vertical ? "16:9" : "9:16";
  const selector = (ratio, active) => {
    const short = ratio === "9:16";
    return '<button role="tab" aria-selected="' + active + '" class="etsy-video-ugc-version' +
      (active ? " is-active" : "") + '"><span class="etsy-video-ugc-version-preview' +
      (short ? " is-vertical" : " is-horizontal") +
      '"><svg viewBox="0 0 24 24"><rect x="4" y="2" width="16" height="20"/></svg></span>' +
      '<span class="etsy-video-ugc-version-copy"><strong>Video ' +
      (short ? "dọc" : "ngang") + '</strong><small>' + ratio +
      ' · MP4</small></span></button>';
  };
  return '<!doctype html><html lang="vi"><head><meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<style>*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:#fafbfc}' + css +
    '</style></head><body><div class="etsy-video-ugc-backdrop"><section class="etsy-video-ugc-modal is-' +
    (vertical ? "vertical" : "horizontal") + '" role="dialog">' +
    '<header class="etsy-video-ugc-modal-head"><div class="etsy-video-ugc-modal-identity">' +
    '<span class="etsy-video-ugc-modal-hero-icon">▣</span>' +
    '<div class="etsy-video-ugc-modal-titles"><span class="etsy-video-ugc-kicker">VIDEO LIBRARY</span>' +
    '<h2>Video UGC</h2><p>Listing 4456979256 · Đã lưu trên Drive</p></div></div>' +
    '<div class="etsy-video-ugc-modal-head-actions"><span class="etsy-video-ugc-modal-count">2/2 video</span>' +
    '<button class="etsy-video-ugc-close" aria-label="Đóng video UGC">×</button></div></header>' +
    '<div class="etsy-video-ugc-modal-content"><div class="etsy-video-ugc-studio is-' +
    (vertical ? "vertical" : "horizontal") + '">' +
    '<div class="etsy-video-ugc-primary is-' + (vertical ? "vertical" : "horizontal") + '">' +
    '<div class="etsy-video-ugc-player-stage is-' + (vertical ? "vertical" : "horizontal") + '">' +
    '<video controls autoplay muted playsinline preload="auto" class="' +
    (vertical ? "vertical" : "horizontal") + '"></video></div>' +
    '<div class="etsy-video-ugc-primary-foot"><a href="#">Mở file gốc</a></div></div>' +
    '<aside class="etsy-video-ugc-selector"><div class="etsy-video-ugc-version-group" role="tablist">' +
    selector(aspect, true) + selector(other, false) + '</div>' +
    '</aside></div></div></section></div></body></html>';
};
const browser = await chromium.launch({
  headless:true, executablePath:"/opt/google/chrome/chrome",
  args:["--no-sandbox","--disable-dev-shm-usage"],
});
try {
  for (const aspect of ["9:16","16:9"]) {
    for (const [width,height] of [
      [320,700],[390,844],[414,896],[440,956],[680,840],[768,1024],
      [930,948],[1024,768],[1280,800],[1440,900],[2048,1100],[844,390]
    ]) {
      const page = await browser.newPage({viewport:{width,height}});
      await page.setContent(markup(aspect));
      const m = await page.evaluate(() => {
        const modal = document.querySelector(".etsy-video-ugc-modal");
        const stage = document.querySelector(".etsy-video-ugc-player-stage");
        const player = document.querySelector(".etsy-video-ugc-primary");
        const selector = document.querySelector(".etsy-video-ugc-selector");
        const rect = el => el.getBoundingClientRect();
        const mr=rect(modal), pr=rect(player), sr=rect(stage), rr=rect(selector);
        return {
          docWidth:document.documentElement.scrollWidth,
          modalWidth:Math.round(mr.width), modalHeight:Math.round(mr.height),
          modalTop:Math.round(mr.top), modalBottom:Math.round(mr.bottom),
          stageWidth:Math.round(sr.width), stageHeight:Math.round(sr.height),
          stageRatio:sr.width/sr.height,
          playerTop:Math.round(pr.top), selectorTop:Math.round(rr.top),
          videoCount:document.querySelectorAll("video").length,
          posterActionCount:document.querySelectorAll(".etsy-video-ugc-poster-action").length,
          linkCount:document.querySelectorAll(".etsy-video-ugc-primary-foot a").length,
          selectorCount:document.querySelectorAll(".etsy-video-ugc-version").length,
          selectorLayout:getComputedStyle(document.querySelector(".etsy-video-ugc-version-group")).display,
          selectorBg:getComputedStyle(selector).backgroundColor,
          selectorBorder:getComputedStyle(selector).borderRightWidth,
          selectorPadding:getComputedStyle(selector).paddingTop,
          groupBg:getComputedStyle(document.querySelector(".etsy-video-ugc-version-group")).backgroundColor,
          headerCount:document.querySelectorAll(".etsy-video-ugc-primary-head, .etsy-video-ugc-qa").length,
          stageTop:Math.round(sr.top),
          scheme:getComputedStyle(modal).colorScheme,
          modalBg:getComputedStyle(modal).backgroundColor,
          stageBg:getComputedStyle(stage).backgroundColor,
          activeBg:getComputedStyle(document.querySelector(".etsy-video-ugc-version.is-active")).backgroundColor,
          inactiveBg:getComputedStyle(document.querySelector(".etsy-video-ugc-version:not(.is-active)")).backgroundColor,
          titleColor:getComputedStyle(document.querySelector(".etsy-video-ugc-modal-titles h2")).color,
          linkColor:getComputedStyle(document.querySelector(".etsy-video-ugc-primary-foot a")).color,
          bodyBg:getComputedStyle(document.body).backgroundColor,
        };
      });
      assert.equal(m.scheme,"dark","modal must be dark regardless of page theme");
      assert.equal(m.modalBg,"rgb(17, 24, 39)");
      assert.equal(m.selectorBg,m.modalBg,"sidebar surface must match the dark modal");
      assert.equal(m.selectorBorder,"0px","global aside border must not leak into modal");
      assert.equal(m.selectorPadding,"0px","selector must align with player");
      assert.equal(m.groupBg,"rgba(0, 0, 0, 0)","gaps must expose the dark selector, not white");
      assert.equal(m.stageBg,"rgb(8, 13, 22)");
      assert.equal(m.activeBg,"rgb(30, 48, 73)");
      assert.equal(m.inactiveBg,"rgb(28, 37, 53)");
      assert.equal(m.titleColor,"rgb(241, 245, 249)");
      assert.equal(m.headerCount,0,"redundant player header and QA badge are removed");
      assert.equal(m.stageTop,m.playerTop,"player starts immediately without reserved header space");
      assert.equal(m.linkColor,"rgb(147, 197, 253)");
      assert.equal(m.bodyBg,"rgb(250, 251, 252)","page remains light");
      assert(m.docWidth <= width+1, width+"px page horizontal overflow "+JSON.stringify(m));
      assert(m.modalWidth <= width-18, width+"px modal too wide "+JSON.stringify(m));
      assert(m.modalHeight <= height+1, width+"px modal too tall "+JSON.stringify(m));
      assert(m.modalBottom <= height+2, width+"px modal exceeds viewport "+JSON.stringify(m));
      assert.equal(m.videoCount,1,"The open modal mounts only the active MP4");
      assert.equal(m.posterActionCount,0,"Autoplay needs no extra poster click");
      const playable = await page.evaluate(() => {
        const stage = document.querySelector(".etsy-video-ugc-player-stage");
        const video = stage.querySelector("video");
        const rect = video.getBoundingClientRect();
        return {width:rect.width,height:rect.height,
          autoplay:video.autoplay,muted:video.muted,controls:video.controls,inline:video.playsInline};
      });
      assert(playable.autoplay && playable.muted && playable.controls && playable.inline,
        "Active player must support muted inline autoplay and native controls");
      assert(Math.abs(playable.width/playable.height-m.stageRatio)<.03,
        "video element must retain the exact stage aspect ratio");
      assert.equal(m.selectorCount,2);
      assert.equal(m.linkCount,1);
      assert(m.stageWidth>95 && m.stageHeight>(aspect==="9:16" ? 155 : 115));
      if(width>740) {
        assert(Math.abs(m.playerTop-m.selectorTop)<=8, "desktop selector not beside player");
        assert(m.modalWidth <= (aspect==="9:16" ? 690 : 970),
          "active modal must resize to video orientation");
      } else {
        assert(m.selectorTop<m.playerTop,"mobile selector must precede player");
      }
      const expectedRatio = aspect==="9:16" ? 9/16 : 16/9;
      assert(Math.abs(m.stageRatio-expectedRatio)<.026,
        width+"px player container must preserve "+aspect+" "+JSON.stringify(m));
      if(width>=930 && aspect==="16:9") {
        assert(m.stageWidth>=550, "landscape stage should be legible on desktop");
      }
      if(width>=930 && height>=800 && aspect==="9:16") {
        assert(m.stageHeight>=430, "portrait stage should use useful desktop height");
      }
      if (capture && ([390,768,1440].includes(width))) {
        await page.screenshot({path:resolve(evidenceDir,
          (aspect==="9:16"?"portrait":"landscape")+"-"+width+"x"+height+".png")});
      }
      console.log("UGC_MODAL_VIEWPORT_PASS "+aspect+" "+width+"x"+height+" "+JSON.stringify({
        modal:[m.modalWidth,m.modalHeight],stage:[m.stageWidth,m.stageHeight]}));
      await page.close();
    }
  }
} finally {
  await browser.close();
}
