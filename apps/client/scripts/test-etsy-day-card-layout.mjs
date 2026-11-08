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
     '<div class="etsy-observed-day-grid">'+cards+'</div></section>'+
     '<section class="etsy-day-groups"><span class="etsy-day-groups-title">Nhóm · Hôm nay</span>'+
     '<div class="etsy-day-groups-list">'+
     ['Tất cả','Christmas','Thanksgiving','Movies & TV','Sports','Books','Gaming','Animals','Faith','Khác']
       .map((name,i)=>'<button class="etsy-day-group"><span>'+name+
         '</span><strong>'+(i===0?148:i===1?45:i+2)+'</strong></button>').join('')+
     '</div></section></body></html>');
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
   const badges=await page.locator('.etsy-day-group').evaluateAll(nodes=>nodes.map(el=>{
     const box=el.getBoundingClientRect();
     const label=el.querySelector('span').getBoundingClientRect();
     const number=el.querySelector('strong').getBoundingClientRect();
     return {noOverlap:label.right+2<=number.left,
       clipped:el.scrollWidth>el.clientWidth+1,
       horizontalOverflow:box.right>document.documentElement.clientWidth+2};
   }));
   const positions=await page.evaluate(()=>({
     datesBottom:document.querySelector('.etsy-observed-days').getBoundingClientRect().bottom,
     badgesTop:document.querySelector('.etsy-day-groups').getBoundingClientRect().top,
   }));
   assert.equal(badges.length,10);
   assert.ok(badges.every(b=>b.noOverlap&&!b.clipped&&!b.horizontalOverflow),
     viewportWidth+'px topic badge overlap/clipping');
   assert.ok(positions.badgesTop>=positions.datesBottom-8,
     viewportWidth+'px topic badges overlap date cards');
   console.log('PASS '+viewportWidth+'px: 10 cards, two rows, 10 topic badges, zero overlap; card width '+metrics[0].width);
   await page.close();
 }
}finally{await browser.close();}