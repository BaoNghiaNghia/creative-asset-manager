/** Future worker callbacks to CAM's lease-fenced control plane.
 * NOT wired to the live heartbeat loop or the browser pilot. An idle worker
 * cannot obtain a lease, and callbacks cannot activate scans.
 */
const ID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FAILURE_CODES=new Set([
  "temporary_network_error", "rate_limited", "source_unavailable",
  "parser_changed", "manual_check_required",
]);

function ensureIdentifier(v,what){
  if(typeof v!=="string"||!ID.test(v))throw Error("invalid_"+what);
  return v.toLowerCase();
}
function intInRange(v,min,max,what){
  if(!Number.isInteger(v)||v<min||v>max)throw Error("invalid_"+what);
  return v;
}
function originAndAgent(config){
  if(!config||typeof config.url!=="string"||typeof config.token!=="string"||
      config.token.length<42)throw Error("invalid_agent_config");
  const u=new URL(config.url);
  if(u.protocol!=="https:"||u.username||u.password||u.search||u.hash||
     u.pathname!=="/"||u.port)throw Error("invalid_cam_origin");
  return {origin:u.origin,agentId:ensureIdentifier(config.agentId,"agent_id")};
}
function requestPacket(config,lease){
  const {origin,agentId}=originAndAgent(config);
  if(lease===null||typeof lease!=="object"||Array.isArray(lease)||
     Object.keys(lease).sort().join(",")!=="lease_generation,lease_id,task_id") {
    throw Error("invalid_lease_packet");
  }
  const taskId=ensureIdentifier(lease.task_id,"task_id");
  const leaseId=ensureIdentifier(lease.lease_id,"lease_id");
  const generation=intInRange(lease.lease_generation,1,1000000,"lease_generation");
  const path=origin+"/api/v1/branding-dna/workers/"+agentId+"/scan-tasks/"+taskId;
  return {path,leaseId,generation,taskId};
}
async function submit(config,lease,kind,extra,requester){
  const {path,leaseId,generation,taskId}=requestPacket(config,lease);
  const options={
    method:"POST",
    headers:{Authorization:"Bearer "+config.token,"Content-Type":"application/json",Accept:"application/json"},
    body:JSON.stringify({lease_id:leaseId,lease_generation:generation,...extra}),
    redirect:"error",signal:AbortSignal.timeout(15000),
  };
  const res=await requester(path+"/"+kind,options);
  if(res.status===401||res.status===403)throw Error("enrollment_revoked");
  if(!res.ok)throw Error("scan_callback_http_"+res.status);
  const data=await res.json();
  if(data?.accepted!==true||data?.scan_enabled!==false||data?.task_id!==taskId||
     data?.lease_generation!==undefined&&data.lease_generation!==generation){
    throw Error("invalid_scan_callback_ack");
  }
  return data;
}

export async function checkpointLease(config,lease,checkpointSeq,requester=fetch){
  intInRange(checkpointSeq,0,1000000,"checkpoint_seq");
  const data=await submit(config,lease,"checkpoint",{checkpoint_seq:checkpointSeq},requester);
  if(data.checkpoint_seq!==checkpointSeq||data.lease_generation!==lease.lease_generation){
    throw Error("invalid_scan_callback_ack");
  }
  return data;
}

export async function failLease(config,lease,errorCode,requester=fetch){
  if(!FAILURE_CODES.has(errorCode))throw Error("invalid_error_code");
  const data=await submit(config,lease,"failure",{error_code:errorCode},requester);
  if(!["retry","blocked"].includes(data.status)||
     (data.status==="blocked"&&data.retry_after!==null)){
    throw Error("invalid_scan_callback_ack");
  }
  return data;
}
