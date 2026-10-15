import { setTimeout as delay } from "node:timers/promises";
import { pathToFileURL } from "node:url";

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

export async function main(){
  const config=resolveSettings();
  let stopped=false;
  for(const sig of ["SIGINT","SIGTERM"])process.once(sig,()=>{stopped=true;});
  let failures=0;
  process.stdout.write("Branding DNA Worker heartbeat connected to "+config.url+" (no scanning capability)\n");
  while(!stopped){
    try {
      await heartbeatOnce(config);
      failures=0;
      process.stdout.write(new Date().toISOString()+" heartbeat accepted; scanner remains disabled\n");
    }catch(e){
      failures++;
      const code=e instanceof Error?e.message:"unknown";
      if(code==="enrollment_revoked"){process.stderr.write("Enrollment disabled/invalid. Agent stopped.\n");process.exitCode=2;break;}
      // Never print request config, headers or secret.
      process.stderr.write("Heartbeat failed: "+(/^(heartbeat_http_|unexpected_scanning_enabled)/.test(code)?code:"network_error")+"\n");
    }
    if(!stopped) await delay(failures?Math.min(300000,15000*(2**Math.min(failures,4))):60000);
  }
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
  main().catch(error=>{
    process.stderr.write("Worker configuration error: "+(error instanceof Error?error.message:"unknown")+"\n");
    process.exitCode=1;
  });
}
