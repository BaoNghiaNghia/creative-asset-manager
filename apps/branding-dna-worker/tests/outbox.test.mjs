import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync, symlinkSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {defaultOutboxPath, flushOutboxOnce, MAX_OUTBOX_EVENTS, openOutbox} from "../src/outbox.mjs";

const AGENT_ID="12345678-1111-4111-8111-123456789012";
const config={url:"https://creative-assets.ddns.net", agentId:AGENT_ID, token:"x".repeat(44)};

function withDir(fn) {
 const dir=mkdtempSync(join(tmpdir(),"bdna-outbox-"));
 try{return fn(join(dir,"agent.sqlite"),dir);}finally{rmSync(dir,{recursive:true,force:true});}
}

test("agent outbox path is private and uniquely namespaced",()=>{
 const a=defaultOutboxPath({LOCALAPPDATA:"C:\\Users\\Demo\\AppData\\Local",XDG_STATE_HOME:"/tmp/local-state"},AGENT_ID);
 assert.ok(a.includes(AGENT_ID));
 assert.ok(a.endsWith("worker-outbox.sqlite"));
 assert.throws(()=>defaultOutboxPath({LOCALAPPDATA:"C:\\"},"../../escape"),/invalid_lifecycle_event_id/);
});

test("WAL outbox persists across restart, preserves event order, no arbitrary content",()=>{
 withDir(path=>{
  const o=openOutbox(path);
  const first=o.enqueue("started","worker_start",new Date("2026-10-16T08:01:00Z"));
  const second=o.enqueue("heartbeat_failure","network_error",new Date("2026-10-16T08:02:00Z"));
  assert.equal(o.count(),2);
  o.close();
  const reopened=openOutbox(path);
  const pending=reopened.batch();
  assert.deepEqual(pending.map(x=>x.event_id),[first,second]);
  assert.deepEqual(Object.keys(pending[0]).sort(),["detail_code","event_id","kind","occurred_at"]);
  assert.equal(reopened.ack([first]),1);
  reopened.close();
  const again=openOutbox(path);
  assert.deepEqual(again.batch().map(x=>x.event_id),[second]);
  again.close();
 });
});

test("does not accept out-of-schema events or invalid ACK identities",()=>{
 withDir(path=>{
  const o=openOutbox(path);
  assert.throws(()=>o.enqueue("product_found","listing_1"),/invalid_lifecycle_event/);
  assert.throws(()=>o.enqueue("started","network_error"),/invalid_lifecycle_event/);
  assert.throws(()=>o.ack(["bad-id"]),/invalid_lifecycle_event_id/);
  const id=o.enqueue("started","worker_start");
  assert.throws(()=>o.ack([id,id]),/invalid_ack_ids/);
  assert.equal(o.count(),1);
  o.close();
 });
});

test("only exact server ACK deletes the durable local rows",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-outbox-"));
 try{
  const o=openOutbox(join(dir,"events.sqlite"));
  const first=o.enqueue("started","worker_start");
  const second=o.enqueue("heartbeat_failure","server_5xx");
  const receiver=async(url,options)=>{
   assert.ok(url.endsWith("/api/v1/branding-dna/workers/"+AGENT_ID+"/events"));
   assert.equal(options.redirect,"error");
   assert.equal(options.headers.Authorization,"Bearer "+config.token);
   const payload=JSON.parse(options.body);
   assert.equal(payload.events.length,2);
   assert.equal(Object.hasOwn(payload.events[0],"token"),false);
   return {ok:true,status:200,json:async()=>({
    accepted_ids:[second,first], scan_enabled:false,
   })};
  };
  const flushed=await flushOutboxOnce(o,config,receiver);
  assert.equal(flushed.sent,2);
  assert.equal(o.count(),0);
  assert.equal((await flushOutboxOnce(o,config,async()=>{throw Error("unreachable")})).sent,0);
  o.close();
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test("offline request, bad response and 500 retain events for replay",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-outbox-"));
 try {
  const path=join(dir,"events.sqlite");
  const o=openOutbox(path);
  const id=o.enqueue("started","worker_start");
  await assert.rejects(()=>flushOutboxOnce(o,config,async()=>{throw Error("network down")}),/network down/);
  await assert.rejects(()=>flushOutboxOnce(o,config,async()=>({ok:false,status:503})),/lifecycle_http_503/);
  await assert.rejects(()=>flushOutboxOnce(o,config,async()=>({
   ok:true,status:200,json:async()=>({scan_enabled:false,accepted_ids:[]}),
  })),/invalid_event_ack/);
  await assert.rejects(()=>flushOutboxOnce(o,config,async()=>({
   ok:true,status:200,json:async()=>({scan_enabled:true,accepted_ids:[id]}),
  })),/invalid_event_ack/);
  assert.equal(o.count(),1);
  o.close();
  const reopened=openOutbox(path);
  assert.equal(reopened.count(),1);
  const r=await flushOutboxOnce(reopened,config,async()=>({
   ok:true,status:200,json:async()=>({scan_enabled:false,accepted_ids:[id]}),
  }));
  assert.equal(r.sent,1);
  assert.equal(reopened.count(),0);
  reopened.close();
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test("credentials revoked: events stay in outbox and no scan is started",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-outbox-"));
 try{
  const o=openOutbox(join(dir,"events.sqlite"));
  o.enqueue("started","worker_start");
  await assert.rejects(()=>flushOutboxOnce(o,config,async()=>({ok:false,status:401})),/enrollment_revoked/);
  assert.equal(o.count(),1);
  o.close();
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test("hard queue cap rejects new events without deleting older ones",()=>{
 withDir(path=>{
  const o=openOutbox(path);
  for(let i=0;i<MAX_OUTBOX_EVENTS;i++)o.enqueue("heartbeat_failure","network_error");
  assert.equal(o.count(),MAX_OUTBOX_EVENTS);
  assert.throws(()=>o.enqueue("started","worker_start"),/outbox_full/);
  assert.equal(o.batch(50).length,50);
  o.close();
 });
});

test("refuses an existing symlink as a database location",()=>{
 withDir((path,dir)=>{
  const target=join(dir,"target.sqlite");
  const o=openOutbox(target);o.close();
  symlinkSync(target,path);
  assert.throws(()=>openOutbox(path),/symlink/);
 });
});
