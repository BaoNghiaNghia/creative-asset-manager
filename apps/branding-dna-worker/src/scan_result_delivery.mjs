/** Isolated future result transport. No current CAM result ACK route exists.
 * In particular, never POST to /scan-results/preview: it is read-only,
 * emits a digest but explicitly does not issue a receipt or persist evidence.
 * Not imported from the production heartbeat/browser pilot.
 */
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HEX=/^[a-f0-9]{64}$/;
const EVENT_FIELDS=["client_receipt_id","client_payload_sha256","task_id","lease_id",
 "lease_generation","shop_id","snapshot_id","manifest"];
const ACK_FIELDS=["accepted","client_receipt_id","client_payload_sha256",
 "task_id","lease_id","lease_generation","shop_id","snapshot_id",
 "status","receipt_id","replayed","persisted","receipt_issued",
 "provenance_verified","marketplace_authorization_verified","scan_enabled"];

export function uuid(value,what){
 if(typeof value!=="string"||!UUID.test(value))throw Error("invalid_"+what);
 return value.toLowerCase();
}
export function exactKeys(object,keys){
 return object!==null && typeof object==="object" && !Array.isArray(object) &&
  (Object.getPrototypeOf(object)===Object.prototype ||
   Object.getPrototypeOf(object)===null) &&
  Object.keys(object).sort().join("|")===keys.slice().sort().join("|");
}
export function validateResultEvent(entry){
 if(!exactKeys(entry,EVENT_FIELDS))throw Error("invalid_result_event_fields");
 const taskId=uuid(entry.task_id,"task_id");
 const leaseId=uuid(entry.lease_id,"lease_id");
 const shopId=uuid(entry.shop_id,"shop_id");
 const snapshotId=uuid(entry.snapshot_id,"snapshot_id");
 const clientReceiptId=uuid(entry.client_receipt_id,"client_receipt_id");
 const gen=entry.lease_generation;
 if(!Number.isInteger(gen)||gen<1||gen>1000000)throw Error("invalid_result_lease_generation");
 if(typeof entry.client_payload_sha256!=="string"||
    !HEX.test(entry.client_payload_sha256))throw Error("invalid_result_payload_hash");
 return {taskId,leaseId,shopId,snapshotId,clientReceiptId,gen};
}
function normalizeConfig(config){
 if(!config||typeof config.url!=="string"||typeof config.token!=="string"||
    config.token.length<42)throw Error("invalid_result_agent_config");
 let url;
 try{url=new URL(config.url)}catch{throw Error("invalid_result_cam_origin")}
 if(url.protocol!=="https:"||url.username||url.password||url.search||
    url.hash||url.pathname!=="/"||url.port||url.origin!==config.url){
   throw Error("invalid_result_cam_origin");
 }
 return {origin:url.origin,agentId:uuid(config.agentId,"agent_id")};
}
export function verifyResultAck(entry,ack){
 const ids=validateResultEvent(entry);
 if(!exactKeys(ack,ACK_FIELDS)||
    ack.accepted!==true||ack.scan_enabled!==false||
    ack.persisted!==true||ack.receipt_issued!==true||
    ack.provenance_verified!==false||
    ack.marketplace_authorization_verified!==false||
    ack.status!=="quarantined_unverified"||
    typeof ack.replayed!=="boolean"||
    ack.task_id!==ids.taskId||
    ack.lease_id!==ids.leaseId||
    ack.lease_generation!==ids.gen||
    ack.shop_id!==ids.shopId||
    ack.snapshot_id!==ids.snapshotId||
    ack.client_receipt_id!==ids.clientReceiptId||
    ack.client_payload_sha256!==entry.client_payload_sha256){
   throw Error("invalid_result_server_ack");
 }
 uuid(ack.receipt_id,"result_server_receipt_id");
 return ack;
}
export async function submitFutureScanResult(config,entry,requester=fetch){
 const ids=validateResultEvent(entry);
 const {origin,agentId}=normalizeConfig(config);
 const path=origin+"/api/v1/branding-dna/workers/"+agentId+
  "/scan-tasks/"+ids.taskId+"/result";
 // No such route is installed on CAM: a 404 MUST retain the local outbox.
 const response=await requester(path,{
  method:"POST",
  headers:{Authorization:"Bearer "+config.token,
           "Content-Type":"application/json",Accept:"application/json"},
  redirect:"error",signal:AbortSignal.timeout(15000),
  body:JSON.stringify(entry),
 });
 if(response.status===401||response.status===403)
   throw Error("result_worker_authorization_revoked");
 if(!response.ok)throw Error("result_ack_http_"+response.status);
 const ack=await response.json();
 return verifyResultAck(entry,ack);
}
