import test from "node:test";
import assert from "node:assert/strict";
import {EventEmitter} from "node:events";
import {mkdtempSync,rmSync,openSync,closeSync,unlinkSync,mkdirSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {
  assertShopUrl,assertProductLink,validateBrowserPermission,
  resourceAllowed,navigationAllowed,isBlockingPage,runBrowserPilot,boundedCooldownMs,
} from "../src/browser_pilot.mjs";

const AMAZON="https://www.amazon.com/stores/AcmeCaps/page/NEW0123";
const ETSY="https://www.etsy.com/shop/AcmeCaps";
const AMAZON_LINK="https://www.amazon.com/dp/B012345678";
const ETSY_LINK="https://www.etsy.com/listing/12345678/my-cap";

function authorization(platform,url) {
 return {platform,shop_url:url,browser_access_approved:true,source_rights_reviewed:true,
  approved_by:"CAM operator",approved_at:new Date().toISOString(),
  platform_terms_reviewed:true,robots_policy_reviewed:true,
  etsy_written_authorization:true,etsy_authorization_reference:"Etsy consent #12345",
  amazon_agent_policy_reviewed:true};
}

test("bounded navigation cooldown has hard lower and upper limits",()=>{
 assert.equal(boundedCooldownMs(10000,18000,()=>0),10000);
 assert.equal(boundedCooldownMs(10000,18000,span=>span-1),18000);
 assert.equal(boundedCooldownMs(12000,12000,()=>0),12000);
 for(const [min,max] of [[4999,5000],[10000,9999],[10000,60001],[10000,NaN],[-1,30000]])
  assert.throws(()=>boundedCooldownMs(min,max),/invalid_navigation_cooldown_range/);
});

test("strict marketplace shop URL allowlists reject login, redirects, userinfo and SSRF",()=>{
 assert.equal(assertShopUrl(ETSY,"etsy").platform,"etsy");
 assert.equal(assertShopUrl(AMAZON,"amazon").host,"www.amazon.com");
 for(const value of [
  "http://www.etsy.com/shop/Caps","https://www.etsy.com.evil.test/shop/Caps",
  "https://www.etsy.com@127.0.0.1/shop/Caps", "https://www.etsy.com/login",
  "https://www.etsy.com/shop/Caps/../../account","https://www.etsy.com/shop/Caps?login=true",
  "https://www.amazon.com/gp/aw/d/B012345678","https://www.amazon.com/stores/Caps/page/Q#one",
  "https://localhost/stores/Caps",
 ]){
  assert.throws(()=>assertShopUrl(value,value.includes("etsy")?"etsy":"amazon"),undefined,value);
 }
 assert.throws(()=>assertShopUrl(ETSY,"shopify"),/unsupported_platform/);
});

test("product links cannot escape origin or hide invalid path",()=>{
 const etsy=assertShopUrl(ETSY,"etsy");
 const amazon=assertShopUrl(AMAZON,"amazon");
 assert.equal(assertProductLink(ETSY_LINK,etsy).key,"etsy:12345678");
 assert.equal(assertProductLink(AMAZON_LINK,amazon).key,"amazon:B012345678");
 for(const [link,shop] of [
  ["https://evil.test/listing/12345678/hat",etsy],
  ["https://www.etsy.com@evil.test/listing/12345678",etsy],
  ["https://www.etsy.com/people/123456",etsy],
  ["https://www.etsy.com/listing/abc",etsy],
  ["https://www.amazon.com/signin",amazon],
  ["https://www.amazon.com/dp/SHORT",amazon],
 ]){
  assert.throws(()=>assertProductLink(link,shop));
 }
});

test("written Etsy authorization and Amazon agent policy are distinct gates",()=>{
 const etsy=assertShopUrl(ETSY,"etsy");
 const amazon=assertShopUrl(AMAZON,"amazon");
 const e=authorization("etsy",etsy.url);
 const a=authorization("amazon",amazon.url);
 assert.equal(validateBrowserPermission(etsy,e),true);
 assert.equal(validateBrowserPermission(amazon,a),true);
 assert.throws(()=>validateBrowserPermission(etsy,{...e,etsy_written_authorization:false}),/etsy_written_authorization_required/);
 assert.throws(()=>validateBrowserPermission(amazon,{...a,amazon_agent_policy_reviewed:false}),/amazon_agent_policy_review_required/);
 assert.throws(()=>validateBrowserPermission(etsy,{...e,shop_url:AMAZON}),/shop_browser_pilot_not_approved/);
 assert.throws(()=>validateBrowserPermission(etsy,{...e,approved_at:"2022-01-01"}),/browser_approval_expired/);
 assert.throws(()=>validateBrowserPermission(amazon,{...a,platform_terms_reviewed:false}),/shop_browser_pilot_not_approved/);
 assert.throws(()=>validateBrowserPermission(amazon,{...a,robots_policy_reviewed:false}),/shop_browser_pilot_not_approved/);
 assert.throws(()=>validateBrowserPermission(amazon,null),/shop_browser_pilot_not_approved/);
});

test("platform review needs no shop ownership but keeps marketplace limits",()=>{
 const policy={review_scope:"marketplace_policy",platform:"amazon",
  approved_by:"Fixture marketplace reviewer",approved_at:new Date().toISOString(),
  platform_terms_reviewed:true,robots_policy_reviewed:true,
  amazon_agent_policy_reviewed:true,amazon_reviewed_hosts:["www.amazon.com"]};
 const competitor=assertShopUrl("https://www.amazon.com/stores/CompetitorCaps","amazon");
 assert.equal(validateBrowserPermission(competitor,policy),true);
 assert.throws(()=>validateBrowserPermission(competitor,{...policy,browser_access_approved:true}),
  /marketplace_policy_scope_invalid/);
 assert.throws(()=>validateBrowserPermission(competitor,{...policy,amazon_agent_policy_reviewed:false}),
  /amazon_agent_policy_review_required/);
 assert.throws(()=>validateBrowserPermission(competitor,{...policy,amazon_reviewed_hosts:[]}),
  /amazon_marketplace_host_review_required/);
 assert.throws(()=>validateBrowserPermission(competitor,{...policy,amazon_reviewed_hosts:["www.amazon.co.uk"]}),
  /amazon_marketplace_host_review_required/);
 assert.throws(()=>validateBrowserPermission(competitor,{...policy,approved_at:"2022-01-01"}),
  /browser_approval_expired/);
 const etsy=assertShopUrl(ETSY,"etsy");
 const written={review_scope:"marketplace_policy",platform:"etsy",
  approved_by:"Fixture marketplace reviewer",approved_at:new Date().toISOString(),
  platform_terms_reviewed:true,robots_policy_reviewed:true,
  etsy_written_authorization:true,etsy_authorization_reference:"Fixture Etsy permission letter",
  etsy_authorized_shop_urls:[etsy.url]};
 assert.equal(validateBrowserPermission(etsy,written),true);
 const other=assertShopUrl("https://www.etsy.com/shop/AnotherCaps","etsy");
 assert.throws(()=>validateBrowserPermission(other,written),/etsy_permission_scope_not_verified/);
 assert.throws(()=>validateBrowserPermission(etsy,{...written,etsy_authorized_shop_urls:["https://www.etsy.com.evil.test/shop/AcmeCaps"]}),
  /etsy_permission_scope_not_verified/);
 assert.throws(()=>validateBrowserPermission(etsy,{...written,etsy_written_authorization:false}),
  /etsy_written_authorization_required/);
 assert.throws(()=>validateBrowserPermission(etsy,{...written,platform_terms_reviewed:false}),
  /shop_browser_pilot_not_approved/);
});

test("resource allowlist forbids external services and suspicious protocols",()=>{
 const etsy=assertShopUrl(ETSY,"etsy"), amazon=assertShopUrl(AMAZON,"amazon");
 assert.ok(resourceAllowed(ETSY_LINK,etsy));
 assert.equal(navigationAllowed(ETSY_LINK,etsy),true);
 assert.equal(navigationAllowed("https://www.etsy.com/login",etsy),false);
 assert.equal(navigationAllowed("https://www.amazon.com/gp/buy/spc",amazon),false);
 assert.ok(resourceAllowed("https://i.etsystatic.com/abc.jpg",etsy));
 assert.ok(resourceAllowed("https://m.media-amazon.com/images/I/hello.jpg",amazon));
 for(const source of ["http://127.0.0.1/","https://localhost/path",
  "https://www.etsy.com.evil.test/x","https://m.media-amazon.com.evil.test/x",
  "file:///etc/passwd","https://www.amazon.com@evil.test/"]){
  assert.equal(resourceAllowed(source,etsy),false);
  assert.equal(resourceAllowed(source,amazon),false);
 }
});

test("page blockers halt scan without CAPTCHA solving",()=>{
 assert.equal(isBlockingPage("Robot Check","Enter the characters you see"),"needs_operator");
 assert.equal(isBlockingPage("Welcome","Please log in to continue"),"needs_operator");
 assert.equal(isBlockingPage("Handmade Caps","Shop all our caps"),null);
});

function fakeBrowser(shopUrl, productUrl, {challenge=false,redirect=false,uiLinks=[productUrl],popupLinks=false,popupRedirect=false}={}){
 let closed=false,routed=null,visitCount=0,clickCount=0,backCount=0,popupsClosed=0;
 const current={url:shopUrl}, popupListeners=new Set();
 const context=new EventEmitter();
 function documentResponse(page){
  if(!page.mainFrame){const frame={page:()=>page,parentFrame:()=>null};page.mainFrame=()=>frame;}
  const frame=page.mainFrame(),url=page.url();
  const request={url:()=>url,isNavigationRequest:()=>true,resourceType:()=>"document",frame:()=>frame};
  const response={url:()=>url,status:()=>200,request:()=>request,frame:()=>frame};
  context.emit("response",response);
  return response;
 }
 const makePopup=(clicked)=>({
  url:()=>popupRedirect ? "https://www.amazon.com/signin" : clicked,
  waitForLoadState:async()=>{},
  title:async()=>challenge ? "Robot Check" : "Product details",
  close:async()=>{popupsClosed++},
  locator(selector){
   if(selector==="body")return {innerText:async()=>challenge?"Verify you are human":"Product details"};
   if(selector==="h1")return {first(){return {textContent:async()=> "Five Panel Hat"}}};
   if(selector.startsWith("img["))return {
    count:async()=>1,
    nth(){return {isVisible:async()=>true,getAttribute:async()=>
      clicked.includes("etsy.com")?"https://i.etsystatic.com/img.jpg":"https://m.media-amazon.com/images/I/pic.jpg"}},
   };
   return {count:async()=>0};
  },
 });
 const page={
  setDefaultTimeout(){},
  on(event,handler){assert.equal(event,"popup");popupListeners.add(handler)},
  off(event,handler){assert.equal(event,"popup");popupListeners.delete(handler)},
  async goto(url){
    visitCount++;
    current.url=redirect && visitCount===2 ? shopUrl:url;
    return documentResponse(page);
  },
  url(){return current.url},
  async goBack(){backCount++;current.url=shopUrl;return documentResponse(page);},
  async waitForLoadState(){},
  getByRole(role){
    assert.equal(role,"link");
    return {
      first(){return {waitFor:async()=>undefined}},
      count:async()=>current.url===shopUrl?uiLinks.length:0,
      nth(i){return {
        isVisible:async()=>true,getAttribute:async()=>uiLinks[i],
        click:async()=>{
          clickCount++;
          if(popupLinks){
            const newPage=makePopup(uiLinks[i]);
            documentResponse(newPage);
            for(const callback of popupListeners)callback(newPage);
          }else {current.url=uiLinks[i];documentResponse(page);}
        },
      }},
    };
  },
  async title(){return challenge?"Robot Check":current.url===shopUrl?"AcmeCaps":"Product details"},
  locator(selector){
    if(selector==="body")return {innerText:async()=>challenge?"Verify you are human":"Welcome to Caps"};
    if(selector==="h1")return {first(){return {textContent:async()=>current.url===shopUrl?"Shop":"Five Panel Hat"}}};
    if(selector.startsWith("a["))return {
      first(){return {waitFor:async()=>undefined}},
      count:async()=>current.url===shopUrl?2:0,
      nth(i){return {isVisible:async()=>true,getAttribute:async()=>i===0?productUrl:"https://evil.test/listing/123456"}},
    };
    if(selector.startsWith("img["))return {
      count:async()=>1,
      nth(){return {isVisible:async()=>true,getAttribute:async()=>
        (current.url.includes("etsy.com") ? "https://i.etsystatic.com/img.jpg":"https://m.media-amazon.com/images/I/pic.jpg")
      }},
    };
    return {count:async()=>0};
  },
 };
 context.newPage=async()=>page;
 context.route=async(_glob,handler)=>{routed=handler};
 context.close=async()=>{closed=true};
 return {
  launchPersistentContext:async(_dir,options)=>{
    assert.match(options.userAgent,/Agent\/CreativeAssetManagerBrandDNA/);
    assert.equal(options.serviceWorkers,"block");
    return context;
  },
  getClosed:()=>closed,
  getRoute:()=>routed,
  getVisits:()=>visitCount,
  getClicks:()=>clickCount,
  getBacks:()=>backCount,
  getPopupsClosed:()=>popupsClosed,
 };
}

test("approved Amazon browser pilot navigates DOM, emits only unverified evidence, and closes context",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-browser-"));
 try{
  const browser=fakeBrowser(AMAZON,AMAZON_LINK);
  let waited=0;
  const report=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"profile"),maxProducts:1,minimumDelayMs:5000,headless:true,
  },{launchPersistentContext:browser.launchPersistentContext,wait:async(ms)=>{waited+=ms},random:()=>0});
  assert.equal(report.status,"preview_completed");
  assert.equal(report.items.length,1);
  assert.equal(report.items[0].listing_key,"amazon:B012345678");
  assert.equal(report.items[0].title,"Five Panel Hat");
  assert.equal(report.items[0].image_count,1);
  assert.equal(report.items[0].source_kind,"browser_dom_observation");
  assert.equal(report.items[0].sales_state,"unknown");
  assert.equal(report.scan_enabled,false);
  assert.equal(report.evidence_committed,false);
  assert.equal(waited,10000);
  assert.equal(browser.getVisits(),2);
  assert.equal(browser.getClosed(),true);
  const r=browser.getRoute();
  let aborted=false;
  await r({
   request:()=>({url:()=>"https://evil.test/track",isNavigationRequest:()=>true}),
   abort:async()=>{aborted=true},
   continue:async()=>{throw Error("unexpected external request")},
  });
  assert.equal(aborted,true);
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("Etsy and Amazon manual challenge and redirect fail closed",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-browser-"));
 try{
  const browser=fakeBrowser(ETSY,ETSY_LINK,{challenge:true});
  const report=await runBrowserPilot({
    shopUrl:ETSY,platform:"etsy",authorization:authorization("etsy",ETSY),
    profileDir:join(dir,"etsy"),maxProducts:1,
  },{launchPersistentContext:browser.launchPersistentContext});
  assert.equal(report.status,"needs_operator");
  assert.equal(report.items.length,0);
  assert.ok(browser.getClosed());
  const redirected=fakeBrowser(AMAZON,AMAZON_LINK,{redirect:true});
  const redirectedReport=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"amazon"),maxProducts:1,
  },{launchPersistentContext:redirected.launchPersistentContext,wait:async()=>{}});
  assert.equal(redirectedReport.status,"needs_operator");
  assert.equal(redirectedReport.reason,"non_product_link");
  assert.deepEqual(redirectedReport.items,[]);
  assert.equal(redirectedReport.observed_count,0);
  assert.ok(redirected.getClosed());
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("visible UI mode uses accessible link clicks and returns to shop for next product",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-visible-click-"));
 try{
  const second="https://www.amazon.com/dp/B012345679";
  const browser=fakeBrowser(AMAZON,AMAZON_LINK,{uiLinks:[AMAZON_LINK,second]});
  const report=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"visual"),interactionMode:"visible_click",maxProducts:2,
  },{launchPersistentContext:browser.launchPersistentContext,wait:async()=>{}});
  assert.equal(report.status,"preview_completed");
  assert.equal(report.items.length,2);
  assert.equal(browser.getVisits(),1,"only shop landing uses page.goto()");
  assert.equal(browser.getClicks(),2);
  assert.equal(browser.getBacks(),1);
  assert.ok(browser.getClosed());
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("visible UI inspects products opened in new tabs and closes each popup",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-popup-"));
 try{
  const second="https://www.amazon.com/dp/B012345679";
  const browser=fakeBrowser(AMAZON,AMAZON_LINK,{uiLinks:[AMAZON_LINK,second],popupLinks:true});
  const result=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"popup-profile"),interactionMode:"visible_click",maxProducts:2,
  },{launchPersistentContext:browser.launchPersistentContext,wait:async()=>{}});
  assert.equal(result.status,"preview_completed");
  assert.deepEqual(result.items.map(item=>item.listing_key),["amazon:B012345678","amazon:B012345679"]);
  assert.equal(browser.getVisits(),1,"never navigate directly to product");
  assert.equal(browser.getClicks(),2);
  assert.equal(browser.getBacks(),0,"parent shop never navigated away");
  assert.equal(browser.getPopupsClosed(),2,"each product popup must close");
  assert.equal(browser.getClosed(),true);
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("visible UI rejects popup redirect to login and cleans up popup and browser",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-popup-deny-"));
 try{
  const browser=fakeBrowser(AMAZON,AMAZON_LINK,{popupLinks:true,popupRedirect:true});
  const report=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"popup-profile"),interactionMode:"visible_click",maxProducts:1,
  },{launchPersistentContext:browser.launchPersistentContext,wait:async()=>{}});
  assert.equal(report.status,"needs_operator");
  assert.equal(report.reason,"non_product_link");
  assert.deepEqual(report.items,[]);
  assert.equal(report.observed_count,0);
  assert.equal(browser.getClicks(),1);
  assert.equal(browser.getPopupsClosed(),1);
  assert.equal(browser.getClosed(),true);
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("visible UI rate limits shop entry, product clicks and return navigation",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-paced-click-"));
 try{
  const second="https://www.amazon.com/dp/B012345679";
  const browser=fakeBrowser(AMAZON,AMAZON_LINK,{uiLinks:[AMAZON_LINK,second]});
  const waits=[];
  const report=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"paced"),interactionMode:"visible_click",
    maxProducts:2,minimumDelayMs:10000,maximumDelayMs:18000,
  },{launchPersistentContext:browser.launchPersistentContext,
      wait:async ms=>{waits.push(ms)},random:span=>span-1});
  assert.equal(report.status,"preview_completed");
  assert.deepEqual(waits,[18000,18000,4000,18000,18000,4000]);
  assert.equal(browser.getClicks(),2);
  assert.equal(browser.getBacks(),1);
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("visible UI mode skips previously recorded products and does not falsely claim full-shop coverage",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-resume-click-"));
 try{
  const second="https://www.amazon.com/dp/B012345679";
  const browser=fakeBrowser(AMAZON,AMAZON_LINK,{uiLinks:[AMAZON_LINK,second]});
  const report=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"shop-a"),interactionMode:"visible_click",maxProducts:1,
    skipProductKeys:["amazon:B012345678"],
  },{launchPersistentContext:browser.launchPersistentContext,wait:async()=>{}});
  assert.equal(report.status,"preview_completed");
  assert.deepEqual(report.items.map(item=>item.listing_key),["amazon:B012345679"]);
  assert.equal(browser.getVisits(),1);
  assert.equal(browser.getClicks(),1);

  const noNew=fakeBrowser(AMAZON,AMAZON_LINK,{uiLinks:[AMAZON_LINK,second]});
  const exhaustedVisible=await runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:join(dir,"shop-b"),interactionMode:"visible_click",maxProducts:2,
    skipProductKeys:["amazon:B012345678","amazon:B012345679"],
  },{launchPersistentContext:noNew.launchPersistentContext,wait:async()=>{}});
  assert.equal(exhaustedVisible.status,"needs_operator");
  assert.equal(exhaustedVisible.reason,"no_new_visible_listings_review_shop_page");
  assert.equal(noNew.getClicks(),0);
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("visible UI mode refuses invisible browser before launching",async()=>{
 let launched=false;
 await assert.rejects(()=>runBrowserPilot({
   shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
   profileDir:"unused",interactionMode:"visible_click",headless:true,
 },{launchPersistentContext:async()=>{launched=true}}),/visible_click_requires_visible_browser/);
 assert.equal(launched,false);
});

test("exclusive profile lock prevents a concurrent shop browser",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-pilot-lock-"));
 const profile=join(dir,"shop");
 mkdirSync(profile);
 const lock=profile+".pilot.lock";
 const fd=openSync(lock,"wx");
 let didLaunch=false;
 try{
  await assert.rejects(()=>runBrowserPilot({
    shopUrl:AMAZON,platform:"amazon",authorization:authorization("amazon",AMAZON),
    profileDir:profile,maxProducts:1,
  },{launchPersistentContext:async()=>{didLaunch=true;}}),/EEXIST/);
  assert.equal(didLaunch,false);
 }finally{
  closeSync(fd);unlinkSync(lock);rmSync(dir,{recursive:true,force:true});
 }
});

test("browser pilot refuses unapproved shops without launching Chromium",async()=>{
 let opened=false;
 await assert.rejects(()=>runBrowserPilot({
  shopUrl:ETSY,platform:"etsy",authorization:authorization("amazon",AMAZON),
  profileDir:"isolated",maxProducts:1,
 },{launchPersistentContext:async()=>{opened=true}}),/shop_browser_pilot_not_approved/);
 assert.equal(opened,false);
});