import test from "node:test";
import assert from "node:assert/strict";
import {assertShopUrl,collectVisibleLinks} from "../src/browser_pilot.mjs";

const shop=assertShopUrl("https://www.amazon.com/stores/AcmeCaps","amazon");
const urls=["https://www.amazon.com/dp/B012345678",
  "https://www.amazon.com/dp/B012345679",
  "https://www.amazon.com/dp/B012345680"];
function scrollingPage({challenge=false,neverMoves=false}={}){
 let position=0,evals=0;
 const page={
  getByRole(role){
   assert.equal(role,"link");
   const visible=()=>position===0?[urls[0]]:position===1?urls.slice(0,2):urls;
   return {
    first:()=>({waitFor:async()=>{}}),
    count:async()=>visible().length,
    nth:i=>({isVisible:async()=>true,getAttribute:async()=>visible()[i]}),
   };
  },
  locator:()=>({innerText:async()=>"Public hats and caps"}),
  title:async()=>challenge && position>0?"Robot Check":"Caps store",
  evaluate:async fn=>{
   evals++;
   if(String(fn).includes("scrollTo")){position=0;return undefined}
   const before=position;
   if(!neverMoves)position=Math.min(2,position+1);
   return {before,after:position};
  },
 };
 return {page,getPosition:()=>position,getEvaluations:()=>evals};
}
test("scrolls from top in bounded steps and collects distinct visible links",async()=>{
 const fake=scrollingPage();const waits=[];
 const links=await collectVisibleLinks(fake.page,shop,"visible_click",new Set(),{
  limit:3,maxScrollSteps:5,wait:async n=>waits.push(n)});
 assert.deepEqual(links.map(l=>l.key),["amazon:B012345678","amazon:B012345679","amazon:B012345680"]);
 assert.equal(waits.every(ms=>ms===600),true);
 assert.ok(waits.length<=5);
});
test("skips already observed listing keys during new top-to-bottom pass",async()=>{
 const fake=scrollingPage();
 const links=await collectVisibleLinks(fake.page,shop,"visible_click",
  new Set(["amazon:B012345678"]),{limit:2,wait:async()=>{}});
 assert.deepEqual(links.map(l=>l.key),["amazon:B012345679","amazon:B012345680"]);
});
test("stops if a new challenge appears after scrolling",async()=>{
 const fake=scrollingPage({challenge:true});
 await assert.rejects(()=>collectVisibleLinks(fake.page,shop,"visible_click",new Set(),
  {limit:3,wait:async()=>{}}),/marketplace_access_limited_on_scroll/);
});
test("bounded scroll exits when site cannot move",async()=>{
 const fake=scrollingPage({neverMoves:true});
 const links=await collectVisibleLinks(fake.page,shop,"visible_click",new Set(),
  {limit:3,wait:async()=>{}});assert.equal(links.length,1);
 assert.ok(fake.getEvaluations()<=2);
});
test("does not scroll for direct-navigation legacy mode",async()=>{
 let evaluates=0;
 const page={locator:()=>({
  first:()=>({waitFor:async()=>{}}),
  count:async()=>1,nth:()=>({isVisible:async()=>true,getAttribute:async()=>urls[0]}),
 }),evaluate:async()=>{evaluates++}};
 const links=await collectVisibleLinks(page,shop,"direct",new Set(),{limit:2});
 assert.equal(links.length,1);assert.equal(evaluates,0);
});
test("rejects unbounded scroll parameters",async()=>{
 const fake=scrollingPage();
 await assert.rejects(()=>collectVisibleLinks(fake.page,shop,"visible_click",new Set(),
 {limit:1000}),/invalid_bounded_scroll_limits/);
});