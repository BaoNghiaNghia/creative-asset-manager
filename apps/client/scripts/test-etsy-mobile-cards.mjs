import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';

const css=await fs.readFile('app/etsy-spy/EtsySpyPage.css','utf8');
const kpis=[
  ['Eligible listings','633','Ready to review','etsy-stat-total'],
  ['Picked','23','Extracted design','etsy-stat-picked'],
  ['Later','0','Auto-grouped design themes','etsy-stat-later'],
  ['Blacklist','5','Blocked keyword phrases','etsy-stat-blacklist'],
  ['Win Keyword','0','Saved keyword notes','etsy-stat-win'],
  ['Keyword nodes','1791','Discovery branches','etsy-stat-keywords'],
].map(([name,num,hint,cls])=>
  '<button class="etsy-stat '+cls+'"><span class="etsy-stat-heading">'+
  '<span class="etsy-stat-icon">◇</span><span>'+name+'</span></span>'+
  '<strong>'+num+'</strong><small>'+hint+'</small></button>').join('');
const dates=[['Hôm nay',174,0],['Hôm qua',194,23],['06/11',260,0],
  ['05/11',5,0],['04/11',0,0],['03/11',0,0],['02/11',0,0],['01/11',0,0]
].map(([d,n,p])=>'<button class="etsy-observed-day"><span class="etsy-observed-day-label">'+d+
  '</span><span class="etsy-observed-day-metrics">'+
  '<span class="etsy-observed-day-eligible"><strong>'+n+'</strong><small>Khả dụng</small></span>'+
  '<span class="etsy-observed-day-pick"><strong>'+p+'</strong><small>Picked</small></span>'+
  '</span></button>').join('');
const groups=[['Tất cả',174],['Christmas',50],['Khác',48],['Crafts',14],
  ['Movies & TV',11],['Animals',11],['Sports',9],['Halloween',8],
  ['Thanksgiving',6],['Books',4],['Faith',4],['Music',3],
  ['Motivation',3],['Family',2],['Gaming',1]
].map(([n,amount],i)=>'<button class="etsy-day-group'+(i===0?' active':'')+
  '"><span>'+n+'</span><strong>'+amount+'</strong></button>').join('');
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  for(const w of [320,360,375,390,414,430,600,760,768,1024]){
    const page=await browser.newPage({viewport:{width:w,height:900},
      deviceScaleFactor:1,hasTouch:w<=760,isMobile:w<=760});
    await page.setContent('<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"/>'+
      '<style>html,body{margin:0;padding:0;width:100%;}'+css+'</style></head>'+
      '<body><div class="etsy-spy-page"><div class="etsy-stage-canvas"><div class="etsy-stage-card">'+
      '<section class="etsy-stats">'+kpis+'</section>'+
      '<section class="etsy-observed-days"><div class="etsy-observed-day-grid">'+dates+'</div></section>'+
      '<section class="etsy-day-groups"><span class="etsy-day-groups-title">Nhóm · Hôm nay</span>'+
      '<div class="etsy-day-groups-list">'+groups+'</div></section>'+
      '</div></div></div></body></html>');
    await page.evaluate(()=>document.fonts.ready);
    const data=await page.evaluate(()=>{
      const rect=el=>el.getBoundingClientRect();
      const grid=document.querySelector('.etsy-stats');
      const cards=[...grid.children];
      const dayScroller=document.querySelector('.etsy-observed-day-grid');
      const dayCards=[...dayScroller.children];
      const groupBtns=[...document.querySelectorAll('.etsy-day-group')];
      return {
        bodyWidth:document.documentElement.scrollWidth,
        gridDisplay:getComputedStyle(grid).display,
        cards:cards.map(x=>({x:rect(x).x,y:rect(x).y,width:rect(x).width,
          height:rect(x).height,
          clipped:[...x.querySelectorAll('.etsy-stat-heading span:last-child,strong,small')]
            .some(t=>t.scrollWidth>t.clientWidth+1)})),
        days:dayCards.map(x=>{
          const date=rect(x.querySelector('.etsy-observed-day-label'));
          const left=rect(x.querySelector('.etsy-observed-day-eligible'));
          const right=rect(x.querySelector('.etsy-observed-day-pick'));
          return {width:rect(x).width,height:rect(x).height,
            overlap:left.right>right.left-2,
            topDate:date.bottom<=left.top+1,
            clipped:[...x.querySelectorAll('strong,small')].some(t=>t.scrollWidth>t.clientWidth+1)};
        }),
        dayScrolls:dayScroller.scrollWidth>dayScroller.clientWidth,
        badges:groupBtns.map(x=>({
          height:rect(x).height,
          textSize:parseFloat(getComputedStyle(x).fontSize),
          clipped:x.scrollWidth>x.clientWidth+1,
          labelOverlaps:rect(x.querySelector('span')).right+2>rect(x.querySelector('strong')).left,
          offscreen:rect(x).right>document.documentElement.clientWidth+1,
        })),
      };
    });
    assert.ok(data.bodyWidth<=w+2,'Page has horizontal overflow at '+w+': '+data.bodyWidth);
    assert.ok(data.days.every(x=>!x.overlap&&x.topDate&&!x.clipped),
      'Day card text overlaps at '+w+': '+JSON.stringify(data.days));
    assert.ok(data.badges.every(x=>!x.clipped&&!x.labelOverlaps&&!x.offscreen),
      'Badge text overlaps or exits viewport at '+w);
    if(w<=760){
      assert.equal(data.gridDisplay,'grid','Mobile KPIs must use grid at '+w);
      assert.equal(data.cards.length,6);
      assert.ok(Math.abs(data.cards[0].y-data.cards[1].y)<2,'Two KPI columns');
      assert.ok(data.cards[1].x>data.cards[0].x+35,'Second KPI on the right');
      assert.ok(data.cards[2].y>data.cards[0].y+35,'Third KPI on next row');
      assert.ok(Math.abs(data.cards[2].y-data.cards[3].y)<2,'Two KPIs in second row');
      assert.ok(data.cards.every(x=>x.height>=78&&x.width>=120&&!x.clipped),
        'Mobile KPI clipped at '+w+': '+JSON.stringify(data.cards));
      assert.ok(data.days.every(x=>x.height>=73&&x.width>=150),'Mobile date card too small');
      assert.ok(data.dayScrolls,'Days should scroll horizontally on phones');
      assert.ok(data.badges.every(x=>x.height>=35&&x.textSize>=11.4),'Mobile badges need touch-size text');
    }
    console.log('PASS '+w+'px: KPI, date cards, group badges fit without collisions'+
      (w<=760?' (2-column mobile grid)':''));
    await page.close();
  }
}finally{await browser.close();}
