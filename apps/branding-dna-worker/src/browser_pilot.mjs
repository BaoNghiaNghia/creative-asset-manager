/** Amazon/Etsy DOM-only browser pilot; never invokes marketplace APIs.
 * No proxy rotation, challenge bypass, stealth plugins, login automation,
 * bulk enumeration, private dashboard access or remote job polling.
 */
import {createHash,randomInt as secureRandomInt} from "node:crypto";
import {openSync, closeSync, unlinkSync, mkdirSync, readFileSync, writeFileSync, existsSync, lstatSync} from "node:fs";
import {resolve, dirname} from "node:path";
import {pathToFileURL} from "node:url";
import {setTimeout as delay} from "node:timers/promises";

const AMAZON_DOMAINS = new Set(["www.amazon.com","www.amazon.co.uk","www.amazon.ca","www.amazon.de","www.amazon.fr","www.amazon.es","www.amazon.it","www.amazon.co.jp"]);
const AMAZON_ASSETS = ["images-na.ssl-images-amazon.com", "m.media-amazon.com", "images-eu.ssl-images-amazon.com"];
const ETSY_ASSETS = ["i.etsystatic.com", "img0.etsystatic.com", "img1.etsystatic.com", "img2.etsystatic.com", "img3.etsystatic.com", "img4.etsystatic.com"];
const MAX_PRODUCT_PAGES=5;
const USER_AGENT = "Agent/CreativeAssetManagerBrandDNA (BrowserPilot; +https://creative-assets.ddns.net)";
const BROWSER_REVIEW_REASONS=new Set([
  "unexpected_shop_redirect","marketplace_access_limited_on_scroll",
  "product_redirect_mismatch","popup_parent_shop_changed",
  "visible_product_link_not_found","shop_back_navigation_mismatch",
  "invalid_product_link","cross_origin_product_link","unsafe_product_path",
  "non_listing_link","non_product_link","product_navigation_response_unavailable",
  "marketplace_access_limited",
]);
const normalizePath=(p)=>{try{return decodeURIComponent(p)}catch{throw new Error("unsafe_path")}};
const digest=(text)=>createHash("sha256").update(text).digest("hex");

// Bounded randomized cooldown is for conservative traffic pacing, never to
// impersonate human navigation, mask the Agent UA, or bypass rate limits.
export function boundedCooldownMs(minimumMs,maximumMs,random=secureRandomInt){
  if(!Number.isInteger(minimumMs) || minimumMs<5000 || minimumMs>60000 ||
     !Number.isInteger(maximumMs) || maximumMs<minimumMs || maximumMs>60000)
    throw new Error("invalid_navigation_cooldown_range");
  if(minimumMs===maximumMs)return minimumMs;
  return minimumMs+random(maximumMs-minimumMs+1);
}

export function assertShopUrl(raw, platform) {
  let url;
  try {url=new URL(raw)}catch{throw new Error("invalid_shop_url")}
  if(url.protocol!=="https:" || url.username || url.password || url.port && url.port!=="443" || url.search || url.hash)throw new Error("unsafe_shop_url");
  const host=url.hostname.toLowerCase().replace(/\.$/,"");
  const path=normalizePath(url.pathname);
  if(path.includes("..") || path.includes("\\") || path.includes("%") || path.includes("@"))throw new Error("unsafe_path");
  if(platform==="etsy"){
    if(host!=="www.etsy.com" && host!=="etsy.com" || !/^\/shop\/[A-Za-z0-9_-]{2,120}\/?$/.test(path))throw new Error("unsupported_etsy_shop_url");
    return {platform,origin:"https://www.etsy.com",url:"https://www.etsy.com"+path.replace(/\/$/,""),host:"www.etsy.com"};
  }
  if(platform==="amazon"){
    if(!AMAZON_DOMAINS.has(host) || !/^\/stores\/[A-Za-z0-9._%-]{2,120}(?:\/(?:page\/)?[A-Za-z0-9._%-]{2,120}){0,6}\/?$/.test(path))
      throw new Error("unsupported_amazon_store_url");
    return {platform,origin:"https://"+host,url:"https://"+host+path.replace(/\/$/,""),host};
  }
  throw new Error("unsupported_platform");
}

export function assertProductLink(raw, shop) {
  let u;try{u=new URL(raw,shop.url)}catch{throw new Error("invalid_product_link")}
  if(u.protocol!=="https:" || u.username || u.password || u.port && u.port!=="443" || u.hostname.toLowerCase()!==shop.host)throw new Error("cross_origin_product_link");
  const path=normalizePath(u.pathname);
  if(path.includes("..") || path.includes("\\") || path.includes("%") || path.includes("@"))throw new Error("unsafe_product_path");
  let key;
  if(shop.platform==="etsy"){
    const match=path.match(/^\/listing\/([0-9]{1,20})(?:\/|$)/);
    if(!match)throw new Error("non_listing_link");
    key="etsy:"+match[1];
  } else {
    const match=path.match(/\/(?:dp|gp\/product)\/([A-Za-z0-9]{10})(?:\/|$)/);
    if(!match)throw new Error("non_product_link");
    key="amazon:"+match[1].toUpperCase();
  }
  return {url:u.origin+u.pathname,key};
}

export function validateBrowserPermission(shop, config){
  // CAM storefronts do not require a declaration from each shop owner.
  // A single reviewed marketplace policy may cover multiple public shops.
  // Legacy exact-shop operator approvals remain valid for older installations.
  if(!config || config.platform!==shop.platform ||
     config.platform_terms_reviewed!==true || config.robots_policy_reviewed!==true ||
     typeof config.approved_by!=="string" || config.approved_by.trim().length<3 ||
     typeof config.approved_at!=="string" || !Number.isFinite(Date.parse(config.approved_at)))
    throw new Error("shop_browser_pilot_not_approved");
  const platformPolicy=config.review_scope==="marketplace_policy";
  if(platformPolicy){
    // The platform-policy form must not silently be treated as an assertion
    // of shop ownership or permission to reuse somebody else's artwork.
    if(Object.hasOwn(config,"shop_url") ||
       Object.hasOwn(config,"browser_access_approved") ||
       Object.hasOwn(config,"source_rights_reviewed"))
      throw new Error("marketplace_policy_scope_invalid");
  }else if(config.review_scope!==undefined || config.shop_url!==shop.url ||
           config.browser_access_approved!==true || config.source_rights_reviewed!==true){
    throw new Error("shop_browser_pilot_not_approved");
  }
  const age=Date.now()-Date.parse(config.approved_at);
  if(age<0 || age>30*24*60*60*1000)throw new Error("browser_approval_expired");
  if(shop.platform==="etsy"){
    if(config.etsy_written_authorization!==true ||
       typeof config.etsy_authorization_reference!=="string" ||
       config.etsy_authorization_reference.trim().length<5)
      throw new Error("etsy_written_authorization_required");
    if(platformPolicy){
      // Written marketplace authorization is only valid for the storefronts
      // explicitly covered by the documented permission scope.
      const allowed=config.etsy_authorized_shop_urls;
      const valid=Array.isArray(allowed) && allowed.length>0 && allowed.length<=50 &&
        allowed.every(url=>{
          if(typeof url!=="string")return false;
          try{return assertShopUrl(url,"etsy").url===url}catch{return false}
        }) && allowed.includes(shop.url);
      if(!valid)throw new Error("etsy_permission_scope_not_verified");
    }
  }
  if(shop.platform==="amazon"){
    if(config.amazon_agent_policy_reviewed!==true)
      throw new Error("amazon_agent_policy_review_required");
    if(platformPolicy){
      const hosts=config.amazon_reviewed_hosts;
      if(!Array.isArray(hosts) || hosts.length===0 || hosts.length>AMAZON_DOMAINS.size ||
         new Set(hosts).size!==hosts.length ||
         hosts.some(host=>typeof host!=="string" || !AMAZON_DOMAINS.has(host)) ||
         !hosts.includes(shop.host))
        throw new Error("amazon_marketplace_host_review_required");
    }
  }
  return true;
}

export function navigationAllowed(raw, shop){
  let u;
  try{u=new URL(raw)}catch{return false}
  if(u.href===shop.url || u.href===shop.url+"/")return true;
  try{assertProductLink(raw,shop);return true}catch{return false}
}

export function resourceAllowed(raw, shop){
  let u;try{u=new URL(raw)}catch{return false}
  if(["about:","data:","blob:"].includes(u.protocol))return true;
  if(u.protocol!=="https:" || u.username || u.password || (u.port && u.port!=="443"))return false;
  const host=u.hostname.toLowerCase().replace(/\.$/,"");
  if(host===shop.host || shop.platform==="etsy" && host==="etsy.com")return true;
  return (shop.platform==="etsy"?ETSY_ASSETS:AMAZON_ASSETS).includes(host);
}

export function isBlockingPage(title,body){
  const sample=(title+" "+body).slice(0,2500).toLowerCase();
  if(/captcha|robot check|verify you are human|unusual traffic|automated access|access denied|enter the characters you see|security challenge|sign in to continue|please log in/i.test(sample))
    return "needs_operator";
  return null;
}

/** Find visible product links in DOM order while reading a bounded portion
 * of the storefront from top to bottom. This only uses the visible browser's
 * normal page scroll. It does not enumerate private endpoints, paginate,
 * bypass a site challenge or claim the entire shop was visited.
 */
export async function collectVisibleLinks(page,shop,interactionMode="direct",skipKeys=new Set(),
    {limit=MAX_PRODUCT_PAGES,wait=delay,maxScrollSteps=8}={}){
  if(!Number.isInteger(limit)||limit<1||limit>MAX_PRODUCT_PAGES||
      !Number.isInteger(maxScrollSteps)||maxScrollSteps<0||maxScrollSteps>12)
    throw Error("invalid_bounded_scroll_limits");
  const selectors=shop.platform==="etsy"?'a[href*="/listing/"]':'a[href*="/dp/"],a[href*="/gp/product/"]';
  const anchors=interactionMode==="visible_click" ? page.getByRole("link") : page.locator(selectors);
  await anchors.first().waitFor({state:"attached",timeout:10000}).catch(()=>{});
  const seen=new Set(), found=[];
  // Real Playwright supports evaluate(). Test doubles without it use the
  // original first-page DOM pass; they cannot manufacture scroll coverage.
  const scrolling=interactionMode==="visible_click" && typeof page.evaluate==="function";
  for(let step=0;step<=(scrolling?maxScrollSteps:0);step++){
    const count=Math.min(await anchors.count(),350);
    for(let i=0;i<count && found.length<limit;i++){
      const node=anchors.nth(i);
      if(!(await node.isVisible().catch(()=>false)))continue;
      const href=await node.getAttribute("href");
      if(!href)continue;
      try{
        const link=assertProductLink(href,shop);
        if(seen.has(link.key) || skipKeys.has(link.key))continue;
        seen.add(link.key);
        found.push(link);
      }catch{/* ignore unrelated, cross-domain and unsafe links */}
    }
    if(found.length===limit||!scrolling||step===maxScrollSteps)break;
    // Normal deterministic scrolling to load public product cards; no
    // randomized "human-like" mouse movement, stealth or API calls.
    const movement=await page.evaluate(()=>{
      const before=window.scrollY;
      const amount=Math.max(360,Math.floor(window.innerHeight*0.8));
      window.scrollBy(0,amount);
      return {before,after:window.scrollY};
    });
    if(!movement || !Number.isFinite(movement.after) ||
        movement.after<=movement.before)break;
    await wait(600);
    const body=(await page.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
    if(isBlockingPage(await page.title(),body))
      throw Error("marketplace_access_limited_on_scroll");
  }
  // Let real Chromium scroll back to the storefront top. Playwright click()
  // will scroll the chosen product card into view when it remains in the DOM.
  if(scrolling)await page.evaluate(()=>window.scrollTo(0,0));
  return found;
}

// Click visible product links through the accessibility tree. Some Amazon
// storefront and Etsy cards legitimately open in a NEW tab. Follow only the
// exact expected product URL, and close the popup after inspection; never
// turn a failed click into an unreviewed direct HTTP navigation.
async function clickVisibleProductLink(page,shop,link){
  const anchors=page.getByRole("link");
  const count=Math.min(await anchors.count(),350);
  for(let i=0;i<count;i++){
    const node=anchors.nth(i);
    if(!(await node.isVisible().catch(()=>false)))continue;
    const href=await node.getAttribute("href");
    if(!href)continue;
    let observed;
    try{observed=assertProductLink(href,shop)}catch{continue}
    if(observed.key!==link.key || observed.url!==link.url)continue;
    let popup=null;
    const onPopup=opened=>{popup=opened};
    page.on("popup",onPopup);
    try {
      await node.click({timeout:10000});
      const detailPage=popup??page;
      await detailPage.waitForLoadState("domcontentloaded",{timeout:30000});
      const actual=assertProductLink(detailPage.url(),shop);
      if(actual.key!==link.key || actual.url!==link.url)
        throw new Error("product_redirect_mismatch");
      if(popup && page.url().replace(/\/$/,"")!==shop.url)
        throw new Error("popup_parent_shop_changed");
      return {detailPage,openedPopup:Boolean(popup)};
    }catch(error){
      if(popup)await popup.close().catch(()=>{});
      throw error;
    }finally{page.off("popup",onPopup)}
  }
  throw new Error("visible_product_link_not_found");
}

async function collectDetail(page,shop,link) {
  const title=String((await page.locator("h1").first().textContent({timeout:5000}).catch(()=>""))??"").trim().slice(0,300);
  if(!title)throw new Error("product_title_missing");
  const selectors=shop.platform==="etsy"?'img[src*="etsystatic.com"]':'img[src*="images-amazon.com"],img[src*="media-amazon.com"]';
  const media=page.locator(selectors);
  const imageDigests=[];
  const count=Math.min(await media.count(),40);
  for(let i=0;i<count && imageDigests.length<12;i++){
    const image=media.nth(i);
    if(!(await image.isVisible().catch(()=>false)))continue;
    const raw=await image.getAttribute("src");
    if(!raw || !resourceAllowed(raw,shop))continue;
    const hash=digest(raw);
    if(!imageDigests.includes(hash))imageDigests.push(hash);
  }
  return {
    listing_key:link.key,
    title,
    observed_page_sha256:digest(link.url),
    image_url_sha256:imageDigests,
    image_count:imageDigests.length,
    source_kind:"browser_dom_observation",
    rights_state:"unknown",
    sales_state:"unknown",
    coverage_state:"partial",
    source_verification:"unverified",
  };
}

export async function runBrowserPilot({shopUrl,platform,authorization,profileDir,
          maxProducts=3,minimumDelayMs=5000,
          maximumDelayMs=Math.min(60000,minimumDelayMs+8000),
          headless=false,interactionMode="direct",skipProductKeys=[]},
          {launchPersistentContext,wait=delay,random=secureRandomInt}={}) {
  const shop=assertShopUrl(shopUrl,platform);
  validateBrowserPermission(shop,authorization);
  // Actual Chromium runs only on authorized Windows hosts; tests may inject
  // a mock launch function on Linux without opening a browser.
  if(!launchPersistentContext && process.platform!=="win32")
    throw new Error("windows_browser_runtime_only");
  if(!["direct","visible_click"].includes(interactionMode))
    throw new Error("unsupported_interaction_mode");
  if(interactionMode==="visible_click" && headless)
    throw new Error("visible_click_requires_visible_browser");
  if(!Array.isArray(skipProductKeys) || skipProductKeys.length>10000 ||
    skipProductKeys.some(key=>typeof key!=="string" ||
      !(platform==="amazon" ? /^amazon:[A-Z0-9]{10}$/ : /^etsy:[0-9]{1,20}$/).test(key)))
    throw new Error("invalid_completed_product_keys");
  const skipKeys=new Set(skipProductKeys);
  if(!Number.isInteger(maxProducts)||maxProducts<1||maxProducts>MAX_PRODUCT_PAGES)
    throw new Error("invalid_product_limit");
  // Validate the cooldown window before launching any browser or saving state.
  boundedCooldownMs(minimumDelayMs,maximumDelayMs,()=>0);
  let accessLimited=false;
  const requireAccess=()=>{
    if(accessLimited)throw new Error("marketplace_access_limited");
  };
  const pauseNavigation=async()=>{
    requireAccess();
    await wait(boundedCooldownMs(minimumDelayMs,maximumDelayMs,random));
    requireAccess();
  };
  if(!profileDir || typeof profileDir!=="string")throw new Error("dedicated_profile_dir_required");
  const dir=resolve(profileDir);
  const normalized=dir.replaceAll("\\","/").toLowerCase();
  if(normalized.includes("/pinterest") || normalized.includes("/chrome/user data") ||
     normalized.includes("/edge/user data") ||
     (existsSync(dir) && lstatSync(dir).isSymbolicLink()))throw new Error("shared_browser_profile_forbidden");
  mkdirSync(dir,{recursive:true,mode:0o700});
  // Lock is never auto-recovered: operator must verify process is stopped.
  const lock=dir+".pilot.lock";
  const fd=openSync(lock,"wx",0o600);
  const records=[];
  const outcome=(status,reason)=>({
    status,platform,shop_url_sha256:digest(shop.url),
    observed_at:new Date().toISOString(),
    items:records,observed_count:records.length,coverage_state:"partial",
    rights_verified:false,sales_verified:false,evidence_committed:false,scan_enabled:false,
    ...(reason ? {reason} : {}),
    note:"Local pilot only; no CAM upload or task dispatch",
  });
  const documentResponses=new WeakMap();
  // Register on the context before navigating: a popup's document response
  // can arrive before the opener emits its popup event. Images and child
  // frames cannot replace the status of the product's main document.
  const onResponse=response=>{
    try{
      const request=response.request();
      if(!request.isNavigationRequest() || request.resourceType()!=="document")return;
      const frame=request.frame(), responsePage=frame.page();
      if(frame===responsePage.mainFrame()){
        const status=response.status();
        documentResponses.set(responsePage,{url:response.url(),status});
        // A refusal stays latched even if another document later succeeds.
        if([403,429].includes(status))accessLimited=true;
      }
    }catch{/* An unavailable/detached frame cannot verify a product document. */}
  };
  let context;
  const execute=async()=>{
    try{
      const launch=launchPersistentContext ?? (await import("playwright")).chromium.launchPersistentContext;
      context=await launch(dir,{
        headless,
        acceptDownloads:false,
        serviceWorkers:"block",
        permissions:[],
        userAgent:USER_AGENT,
        viewport:{width:1280,height:900},
      });
      context.on("response",onResponse);
      await context.route("**/*",async route=>{
        const request=route.request();
        const url=request.url();
        const allowed=request.isNavigationRequest()
          ? navigationAllowed(url,shop)
          : resourceAllowed(url,shop);
        if(allowed)await route.continue();
        else await route.abort();
      });
      const page=await context.newPage();
      page.setDefaultTimeout(10000);
      await pauseNavigation();
      const shopResponse=await page.goto(shop.url,{waitUntil:"domcontentloaded",timeout:30000});
      if([403,429].includes(shopResponse?.status?.()))
        return outcome("needs_operator","marketplace_access_limited");
      if(page.url().replace(/\/$/,"")!==shop.url)throw new Error("unexpected_shop_redirect");
      const summary=(await page.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
      const blocker=isBlockingPage(await page.title(),summary);
      if(blocker)return outcome("needs_operator",blocker);
      const links=await collectVisibleLinks(page,shop,interactionMode,skipKeys,{limit:maxProducts,wait});
      if(links.length===0)return outcome("needs_operator",
        skipKeys.size ? "no_new_visible_listings_review_shop_page" : "no_visible_listings_or_parser_changed");

      let mainPageOnProduct=false;
      for(const [index,link] of links.slice(0,maxProducts).entries()){
        let detailPage=page,openedPopup=false;
        if(interactionMode==="visible_click"){
          // Same-tab products require Back; popup products leave the original
          // shop page unchanged. Never walk unrelated browser history.
          if(index>0 && mainPageOnProduct){
            await pauseNavigation();
            const backResponse=await page.goBack({waitUntil:"domcontentloaded",timeout:30000});
            if([403,429].includes(backResponse?.status?.()))
              return outcome("needs_operator","marketplace_access_limited_on_return");
            if(page.url().replace(/\/$/,"")!==shop.url)
              throw new Error("shop_back_navigation_mismatch");
            const returnedBody=(await page.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
            if(isBlockingPage(await page.title(),returnedBody))
              return outcome("needs_operator","page_blocked_on_return");
            mainPageOnProduct=false;
          }
          await pauseNavigation();
          documentResponses.delete(page);
          ({detailPage,openedPopup}=await clickVisibleProductLink(page,shop,link));
          mainPageOnProduct=!openedPopup;
        }else{
          await pauseNavigation();
          const productResponse=await page.goto(link.url,{waitUntil:"domcontentloaded",timeout:30000});
          if([403,429].includes(productResponse?.status?.()))
            return outcome("needs_operator","marketplace_access_limited");
        }
        try {
          if(interactionMode==="visible_click"){
            const response=documentResponses.get(detailPage);
            if(!response || response.url.split("#",1)[0]!==detailPage.url().split("#",1)[0])
              throw new Error("product_navigation_response_unavailable");
            if([403,429].includes(response.status))
              return outcome("needs_operator","marketplace_access_limited");
          }
          const finalProduct=assertProductLink(detailPage.url(),shop);
          if(finalProduct.key!==link.key)throw new Error("product_redirect_mismatch");
          const text=(await detailPage.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
          const stopped=isBlockingPage(await detailPage.title(),text);
          if(stopped)return outcome("needs_operator",stopped);
          let record;
          try{record=await collectDetail(detailPage,shop,link)}
          catch {return outcome("needs_operator",accessLimited
            ? "marketplace_access_limited" : "detail_parser_review_required");}
          requireAccess();
          records.push(record);
          // Hold the visible product page for an operator to inspect. This is
          // display time, not stealth or an attempt to imitate a person.
          if(interactionMode==="visible_click")await wait(4000);
          requireAccess();
        }finally {
          if(openedPopup)await detailPage.close().catch(()=>{});
        }
      }
      return outcome("preview_completed");
    } catch(error){
      // Prelaunch rejection is still a rejection. Once a browser exists, save
      // the successful prefix for explicit resume, and halt the serial batch.
      // Playwright messages can include page URLs/content; persist fixed codes.
      if(!context)throw error;
      return outcome("needs_operator",BROWSER_REVIEW_REASONS.has(error?.message)
        ? error.message : "browser_navigation_review_required");
    }
  };
  let report,cleanupFailed=false;
  try{
    report=await execute();
  }finally{
    try{await context?.close()}
    catch{cleanupFailed=true}
    finally{
      context?.off("response",onResponse);
      closeSync(fd);
      // A failed close leaves browser ownership uncertain. Preserve the
      // lock for operator review while still saving the successful prefix.
      if(!cleanupFailed)unlinkSync(lock);
    }
  }
  if(cleanupFailed)return {
    ...outcome("needs_operator","browser_cleanup_review_required"),
    profile_lock_retained:true,
  };
  if(accessLimited)return outcome("needs_operator","marketplace_access_limited");
  return report;
}

function getCliArg(name){
  const i=process.argv.indexOf(name);return i>=0?process.argv[i+1]:undefined;
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
  const shopUrl=getCliArg("--shop-url");
  const platform=getCliArg("--platform");
  const authPath=getCliArg("--approval-file");
  const profileDir=getCliArg("--profile-dir");
  // No environment variable or remote API can silently enable browser scans.
  if(!shopUrl||!platform||!authPath||!profileDir){
    process.stderr.write("Usage: node src/browser_pilot.mjs --platform amazon|etsy --shop-url HTTPS_SHOP --approval-file PATH --profile-dir DEDICATED_DIR\n");
    process.exitCode=2;
  }else{
    try{
      const authorization=JSON.parse(readFileSync(authPath,"utf8"));
      const result=await runBrowserPilot({shopUrl,platform,authorization,profileDir,maxProducts:3});
      const output=getCliArg("--output");
      if(output){
        mkdirSync(dirname(resolve(output)),{recursive:true});
        writeFileSync(output,JSON.stringify(result,null,2),{mode:0o600,flag:"wx"});
        process.stdout.write("Local browser pilot report saved (no remote upload)\n");
      }else{process.stdout.write(JSON.stringify(result,null,2)+"\n")}
    }catch(error){
      process.stderr.write("Browser pilot stopped: "+(error instanceof Error?error.message:"unknown")+"\n");
      process.exitCode=1;
    }
  }
}