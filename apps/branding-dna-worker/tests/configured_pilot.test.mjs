import test from "node:test";
import {createHash} from "node:crypto";
import assert from "node:assert/strict";
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,readdirSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {validatePilotConfig,validateConfiguredApproval,readPilotConfig,runConfiguredPilot,cliArgs,VERSION} from "../src/run_configured_pilot.mjs";
import {checkRuntime} from "../src/check_setup.mjs";

const SHOP="https://www.amazon.com/stores/TestBrand/page/A123ABC";
function setup(){
 const base=mkdtempSync(join(tmpdir(),"brand-dna-configured-"));
 const config={
   platform:"amazon",shop_url:SHOP,approval_file:join(base,"secrets","approval.json"),
   profile_dir:join(base,"profiles","amazon-a"),report_dir:join(base,"reports"),
   max_products:2,minimum_delay_ms:5000,headless:true,interaction_mode:"direct",
 };
 mkdirSync(join(base,"secrets"),{recursive:true});
 const approved={
   platform:"amazon",shop_url:SHOP,
   browser_access_approved:true,source_rights_reviewed:true,
   platform_terms_reviewed:true,robots_policy_reviewed:true,
   approved_by:"authorized operator",approved_at:new Date().toISOString(),
   amazon_agent_policy_reviewed:true,
 };
 writeFileSync(config.approval_file,JSON.stringify(approved));
 const configPath=join(base,"pilot.json");
 writeFileSync(configPath,JSON.stringify(config));
 return {base,config,configPath,approved};
}
test("config validation rejects arbitrary automation arguments and worker tree paths",()=>{
 const {base,config}=setup();
 try {
   assert.ok(validatePilotConfig(config,resolve(base,"inside_worker")),"valid explicit external state dirs");
   for(const bad of [
     {...config,shop_url:"http://127.0.0.1/test"},
     {...config,platform:"shopify"},
     {...config,max_products:50},
     {...config,minimum_delay_ms:250},
     {...config,headless:"true"},
     {...config,interaction_mode:"stealth"},
     {...config,interaction_mode:"visible_click",headless:true},
     {...config,dispatch:true},
     {...config,approval_file:"secrets/approval.json"},
     {...config,report_dir:base},
   ])assert.throws(()=>validatePilotConfig(bad,base));
   assert.throws(()=>cliArgs(["--platform","etsy"]));
   assert.equal(cliArgs(["--config","C:/file.json"]),"C:/file.json");
 }finally {rmSync(base,{recursive:true,force:true})}
});
test("approval is not self-granted via config or network",async()=>{
 const {base,config,configPath,approved}=setup();
 try{
   assert.equal(readPilotConfig(configPath,resolve(base,"worker")).platform,"amazon");
   assert.equal(validateConfiguredApproval(config,resolve(base,"worker")).shop.platform,"amazon");
   writeFileSync(config.approval_file,JSON.stringify({...approved,platform_terms_reviewed:false}));
   assert.throws(()=>validateConfiguredApproval(config,resolve(base,"worker")),/shop_browser_pilot_not_approved/);
   let invoked=false;
   await assert.rejects(
     ()=>runConfiguredPilot(config,{root:resolve(base,"worker"),runner:async()=>{invoked=true}}),
     /shop_browser_pilot_not_approved/,
   );
   assert.equal(invoked,false);
 }finally{rmSync(base,{recursive:true,force:true})}
});
test("manual runner reports only local unverified evidence and never overwrites previous output",async()=>{
 const {base,config}=setup();
 try{
   let calls=0;
   const runner=async options=>{
     calls++;
     assert.equal(options.maxProducts,2);
     assert.equal(options.minimumDelayMs,5000);
     assert.equal(options.interactionMode,"direct");
     return {status:"preview_completed",platform:"amazon",items:[{
       listing_key:"amazon:B012345678",title:"Canvas Cap",
       source_kind:"browser_dom_observation",sales_state:"unknown",
     }],scan_enabled:false,rights_verified:false};
   };
   const first=await runConfiguredPilot(config,{root:resolve(base,"worker"),runner});
   const second=await runConfiguredPilot(config,{root:resolve(base,"worker"),runner});
   assert.equal(calls,2);
   assert.notEqual(first.output,second.output);
   assert.equal(readdirSync(config.report_dir).length,2);
   const report=JSON.parse(readFileSync(first.output,"utf8"));
   assert.equal(report.pilot_version,VERSION);
   assert.equal(report.evidence_committed,false);
   assert.equal(report.sales_verified,false);
   assert.equal(report.scan_enabled,false);
   assert.equal(report.items.length,1);
   assert.equal(report.mode,"local_browser_dom_only");
   assert.equal(report.shop_url_sha256,createHash("sha256").update(SHOP).digest("hex"));
   assert.match(report.observed_at,/^\d{4}-\d\d-\d\dT/);
   assert.ok(!readFileSync(first.output,"utf8").includes("browser_access_approved"));
 }finally{rmSync(base,{recursive:true,force:true})}
});
test("Scout Manager visible browser defaults to real UI link clicks without config migration",async()=>{
 const {base,config}=setup();
 try{
  const visible={...config,headless:false};
  delete visible.interaction_mode; // Legacy Scout Manager config without new key
  let observed="";
  const runner=async options=>{
    observed=options.interactionMode;
    return {status:"preview_completed",items:[],scan_enabled:false};
  };
  const saved=await runConfiguredPilot(visible,{root:resolve(base,"worker"),runner});
  const report=JSON.parse(readFileSync(saved.output,"utf8"));
  assert.equal(observed,"visible_click");
  assert.equal(report.interaction_mode,"visible_click");
 }finally{rmSync(base,{recursive:true,force:true})}
});

test("blocked pilot report retains shop identity without enabling scans",async()=>{
 const {base,config}=setup();
 try{
   const saved=await runConfiguredPilot(config,{
     root:resolve(base,"worker"),
     runner:async()=>({status:"needs_operator",reason:"captcha",items:[],scan_enabled:false}),
   });
   const body=JSON.parse(readFileSync(saved.output,"utf8"));
   assert.equal(body.status,"needs_operator");
   assert.equal(body.scan_enabled,false);
   assert.equal(body.evidence_committed,false);
   assert.equal(body.shop_url_sha256,createHash("sha256").update(SHOP).digest("hex"));
   assert.ok(body.observed_at);
 }finally{rmSync(base,{recursive:true,force:true})}
});
test("runner fails closed on unexpected worker result",async()=>{
 const {base,config}=setup();
 try{
   await assert.rejects(()=>runConfiguredPilot(config,{
     root:resolve(base,"worker"),
     runner:async()=>({status:"completed",scan_enabled:true}),
   }),/invalid_local_pilot_result/);
 }finally{rmSync(base,{recursive:true,force:true})}
});
test("runtime check is offline and validates executable existence and Node version",()=>{
 const {base,configPath}=setup();
 try{
   assert.equal(checkRuntime({nodeVersion:"22.13.0",executable:configPath}).chromiumReady,true);
   assert.throws(()=>checkRuntime({nodeVersion:"22.12.0",executable:configPath}),/Node/);
   assert.throws(()=>checkRuntime({nodeVersion:"23.0.0",executable:join(base,"missing")}),/Chromium/);
 }finally{rmSync(base,{recursive:true,force:true})}
});
