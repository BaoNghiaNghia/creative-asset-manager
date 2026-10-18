/** Future-only durable metadata result outbox. Not imported from heartbeat.
 *
 * SQLite WAL + synchronous FULL commits before any attempted HTTP send. An
 * incomplete/invalid server ACK NEVER deletes a queued result. Data contains
 * hashes and bounded listing metadata only: no URLs, page HTML, image bytes,
 * browser credentials or source media.
 */
import {DatabaseSync} from "node:sqlite";
import {createHash,randomUUID} from "node:crypto";
import {existsSync,lstatSync,mkdirSync} from "node:fs";
import {homedir} from "node:os";
import {dirname,join} from "node:path";
import {validateFutureResultManifest} from "./scan_result_manifest.mjs";
import {exactKeys,uuid,submitFutureScanResult,validateResultEvent,verifyResultAck} from "./scan_result_delivery.mjs";

export const MAX_SCAN_RESULT_EVENTS=200;
export const MAX_SCAN_RESULT_BYTES=8192;
const TARGET_FIELDS=["shop_id","snapshot_id"];
const LEASE_FIELDS=["task_id","lease_id","lease_generation"];
const LIMIT_BATCH=10;

export function defaultScanResultOutboxPath(env,agentId){
 const id=uuid(agentId,"agent_id");
 const root=process.platform==="win32"?env.LOCALAPPDATA:
  (env.XDG_STATE_HOME||join(homedir(),".local","state"));
 if(typeof root!=="string"||!root.trim())throw Error("local_result_state_directory_required");
 return join(root,"CreativeAssetManager","BrandingDNA",id,"scan-result-outbox.sqlite");
}

function packet(lease,target,manifest,expectedListingKey,clock){
 if(!exactKeys(lease,LEASE_FIELDS))throw Error("invalid_result_lease_fields");
 if(!exactKeys(target,TARGET_FIELDS))throw Error("invalid_result_shop_snapshot_fields");
 const taskId=uuid(lease.task_id,"task_id");
 const leaseId=uuid(lease.lease_id,"lease_id");
 const shopId=uuid(target.shop_id,"shop_id");
 const snapshotId=uuid(target.snapshot_id,"snapshot_id");
 const gen=lease.lease_generation;
 if(!Number.isInteger(gen)||gen<1||gen>1000000)throw Error("invalid_result_lease_generation");
 const valid=validateFutureResultManifest(manifest,expectedListingKey,clock);
 const payload={
  task_id:taskId,lease_id:leaseId,lease_generation:gen,
  shop_id:shopId,snapshot_id:snapshotId,manifest:valid,
 };
 const json=JSON.stringify(payload);
 if(Buffer.byteLength(json)>MAX_SCAN_RESULT_BYTES)throw Error("result_payload_too_large");
 const sha=createHash("sha256").update(json).digest("hex");
 return {...payload,client_payload_sha256:sha};
}

function validatedRow(row){
 const entry={
  client_receipt_id:row.client_receipt_id,
  client_payload_sha256:row.client_payload_sha256,
  ...JSON.parse(row.payload_json),
 };
 const ids=validateResultEvent(entry);
 const content={
  task_id:ids.taskId,lease_id:ids.leaseId,lease_generation:ids.gen,
  shop_id:ids.shopId,snapshot_id:ids.snapshotId,
  manifest:entry.manifest,
 };
 const bytes=JSON.stringify(content);
 if(Buffer.byteLength(bytes)>MAX_SCAN_RESULT_BYTES||
    createHash("sha256").update(bytes).digest("hex")!==entry.client_payload_sha256)
   throw Error("result_outbox_corrupt_payload");
 // Recheck schema rather than trusting a mutable local SQLite file.
 const valid=validateFutureResultManifest(entry.manifest,entry.manifest?.listing_key,
   new Date(entry.manifest?.captured_at));
 if(JSON.stringify(valid)!==JSON.stringify(entry.manifest))
   throw Error("result_outbox_noncanonical_manifest");
 return entry;
}

export function openScanResultOutbox(filename){
 if(typeof filename!=="string"||!filename.endsWith(".sqlite"))
   throw Error("invalid_result_outbox_path");
 const dir=dirname(filename);
 mkdirSync(dir,{recursive:true,mode:0o700});
 if(lstatSync(dir).isSymbolicLink()||
    existsSync(filename)&&lstatSync(filename).isSymbolicLink())
   throw Error("result_outbox_symlink_forbidden");
 const db=new DatabaseSync(filename,{open:true});
 db.exec("PRAGMA busy_timeout=5000");
 db.exec("PRAGMA journal_mode=WAL");
 db.exec("PRAGMA synchronous=FULL");
 db.exec(`CREATE TABLE IF NOT EXISTS result_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  lease_id TEXT NOT NULL,
  lease_generation INTEGER NOT NULL,
  shop_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  client_receipt_id TEXT NOT NULL UNIQUE,
  client_payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(task_id,lease_generation)
 )`);
 const find=db.prepare("SELECT * FROM result_events WHERE task_id=? AND lease_generation=?");
 const insert=db.prepare(`INSERT INTO result_events(task_id,lease_id,lease_generation,shop_id,
  snapshot_id,client_receipt_id,client_payload_sha256,payload_json)
  VALUES(?,?,?,?,?,?,?,?)`);
 const remove=db.prepare(`DELETE FROM result_events WHERE task_id=? AND lease_id=?
  AND lease_generation=? AND client_receipt_id=? AND client_payload_sha256=?`);
 const count=db.prepare("SELECT COUNT(*) AS n FROM result_events");
 const batch=db.prepare("SELECT * FROM result_events ORDER BY sequence LIMIT ?");
 return {
  enqueue(lease,target,manifest,expectedListingKey,clock=new Date()){
   const input=packet(lease,target,manifest,expectedListingKey,clock);
   const {client_payload_sha256,...body}=input;
   db.exec("BEGIN IMMEDIATE");
   try{
    const old=find.get(input.task_id,input.lease_generation);
    if(old){
     if(old.lease_id!==input.lease_id||old.shop_id!==input.shop_id||
        old.snapshot_id!==input.snapshot_id||
        old.client_payload_sha256!==client_payload_sha256)
       throw Error("conflicting_scan_result_event");
     // Detect corrupted local records on replay rather than treating
     // mismatched SQLite metadata as successful persistence.
     validatedRow(old);
     db.exec("COMMIT");
     return false;
    }
    if(count.get().n>=MAX_SCAN_RESULT_EVENTS)
      throw Error("scan_result_outbox_full");
    const id=randomUUID();
    const json=JSON.stringify(body);
    insert.run(input.task_id,input.lease_id,input.lease_generation,
      input.shop_id,input.snapshot_id,id,client_payload_sha256,json);
    db.exec("COMMIT");
    return true;
   }catch(error){db.exec("ROLLBACK");throw error}
  },
  batch(limit=LIMIT_BATCH){
   if(!Number.isInteger(limit)||limit<1||limit>LIMIT_BATCH)
     throw Error("invalid_scan_result_batch_size");
   return batch.all(limit).map(validatedRow);
  },
  ack(entry,serverAck){
   verifyResultAck(entry,serverAck);
   const ids=validateResultEvent(entry);
   db.exec("BEGIN IMMEDIATE");
   try{
    const changed=remove.run(ids.taskId,ids.leaseId,ids.gen,
     ids.clientReceiptId,entry.client_payload_sha256).changes;
    if(changed!==1)throw Error("result_outbox_ack_mismatch");
    db.exec("COMMIT");
   }catch(error){db.exec("ROLLBACK");throw error}
  },
  count(){return count.get().n},
  close(){db.close()},
 };
}
export async function flushScanResultsOnce(outbox,config,requester=fetch){
 let acknowledged=0;
 for(const entry of outbox.batch()){
  // The server route intentionally does not exist yet. A 404/409 leaves
  // the result pending. A fake preview digest is never accepted as an ACK.
  const ack=await submitFutureScanResult(config,entry,requester);
  outbox.ack(entry,ack);
  acknowledged++;
 }
 return {acknowledged,remaining:outbox.count()};
}
