import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';

// Actual CSS in a real browser, including tiny screens and large counts.
const css=await fs.readFile('app/etsy-spy/EtsySpyPage.css','utf8');
const data=[
  ['Hôm nay',148,0],['Hôm qua',194,23],['06/11',262,0],['05/11',5,0],
  ['04/11',0,0],['03/11',0,0],['02/11',0,0],['01/11',0,0],
  ['31/10',12500,1200],['30/10',0,0]
];
const cards=data.map(([day,available,picked])=>'<button type="button" class="etsy-observed-day">'+
  '<span class="etsy-observed-day-label">'+day+'</span>'+
  '<span class="etsy-observed-day-metrics">'+
  '<span class="etsy-observed-day-eligible"><strong>'+Number(available).toLocaleString('vi-VN')+'</strong><small>Khả dụng</small></span>'+
  '<span class="etsy-observed-day-pick"><strong>'+Number(picked).toLocaleString('vi-VN')+'</strong><small>Picked</small></span>'+
  '</span></button>').join('');
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 for(const viewportWidth of [375,768,1118,1440]){
   const page=await browser.newPage({viewport:{width:viewportWidth,height:600}});
   await page.setContent('<!doctype html><html><head><style>'+
     'body{margin:0;padding:12px;box-sizing:border-box;font-family:Arial,sans-serif;}'+
     '*{box-sizing:border-box;}'+css+'</style></head><body>'+
     '<section class="etsy-observed-days" style="width:100%">'+
     '<div class="etsy-observed-day-grid">'+cards+'</div></section></body></html>');
   await page.evaluate(()=>document.fonts.ready);
   const metrics=await page.locator('.etsy-observed-day').evaluateAll(nodes=>nodes.map(node=>{
     const card=node.getBoundingClientRect();
     const left=node.querySelector('.etsy-observed-day-eligible').getBoundingClientRect();
     const right=node.querySelector('.etsy-observed-day-pick').getBoundingClientRect();
     const label=node.querySelector('.etsy-observed-day-label').getBoundingClientRect();
     return {day:label.textContent,overlap:left.right>right.left-3,
       leftOutside:left.left<card.left,rightOutside:right.right>card.right-2,
       dateAbove:label.bottom<Math.min(left.top,right.top),
       width:card.width,
       textOverflow:[...node.querySelectorAll('strong,small')].some(n=>n.scrollWidth>n.clientWidth+1)};
   }));
   for(const m of metrics){
     assert.equal(m.overlap,false,viewportWidth+'px overlap: '+m.day);
     assert.equal(m.leftOutside,false,viewportWidth+'px left outside: '+m.day);
     assert.equal(m.rightOutside,false,viewportWidth+'px right outside: '+m.day);
     assert.equal(m.dateAbove,true,viewportWidth+'px date not on top: '+m.day);
     assert.equal(m.textOverflow,false,viewportWidth+'px clipping: '+m.day);
   }
   console.log('PASS '+viewportWidth+'px: 10 cards, two rows, zero overlap; card width '+metrics[0].width);
   await page.close();
 }
}finally{await browser.close();}
