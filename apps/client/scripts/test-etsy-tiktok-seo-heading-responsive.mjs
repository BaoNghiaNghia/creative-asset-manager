import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {chromium} from "playwright";

// Regression for the old .etsy-tiktok-listing-open small {width:100%}
// shrinking "Search terms" into one letter per line on the Picked table.
const files=["EtsySpyPage.css","TikTokListingModalV4.css","EtsySpyEvo.css"];
const css=(await Promise.all(files.map(name=>readFile(
  new URL("../app/etsy-spy/"+name,import.meta.url),"utf8")))).join("\n");
const widths=[320,375,480,680,768,1024,1366,1920];
const cellWidths=[188,205,218,240,270];
function markup(cellWidth) {
  const terms=["joy to the world sweatshirt","Christian Christmas sweatshirt"];
  return '<!doctype html><html lang="vi"><head><meta name="viewport" content="width=device-width,initial-scale=1">'+
    '<style>'+css+'\n.test-table{min-width:0!important;width:'+cellWidth+'px!important;table-layout:fixed}'+
    '</style></head><body><main class="etsy-spy-page"><div class="etsy-table-wrap">'+
    '<table class="etsy-data-table is-products is-picked-listings test-table"><tbody><tr class="etsy-product-row">'+
    '<td class="etsy-tiktok-listing-cell" style="width:'+cellWidth+'px;max-width:'+cellWidth+'px">'+
    '<div class="etsy-tiktok-cell-stack"><button class="etsy-tiktok-listing-open has-preview" type="button">'+
    '<span class="etsy-tiktok-search-heading"><span>Search terms</span><small>2 gợi ý</small></span>'+
    '<span class="etsy-tiktok-term-list">'+terms.map(t=>'<span class="etsy-tiktok-term">'+t+'</span>').join("")+'</span>'+
    '</button></div></td></tr></tbody></table></div></main></body></html>';
}
const browser=await chromium.launch({channel:"chrome",headless:true});
try {
  for(const viewportWidth of widths) {
    const page=await browser.newPage({viewport:{width:viewportWidth,height:700}});
    try {
      for(const cellWidth of cellWidths) {
        await page.setContent(markup(cellWidth));
        const r=await page.evaluate(()=>{
          const get=q=>document.querySelector(q);
          const button=get(".etsy-tiktok-listing-open");
          const label=get(".etsy-tiktok-search-heading > span");
          const count=get(".etsy-tiktok-search-heading small");
          const heading=get(".etsy-tiktok-search-heading");
          const terms=[...document.querySelectorAll(".etsy-tiktok-term")];
          const box=el=>{const b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,right:b.right,bottom:b.bottom};};
          return {
            button:box(button),heading:box(heading),label:box(label),count:box(count),
            smallCssWidth:getComputedStyle(count).width,
            labelWrap:getComputedStyle(label).whiteSpace,
            termBoxes:terms.map(box),
            countText:count.textContent,
            labelText:label.textContent,
          };
        });
        const p=viewportWidth+"px viewport, "+cellWidth+"px cell";
        assert.equal(r.labelText,"Search terms",p+" label missing");
        assert.equal(r.countText,"2 gợi ý",p+" count missing");
        assert.ok(r.label.width>60,p+" heading shrunk to individual letters: "+JSON.stringify(r));
        assert.ok(r.label.height<=17,p+" heading broken into several rows: "+JSON.stringify(r));
        assert.ok(r.count.height<=17,p+" count broken into rows");
        assert.ok(r.label.right+3<=r.count.x,p+" label overlaps count");
        assert.ok(r.heading.height<=19,p+" header too tall");
        assert.ok(r.button.height<112,p+" button tall due to broken heading");
        assert.ok(r.termBoxes.every(t=>t.height<19),p+" keyword broke into multiple lines");
        assert.ok(r.count.right<=r.button.right-2,p+" count overflows card");
        console.log("PASS "+p+"; header "+Math.round(r.heading.height)+"px, card "+
          Math.round(r.button.height)+"px");
      }
    } finally {await page.close();}
  }
} finally {await browser.close();}
