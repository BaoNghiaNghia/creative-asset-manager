/** CAM-controlled Windows agent session, with no local shop-selection or
 * permission bypass. Current CAM contract is explicitly non-dispatchable.
 * This runner NEVER browses a marketplace until an approved, audited
 * scan-task lease protocol exists. */
import {setTimeout as sleep} from "node:timers/promises";
import {pathToFileURL} from "node:url";

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
 return {shopCount:data.items.length,dispatchEnabled:false};
}

export async function run(config=settings(),{request=fetch,wait=sleep,log=console.log,onStop=()=>false}={}){
 let failures=0;
 while(!onStop()){
  try{
   const result=await readCamControl(config,request);
   log(JSON.stringify({event:"cam_heartbeat",connected:true,shops:result.shopCount,
     dispatch_enabled:false,reason:"scan_pipeline_not_deployed"}));
   failures=0;
  }catch(error){
   const reason=error instanceof Error?error.message:"cam_unavailable";
   // Token never gets logged and authorization failures stop the process.
   log(JSON.stringify({event:"cam_connection_error",reason}));
   if(["scout_pairing_revoked","dispatch_contract_not_audited","invalid_cam_contract","invalid_shop_metadata"].includes(reason))
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
