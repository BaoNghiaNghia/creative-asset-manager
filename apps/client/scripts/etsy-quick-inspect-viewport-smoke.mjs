import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { chromium } from "playwright";

const client = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sources = [
  "app/etsy-spy/EtsySpyPage.css",
  "app/etsy-spy/EtsySpyWorkspaceV2.css",
  "app/etsy-spy/EtsySpyQuickInspect.css",
];
const css = (await Promise.all(sources.map(s => readFile(resolve(client,s),"utf8")))).join("\n");
const thumb = i => `<button type="button" class="${i===0?"is-active":""}" aria-pressed="${i===0}">
<span style="display:block;width:100%;height:100%;background:#d4dce7;border-radius:5px"></span></button>`;
const html = `<!doctype html><html lang="vi"><head><meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:#fff}${css}</style></head>
<body><main class="etsy-spy-page" style="min-width:0;width:100%">
<div class="etsy-v2-results-layout has-inspector" style="min-height:2050px">
<div class="etsy-table-card" style="min-width:0;min-height:2050px;background:#fafbfc">Etsy Spy table</div>
<div class="etsy-v2-inspector-layer">
<button class="etsy-v2-inspector-scrim" aria-label="Đóng Quick Inspect"></button>
<aside class="etsy-v2-inspector" aria-label="Quick Inspect sản phẩm">
<header class="etsy-v2-inspector-header"><div><strong>Quick Inspect</strong><small>Listing #4456979256</small></div><button>×</button></header>
<div class="etsy-v2-inspector-scroll">
<div class="etsy-v2-inspector-visual">
<div class="etsy-v2-inspector-hero"><div class="etsy-v2-no-preview">Image thumbnail</div><span class="etsy-v2-inspector-hero-label">Ảnh listing</span></div>
<div class="etsy-v2-inspector-thumbs">${[0,1,2].map(thumb).join("")}<span>+10 ảnh</span></div></div>
<div class="etsy-v2-inspector-identity">
<strong class="etsy-v2-inspector-title">Dinosaurs Didn't Read Comfort Colors Shirt, Funny Book Lover T-Shirt, Funny Dinosaur Teaching Tee, Reading Shirt, Librarian Teacher Shirt</strong>
<span class="etsy-v2-inspector-badge is-bestseller">Bestseller</span></div>
<div class="etsy-v2-inspector-metrics"><div><small>Views / 24h</small><strong>—</strong></div>
<div><small>Carts</small><strong>20+</strong></div><div><small>Bought / 24h</small><strong>—</strong></div></div>
<section class="etsy-v2-inspector-section etsy-v2-inspector-assets"><h3>Creative assets</h3>
<div class="etsy-v2-inspector-medias"><span><span>Listing Image</span><strong>13 <small>ảnh</small></strong></span>
<span><span>Video UGC</span><strong>2 <small>video</small></strong></span></div></section>
<div class="etsy-v2-inspector-keywords">
<section class="etsy-v2-inspector-section"><h3>TikTok SEO</h3><div class="etsy-v2-inspector-tags">
<span>dinosaur reading shirt</span><span>dinosaurs didnt read</span></div></section>
<section class="etsy-v2-inspector-section"><h3>Keyword SMS</h3><div class="etsy-v2-inspector-tags">
<span>Dinosaurs Didn't Read Comfort Colors Shirt</span></div></section></div></div>
<footer class="etsy-v2-inspector-footer"><div class="etsy-v2-inspector-actions">
<a href="#">Mở trên Etsy ↗</a><button>Copy title</button>
<button>Bỏ Pick</button><button disabled>Later</button></div></footer>
</aside></div></div></main></body></html>`;

const browser = await chromium.launch({
  headless:true,
  executablePath:"/opt/google/chrome/chrome",
  args:["--no-sandbox","--disable-dev-shm-usage"],
});
try {
  for (const [width,height] of [[390,844],[414,896],[768,1024],[1024,768],
    [1280,900],[1440,900],[1536,1000],[1800,1080],[2048,1324]]) {
    const page = await browser.newPage({viewport:{width,height}, deviceScaleFactor:1});
    await page.setContent(html);
    if(width>1180) await page.evaluate(()=>window.scrollTo(0,320));
    const m=await page.evaluate(()=>{
      const panel=document.querySelector(".etsy-v2-inspector");
      const scroll=document.querySelector(".etsy-v2-inspector-scroll");
      const footer=document.querySelector(".etsy-v2-inspector-footer");
      const hero=document.querySelector(".etsy-v2-inspector-hero");
      const thumbs=document.querySelectorAll(".etsy-v2-inspector-thumbs button");
      const keywords=document.querySelector(".etsy-v2-inspector-keywords");
      const rect=n=>n.getBoundingClientRect();
      const box=rect(panel), s=rect(scroll),f=rect(footer), h=rect(hero);
      return {
        viewport:[window.innerWidth, window.innerHeight],
        documentWidth:document.documentElement.scrollWidth,
        panelPosition:getComputedStyle(panel).position,
        panelWidth:Math.round(box.width),
        panelHeight:Math.round(box.height),
        panelTop:Math.round(box.top),
        panelBottom:Math.round(box.bottom),
        scrollTop:Math.round(s.top),
        scrollBottom:Math.round(s.bottom),
        footerTop:Math.round(f.top),
        footerBottom:Math.round(f.bottom),
        heroHeight:Math.round(h.height),
        thumbCount:thumbs.length,
        actions:footer.querySelectorAll("button,a").length,
        keywordColumns:getComputedStyle(keywords).gridTemplateColumns.split(" ").length,
      };
    });
    assert(m.documentWidth<=width+2, `${width}px page overflow: ${JSON.stringify(m)}`);
    assert(m.panelHeight>=height-26, `${width}px panel is height capped: ${JSON.stringify(m)}`);
    assert(m.panelBottom<=height+3, `${width}px panel extends beyond viewport: ${JSON.stringify(m)}`);
    assert(m.footerTop>=m.scrollTop, `${width}px footer overlaps content`);
    assert(m.scrollBottom<=m.footerTop+2, `${width}px scroll extends over action dock`);
    assert(m.footerBottom<=m.panelBottom+1, `${width}px action dock falls outside panel`);
    assert(m.thumbCount===3 && m.actions===4);
    if(m.panelWidth>=420) {
      assert(m.keywordColumns===2,
        `${width}px SEO/SMS sections not using available width: ${JSON.stringify(m)}`);
    }
    if(width>1180){
      assert(m.panelPosition==="sticky", `${width}px panel is not sticky`);
      assert(m.panelWidth>=385 && m.panelWidth<=470,
        `${width}px rail width not improved: ${JSON.stringify(m)}`);
      assert(m.heroHeight>=250,`${width}px image remains too small`);
    }else{
      assert(m.panelPosition==="absolute", `${width}px narrow drawer incorrect`);
      assert(m.panelWidth<=width,`${width}px drawer too wide`);
    }
    console.log(`QUICK_INSPECT_VIEWPORT_PASS ${width}x${height} ${JSON.stringify({
      rail:m.panelWidth,height:m.panelHeight,hero:m.heroHeight,footer:m.footerTop,
      keywordColumns:m.keywordColumns})}`);
    await page.close();
  }
}finally{
  await browser.close();
}
