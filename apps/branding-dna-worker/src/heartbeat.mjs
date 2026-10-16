import { setTimeout as delay } from "node:timers/promises";
import { pathToFileURL } from "node:url";
import { defaultOutboxPath, openOutbox, flushOutboxOnce } from "./outbox.mjs";

export function resolveSettings(env = process.env) {
  const address = env.BRANDING_DNA_SERVER_URL || "https://creative-assets.ddns.net";
  const base = new URL(address);
  if(base.protocol !== "https:" || base.username || base.password || base.search || base.hash || base.pathname !== "/") {
    throw new Error("BRANDING_DNA_SERVER_URL must be an HTTPS origin without credentials or path");
  }
  const agentId = (env.BRANDING_DNA_AGENT_ID || "").trim();
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(agentId)) {
    throw new Error("BRANDING_DNA_AGENT_ID must be the ID issued by CAM");
  }
  const secret = env.BRANDING_DNA_AGENT_TOKEN || "";
  if(secret.length < 42) throw new Error("BRANDING_DNA_AGENT_TOKEN must be the one-time CAM enrollment token");
  return {url: base.origin, agentId, token: secret};
}

export async function heartbeatOnce(config, requester=fetch) {
  const response=await requester(
    config.url + "/api/v1/branding-dna/workers/" + encodeURIComponent(config.agentId) + "/heartbeat",
    {method:"POST",headers:{"Authorization":"Bearer "+config.token,"Content-Type":"application/json"},
     body:JSON.stringify({status:"idle",agent_version:"0.1.0"}),redirect:"error",signal:AbortSignal.timeout(15000)}
  );
  if(response.status===401 || response.status===403) throw new Error("enrollment_revoked");
  if(!response.ok) throw new Error("heartbeat_http_" + response.status);
  const result=await response.json();
  if(result.scan_enabled!==false) throw new Error("unexpected_scanning_enabled");
  return result;
}

export function classifyHeartbeatError(code){
  if(/^heartbeat_http_5[0-9][0-9]$/.test(code))return "server_5xx";
  if(/^heartbeat_http_4[0-9][0-9]$/.test(code))return "server_4xx";
  return "network_error";
}

export async function main(){
  const config=resolveSettings();
  const outbox=openOutbox(defaultOutboxPath(process.env,config.agentId));
  let stopped=false;
  let revoked=false;
  for(const sig of ["SIGINT","SIGTERM"])process.once(sig,()=>{stopped=true;});
  let failures=0;
  // Persist first; only the server ACK can remove the event.
  outbox.enqueue("started","worker_start");
  process.stdout.write("Branding DNA Worker connected to "+config.url+" (scanner disabled, durable lifecycle outbox)\n");
  try {
    while(!stopped){
      try {
        await heartbeatOnce(config);
        if(failures>0)outbox.enqueue("heartbeat_recovered","recovered");
        failures=0;
        process.stdout.write(new Date().toISOString()+" heartbeat accepted; scanner remains disabled\n");
        try {
          await flushOutboxOnce(outbox,config);
        }catch(e) {
          const code=e instanceof Error?e.message:"unknown";
          if(code==="enrollment_revoked"){revoked=true;process.exitCode=2;break;}
          // A failed POST or invalid ACK never deletes local events.
          process.stderr.write("Lifecycle event sync deferred; retained in outbox.\n");
        }
      }catch(e){
        const code=e instanceof Error?e.message:"unknown";
        if(code==="enrollment_revoked"||code==="unexpected_scanning_enabled"){
          revoked=true;process.exitCode=2;process.stderr.write("Worker protocol revoked or unsafe. Agent stopped.\n");break;
        }
        if(failures===0)outbox.enqueue("heartbeat_failure",classifyHeartbeatError(code));
        failures++;
        process.stderr.write("Heartbeat failed: "+(/^heartbeat_http_/.test(code)?classifyHeartbeatError(code):"network_error")+"\n");
      }
      if(!stopped)await delay(failures?Math.min(300000,15000*(2**Math.min(failures,4))):60000);
    }
  }finally{
    if(!revoked && stopped){
      outbox.enqueue("stopped","graceful_shutdown");
      try {await flushOutboxOnce(outbox,config);}catch { /* retain for restart */ }
    }
    outbox.close();
  }
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
  main().catch(error=>{
    process.stderr.write("Worker configuration error: "+(error instanceof Error?error.message:"unknown")+"\n");
    process.exitCode=1;
  });
}
