/** CAM-controlled Windows agent session, with no local shop-selection or
 * permission bypass. Current CAM contract is explicitly non-dispatchable.
 * This runner NEVER browses a marketplace until an approved, audited
 * scan-task lease protocol exists. */
import {setTimeout as sleep} from "node:timers/promises";
import {pathToFileURL} from "node:url";

import {WORKER_VERSION} from "./version.mjs";

export const AGENT_VERSION=WORKER_VERSION;

export function settings(env=process.env){
 const u=new URL(env.BRANDING_DNA_SERVER_URL||"https://creative-assets.ddns.net");
 if(u.protocol!=="https:"||u.username||u.password||u.pathname!=="/"||u.search||u.hash||u.port)
   throw Error("invalid_cam_origin");
 const id=env.RRUGC_AGENT_ID||"";
 const token=env.RRUGC_SCOUT_TOKEN||"";
 if(!/^[a-f0-9-]{36}$/i.test(id)||token.length<20)throw Error("scout_pairing_required");
 return {origin:u.origin,id,token};
}

export async function readCamControl(config,request=fetch){
 const resp=await request(config.origin+"/api/v1/branding-dna/scout-agents/"+encodeURIComponent(config.id)+"/shops",{
  method:"GET",headers:{Authorization:"Bearer "+config.token,Accept:"application/json"},
  redirect:"error",signal:AbortSignal.timeout(12000),
 });
 if(resp.status===401||resp.status===403)throw Error("scout_pairing_revoked");
 if(!resp.ok)throw Error("cam_http_"+resp.status);
 const data=await resp.json();
 if(!Array.isArray(data.items)||data.items.length>200||typeof data.scan_enabled!=="boolean")
   throw Error("invalid_cam_contract");
 if(data.scan_enabled!==false)throw Error("dispatch_contract_not_audited");
 if(data.items.some(s=>!["amazon","etsy"].includes(s.platform)||typeof s.storefront_url!=="string"))
   throw Error("invalid_shop_metadata");
 if(data.dispatch && (data.dispatch.state!=="disabled"||
       data.dispatch.reason!=="scan_task_dispatch_not_deployed"||
       data.dispatch.active_scan_jobs!==0))throw Error("dispatch_contract_not_audited");
 return {shopCount:data.items.length,dispatchEnabled:false};
}

/** Inspect blocked shops only. Never interpret this response as a lease. */
export async function readDispatchPreview(config,request=fetch){
 const resp=await request(config.origin+"/api/v1/branding-dna/scout-agents/"+
   encodeURIComponent(config.id)+"/dispatch-preview",{
  method:"GET",headers:{Authorization:"Bearer "+config.token,Accept:"application/json"},
  redirect:"error",signal:AbortSignal.timeout(12000),
 });
 if(resp.status===401||resp.status===403)throw Error("scout_pairing_revoked");
 // Allow staggered API and Windows worker upgrades. The old API still
 // cannot dispatch, and this fallback never activates the browser.
 if(resp.status===404)return {available:false,blocked:0,stagedTasks:0,blockedReasons:{}};
 if(!resp.ok)throw Error("dispatch_preview_http_"+resp.status);
 const data=await resp.json();
 if(data?.version!==1||data.scan_enabled!==false||
   data.dispatch?.state!=="disabled"||
   data.dispatch.reason!=="scan_task_dispatch_not_deployed"||
   data.dispatch.active_scan_jobs!==0||
   data.dispatch.marketplace_authorization_verified!==false||
   !Array.isArray(data.items)||data.items.length>200||
   data.shop_count!==data.items.length||
   data.blocked_count!==data.shop_count||
   data.ready_count!==0||
   data.items.some(item=>item.status!=="blocked"||
      !["amazon","etsy"].includes(item.platform)||
      typeof item.shop_id!=="string"||
      !Number.isInteger(item.staged_tasks)||item.staged_tasks<0||
      !Array.isArray(item.blockers)||
      !item.blockers.includes("scan_task_dispatch_not_deployed")||
      !item.blockers.includes("marketplace_authorization_not_verified")))
   throw Error("dispatch_preview_cannot_enable_scan");
 // Only aggregate validated blocker codes. Never expose shop URLs or tokens
 // in the agent's operational logs.
 const reasonCounts=new Map();
 for(const item of data.items){
  for(const reason of new Set(item.blockers)){
   if(typeof reason!=="string"||!/^[a-z][a-z0-9_]{2,79}$/.test(reason))
     throw Error("dispatch_preview_cannot_enable_scan");
   reasonCounts.set(reason,(reasonCounts.get(reason)??0)+1);
  }
 }
 const blockedReasons=Object.fromEntries([...reasonCounts].sort(([a],[b])=>a.localeCompare(b)));
 return {available:true,blocked:data.blocked_count,
         stagedTasks:data.items.reduce((sum,item)=>sum+item.staged_tasks,0),
         blockedReasons};
}

export async function sendStandbyHeartbeat(config,request=fetch){
 const resp=await request(config.origin+"/api/v1/branding-dna/scout-agents/"+
   encodeURIComponent(config.id)+"/branding-heartbeat",{
    method:"POST",headers:{Authorization:"Bearer "+config.token,
      Accept:"application/json","Content-Type":"application/json"},
    body:JSON.stringify({version:AGENT_VERSION,state:"connected_waiting",
      scan_enabled:false,active_scan_jobs:0}),
    redirect:"error",signal:AbortSignal.timeout(12000),
   });
 if(resp.status===401||resp.status===403)throw Error("scout_pairing_revoked");
 if(!resp.ok)throw Error("cam_heartbeat_http_"+resp.status);
 const data=await resp.json();
 if(data.accepted!==true||data.state!=="connected_waiting"||
    data.scan_enabled!==false||data.active_scan_jobs!==0||
    !Number.isInteger(data.shop_count)||data.shop_count<0||data.shop_count>200)
      throw Error("invalid_cam_heartbeat_contract");
 return {shopCount:data.shop_count};
}

export async function run(config=settings(),{request=fetch,wait=sleep,log=console.log,onStop=()=>false}={}){
 let failures=0;
 while(!onStop()){
  try{
   const result=await readCamControl(config,request);
   const preview=await readDispatchPreview(config,request);
   const receipt=await sendStandbyHeartbeat(config,request);
   log(JSON.stringify({event:"cam_heartbeat",connected:true,shops:receipt.shopCount,
     dispatch_enabled:false,reason:"scan_pipeline_not_deployed",
     state:"connected_waiting",preview_available:preview.available,
     blocked_shops:preview.blocked,staged_tasks:preview.stagedTasks,
     blocked_reasons:preview.blockedReasons}));
   failures=0;
  }catch(error){
   const reason=error instanceof Error?error.message:"cam_unavailable";
   // Token never gets logged and authorization failures stop the process.
   log(JSON.stringify({event:"cam_connection_error",reason}));
   if(["scout_pairing_revoked","dispatch_contract_not_audited","invalid_cam_contract","invalid_shop_metadata","invalid_cam_heartbeat_contract","dispatch_preview_cannot_enable_scan"].includes(reason))
     throw error;
   failures++;
  }
  if(!onStop())await wait(failures?Math.min(60000,10000*failures):60000);
 }
}

export async function main(){
 let stopped=false;
 for(const sig of ["SIGINT","SIGTERM"])process.once(sig,()=>{stopped=true});
 const config=settings();
 console.log(JSON.stringify({event:"agent_started",source:"CAM",shop_config:"server_only",marketplace_requests:0}));
 await run(config,{onStop:()=>stopped});
 console.log(JSON.stringify({event:"agent_stopped"}));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 main().catch(error=>{
  const msg=error instanceof Error?error.message:"unknown";
  console.error(JSON.stringify({event:"agent_exited",reason:msg.slice(0,90)}));
  process.exitCode=1;
 });
}