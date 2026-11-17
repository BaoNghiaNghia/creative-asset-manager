import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {chromium} from "playwright";

// Static Chromium geometry QA for the actual gallery class names/styles.
// Covers small phones, tablets, desktops and short viewport heights.
const css=await readFile(new URL("../app/etsy-spy/ListingImageCell.css",import.meta.url),"utf8");
const img="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='150' height='150'%3E%3Crect width='150' height='150' fill='%23e1ebf5'/%3E%3Ccircle cx='75' cy='70' r='44' fill='%2387a8c8'/%3E%3C/svg%3E";
function tiles(count){
  return Array.from({length:count},(_,i)=>'<button type="button" class="etsy-listing-image-tile">'+
    '<img alt="" src="'+img+'"><span>Ảnh '+String(i+1).padStart(2,"0")+'</span></button>').join("");
}
function product(code,counts){
  return '<section class="etsy-listing-image-group">'+
    '<div class="etsy-listing-image-group-heading"><h3>'+code+'</h3><span>'+
    counts.reduce((sum,n)=>sum+n,0)+' ảnh</span></div>'+
    counts.map((n,i)=>'<div class="etsy-listing-image-design">'+
    '<div class="etsy-listing-image-design-title"><strong>Design '+i+'abcd1234</strong>'+
    '<small>front center · '+n+'/10 · Đã lưu đủ</small></div>'+
    '<div class="etsy-listing-image-grid">'+tiles(n)+'</div></div>').join("")+
    '</section>';
}
function html(){
  return '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'+
    '<style>body{margin:0;font-family:Arial, sans-serif;}'+css+'</style></head><body>'+
    '<div class="etsy-listing-image-overlay"><section role="dialog" class="etsy-listing-image-dialog">'+
    '<header class="etsy-listing-image-dialog-head"><div><span class="etsy-listing-image-kicker">CAM IMAGE EXTENSION / SAVED OUTPUTS</span>'+
    '<h2>Listing Image <span>#4595979631</span></h2>'+
    '<p>Joy to the World Sweatshirt, Vintage Christian Christmas Sweater</p></div>'+
    '<button class="etsy-listing-image-close">×</button></header>'+
    '<div class="etsy-listing-image-body"><div class="etsy-listing-image-workspace">'+
    '<section class="etsy-listing-image-preview"><div class="etsy-listing-image-pane-heading">'+
    '<strong>Ảnh xem trước</strong><span>Ảnh 01</span></div>'+
    '<div class="etsy-listing-image-large"><div class="etsy-listing-image-review-frame">'+
    '<img src="'+img+'" alt="Ảnh xem trước"></div>'+
    '<div class="etsy-listing-image-preview-meta"><div><strong>Ảnh 01</strong>'+
    '<small>1254 × 1254 px</small></div><a href="#">Xem PNG gốc ↗</a></div></div></section>'+
    '<div class="etsy-listing-image-library"><div class="etsy-listing-image-library-heading">'+
    '<strong>Ảnh theo sản phẩm</strong><span>26 ảnh đã lưu</span></div>'+
    product("C1717",[10,10])+product("G180",[6])+'</div></div></div>'+
    '<footer class="etsy-listing-image-foot">Chỉ hiển thị ảnh PNG đã đồng bộ</footer>'+
    '</section></div></body></html>';
}
const widths=[320,375,430,600,760,768,1024,1440,1920];
const heights=[650,850];
const browser=await chromium.launch({channel:"chrome",headless:true});
try{
  for(const width of widths){
    for(const height of heights){
      const page=await browser.newPage({viewport:{width,height}});
      try{
        await page.setContent(html());
        const result=await page.evaluate(()=>{
          const get=selector=>document.querySelector(selector);
          const rect=el=>el?.getBoundingClientRect().toJSON();
          const dialog=get(".etsy-listing-image-dialog");
          const workspace=get(".etsy-listing-image-workspace");
          const preview=get(".etsy-listing-image-preview");
          const reviewFrame=get(".etsy-listing-image-review-frame");
          const library=get(".etsy-listing-image-library");
          const body=get(".etsy-listing-image-body");
          const header=get(".etsy-listing-image-dialog-head");
          const foot=get(".etsy-listing-image-foot");
          const panes={dialog:rect(dialog),workspace:rect(workspace),
            preview:rect(preview),reviewFrame:rect(reviewFrame),
            library:rect(library),body:rect(body),header:rect(header),foot:rect(foot)};
          return {panes,gridMode:getComputedStyle(workspace).display,
            libraryOverflow:getComputedStyle(library).overflowY,
            bodyOverflow:getComputedStyle(body).overflowY,
            libraryScrollHeight:library.scrollHeight,libraryClientHeight:library.clientHeight,
            bodyScrollHeight:body.scrollHeight,bodyClientHeight:body.clientHeight,
            docScrollWidth:document.documentElement.scrollWidth,
            thumbCount:get(".etsy-listing-image-library").querySelectorAll(".etsy-listing-image-tile").length,
            groupCount:get(".etsy-listing-image-library").querySelectorAll(".etsy-listing-image-group").length,
            noClippedThumbs:[...get(".etsy-listing-image-library").querySelectorAll(".etsy-listing-image-tile")].every(tile=>
              tile.querySelector("img").getBoundingClientRect().width>=50)};
        });
        const p=result.panes;
        const where=width+"x"+height+": ";
        assert.equal(result.groupCount,2,where+"missing product group");
        assert.equal(result.thumbCount,26,where+"missing thumbnails");
        assert.ok(p.dialog.left>=-1 && p.dialog.right<=width+1,where+"dialog horizontally clipped");
        assert.ok(p.dialog.top>=-1 && p.dialog.bottom<=height+1,where+"dialog vertically clipped");
        assert.ok(result.docScrollWidth<=width+1,where+"document x-overflow");
        assert.ok(result.noClippedThumbs,where+"tiny or clipped gallery thumb");
        if(width>760){
          assert.equal(result.gridMode,"grid",where+"should use left/right grid");
          assert.ok(p.preview.right<=p.library.left+2,where+"preview and library overlap");
          assert.ok(Math.abs(p.preview.top-p.library.top)<2,where+"columns not aligned");
          assert.equal(result.libraryOverflow,"auto",where+"library must scroll independently");
          assert.ok(result.libraryScrollHeight>result.libraryClientHeight,
            where+"long library should have internal scroll");
          const before=p.preview.top;
          const after=await page.evaluate(()=>{
            const lib=document.querySelector(".etsy-listing-image-library");
            lib.scrollTop=120;
            return document.querySelector(".etsy-listing-image-preview")
              .getBoundingClientRect().top;
          });
          assert.equal(after,before,where+"review moved with gallery scroll");
        }else{
          assert.equal(result.gridMode,"flex",where+"should stack columns on mobile");
          assert.ok(p.preview.bottom<=p.library.top+2,where+"mobile preview overlaps library");
          assert.equal(result.bodyOverflow,"auto",where+"mobile should scroll whole body");
          assert.equal(result.libraryOverflow,"visible",where+"mobile library should not nest scroll");
        }
        console.log("PASS "+where+result.gridMode+"; gallery "+
          Math.round(p.library.width)+"px; modal "+Math.round(p.dialog.height)+"px");
      }finally{await page.close();}
    }
  }
}finally{await browser.close();}
