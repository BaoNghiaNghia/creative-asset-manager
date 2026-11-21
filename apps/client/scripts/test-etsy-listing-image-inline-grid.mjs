import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {chromium} from "playwright";

// Browser geometry QA: the Main and garment badges must share a single row.
const css=(await Promise.all(["EtsySpyPage.css","EtsySpyEvo.css","ListingImageCell.css"]
  .map(name=>readFile(new URL("../app/etsy-spy/"+name,import.meta.url),"utf8")))).join("\n");
const widths=[320,375,480,680,768,1024,1360,1920];
const scenarios=[
  {name:"two badges",codes:["C1717","G180"],image:true,limit:104},
  {name:"four badges",codes:["C1717","G180","G185","G500"],image:true,limit:114},
  {name:"pending",codes:["C1717","G180"],image:false,limit:104},
  {name:"empty",codes:[],image:false,limit:104},
];
function markup(scenario){
  const main=scenario.image
    ? '<button class="etsy-listing-image-main"><img alt="Main" src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="/></button>'
    : '<span class="etsy-listing-image-pending">Chưa có ảnh</span>';
  const badges=scenario.codes.length
    ? '<div class="etsy-listing-image-badges">'+scenario.codes.map(code=>
      '<button class="etsy-listing-image-badge"><span>10</span> '+code+'</button>').join("")+'</div>' : "";
  const ugc='<span class="etsy-listing-media-ugc"><svg viewBox="0 0 20 20" width="12" height="12"></svg><span>UGC: chưa có</span></span>';
  const td='<td class="etsy-listing-image-cell"><div class="etsy-listing-image-layout">'+
    '<div class="etsy-listing-image-primary">'+main+ugc+'</div>'+badges+'</div></td>';
  return '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'+
    '<style>'+css+'</style></head><body><main class="etsy-spy-page">'+
    '<div class="etsy-table-card"><div class="etsy-table-wrap">'+
    '<table class="etsy-data-table is-products"><thead><tr>'+('<th>header</th>'.repeat(12))+
    '</tr></thead><tbody><tr class="etsy-product-row">'+
    '<td>✓</td><td>Product</td><td>Design</td><td>SEO</td>'+td+
    '<td>Keyword</td><td>TM</td><td>Orders</td><td>Views</td><td>Carts</td><td>Observed</td><td>Actions</td>'+
    '</tr></tbody></table></div></div></main></body></html>';
}
const browser=await chromium.launch({channel:"chrome",headless:true});
try{
  for(const width of widths){
    const page=await browser.newPage({viewport:{width,height:850}});
    try{
      for(const s of scenarios){
        await page.setContent(markup(s));
        const result=await page.evaluate(()=>{
          const get=selector=>document.querySelector(selector);
          const rect=e=>e?e.getBoundingClientRect().toJSON():null;
          const layout=get(".etsy-listing-image-layout");
          const main=get(".etsy-listing-image-primary");
          const badges=get(".etsy-listing-image-badges");
          const buttons=[...document.querySelectorAll(".etsy-listing-image-badge")];
          const mainButton=get(".etsy-listing-image-main");
          const mainStyle=mainButton?getComputedStyle(mainButton):null;
          const ugc=get(".etsy-listing-media-ugc > span");
          return {layout:rect(layout),main:rect(main),badges:rect(badges),
            mainHasOnlyImage:Boolean(mainButton && mainButton.children.length===1 &&
              mainButton.children[0].tagName==="IMG" && mainButton.textContent.trim()===""),
            mainBorder:mainStyle?.borderTopWidth??null,
            mainPadding:mainStyle?.paddingTop??null,
            cell:rect(get(".etsy-listing-image-cell")),
            fits:buttons.every(b=>b.scrollWidth<=b.clientWidth+1),
            ugcFits:Boolean(ugc && ugc.scrollWidth<=ugc.clientWidth+1),
            label:getComputedStyle(get(".etsy-listing-image-cell"),"::before").content,
            gridStart:getComputedStyle(get(".etsy-listing-image-cell")).gridColumnStart};
        });
        assert.ok(result.layout.height<=s.limit,
          width+"px "+s.name+" grid too tall: "+JSON.stringify(result));
        assert.ok(result.layout.right<=result.cell.right+2,width+"px grid overflows cell");
        assert.ok(result.ugcFits,width+"px UGC label clipped: "+JSON.stringify(result));
        if(s.image){
          assert.ok(result.mainHasOnlyImage,width+"px Main includes extra text");
          assert.equal(result.mainBorder,"0px",width+"px Main still has outer card");
          assert.equal(result.mainPadding,"0px",width+"px Main still has padded frame");
        }
        if(result.badges){
          assert.ok(result.badges.left>=result.main.right+6,width+"px badges not right");
          assert.ok(result.badges.top<result.main.bottom && result.badges.bottom>result.main.top,
            width+"px badges below Main");
          assert.ok(result.badges.right<=result.layout.right+1,width+"px badges overflow right");
          assert.ok(result.fits,width+"px clipped badges");
        }
        if(width<=680){
          assert.equal(result.label,'"Media · Ảnh + UGC"');
          assert.equal(result.gridStart,"1");
        }
        console.log("PASS "+width+"px "+s.name+" - "+Math.round(result.layout.height)+"px");
      }
    }finally{await page.close();}
  }
}finally{await browser.close();}
