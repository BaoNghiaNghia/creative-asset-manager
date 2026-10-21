import test from "node:test";
import assert from "node:assert/strict";
import {EventEmitter} from "node:events";
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,readdirSync,existsSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname,join} from "node:path";
import {runBrowserPilot} from "../src/browser_pilot.mjs";
import {runConfiguredPilot} from "../src/run_configured_pilot.mjs";
import {localShopPaths,runCamShopBatch} from "../src/cam_shop_batch.mjs";

const SHOP="https://www.amazon.com/stores/RecoveryCaps";
const PRODUCTS=[
 "https://www.amazon.com/dp/B012345678",
 "https://www.amazon.com/dp/B012345679",
 "https://www.amazon.com/dp/B012345680",
];
const KEYS=["amazon:B012345678","amazon:B012345679","amazon:B012345680"];
const UNSAFE_ERROR="Navigation timeout at https://www.amazon.com/dp/B012345679?token=private-test-value";

function approval(url=SHOP){
 return {platform:"amazon",shop_url:url,browser_access_approved:true,
  source_rights_reviewed:true,platform_terms_reviewed:true,robots_policy_reviewed:true,
  amazon_agent_policy_reviewed:true,approved_by:"Test operator",
  approved_at:new Date().toISOString()};
}
async function withDir(fn){
 const dir=mkdtempSync(join(tmpdir(),"bdna-recovery-"));
 try{return await fn(dir)}finally{rmSync(dir,{recursive:true,force:true})}
}

/** Event-driven Playwright double. All responses and DOM values are local;
 * no Chromium, fetch, marketplace account or remote service is involved.
 */
function fakeBrowser({shopUrl=SHOP,links=PRODUCTS,popup=false,missingTitle=null,
 failClick=null,failGoto=null,failBack=false,backMismatch=false,
 statuses={},auxiliaryResponses=[],missingDocument=null,lateStatus=null,
 refuseDuringDetail=null,failClose=false}={}){
 const context=new EventEmitter();
 const state={closed:false,clicks:[],gotos:[],backs:0,popupsOpened:0,popupsClosed:0};
 let latestPage,lateSent=false;
 function emitResponse(page,url,status,{navigation=true,type="document",child=false}={}){
  const frame=child?{page:()=>page,parentFrame:()=>page.mainFrame()}:page.mainFrame();
  const request={url:()=>url,isNavigationRequest:()=>navigation,
   resourceType:()=>type,frame:()=>frame};
  const response={url:()=>url,status:()=>status,request:()=>request,frame:()=>frame};
  context.emit("response",response);
  return response;
 }
 function makePage(initialUrl,isPopup=false){
  const page=new EventEmitter();
  const frame={page:()=>page,parentFrame:()=>null};
  let url=initialUrl;
  page.mainFrame=()=>frame;
  page.url=()=>url;
  page.setDefaultTimeout=()=>{};
  page.waitForLoadState=async()=>{};
  page.title=async()=>url===shopUrl?"Recovery Caps":"Product details";
  page.close=async()=>{if(isPopup)state.popupsClosed++};
  function navigate(next){
   url=next;
   latestPage=page;
   const response=next===missingDocument?null:emitResponse(page,next,statuses[next]??200);
   for(const entry of auxiliaryResponses)
    emitResponse(page,entry.url??next,entry.status,entry);
   return response;
  }
  page.goto=async next=>{
   state.gotos.push(next);
   if(next===failGoto)throw Error(UNSAFE_ERROR);
   return navigate(next);
  };
  page.goBack=async()=>{
   state.backs++;
   if(failBack)throw Error(UNSAFE_ERROR);
   return navigate(backMismatch?"https://www.amazon.com/stores/OtherCaps":shopUrl);
  };
  const anchors=()=>({
   first:()=>({waitFor:async()=>{}}),
   count:async()=>url===shopUrl?links.length:0,
   nth:i=>({
    isVisible:async()=>true,getAttribute:async()=>links[i],
    click:async()=>{
     const next=links[i];state.clicks.push(next);
     if(next===failClick)throw Error(UNSAFE_ERROR);
     if(popup){
      const detail=makePage(next,true);state.popupsOpened++;
      // A popup's document response can precede the page's popup event.
      latestPage=detail;
      if(next!==missingDocument)emitResponse(detail,next,statuses[next]??200);
      for(const entry of auxiliaryResponses)
       emitResponse(detail,entry.url??next,entry.status,entry);
      page.emit("popup",detail);
     }else navigate(next);
    },
   }),
  });
  page.getByRole=role=>{assert.equal(role,"link");return anchors()};
  page.locator=selector=>{
   if(selector==="body")return {innerText:async()=>"Public product details"};
   if(selector==="h1")return {first:()=>({
    textContent:async()=>{
     if(url===refuseDuringDetail)emitResponse(page,url,429);
     return url===missingTitle?"":"Canvas cap "+url.slice(-10);
    },
   })};
   if(selector.startsWith("a["))return anchors();
   if(selector.startsWith("img["))return {
    count:async()=>1,nth:()=>({
     isVisible:async()=>true,
     getAttribute:async()=>"https://m.media-amazon.com/images/I/fixture.jpg",
    }),
   };
   throw Error("Unexpected test selector");
  };
  return page;
 }
 const page=makePage(shopUrl);
 context.newPage=async()=>page;
 context.route=async()=>{};
 context.close=async()=>{if(failClose)throw Error(UNSAFE_ERROR);state.closed=true};
 return {state,launchPersistentContext:async()=>context,
  wait:async ms=>{
   if(ms===4000 && lateStatus && !lateSent){
    lateSent=true;emitResponse(latestPage,latestPage.url(),lateStatus);
   }
  },
 };
}
async function runFake(dir,browser,options={}){
 const profileDir=join(dir,"profile");
 const result=await runBrowserPilot({
  shopUrl:SHOP,platform:"amazon",authorization:approval(),profileDir,
  interactionMode:"visible_click",maxProducts:3,minimumDelayMs:5000,maximumDelayMs:5000,
  ...options,
 },{launchPersistentContext:browser.launchPersistentContext,wait:browser.wait,random:()=>0});
 assert.equal(browser.state.closed,true,"browser context is closed after a halted run");
 assert.equal(existsSync(profileDir+".pilot.lock"),false,"profile lock is released");
 return result;
}
function assertHalted(result,reason,successfulKeys){
 assert.equal(result.status,"needs_operator");
 assert.equal(result.reason,reason);
 assert.deepEqual(result.items.map(item=>item.listing_key),successfulKeys);
 assert.equal(result.observed_count,successfulKeys.length);
 assert.equal(result.items.some(item=>item.error_code),false,"failed details are not observations");
 assert.equal(result.scan_enabled,false);
 assert.equal(result.evidence_committed,false);
 assert.equal(result.rights_verified,false);
 assert.equal(result.sales_verified,false);
 assert.equal(result.coverage_state,"partial");
 assert.equal(JSON.stringify(result).includes("private-test-value"),false);
 assert.equal(JSON.stringify(result).includes("?token="),false);
}

for(const failedIndex of [0,1]){
 test("parser failure at product "+(failedIndex+1)+" halts with successful observations only",async()=>withDir(async dir=>{
  const browser=fakeBrowser({missingTitle:PRODUCTS[failedIndex]});
  const result=await runFake(dir,browser);
  assertHalted(result,"detail_parser_review_required",KEYS.slice(0,failedIndex));
  assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,failedIndex+1));
 }));
}
for(const scenario of [
 {name:"visible click timeout",settings:{failClick:PRODUCTS[1]},mode:"visible_click"},
 {name:"Back timeout",settings:{failBack:true},mode:"visible_click"},
 {name:"direct product navigation timeout",settings:{failGoto:PRODUCTS[1]},mode:"direct"},
]){
 test("later "+scenario.name+" preserves the successful prefix",async()=>withDir(async dir=>{
  const browser=fakeBrowser(scenario.settings);
  const result=await runFake(dir,browser,{interactionMode:scenario.mode});
  assertHalted(result,"browser_navigation_review_required",KEYS.slice(0,1));
  assert.equal(browser.state.clicks.includes(PRODUCTS[2]),false);
  assert.equal(browser.state.gotos.includes(PRODUCTS[2]),false);
 }));
}
test("a Back destination mismatch retains a safe reason and earlier observations",async()=>withDir(async dir=>{
 const browser=fakeBrowser({backMismatch:true});
 const result=await runFake(dir,browser);
 assertHalted(result,"shop_back_navigation_mismatch",KEYS.slice(0,1));
 assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,1));
}));

for(const popup of [false,true]){
 for(const status of [403,429]){
  test((popup?"popup":"same-tab")+" HTTP "+status+" halts before treating a neutral error page as a product",async()=>withDir(async dir=>{
   const browser=fakeBrowser({popup,statuses:{[PRODUCTS[1]]:status}});
   const result=await runFake(dir,browser);
   assertHalted(result,"marketplace_access_limited",KEYS.slice(0,1));
   assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,2));
   if(popup){
    assert.equal(browser.state.popupsOpened,2);
    assert.equal(browser.state.popupsClosed,2,"including the refused product popup");
   }
  }));
 }
}
for(const popup of [false,true]){
 test((popup?"missing popup":"stale same-tab")+" document response cannot mark a product observed",async()=>withDir(async dir=>{
  const browser=fakeBrowser({popup,missingDocument:PRODUCTS[1]});
  const result=await runFake(dir,browser);
  assertHalted(result,"product_navigation_response_unavailable",KEYS.slice(0,1));
  assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,2));
  if(popup)assert.equal(browser.state.popupsClosed,2);
 }));
}
for(const scenario of [
 {name:"same-tab refusal before the next product",popup:false,status:429,maxProducts:3},
 {name:"popup refusal on the final product",popup:true,status:403,maxProducts:1},
]){
 test(scenario.name+" during display halts the run",async()=>withDir(async dir=>{
  const browser=fakeBrowser({popup:scenario.popup,lateStatus:scenario.status});
  const result=await runFake(dir,browser,{maxProducts:scenario.maxProducts});
  assertHalted(result,"marketplace_access_limited",KEYS.slice(0,1));
  assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,1));
  if(scenario.popup)assert.equal(browser.state.popupsClosed,1);
 }));
}
test("a refusal while parsing a product prevents that product becoming an observation",async()=>withDir(async dir=>{
 const browser=fakeBrowser({refuseDuringDetail:PRODUCTS[0]});
 const result=await runFake(dir,browser);
 assertHalted(result,"marketplace_access_limited",[]);
 assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,1));
}));

test("unrelated image and child-frame refusal responses do not reject a successful product document",async()=>withDir(async dir=>{
 const browser=fakeBrowser({links:PRODUCTS.slice(0,1),auxiliaryResponses:[
  {status:403,navigation:false,type:"image",url:"https://m.media-amazon.com/images/I/missing.jpg"},
  {status:429,navigation:true,type:"document",child:true},
 ]});
 const result=await runFake(dir,browser,{maxProducts:1});
 assert.equal(result.status,"preview_completed");
 assert.deepEqual(result.items.map(item=>item.listing_key),KEYS.slice(0,1));
 assert.equal(result.observed_count,1);
}));

function configuredSetup(dir){
 const config={platform:"amazon",shop_url:SHOP,
  approval_file:join(dir,"approval.json"),profile_dir:join(dir,"profile"),
  report_dir:join(dir,"reports"),max_products:3,
  minimum_delay_ms:5000,maximum_delay_ms:5000,headless:false,interaction_mode:"visible_click"};
 writeFileSync(config.approval_file,JSON.stringify(approval()));
 return config;
}
test("a later browser failure saves an immutable partial report and resumes after its successful prefix",async()=>withDir(async dir=>{
 const config=configuredSetup(dir);
 const firstBrowser=fakeBrowser({failClick:PRODUCTS[1]});
 const first=await runConfiguredPilot(config,{
  runner:options=>runBrowserPilot(options,{
   launchPersistentContext:firstBrowser.launchPersistentContext,wait:async()=>{},random:()=>0,
  }),
 });
 assert.equal(first.status,"needs_operator");
 assert.equal(first.count,1);
 assert.equal(first.totalObserved,1);
 const firstBytes=readFileSync(first.output,"utf8");
 const saved=JSON.parse(firstBytes);
 assertHalted(saved,"browser_navigation_review_required",KEYS.slice(0,1));
 assert.equal(firstBrowser.state.closed,true);
 assert.equal(existsSync(config.profile_dir+".pilot.lock"),false);

 const secondBrowser=fakeBrowser();
 let skipped;
 const second=await runConfiguredPilot(config,{
  runner:options=>{
   skipped=options.skipProductKeys;
   return runBrowserPilot(options,{
    launchPersistentContext:secondBrowser.launchPersistentContext,wait:async()=>{},random:()=>0,
   });
  },
 });
 assert.deepEqual(skipped,KEYS.slice(0,1));
 assert.deepEqual(secondBrowser.state.clicks,PRODUCTS.slice(1));
 assert.equal(second.status,"preview_completed");
 assert.equal(second.previouslySeen,1);
 assert.equal(second.count,2);
 assert.equal(second.totalObserved,3);
 assert.equal(readFileSync(first.output,"utf8"),firstBytes,"first report remains immutable");
 assert.equal(readdirSync(config.report_dir).filter(name=>name.endsWith(".json")).length,2);
}));

for(const failClick of [null,PRODUCTS[1]]){
 test("cleanup rejection preserves a "+(failClick?"halted":"completed")+" run and retains its profile lock",async()=>withDir(async dir=>{
  const config={...configuredSetup(dir),max_products:failClick?3:1};
  const browser=fakeBrowser({failClick,failClose:true});
  const result=await runConfiguredPilot(config,{
   runner:options=>runBrowserPilot(options,{
    launchPersistentContext:browser.launchPersistentContext,wait:async()=>{},random:()=>0,
   }),
  });
  assert.equal(result.status,"needs_operator");
  assert.equal(result.count,1);
  assert.equal(result.totalObserved,1);
  const saved=JSON.parse(readFileSync(result.output,"utf8"));
  assertHalted(saved,"browser_cleanup_review_required",KEYS.slice(0,1));
  assert.equal(saved.profile_lock_retained,true);
  assert.equal(browser.state.closed,false);
  assert.equal(existsSync(config.profile_dir+".pilot.lock"),true);
  let launched=false;
  await assert.rejects(()=>runBrowserPilot({
   shopUrl:SHOP,platform:"amazon",authorization:approval(),profileDir:config.profile_dir,
  },{launchPersistentContext:async()=>{launched=true}}),/EEXIST/);
  assert.equal(launched,false,"uncertain browser cleanup cannot start another context");
 }));
}

test("a real parser failure stops a multi-shop batch and checkpoints only successful observations",async()=>withDir(async dir=>{
 const idA="12345678-1234-1234-1234-123456789abc";
 const idB="12345678-1234-1234-1234-123456789abd";
 const shops=[
  {id:idA,platform:"amazon",storefront_url:SHOP,status:"active",access_mode:"owned"},
  {id:idB,platform:"amazon",storefront_url:"https://www.amazon.com/stores/SecondCaps",status:"active",access_mode:"owned"},
 ];
 for(const shop of shops){
  const paths=localShopPaths(dir,shop.id);
  mkdirSync(dirname(paths.approvalFile),{recursive:true});
  writeFileSync(paths.approvalFile,JSON.stringify(approval(shop.storefront_url)));
 }
 const config={origin:"https://cam.example.test",id:idA,token:"test-only-token"};
 const request=async(_url,options)=>{
  assert.equal(options.method,"GET");
  return {ok:true,status:200,json:async()=>({
   scan_enabled:false,dispatch:{state:"disabled",active_scan_jobs:0},count:shops.length,items:shops,
  })};
 };
 const browser=fakeBrowser({missingTitle:PRODUCTS[1]});
 const runs=[];
 const result=await runCamShopBatch(config,{
  mode:"run_approved",baseDir:dir,maxShops:2,request,
  pilot:options=>{
   runs.push(options.shopUrl);
   return runBrowserPilot(options,{
    launchPersistentContext:browser.launchPersistentContext,wait:async()=>{},random:()=>0,
   });
  },
 });
 assert.deepEqual(runs,[SHOP],"second eligible shop is not opened after parser failure");
 assert.equal(result.run_count,1);
 assert.equal(result.needs_operator,1);
 assert.equal(result.items[0].state,"needs_operator");
 assert.equal(result.items[0].observed_count,1);
 assert.equal(result.items[0].total_observed,1);
 assert.equal(result.items[1].state,"locally_approved");
 assert.deepEqual(browser.state.clicks,PRODUCTS.slice(0,2));
 const paths=localShopPaths(dir,idA);
 const filenames=readdirSync(paths.reportDir).filter(name=>name.endsWith(".json"));
 assert.equal(filenames.length,1);
 assertHalted(JSON.parse(readFileSync(join(paths.reportDir,filenames[0]),"utf8")),
  "detail_parser_review_required",KEYS.slice(0,1));
}));