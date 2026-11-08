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
  const scenarios=[320,360,375,390,414,430,600,760]
    .map(w=>({w,touch:true,mobile:true}));
  scenarios.push(
    {w:768,touch:false,mobile:false},
    {w:980,touch:false,mobile:false},
    {w:980,touch:true,mobile:false}, // Chrome Android desktop-site viewport
    {w:1024,touch:true,mobile:false},
    {w:1100,touch:true,mobile:false},
    {w:1024,touch:false,mobile:false},
  );
  for(const {w,touch,mobile} of scenarios){
    const page=await browser.newPage({viewport:{width:w,height:900},
      deviceScaleFactor:touch?2:1,hasTouch:touch,isMobile:mobile});
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
      const root=document.querySelector('.etsy-spy-page');
      const stats=root.querySelector('.etsy-stats');
      const kpis=[...stats.children];
      const dayScroller=root.querySelector('.etsy-observed-day-grid');
      const dates=[...dayScroller.children];
      const groupScroller=root.querySelector('.etsy-day-groups-list');
      const badgeEls=[...groupScroller.children];
      const statsBox=rect(stats),groupBox=rect(groupScroller);
      return {
        bodyWidth:document.documentElement.scrollWidth,
        statsDisplay:getComputedStyle(stats).display,
        statsColumns:getComputedStyle(stats).gridTemplateColumns.split(' ').length,
        statsHeight:statsBox.height,
        kpis:kpis.map(el=>{
          const name=rect(el.querySelector('.etsy-stat-heading'));
          const number=rect(el.querySelector('strong'));
          return {left:rect(el).left,top:rect(el).top,
            height:rect(el).height,width:rect(el).width,
            overlap:name.right>number.left-2,
            hintVisible:getComputedStyle(el.querySelector('small')).display!=='none',
            clipped:[...el.querySelectorAll('.etsy-stat-heading span:last-child,strong')]
              .some(t=>t.scrollWidth>t.clientWidth+1)};
        }),
        days:dates.map(el=>{
          const label=rect(el.querySelector('.etsy-observed-day-label'));
          const left=rect(el.querySelector('.etsy-observed-day-eligible'));
          const right=rect(el.querySelector('.etsy-observed-day-pick'));
          return {width:rect(el).width,height:rect(el).height,
            overlap:left.right>right.left-2,topDate:label.bottom<=left.top+1,
            clipped:[...el.querySelectorAll('strong,small')].some(t=>t.scrollWidth>t.clientWidth+1)};
        }),
        dayScrolls:dayScroller.scrollWidth>dayScroller.clientWidth,
        groupDisplay:getComputedStyle(groupScroller).display,
        groupFlow:getComputedStyle(groupScroller).gridAutoFlow,
        groupHeight:groupBox.height,
        groupScrolls:groupScroller.scrollWidth>groupScroller.clientWidth,
        badges:badgeEls.map(el=>{
          const r=rect(el);
          return {height:r.height,width:r.width,
            textSize:parseFloat(getComputedStyle(el).fontSize),
            clipped:el.scrollWidth>el.clientWidth+1,
            overlapping:rect(el.querySelector('span')).right+2>rect(el.querySelector('strong')).left};
        }),
      };
    });
    assert.ok(data.bodyWidth<=w+2,'Page horizontal overflow at '+w+': '+data.bodyWidth);
    assert.ok(data.days.every(d=>!d.overlap&&d.topDate&&!d.clipped),
      'Day labels/counts collide at '+w+': '+JSON.stringify(data.days));
    assert.ok(data.badges.every(b=>!b.clipped&&!b.overlapping),
      'Badge text clipped/overlapping at '+w);
    const touchLayout=w<=760||(touch&&w<=1200);
    if(touchLayout){
      assert.equal(data.statsDisplay,'grid','KPI layout must be grid at '+w);
      assert.equal(data.kpis.length,6);
      const cols=w>760?3:2;
      assert.equal(data.statsColumns,cols,'Wrong columns at '+w);
      assert.ok(data.kpis.every(x=>!x.overlap&&!x.clipped&&!x.hintVisible),
        'KPI labels/values collide or hints take up space at '+w+': '+JSON.stringify(data.kpis));
      for(let i=0;i<6;i++){
        const row=Math.floor(i/cols),col=i%cols;
        const rowBase=data.kpis[row*cols].top;
        assert.ok(Math.abs(data.kpis[i].top-rowBase)<2,'Wrong KPI row '+i+' at '+w);
        if(col>0)assert.ok(data.kpis[i].left>data.kpis[i-1].left+30,
          'Wrong KPI column '+i+' at '+w);
      }
      assert.ok(data.statsHeight<=(w>760?190:235),
        'KPI section too tall at '+w+': '+data.statsHeight);
      assert.ok(data.kpis.every(x=>x.height<=(w>760?90:82)&&x.width>=105),
        'Cards too large or too narrow at '+w);
      assert.ok(data.days.every(x=>x.height<=(w>760?82:72)),
        'Day cards too tall at '+w);
      assert.equal(data.dayScrolls,true,'Day cards need horizontal scroll');
      assert.equal(data.groupDisplay,'grid','Groups should use two-row scrolling grid');
      assert.equal(data.groupFlow,'column','Group order should fill columns');
      assert.ok(data.groupHeight<=(w>760?90:78),
        'Group badges section too tall at '+w+': '+data.groupHeight);
      assert.equal(data.groupScrolls,true,'Every group must be reachable by horizontal swipe');
      assert.ok(data.badges.every(x=>x.height>=27&&x.height<=37),
        'Group badge too small/large at '+w);
      await page.locator('.etsy-day-groups-list').evaluate(node=>{node.scrollLeft=9999});
      const scrolled=await page.locator('.etsy-day-groups-list').evaluate(node=>node.scrollLeft);
      assert.ok(scrolled>0,'Swipe target inaccessible at '+w);
    }
    console.log('PASS '+w+'px '+(touch?'touch':'mouse')+': '+
      (touchLayout?colsLabel(w)+' compact KPIs, ':'desktop preserved, ')+
      'compact dates, 2-row swipeable groups, no collisions');
    await page.close();
  }
}finally{await browser.close();}
function colsLabel(w){return w>760?'3x2':'2x3';}
