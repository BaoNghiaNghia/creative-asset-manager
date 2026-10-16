/** Local Windows launcher: explicit config, approval file, bounded browser pilot.
 * No CAM dispatch, no marketplace API and no automatic shop execution.
 */
import {createHash,randomUUID} from "node:crypto";
import {readFileSync,writeFileSync,existsSync,lstatSync,mkdirSync} from "node:fs";
import {isAbsolute,relative,resolve,join} from "node:path";
import {pathToFileURL,fileURLToPath} from "node:url";
import {assertShopUrl,validateBrowserPermission,runBrowserPilot} from "./browser_pilot.mjs";

export const VERSION="0.2.0";
const ROOT=resolve(fileURLToPath(new URL("..",import.meta.url)));
const KEYS=new Set([
  "platform","shop_url","approval_file","profile_dir","report_dir",
  "max_products","minimum_delay_ms","headless",
]);

function isChild(path,parent){
  const rel=relative(parent,path);
  return rel==="" || rel===".." || rel.startsWith("../") || rel.startsWith("..\\") ? rel==="" : !isAbsolute(rel);
}
function loadSmallJson(path,label){
  if(!isAbsolute(path) || !existsSync(path) || lstatSync(path).isSymbolicLink() || lstatSync(path).size>8192)
    throw new Error(label+"_file_not_present_or_untrusted");
  let obj;
  try{obj=JSON.parse(readFileSync(path,"utf8"))}catch{throw new Error(label+"_json_invalid")}
  if(!obj || typeof obj!=="object" || Array.isArray(obj))throw new Error(label+"_object_required");
  return obj;
}
export function validatePilotConfig(config,root=ROOT){
  if(!config || typeof config!=="object" || Array.isArray(config)
    || Object.keys(config).some(key=>!KEYS.has(key)))throw new Error("pilot_config_invalid");
  const shop=assertShopUrl(config.shop_url,config.platform);
  for(const field of ["approval_file","profile_dir","report_dir"]){
    if(typeof config[field]!=="string" || !isAbsolute(config[field]))
      throw new Error(field+"_must_be_absolute");
    const value=resolve(config[field]);
    if(isChild(value,root))throw new Error(field+"_must_be_outside_worker_install");
    if(field!=="approval_file" && existsSync(value) && lstatSync(value).isSymbolicLink())
      throw new Error(field+"_symlink_forbidden");
  }
  if(!Number.isInteger(config.max_products) || config.max_products<1 || config.max_products>5)
    throw new Error("max_products_range_1_to_5");
  if(!Number.isInteger(config.minimum_delay_ms) || config.minimum_delay_ms<5000 || config.minimum_delay_ms>60000)
    throw new Error("minimum_delay_ms_range_5000_to_60000");
  if(typeof config.headless!=="boolean")throw new Error("headless_boolean_required");
  return shop;
}
export function validateConfiguredApproval(config,root=ROOT){
  const shop=validatePilotConfig(config,root);
  const approval=loadSmallJson(resolve(config.approval_file),"approval");
  validateBrowserPermission(shop,approval);
  return {shop,approval};
}
export function readPilotConfig(path,root=ROOT){
  const config=loadSmallJson(resolve(path),"pilot_config");
  validatePilotConfig(config,root);
  return config;
}
function nextReportFile(dir){
  const time=new Date().toISOString().replace(/[-:.]/g,"").replace("T","-").replace("Z","Z");
  return join(dir,"branding-dna-"+time+"-"+randomUUID().slice(0,8)+".json");
}
export async function runConfiguredPilot(config,{runner=runBrowserPilot,root=ROOT}={}){
  const {shop,approval}=validateConfiguredApproval(config,root);
  const result=await runner({
    platform:config.platform,
    shopUrl:config.shop_url,
    authorization:approval,
    profileDir:config.profile_dir,
    maxProducts:config.max_products,
    minimumDelayMs:config.minimum_delay_ms,
    headless:config.headless,
  });
  if(!result || !["preview_completed","needs_operator"].includes(result.status)
    || result.scan_enabled!==false)throw new Error("invalid_local_pilot_result");
  mkdirSync(config.report_dir,{recursive:true,mode:0o700});
  if(lstatSync(config.report_dir).isSymbolicLink())throw new Error("report_dir_symlink_forbidden");
  const output=nextReportFile(config.report_dir);
  const manifest={
    ...result,
    platform:shop.platform,
    shop_url_sha256:createHash("sha256").update(shop.url).digest("hex"),
    observed_at:result.observed_at??new Date().toISOString(),
    pilot_version:VERSION,
    mode:"local_browser_dom_only",
    local_approval_sha256:createHash("sha256").update(JSON.stringify(approval)).digest("hex"),
    evidence_committed:false,
    rights_verified:false,
    sales_verified:false,
    scan_enabled:false,
  };
  writeFileSync(output,JSON.stringify(manifest,null,2)+"\n",{mode:0o600,flag:"wx"});
  return {output,status:manifest.status,count:manifest.items?.length??0};
}

export function cliArgs(args){
  if(args.length!==2 || args[0]!=="--config" || !args[1])throw new Error("usage: node src/run_configured_pilot.mjs --config ABSOLUTE_PILOT_CONFIG");
  return args[1];
}
if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
  try{
    const configPath=cliArgs(process.argv.slice(2));
    const config=readPilotConfig(configPath);
    const outcome=await runConfiguredPilot(config);
    process.stdout.write("Branding DNA "+VERSION+" | "+outcome.status+
      " | "+outcome.count+" partial items | local report: "+outcome.output+"\n");
  }catch(error){
    // No approval JSON, full shop URL, token or page HTML in error output.
    process.stderr.write("Browser pilot blocked: "+(error instanceof Error?error.message:"unknown")+"\n");
    process.exitCode=1;
  }
}
