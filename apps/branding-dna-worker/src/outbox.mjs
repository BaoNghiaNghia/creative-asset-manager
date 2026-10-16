/** Durable local SQLite outbox for Branding DNA lifecycle events.
 * Requires Node.js >=22.13 node:sqlite. No browser or secret/media data.
 * Each INSERT and ACK deletion is committed before returning.
 */
import { DatabaseSync } from "node:sqlite";
import { randomUUID } from "node:crypto";
import { existsSync, lstatSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const MAX_OUTBOX_EVENTS = 2000;
export const MAX_BATCH = 50;

const EVENTS = Object.freeze({
  started: new Set(["worker_start"]),
  stopped: new Set(["graceful_shutdown"]),
  heartbeat_failure: new Set(["network_error", "server_4xx", "server_5xx"]),
  heartbeat_recovered: new Set(["recovered"]),
});

function validateId(value) {
  if (typeof value !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error("invalid_lifecycle_event_id");
  }
}

export function defaultOutboxPath(env, agentId) {
  validateId(agentId);
  const stateHome = process.platform === "win32"
    ? env.LOCALAPPDATA
    : env.XDG_STATE_HOME || join(homedir(), ".local", "state");
  if (!stateHome) throw new Error("local_state_directory_required");
  return join(stateHome, "CreativeAssetManager", "BrandingDNA", agentId, "worker-outbox.sqlite");
}

export function openOutbox(filename) {
  if (typeof filename !== "string" || !filename.endsWith(".sqlite")) {
    throw new Error("SQLite filename required");
  }
  const parent = dirname(filename);
  mkdirSync(parent, {recursive:true, mode:0o700});
  if (existsSync(filename) && lstatSync(filename).isSymbolicLink()) {
    throw new Error("Outbox must not be a symlink");
  }
  const db = new DatabaseSync(filename, {open:true});
  db.exec("PRAGMA busy_timeout = 5000");
  db.exec("PRAGMA journal_mode = WAL");
  db.exec("PRAGMA synchronous = FULL");
  db.exec(
    "CREATE TABLE IF NOT EXISTS pending_events (" +
      "event_id TEXT PRIMARY KEY NOT NULL," +
      "kind TEXT NOT NULL," +
      "detail_code TEXT NOT NULL," +
      "occurred_at TEXT NOT NULL," +
      "created_order INTEGER NOT NULL" +
    ");" +
    "CREATE INDEX IF NOT EXISTS ix_pending_events_order ON pending_events(created_order,event_id)"
  );
  const insert = db.prepare("INSERT INTO pending_events (event_id,kind,detail_code,occurred_at,created_order) VALUES (?,?,?,?,?)");
  const count = db.prepare("SELECT COUNT(*) AS total FROM pending_events");
  const select = db.prepare("SELECT event_id,kind,detail_code,occurred_at FROM pending_events ORDER BY created_order,event_id LIMIT ?");
  const remove = db.prepare("DELETE FROM pending_events WHERE event_id = ?");

  return {
    enqueue(kind, detailCode, occurredAt=new Date()) {
      if (!Object.hasOwn(EVENTS,kind) || !EVENTS[kind].has(detailCode)) {
        throw new Error("invalid_lifecycle_event");
      }
      if (!(occurredAt instanceof Date) || !Number.isFinite(occurredAt.getTime())) {
        throw new Error("invalid_lifecycle_timestamp");
      }
      db.exec("BEGIN IMMEDIATE");
      try {
        if (count.get().total >= MAX_OUTBOX_EVENTS) throw new Error("outbox_full");
        const eventId = randomUUID();
        // Millisecond order is stable, even across wall-clock adjustments.
        const order = db.prepare("SELECT COALESCE(MAX(created_order),0)+1 AS n FROM pending_events").get().n;
        insert.run(eventId, kind, detailCode, occurredAt.toISOString(), order);
        db.exec("COMMIT");
        return eventId;
      } catch(error) {
        db.exec("ROLLBACK");
        throw error;
      }
    },
    batch(limit=MAX_BATCH) {
      if (!Number.isInteger(limit) || limit<1 || limit>MAX_BATCH) throw new Error("invalid_batch_limit");
      return select.all(limit);
    },
    ack(ids) {
      if (!Array.isArray(ids) || ids.length>MAX_BATCH || ids.length!==new Set(ids).size) {
        throw new Error("invalid_ack_ids");
      }
      for(const id of ids) validateId(id);
      db.exec("BEGIN IMMEDIATE");
      let removed=0;
      try {
        for(const id of ids) removed += remove.run(id).changes;
        db.exec("COMMIT");
      } catch(error) {
        db.exec("ROLLBACK");
        throw error;
      }
      return removed;
    },
    count() {return count.get().total;},
    close() { db.close(); },
  };
}

export async function flushOutboxOnce(outbox, config, requester=fetch) {
  const batch=outbox.batch();
  if(!batch.length) return {sent:0,remaining:0};
  const endpoint=config.url+"/api/v1/branding-dna/workers/"+encodeURIComponent(config.agentId)+"/events";
  const result=await requester(endpoint,{
    method:"POST",
    headers:{"Authorization":"Bearer "+config.token,"Content-Type":"application/json"},
    body:JSON.stringify({events:batch}),
    redirect:"error",
    signal:AbortSignal.timeout(15000),
  });
  if(result.status===401 || result.status===403) throw new Error("enrollment_revoked");
  if(!result.ok) throw new Error("lifecycle_http_"+result.status);
  const body=await result.json();
  const expected=batch.map(item=>item.event_id);
  if(body?.scan_enabled!==false || !Array.isArray(body.accepted_ids)
      || body.accepted_ids.length!==expected.length
      || new Set(body.accepted_ids).size!==expected.length
      || expected.some(id=>!body.accepted_ids.includes(id))) {
    throw new Error("invalid_event_ack");
  }
  outbox.ack(expected);
  return {sent:expected.length,remaining:outbox.count()};
}
