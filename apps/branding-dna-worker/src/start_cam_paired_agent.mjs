/** CAM-paired Windows runtime. This is a real authenticated CAM session,
 * NOT a marketplace crawler. Never claim jobs without independent rights
 * verification and a separately reviewed lease protocol.
 */
import {pathToFileURL,fileURLToPath} from "node:url";
import {resolve} from "node:path";
import {readPairingFile} from "./cam_shop_pilot.mjs";
import {settings,readCamControl,readDispatchPreview,sendStandbyHeartbeat,run} from "./agent_cam.mjs";

const DEFAULT_PAIRING=resolve(fileURLToPath(new URL("../../../scout.local.env",import.meta.url)));

export function parseArgs(args=[]){
 let mode=null,envFile=DEFAULT_PAIRING;
 for(let i=0;i<args.length;i++){
  const arg=args[i];
  if((arg==="--once"||arg==="--run") && mode===null)mode=arg.slice(2);
  else if(arg==="--env-file" && i+1<args.length && args[i+1] && !args[i+1].startsWith("--")){
   envFile=resolve(args[++i]);
  }else throw Error("usage: --once|--run [--env-file PATH]");
 }
 if(!mode)throw Error("usage: --once|--run [--env-file PATH]");
 return {mode,envFile};
}

export async function checkOnce(config,request=fetch){
 const control=await readCamControl(config,request);
 const preview=await readDispatchPreview(config,request);
 const heartbeat=await sendStandbyHeartbeat(config,request);
 return {
  event:"cam_live_check",connected:true,state:"connected_waiting",
  shop_count:heartbeat.shopCount,preview_available:preview.available,
  blocked_shops:preview.blocked,staged_tasks:preview.stagedTasks,
  blocked_reasons:preview.blockedReasons,
  dispatch_enabled:control.dispatchEnabled,active_scan_jobs:0,
  marketplace_requests:0,
 };
}

export async function main(args=process.argv.slice(2),deps={}){
 const options=parseArgs(args);
 const pairing=readPairingFile(options.envFile);
 const config=settings({...process.env,...pairing});
 const log=deps.log??(message=>process.stdout.write(message+"\n"));
 if(options.mode==="once"){
  const response=await checkOnce(config,deps.request??fetch);
  log(JSON.stringify(response));
  return response;
 }
 let stopped=false;
 const onSignal=()=>{stopped=true};
 for(const signal of ["SIGINT","SIGTERM"])process.once(signal,onSignal);
 log(JSON.stringify({event:"cam_agent_started",source:"CAM",mode:"paired",
  marketplace_requests:0,scan_enabled:false}));
 try{
  await run(config,{request:deps.request??fetch,wait:deps.wait,
    log,onStop:()=>stopped || deps.onStop?.()===true});
 }finally{
  for(const signal of ["SIGINT","SIGTERM"])process.removeListener(signal,onSignal);
  log(JSON.stringify({event:"cam_agent_stopped",scan_enabled:false}));
 }
}

if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 main().catch(error=>{
  const reason=error instanceof Error?error.message:"unknown_error";
  console.error(JSON.stringify({event:"cam_agent_failed",reason:reason.slice(0,90)}));
  process.exitCode=1;
 });
}