/** Durable metadata-only failure outbox for future, authorized scan leases.
 * Deliberately NOT imported by the heartbeat loop or browser pilot.
 */
import { DatabaseSync } from "node:sqlite";
import { existsSync, lstatSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { failLease } from "./scan_lease_protocol.mjs";

const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CODES=new Set(["temporary_network_error","rate_limited","source_unavailable",
                     "parser_changed","manual_check_required"]);
export const MAX_SCAN_FAILURE_EVENTS = 500;
function uuid(value) {
  if(typeof value!=="string" || !ID.test(value)) throw Error("invalid_scan_failure_identifier");
  return value.toLowerCase();
}
function normalized(lease,errorCode){
  if(!lease || typeof lease!=="object" || Array.isArray(lease) ||
     Object.keys(lease).sort().join(",")!=="lease_generation,lease_id,task_id") {
    throw Error("invalid_scan_failure_lease");
  }
  const generation=lease.lease_generation;
  if(!Number.isInteger(generation)||generation<1||generation>1000000) {
    throw Error("invalid_scan_failure_generation");
  }
  if(!CODES.has(errorCode))throw Error("invalid_scan_failure_code");
  return {task_id:uuid(lease.task_id),lease_id:uuid(lease.lease_id),
          lease_generation:generation,error_code:errorCode};
}
export function defaultScanFailureOutboxPath(env,agentId){
  const id=uuid(agentId);
  const stateRoot=process.platform==="win32" ? env.LOCALAPPDATA :
    (env.XDG_STATE_HOME || join(homedir(),".local","state"));
  if(typeof stateRoot!=="string"||!stateRoot)throw Error("local_state_directory_required");
  return join(stateRoot,"CreativeAssetManager","BrandingDNA",id,"scan-failure-outbox.sqlite");
}
export function openScanFailureOutbox(filename){
  if(typeof filename!=="string"||!filename.endsWith(".sqlite"))throw Error("invalid_scan_outbox_path");
  const dir=dirname(filename);
  mkdirSync(dir,{recursive:true,mode:0o700});
  if(lstatSync(dir).isSymbolicLink() || existsSync(filename)&&lstatSync(filename).isSymbolicLink()) {
    throw Error("scan_outbox_symlink_forbidden");
  }
  const db=new DatabaseSync(filename,{open:true});
  db.exec("PRAGMA busy_timeout=5000");
  db.exec("PRAGMA journal_mode=WAL");
  db.exec("PRAGMA synchronous=FULL");
  db.exec(`CREATE TABLE IF NOT EXISTS failures (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    lease_generation INTEGER NOT NULL,
    error_code TEXT NOT NULL,
    UNIQUE(task_id,lease_generation)
  )`);
  const find=db.prepare("SELECT * FROM failures WHERE task_id=? AND lease_generation=?");
  const insert=db.prepare("INSERT INTO failures(task_id,lease_id,lease_generation,error_code) VALUES(?,?,?,?)");
  const count=db.prepare("SELECT COUNT(*) AS n FROM failures");
  const batch=db.prepare("SELECT task_id,lease_id,lease_generation,error_code FROM failures ORDER BY sequence LIMIT ?");
  const remove=db.prepare("DELETE FROM failures WHERE task_id=? AND lease_id=? AND lease_generation=? AND error_code=?");
  return {
    enqueue(lease,errorCode){
      const e=normalized(lease,errorCode);
      db.exec("BEGIN IMMEDIATE");
      try{
        const old=find.get(e.task_id,e.lease_generation);
        if(old){
          if(old.lease_id!==e.lease_id||old.error_code!==e.error_code)
            throw Error("conflicting_scan_failure_receipt");
          db.exec("COMMIT");
          return false;
        }
        if(count.get().n>=MAX_SCAN_FAILURE_EVENTS)throw Error("scan_failure_outbox_full");
        insert.run(e.task_id,e.lease_id,e.lease_generation,e.error_code);
        db.exec("COMMIT");
        return true;
      }catch(error){db.exec("ROLLBACK");throw error;}
    },
    batch(limit=25){
      if(!Number.isInteger(limit)||limit<1||limit>25)throw Error("invalid_scan_failure_batch_limit");
      return batch.all(limit);
    },
    ack(entry){
      const e=normalized({
        task_id:entry.task_id,lease_id:entry.lease_id,
        lease_generation:entry.lease_generation,
      },entry.error_code);
      db.exec("BEGIN IMMEDIATE");
      try{
        const n=remove.run(e.task_id,e.lease_id,e.lease_generation,e.error_code).changes;
        if(n!==1)throw Error("scan_failure_ack_mismatch");
        db.exec("COMMIT");
      }catch(error){db.exec("ROLLBACK");throw error;}
    },
    count(){return count.get().n;},
    close(){db.close();},
  };
}
export async function flushScanFailuresOnce(outbox,config,requester=fetch){
  let acknowledged=0;
  for(const e of outbox.batch()){
    const lease={task_id:e.task_id,lease_id:e.lease_id,
                 lease_generation:e.lease_generation};
    const result=await failLease(config,lease,e.error_code,requester);
    if(typeof result.replayed!=="boolean"){
      throw Error("invalid_scan_failure_receipt_ack");
    }
    outbox.ack(e);
    acknowledged++;
  }
  return {acknowledged,remaining:outbox.count()};
}
