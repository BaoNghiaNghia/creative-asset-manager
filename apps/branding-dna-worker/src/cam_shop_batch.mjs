/** Serial, expressly invoked CAM-shop browser pilots. Not a production
 * dispatcher: no lease, periodic polling, CAM upload or marketplace API.
 * Shop URLs are sourced exclusively from the authenticated CAM Shop Library.
 */
import {existsSync} from "node:fs";
import {join,isAbsolute,resolve} from "node:path";
import {pathToFileURL} from "node:url";
import {readPairingFile,fetchCamShopLibrary,runCamShopPilotWithProgress} from "./cam_shop_pilot.mjs";
import {settings} from "./agent_cam.mjs";
import {validateConfiguredApproval} from "./run_configured_pilot.mjs";

const UUID=/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const SAFE_ERRORS=new Set([
 "approval_file_not_present_or_untrusted","approval_json_invalid",
 "shop_browser_pilot_not_approved","browser_approval_expired",
 "marketplace_policy_scope_invalid","etsy_permission_scope_not_verified",
 "amazon_marketplace_host_review_required",
 "etsy_written_authorization_required","amazon_agent_policy_review_required",
 "cam_shop_paused",
 "cam_shop_not_found_or_ambiguous","cam_shop_url_not_canonical",
 "invalid_shop_url","unsupported_amazon_store_url","unsupported_etsy_shop_url",
 "progress_directory_untrusted","progress_report_immutable_violation",
 "exclusive_pilot_profile_lock","no_visible_listings_or_parser_changed",
 "scout_pairing_revoked","cam_shop_library_contract_untrusted",
 "marketplace_access_limited","shop_back_navigation_mismatch",
]);
const reason=err=>SAFE_ERRORS.has(err?.message)?err.message:"operator_review_required";

export function localShopPaths(baseDir,id){
 if(!UUID.test(id)||typeof baseDir!=="string"||!isAbsolute(baseDir))
   throw Error("invalid_batch_shop_path");
 const dir=resolve(baseDir,"cam-shop-pilots",id.toLowerCase());
 return {approvalFile:join(dir,"approval.json"),
         profileDir:join(dir,"chromium-profile"),reportDir:join(dir,"reports")};
}
/** One marketplace review file replaces repeated per-shop permission files.
 * The old exact-shop path is read only as a backwards-compatible fallback.
 */
export function marketplacePolicyFile(baseDir,platform){
 if(typeof baseDir!=="string"||!isAbsolute(baseDir)||
    !["amazon","etsy"].includes(platform))throw Error("invalid_marketplace_policy_path");
 return join(resolve(baseDir),"marketplace-access",platform+".json");
}
function approvalPath(baseDir,shop){
 const policy=marketplacePolicyFile(baseDir,shop.platform);
 return existsSync(policy)?policy:localShopPaths(baseDir,shop.id).approvalFile;
}
export function checkShopEligibility(shop,baseDir){
 const id=shop?.id;
 if(!UUID.test(id)||!["amazon","etsy"].includes(shop.platform))
   return {shop_id:String(id??"invalid").slice(0,40),platform:"unknown",
     state:"blocked",reason:"invalid_cam_shop_record"};
 const row={shop_id:id,platform:shop.platform};
 // Registry ownership labels and draft state never confer or revoke
 // external browser permission. Only an explicit pause stops this shop.
 if(shop.status==="paused")return {...row,state:"blocked",reason:"shop_paused"};
 if(!["active","draft"].includes(shop.status))
  return {...row,state:"blocked",reason:"shop_status_not_eligible"};
 const p=localShopPaths(baseDir,id);
 const reviewFile=approvalPath(baseDir,shop);
 if(!existsSync(reviewFile))return {...row,state:"blocked",reason:"marketplace_policy_or_legacy_approval_missing"};
 try {
  validateConfiguredApproval({
   platform:shop.platform,shop_url:shop.storefront_url,
   approval_file:reviewFile,profile_dir:p.profileDir,report_dir:p.reportDir,
   max_products:3,minimum_delay_ms:10000,maximum_delay_ms:18000,
   headless:false,interaction_mode:"visible_click",
  });
  return {...row,state:"locally_approved",reason:"operator_still_responsible_for_external_permission"};
 }catch(error){return {...row,state:"blocked",reason:reason(error)}}
}

/** Never auto-enables scans; each call is a finite user-initiated batch,
 * max five sequential shops, max three visible product pages per shop.
 * Marketplace permission must still be documented independently of CAM.
 */
export async function runCamShopBatch(config,{mode="inspect",shopId=null,maxShops=1,
 baseDir,request=fetch,pilot,stopping=()=>false}={}){
 if(!["inspect","run_approved"].includes(mode)||!Number.isInteger(maxShops)||
    maxShops<1||maxShops>5||shopId!==null&&!UUID.test(shopId))
    throw Error("invalid_batch_options");
 const shops=await fetchCamShopLibrary(config,request);
 const selected=shopId?shops.filter(s=>s.id===shopId):shops;
 if(shopId&&selected.length!==1)throw Error("cam_shop_not_found_or_ambiguous");
 if(new Set(selected.map(s=>s.id)).size!==selected.length)
  throw Error("cam_shop_library_contract_untrusted");
 const rows=selected.map(s=>checkShopEligibility(s,baseDir));
 const summary={mode,shops:selected.length,eligible:rows.filter(r=>r.state==="locally_approved").length,
  blocked:rows.filter(r=>r.state==="blocked").length,items:rows,run_count:0,
  needs_operator:0,scan_enabled:false,evidence_committed:false};
 if(mode==="inspect")return summary;
 if(process.platform!=="win32" && !pilot)throw Error("windows_browser_runtime_only");
 for(const [index,row] of rows.entries()){
  if(row.state!=="locally_approved"||summary.run_count>=maxShops||stopping())continue;
  const p=localShopPaths(baseDir,row.shop_id);
  try{
   const shop=selected[index];
   const result=await runCamShopPilotWithProgress(config,{
    shopId:row.shop_id,...p,approvalFile:approvalPath(baseDir,shop),maxProducts:3,
    minimumDelayMs:10000,maximumDelayMs:18000,
    headless:false,interactionMode:"visible_click",
   },{request,pilot});
   row.state=result.status==="preview_completed"?"completed":"needs_operator";
   row.reason=result.status==="preview_completed"?"local_report_saved":"local_operator_review_required";
   row.observed_count=result.count;row.total_observed=result.totalObserved;
   summary.run_count++;
   if(row.state==="needs_operator"){summary.needs_operator++;break}
  }catch(error){
   row.state="needs_operator";row.reason=reason(error);
   summary.needs_operator++;summary.run_count++;
   // Stop all shops if one browser encounters an unexpected condition.
   break;
  }
 }
 return summary;
}
export function parseBatchCli(argv=[]){
 let mode=null,shopId=null,maxShops=1,envFile="scout.local.env";
 for(let i=0;i<argv.length;i++){
  const flag=argv[i];
  if(flag==="--inspect"||flag==="--run-approved"){
   if(mode)throw Error("batch_mode_duplicate");
   mode=flag==="--inspect"?"inspect":"run_approved";
  }else if(["--shop-id","--max-shops","--env-file"].includes(flag)){
   const value=argv[++i];if(!value||value.startsWith("--"))throw Error("batch_arg_missing");
   if(flag==="--shop-id")shopId=value;
   else if(flag==="--env-file")envFile=value;
   else maxShops=Number(value);
  }else throw Error("batch_unknown_arg");
 }
 if(!mode)throw Error("batch_mode_required");
 return {mode,shopId,maxShops,envFile};
}
export async function main(argv=process.argv.slice(2)){
 const args=parseBatchCli(argv);
 const local=process.env.LOCALAPPDATA;
 if(!local||!isAbsolute(local))throw Error("windows_local_appdata_required");
 let stop=false;const signal=()=>{stop=true};
 process.once("SIGINT",signal);process.once("SIGTERM",signal);
 try{
  const config=settings({...process.env,...readPairingFile(args.envFile)});
  const result=await runCamShopBatch(config,{
   ...args,baseDir:join(local,"CreativeAssetManager","BrandingDNA"),
   stopping:()=>stop,
  });
  process.stdout.write(JSON.stringify(result)+"\n");
  return result;
 }finally{
  process.removeListener("SIGINT",signal);
  process.removeListener("SIGTERM",signal);
 }
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href)
 main().catch(error=>{
  process.stderr.write(JSON.stringify({event:"batch_stopped",reason:reason(error)})+"\n");
  process.exitCode=1;
 });