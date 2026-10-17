/** Amazon/Etsy DOM-only browser pilot; never invokes marketplace APIs.
 * No proxy rotation, challenge bypass, stealth plugins, login automation,
 * bulk enumeration, private dashboard access or remote job polling.
 */
import {createHash} from "node:crypto";
import {openSync, closeSync, unlinkSync, mkdirSync, readFileSync, writeFileSync, existsSync, lstatSync} from "node:fs";
import {resolve, dirname} from "node:path";
import {pathToFileURL} from "node:url";
import {setTimeout as delay} from "node:timers/promises";

const AMAZON_DOMAINS = new Set(["www.amazon.com","www.amazon.co.uk","www.amazon.ca","www.amazon.de","www.amazon.fr","www.amazon.es","www.amazon.it","www.amazon.co.jp"]);
const AMAZON_ASSETS = ["images-na.ssl-images-amazon.com", "m.media-amazon.com", "images-eu.ssl-images-amazon.com"];
const ETSY_ASSETS = ["i.etsystatic.com", "img0.etsystatic.com", "img1.etsystatic.com", "img2.etsystatic.com", "img3.etsystatic.com", "img4.etsystatic.com"];
const MAX_PRODUCT_PAGES=5;
const USER_AGENT = "Agent/CreativeAssetManagerBrandDNA (BrowserPilot; +https://creative-assets.ddns.net)";
const normalizePath=(p)=>{try{return decodeURIComponent(p)}catch{throw new Error("unsafe_path")}};
const digest=(text)=>createHash("sha256").update(text).digest("hex");

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
  if(!config || config.platform!==shop.platform || config.shop_url!==shop.url ||
     config.browser_access_approved!==true || config.source_rights_reviewed!==true ||
     config.platform_terms_reviewed!==true || config.robots_policy_reviewed!==true ||
     typeof config.approved_by!=="string" || config.approved_by.trim().length<3 ||
     typeof config.approved_at!=="string" || !Number.isFinite(Date.parse(config.approved_at)))
    throw new Error("shop_browser_pilot_not_approved");
  const age=Date.now()-Date.parse(config.approved_at);
  if(age<0 || age>30*24*60*60*1000)throw new Error("browser_approval_expired");
  if(shop.platform==="etsy" && (config.etsy_written_authorization!==true ||
      typeof config.etsy_authorization_reference!=="string" || config.etsy_authorization_reference.trim().length<5))
    throw new Error("etsy_written_authorization_required");
  if(shop.platform==="amazon" && config.amazon_agent_policy_reviewed!==true)
    throw new Error("amazon_agent_policy_review_required");
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

async function collectVisibleLinks(page,shop,interactionMode="direct"){
  const selectors=shop.platform==="etsy"?'a[href*="/listing/"]':'a[href*="/dp/"],a[href*="/gp/product/"]';
  const anchors=interactionMode==="visible_click" ? page.getByRole("link") : page.locator(selectors);
  await anchors.first().waitFor({state:"attached",timeout:10000}).catch(()=>{});
  const seen=new Set(), found=[];
  const count=Math.min(await anchors.count(),150);
  for(let i=0;i<count && found.length<MAX_PRODUCT_PAGES;i++){
    const node=anchors.nth(i);
    if(!(await node.isVisible().catch(()=>false)))continue;
    const href=await node.getAttribute("href");
    if(!href)continue;
    try{
      const link=assertProductLink(href,shop);
      if(seen.has(link.key))continue;
      seen.add(link.key);
      found.push(link);
    }catch{/* skip unrelated and unsafe links */}
  }
  return found;
}

// Click the actual visible link in the browser's accessibility tree instead
// of navigating to its URL programmatically. This is a UI interaction mode,
// NOT human impersonation: the browser still identifies itself as an Agent.
async function clickVisibleProductLink(page,shop,link){
  const anchors=page.getByRole("link");
  const count=Math.min(await anchors.count(),150);
  for(let i=0;i<count;i++){
    const node=anchors.nth(i);
    if(!(await node.isVisible().catch(()=>false)))continue;
    const href=await node.getAttribute("href");
    if(!href)continue;
    let observed;
    try{observed=assertProductLink(href,shop)}catch{continue}
    if(observed.key!==link.key || observed.url!==link.url)continue;
    await node.click({timeout:10000});
    await page.waitForLoadState("domcontentloaded",{timeout:30000});
    return;
  }
  // Fail closed instead of bypassing the interface with page.goto().
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
          maxProducts=3,minimumDelayMs=5000,headless=false,interactionMode="direct"},
          {launchPersistentContext,wait=delay}={}) {
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
  if(!Number.isInteger(maxProducts)||maxProducts<1||maxProducts>MAX_PRODUCT_PAGES)
    throw new Error("invalid_product_limit");
  if(!Number.isInteger(minimumDelayMs)||minimumDelayMs<5000)
    throw new Error("scan_pace_too_fast");
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
  let context;
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
    await page.goto(shop.url,{waitUntil:"domcontentloaded",timeout:30000});
    if(page.url().replace(/\/$/,"")!==shop.url)throw new Error("unexpected_shop_redirect");
    const summary=(await page.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
    const blocker=isBlockingPage(await page.title(),summary);
    if(blocker)return {status:"needs_operator",platform,reason:blocker,items:[],scan_enabled:false};
    const links=await collectVisibleLinks(page,shop,interactionMode);
    if(links.length===0)return {
      status:"needs_operator",platform,reason:"no_visible_listings_or_parser_changed",
      items:[],scan_enabled:false,
    };
    const records=[];
    for(const [index,link] of links.slice(0,maxProducts).entries()){
      await wait(minimumDelayMs);
      if(interactionMode==="visible_click"){
        if(index>0){
          await page.goBack({waitUntil:"domcontentloaded",timeout:30000});
          if(page.url().replace(/\/$/,"")!==shop.url)
            throw new Error("shop_back_navigation_mismatch");
          const returnedBody=(await page.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
          if(isBlockingPage(await page.title(),returnedBody))
            return {status:"needs_operator",platform,reason:"page_blocked_on_return",items:records,scan_enabled:false};
        }
        await clickVisibleProductLink(page,shop,link);
      }else{
        await page.goto(link.url,{waitUntil:"domcontentloaded",timeout:30000});
      }
      const finalProduct=assertProductLink(page.url(),shop);
      if(finalProduct.key!==link.key)throw new Error("product_redirect_mismatch");
      const text=(await page.locator("body").innerText({timeout:5000}).catch(()=>"")).slice(0,2500);
      const stopped=isBlockingPage(await page.title(),text);
      if(stopped)return {status:"needs_operator",platform,reason:stopped,items:records,scan_enabled:false};
      try{records.push(await collectDetail(page,shop,link))}
      catch {records.push({listing_key:link.key,source_kind:"browser_dom_observation",source_verification:"unverified",error_code:"detail_parser_review_required"});}
    }
    return {
      status:"preview_completed",platform,shop_url_sha256:digest(shop.url),
      observed_at:new Date().toISOString(),
      items:records,observed_count:records.length,coverage_state:"partial",
      rights_verified:false,sales_verified:false,evidence_committed:false,scan_enabled:false,
      note:"Local pilot only; no CAM upload or task dispatch",
    };
  } finally{
    try{await context?.close()}finally{closeSync(fd);unlinkSync(lock)}
  }
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
