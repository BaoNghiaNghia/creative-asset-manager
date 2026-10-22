/** Select a CAM-configured shop for a bounded, *locally approved* browser pilot.
 * Shop URL comes only from the authenticated CAM Shop Library, never local JSON.
 * This is NOT a dispatch agent, never polls/claims jobs or uploads evidence.
 */
import {readFileSync,existsSync,lstatSync,writeFileSync,mkdirSync} from "node:fs";
import {createHash} from "node:crypto";
import {dirname,resolve} from "node:path";
import {pathToFileURL} from "node:url";
import {settings} from "./agent_cam.mjs";
import {assertShopUrl,validateBrowserPermission,runBrowserPilot} from "./browser_pilot.mjs";
import {runConfiguredPilot} from "./run_configured_pilot.mjs";

const UUID=/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i;

export async function fetchCamShopLibrary(config,request=fetch){
 const response=await request(config.origin+"/api/v1/branding-dna/scout-agents/"+
  encodeURIComponent(config.id)+"/shops",{
  method:"GET",headers:{Authorization:"Bearer "+config.token,Accept:"application/json"},
  redirect:"error",signal:AbortSignal.timeout(12000),
 });
 if(response.status===401||response.status===403)throw Error("scout_pairing_revoked");
 if(!response.ok)throw Error("cam_shop_library_http_"+response.status);
 const data=await response.json();
 if(data?.scan_enabled!==false||data.dispatch?.state!=="disabled"||
    data.dispatch?.active_scan_jobs!==0||!Array.isArray(data.items)||
    data.items.length>200||data.count!==data.items.length)
   throw Error("cam_shop_library_contract_untrusted");
 return data.items;
}

export function summarizeCamShopPreflight(items){
 if(!Array.isArray(items)||items.length>200)throw Error("invalid_cam_shop_list");
 const result={shops:items.length,amazon:0,etsy:0,inactive:0,
  paused:0,invalid_shop_url:0,
  browser_jobs_ready:0,reason:"approval_and_dispatch_not_deployed"};
 for(const shop of items){
  if(shop?.platform==="amazon")result.amazon++;
  else if(shop?.platform==="etsy")result.etsy++;
  else{result.invalid_shop_url++;continue}
  if(shop.status==="paused")result.paused++;
  if(shop.status!=="active")result.inactive++;
  try{assertShopUrl(shop.storefront_url,shop.platform)}
  catch{result.invalid_shop_url++}
 }
 return result;
}

export async function fetchConfiguredShop(config,shopId,request=fetch){
 if(!UUID.test(shopId))throw Error("invalid_cam_shop_id");
 const items=await fetchCamShopLibrary(config,request);
 const matches=items.filter(s=>s.id===shopId);
 if(matches.length!==1)throw Error("cam_shop_not_found_or_ambiguous");
 const item=matches[0];
 if(!["amazon","etsy"].includes(item.platform)||typeof item.storefront_url!=="string")
   throw Error("unsupported_cam_shop_platform");
 const shop=assertShopUrl(item.storefront_url,item.platform);
 if(shop.url!==item.storefront_url)throw Error("cam_shop_url_not_canonical");
 // CAM ownership/declaration metadata is classification, NOT the gate
 // for opening or running a separately permitted browser pilot.
 if(item.status==="paused")throw Error("cam_shop_paused");
 return {id:shopId,platform:shop.platform,shopUrl:shop.url};
}

export async function runCamShopPilot(config,{shopId,approval,profileDir,
 maxProducts=3,minimumDelayMs=10000,maximumDelayMs=18000,
 interactionMode="visible_click",headless=false},deps={}){
 const shop=await fetchConfiguredShop(config,shopId,deps.request??fetch);
 // CAM ownership never grants browser rights: a dated marketplace policy
 // review (or a legacy exact-shop approval) remains mandatory.
 validateBrowserPermission(assertShopUrl(shop.shopUrl,shop.platform),approval);
 return await (deps.pilot??runBrowserPilot)({
  shopUrl:shop.shopUrl,platform:shop.platform,authorization:approval,
  profileDir,maxProducts,minimumDelayMs,maximumDelayMs,interactionMode,headless,
 },deps.browserDeps);
}

/** CAM-authoritative shop selection with the same durable, per-shop
 * checkpoint and immutable report contract as the existing local pilot.
 * No task claim, background scan, marketplace API or automatic CAM import.
 * Approval and local output locations are supplied explicitly by the
 * operator, never from CAM's shop metadata.
 */
export async function runCamShopPilotWithProgress(config,{
 shopId,approvalFile,profileDir,reportDir,maxProducts=3,
 minimumDelayMs=10000,maximumDelayMs=18000,headless=false,
 interactionMode="visible_click",
},deps={}){
 const shop=await fetchConfiguredShop(config,shopId,deps.request??fetch);
 return runConfiguredPilot({
  platform:shop.platform,shop_url:shop.shopUrl,
  approval_file:approvalFile,profile_dir:profileDir,report_dir:reportDir,
  max_products:maxProducts,minimum_delay_ms:minimumDelayMs,
  maximum_delay_ms:maximumDelayMs,headless,interaction_mode:interactionMode,
 },{runner:deps.pilot??runBrowserPilot});
}

/**
 * Prepare a strictly quarantined CAM metadata report. This prepares JSON
 * for authenticated OPERATOR import; it never uploads, requests a lease,
 * treats a title as independently verified, or persists image bytes.
 */
export function prepareCamObservationReport(result){
 if(result?.status!=="preview_completed"||
    !["etsy","amazon"].includes(result.platform)||
    !Array.isArray(result.items)||result.items.length<1||result.items.length>5||
    result.observed_count!==result.items.length||
    result.rights_verified!==false||result.sales_verified!==false||
    result.evidence_committed!==false||result.scan_enabled!==false||
    result.coverage_state!=="partial"||
    typeof result.observed_at!=="string"||
    !Number.isFinite(Date.parse(result.observed_at)))
   throw Error("pilot_report_not_importable");
 const sha=/^[0-9a-f]{64}$/;
 if(!sha.test(result.shop_url_sha256))throw Error("pilot_shop_hash_invalid");
 const listingPattern=result.platform==="etsy"?/^etsy:[0-9]{1,20}$/:/^amazon:[A-Z0-9]{10}$/;
 const items=result.items.map(item=>{
  if(!listingPattern.test(item?.listing_key??"")||
     typeof item.title!=="string"||item.title.trim().length<2||item.title.length>300||
     !sha.test(item.observed_page_sha256)||
     !Array.isArray(item.image_url_sha256)||item.image_url_sha256.length>12||
     item.image_url_sha256.some(x=>!sha.test(x))||
     new Set(item.image_url_sha256).size!==item.image_url_sha256.length||
     item.image_count!==item.image_url_sha256.length||
     item.source_kind!=="browser_dom_observation"||
     item.source_verification!=="unverified"||
     item.coverage_state!=="partial"||
     item.rights_state!=="unknown"||item.sales_state!=="unknown")
    throw Error("pilot_listing_not_importable");
  return {
   listing_key:item.listing_key,title:item.title,
   observed_page_sha256:item.observed_page_sha256,
   image_url_sha256:item.image_url_sha256,
   image_count:item.image_count,
   source_kind:"browser_dom_observation",
   rights_state:"unknown",sales_state:"unknown",
   coverage_state:"partial",source_verification:"unverified",
  };
 });
 if(new Set(items.map(i=>i.listing_key)).size!==items.length)
   throw Error("pilot_duplicate_listings");
 const key=createHash("sha256").update(JSON.stringify({
  shop_url_sha256:result.shop_url_sha256,observed_at:result.observed_at,items,
 })).digest("hex");
 return {
  report_key:"pilot:"+key,status:"preview_completed",platform:result.platform,
  shop_url_sha256:result.shop_url_sha256,observed_at:result.observed_at,
  items,observed_count:items.length,coverage_state:"partial",
  rights_verified:false,sales_verified:false,
  evidence_committed:false,scan_enabled:false,
 };
}

function flag(name){
 const index=process.argv.indexOf(name);
 return index>=0?process.argv[index+1]:undefined;
}
export function readPairingFile(filename){
 const path=resolve(filename);
 if(!existsSync(path)||lstatSync(path).isSymbolicLink()||lstatSync(path).size>16384)
   throw Error("pairing_file_untrusted");
 const result={};
 for(const line of readFileSync(path,"utf8").replace(/^\uFEFF/,"").split(/\r?\n/)){
  const trimmed=line.trim();
  if(!trimmed||trimmed.startsWith("#"))continue;
  const match=trimmed.match(/^([A-Z_][A-Z_0-9]*)=(.*)$/);
  if(!match)continue;
  if(["RRUGC_AGENT_ID","RRUGC_SCOUT_TOKEN","BRANDING_DNA_SERVER_URL"].includes(match[1])){
   let value=match[2].trim();
   if((value.startsWith('"')&&value.endsWith('"'))||
      (value.startsWith("'")&&value.endsWith("'")))value=value.slice(1,-1);
   result[match[1]]=value;
  }
 }
 return result;
}
export async function main(){
 const id=flag("--shop-id"),approvalFile=flag("--approval-file"),profileDir=flag("--profile-dir");
 const reportFile=flag("--report"),reportDir=flag("--report-dir");
 const preflight=process.argv.includes("--preflight");
 const resume=process.argv.includes("--resume");
 if(!preflight&&(!id||!approvalFile||!profileDir||
   (resume?(!reportDir||reportFile||process.argv.includes("--prepare-import")):(!reportFile||reportDir))))
   throw Error("usage: node src/cam_shop_pilot.mjs --preflight | --shop-id UUID --approval-file ABS_PATH --profile-dir ABS_PATH --report ABS_PATH | --resume --shop-id UUID --approval-file ABS_PATH --profile-dir ABS_PATH --report-dir ABS_PATH [--env-file PATH]");
 const credentials=flag("--env-file")?readPairingFile(flag("--env-file")):{};
 const config=settings({...process.env,...credentials});
 if(preflight){
  const shops=await fetchCamShopLibrary(config);
  console.log(JSON.stringify({mode:"CAM_READ_ONLY_PREFLIGHT",...summarizeCamShopPreflight(shops)}));
  return;
 }
 if(resume){
  const progress=await runCamShopPilotWithProgress(config,{
   shopId:id,approvalFile,profileDir,reportDir,
  });
  console.log(JSON.stringify({mode:"CAM_SHOP_LOCAL_RESUMABLE_PILOT",
   status:progress.status,observed_count:progress.count,
   previously_seen:progress.previouslySeen,total_observed:progress.totalObserved,
   report_saved:true,report_path:progress.output,
   scan_enabled:false,evidence_committed:false}));
  return;
 }
 const approvalPath=resolve(approvalFile),reportPath=resolve(reportFile);
 if(!existsSync(approvalPath)||lstatSync(approvalPath).isSymbolicLink()||
    lstatSync(approvalPath).size>8192||existsSync(reportPath))
   throw Error("untrusted_approval_or_existing_report");
 const approval=JSON.parse(readFileSync(approvalPath,"utf8"));
 const result=await runCamShopPilot(config,{shopId:id,approval,profileDir});
 mkdirSync(dirname(reportPath),{recursive:true});
 writeFileSync(reportPath,JSON.stringify(result,null,2),{flag:"wx",mode:0o600});
 let importPayloadPrepared=false;
 if(process.argv.includes("--prepare-import")){
  // A parser error/needs_operator must not be converted into evidence.
  const payload=prepareCamObservationReport(result);
  writeFileSync(reportPath+".cam-import.json",JSON.stringify(payload,null,2),
    {flag:"wx",mode:0o600});
  importPayloadPrepared=true;
 }
 console.log(JSON.stringify({status:result.status,observed_count:result.observed_count??0,
   evidence_committed:false,scan_enabled:false,import_payload_prepared:importPayloadPrepared,
   report_saved:true,reason:result.reason??null}));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)
 main().catch(err=>{console.error("CAM pilot stopped: "+String(err?.message??"unexpected_error").slice(0,110));process.exitCode=1});