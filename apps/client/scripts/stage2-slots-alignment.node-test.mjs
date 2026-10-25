import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { chromium } from "playwright";

const base = new URL("../app/realistic-review-ugc/", import.meta.url);
const css = ["ProductsPhotoWorkflow.css", "ProductsPhotoWorkflowTwoColumns.css"]
  .map(name => readFileSync(new URL(name, base), "utf8")).join("\n");
const hat = "data:image/jpeg;base64," +
  readFileSync(new URL("../public/rrugc/blueprint/stock/natural-black/front.jpg", import.meta.url)).toString("base64");

function stage2Fixture() {
  const stages = ["Ecommerce","Ecommerce","Ecommerce","Ecommerce","Multi-hat","UGC"];
  const steps = stages.map((name, i) => {
    const images = Array.from({length:i < 4 ? 1 : 3}, (_, j) =>
      `<button class="rrugc-products-photo-empty-shot is-completed" type="button"><img src="${hat}" alt="Output ${j + 1}"></button>`).join("");
    return `<div class="rrugc-products-photo-stage-row" data-skill="slot${i+1}">
      <div class="rrugc-products-photo-stage-title">
        <span class="rrugc-products-photo-number">${i+1}</span>
        <div><strong>${name}</strong><small>Image ${i+1}</small></div>
      </div>
      <div class="rrugc-products-photo-output-strip">${images}</div>
      <div class="rrugc-products-photo-skill"><span>Skill ${i+1}</span><strong>8869-ecom-${i+1}</strong></div>
      <div class="rrugc-products-photo-stage-controls">
        <time class="rrugc-products-photo-updated">10/23/2026</time>
        <span class="rrugc-products-photo-duration">5m 42s</span>
        <button class="rrugc-products-photo-run-button is-completed">Done</button>
        <button class="rrugc-products-photo-detail-toggle">Details</button>
      </div>
    </div>`;
  }).join("");
  const colors = Array.from({length:13}, (_,i) =>
    `<button class="rrugc-colorway-slot is-completed"><img alt="Color ${i+1}" src="${hat}"></button>`).join("");
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;padding:20px">
  <main class="rrugc-products-photo-pipeline">
    <div class="rrugc-products-photo-stage-list is-two-column">
    ${steps}
    <div class="rrugc-products-photo-stage-row is-colorway" data-skill="slot7">
      <div class="rrugc-products-photo-stage-title"><span class="rrugc-products-photo-number">7</span><div><strong>13 Hat Colors</strong></div></div>
      <div class="rrugc-products-photo-skill"><span>Skill 7</span><strong>s7-8869-scale-12-color</strong></div>
      <div class="rrugc-products-photo-stage-controls">
        <time class="rrugc-products-photo-updated">10/23/2026</time>
        <span class="rrugc-products-photo-duration">25m 47s</span>
        <button class="rrugc-products-photo-run-button is-completed">Done</button>
        <button class="rrugc-products-photo-detail-toggle">Details</button>
      </div>
      <div class="rrugc-products-photo-output-strip is-colors">${colors}</div>
    </div>
    </div>
  </main></body></html>`;
}

const checks = [
  {width:1920, stacked:false},
  {width:1200, stacked:false},
  {width:900, stacked:false},
  {width:700, stacked:false},
  {width:620, stacked:true},
  {width:390, stacked:true},
];

test("Stage 2 slots 1-6 keep metadata/buttons left and output tiles right", async () => {
  const executablePath=[process.env.CHROME_BIN,chromium.executablePath(),"/usr/bin/google-chrome"].find(x=>x&&existsSync(x));
  assert.ok(executablePath, "Chrome must be installed for alignment checks");
  const browser=await chromium.launch({headless:true,executablePath});
  try {
    for(const {width,stacked} of checks) {
      const page=await browser.newPage({viewport:{width,height:900}});
      try {
        await page.setContent(stage2Fixture());
        await page.addStyleTag({content:css});
        const results=await page.evaluate(() => {
          const rc=(e)=>{const r=e.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
          const rows=[...document.querySelectorAll('.rrugc-products-photo-stage-row:not(.is-colorway)')];
          return {
            documentOverflow: document.documentElement.scrollWidth-document.documentElement.clientWidth,
            cards:rows.map(card => ({
              card:rc(card),
              title:rc(card.querySelector('.rrugc-products-photo-stage-title')),
              skill:rc(card.querySelector('.rrugc-products-photo-skill')),
              controls:rc(card.querySelector('.rrugc-products-photo-stage-controls')),
              date:rc(card.querySelector('.rrugc-products-photo-updated')),
              duration:rc(card.querySelector('.rrugc-products-photo-duration')),
              run:rc(card.querySelector('.rrugc-products-photo-run-button')),
              detail:rc(card.querySelector('.rrugc-products-photo-detail-toggle')),
              outputs:rc(card.querySelector('.rrugc-products-photo-output-strip')),
              tiles:[...card.querySelectorAll('.rrugc-products-photo-empty-shot')].map(rc),
            })),
          };
        });
        assert.ok(results.documentOverflow < 2, `document overflow at ${width}: ${results.documentOverflow}`);
        assert.equal(results.cards.length,6);
        for(const [i,c] of results.cards.entries()) {
          const ctx=`slot ${i+1} at ${width}px`;
          assert.ok(Math.abs(c.title.left-c.skill.left)<2, `title and skill left alignment: ${ctx}`);
          assert.ok(Math.abs(c.title.left-c.controls.left)<2, `controls left alignment: ${ctx} (${c.title.left},${c.controls.left})`);
          assert.ok(Math.abs(c.date.left-c.controls.left)<3, `date is first left control: ${ctx}`);
          assert.ok(c.run.left >= c.date.left, `run button not left of date: ${ctx}`);
          assert.ok(c.detail.left >= c.date.left, `details button not left of date: ${ctx}`);
          assert.ok(c.controls.right < c.card.right-8, `controls not at the far right: ${ctx}`);
          assert.ok(c.controls.top-c.skill.bottom<20, `no vertical gap between skill and actions: ${ctx}`);
          assert.ok(Math.abs(c.outputs.right - (c.card.right-12)) < 3, `output aligned right: ${ctx} (${c.outputs.right},${c.card.right})`);
          if(stacked) {
            assert.ok(c.outputs.top>=c.controls.bottom-1, `mobile output follows controls: ${ctx}`);
          } else {
            assert.ok(c.controls.right<=c.outputs.left-8, `left controls and right gallery do not overlap: ${ctx}`);
          }
          assert.ok(c.tiles.every(t=>t.right<=c.outputs.right+1), `all output images within right strip: ${ctx}`);
        }
        console.log(`slots 1-6 @${width}px: ${results.cards.map(c=>Math.round(c.controls.width)).join("/")}px controls; ${stacked?"gallery below, anchored right":"gallery at right"}`);
        if(width===1920 || width===1200) { const c=results.cards[4]; console.log(`slot5 @${width}: title(${Math.round(c.title.left)},${Math.round(c.title.top)}) controls(${Math.round(c.controls.left)},${Math.round(c.controls.top)}) dateY=${Math.round(c.date.top)} durationY=${Math.round(c.duration.top)} runY=${Math.round(c.run.top)} detailY=${Math.round(c.detail.top)} outputLeft=${Math.round(c.outputs.left)}`); }
      } finally { await page.close(); }
    }
  } finally {await browser.close();}
});
