import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,rmSync,symlinkSync,writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {DatabaseSync} from "node:sqlite";
import {
  MAX_SCAN_RESULT_EVENTS,MAX_SCAN_RESULT_BYTES,defaultScanResultOutboxPath,
  openScanResultOutbox,flushScanResultsOnce,
} from "../src/scan_result_outbox.mjs";
import {verifyResultAck} from "../src/scan_result_delivery.mjs";

const config={
 url:"https://creative-assets.ddns.net",
 agentId:"12345678-1111-4111-8111-123456789012",
 token:"x".repeat(44),
};
const lease={
 task_id:"12345678-2222-4222-8222-123456789012",
 lease_id:"12345678-3333-4333-8333-123456789012",
 lease_generation:2,
};
const target={
 shop_id:"12345678-4444-4444-8444-123456789012",
 snapshot_id:"12345678-5555-4555-8555-123456789012",
};
const clock=new Date("2026-10-18T05:00:00.000Z");
function manifest(){
 return {
  listing_key:"Listing_001",observed_revision:"revision-2",
  captured_at:"2026-10-18T04:55:00Z",page_count:1,
  images:[{sequence_index:0,asset_sha256:"a".repeat(64),
    source_url_sha256:"b".repeat(64),role:"hero",rights_state:"unknown"}],
 };
}
function temp(){
 const dir=mkdtempSync(join(tmpdir(),"bdna-result-outbox-"));
 return {dir,file:join(dir,"result.sqlite"),
  cleanup:()=>rmSync(dir,{recursive:true,force:true})};
}
function ack(entry,extra={}){
 return {
  accepted:true,
  client_receipt_id:entry.client_receipt_id,
  client_payload_sha256:entry.client_payload_sha256,
  task_id:entry.task_id,lease_id:entry.lease_id,
  lease_generation:entry.lease_generation,
  shop_id:entry.shop_id,snapshot_id:entry.snapshot_id,
  status:"quarantined_unverified",
  receipt_id:"12345678-6666-4666-8666-123456789012",
  replayed:false,persisted:true,receipt_issued:true,
  provenance_verified:false,
  marketplace_authorization_verified:false,scan_enabled:false,
  ...extra,
 };
}

test("result outbox path is per-agent, never derived from shop URLs",()=>{
 const root=join(tmpdir(),"result-owned-root");
 const path=defaultScanResultOutboxPath({
  LOCALAPPDATA:root,XDG_STATE_HOME:root,
 },config.agentId);
 assert.equal(path.endsWith(join(config.agentId,"scan-result-outbox.sqlite")),true);
 assert.throws(()=>defaultScanResultOutboxPath({LOCALAPPDATA:root},"../../escape"));
 assert.equal(MAX_SCAN_RESULT_EVENTS,200);
 assert.equal(MAX_SCAN_RESULT_BYTES,8192);
});

test("durable restart/replay and server ACK require exact receipt and lease identity",async()=>{
 const t=temp();
 try{
  let box=openScanResultOutbox(t.file);
  assert.equal(box.enqueue(lease,target,manifest(),"Listing_001",clock),true);
  assert.equal(box.enqueue(lease,target,manifest(),"Listing_001",clock),false);
  assert.equal(box.count(),1);
  assert.throws(()=>box.enqueue({...lease,lease_id:randomUUID()},target,manifest(),
    "Listing_001",clock),/conflicting_scan_result_event/);
  assert.throws(()=>box.enqueue(lease,{...target,shop_id:randomUUID()},manifest(),
    "Listing_001",clock),/conflicting_scan_result_event/);
  assert.throws(()=>box.enqueue(lease,target,{...manifest(),page_count:2},
    "Listing_001",clock),/conflicting_scan_result_event/);
  box.close();
  box=openScanResultOutbox(t.file);
  assert.equal(box.count(),1);
  const entry=box.batch()[0];
  assert.equal(entry.client_receipt_id.length,36);
  assert.equal(entry.client_payload_sha256.length,64);
  assert.deepEqual(entry.manifest,{
   ...manifest(),captured_at:"2026-10-18T04:55:00.000Z",
  });
  assert.equal("source_url" in entry,false);
  assert.equal("token" in entry,false);
  assert.throws(()=>box.ack(entry),/invalid_result_server_ack/);
  assert.equal(box.count(),1);
  let requests=0;
  const result=await flushScanResultsOnce(box,config,async(url,opts)=>{
   requests++;
   assert.equal(url,config.url+"/api/v1/branding-dna/workers/"+config.agentId+
     "/scan-tasks/"+lease.task_id+"/result");
   assert.equal(opts.redirect,"error");
   assert.equal(opts.method,"POST");
   assert.equal(opts.headers.Authorization,"Bearer "+config.token);
   assert.equal(opts.body.includes("https://www.etsy.com"),false);
   assert.deepEqual(JSON.parse(opts.body),entry);
   return {ok:true,status:200,json:async()=>ack(entry)};
  });
  assert.equal(requests,1);
  assert.deepEqual(result,{acknowledged:1,remaining:0});
  assert.deepEqual(await flushScanResultsOnce(box,config,async()=>{
   throw Error("outbox empty — must not call network");
  }),{acknowledged:0,remaining:0});
  box.close();
 }finally{t.cleanup()}
});

test("lost ACK, fake digest, incomplete server response, and 401/409/404 all retain data",async()=>{
 const t=temp();const box=openScanResultOutbox(t.file);
 try{
  box.enqueue(lease,target,manifest(),"Listing_001",clock);
  const entry=box.batch()[0];
  const reply=(res)=>flushScanResultsOnce(box,config,async()=>res);
  await assert.rejects(()=>flushScanResultsOnce(box,config,async()=>{
   throw Error("network_offline");
  }),/network_offline/);
  for(const status of [404,409,401,403,503]){
   await assert.rejects(()=>reply({ok:false,status}),
     status===401||status===403?/result_worker_authorization_revoked/:
      new RegExp("result_ack_http_"+status));
   assert.equal(box.count(),1);
  }
  for(const res of [
   {manifest_sha256:entry.client_payload_sha256,scan_enabled:false},
   {...ack(entry),client_payload_sha256:"c".repeat(64)},
   {...ack(entry),client_receipt_id:randomUUID()},
   {...ack(entry),task_id:randomUUID()},
   {...ack(entry),lease_generation:3},
   {...ack(entry),status:"verified"},
   {...ack(entry),scan_enabled:true},
   {...ack(entry),receipt_issued:false},
   {...ack(entry),provenance_verified:true},
   {...ack(entry),persisted:false},
   {...ack(entry),marketplace_authorization_verified:true},
   {...ack(entry),receipt_id:"https://evil.example"},
   {...ack(entry),unexpected:"src"},
  ]){
   await assert.rejects(()=>reply({
    ok:true,status:200,json:async()=>res,
   }),/invalid_result_server_ack|invalid_result_server_receipt_id/);
   assert.equal(box.count(),1);
  }
  assert.throws(()=>verifyResultAck(entry,{...ack(entry),replayed:"true"}),
                /invalid_result_server_ack/);
  assert.equal(box.count(),1);
 }finally{box.close();t.cleanup()}
});

test("enqueue strictly forbids URL/bytes/credentials/forged rights and corrupt local DB",()=>{
 const t=temp();
 try{
  const box=openScanResultOutbox(t.file);
  for(const illegal of [
   {...manifest(),images:[{...manifest().images[0],rights_state:"verified"}]},
   {...manifest(),raw_url:"https://evil.example"},
   {...manifest(),images:[{...manifest().images[0],media_bytes:"iVBOR"}]},
   {...manifest(),images:[{...manifest().images[0],sequence_index:1}]},
   {...manifest(),page_count:9},
  ])assert.throws(()=>box.enqueue(lease,target,illegal,"Listing_001",clock));
  assert.throws(()=>box.enqueue({...lease,endpoint:"https://evil.test"},target,
    manifest(),"Listing_001",clock),/invalid_result_lease_fields/);
  assert.throws(()=>box.enqueue(lease,{...target,token:"secret"},manifest(),
    "Listing_001",clock),/invalid_result_shop_snapshot_fields/);
  assert.equal(box.count(),0);
  box.enqueue(lease,target,manifest(),"Listing_001",clock);
  assert.throws(()=>box.batch(100),/invalid_scan_result_batch_size/);
  box.close();
  const db=new DatabaseSync(t.file);
  db.prepare("UPDATE result_events SET client_payload_sha256=?").run("f".repeat(64));
  db.close();
  const restarted=openScanResultOutbox(t.file);
  assert.throws(()=>restarted.batch(),/result_outbox_corrupt_payload/);
  assert.equal(restarted.count(),1);
  restarted.close();
  const linked=join(t.dir,"linked.sqlite");
  symlinkSync(t.file,linked);
  assert.throws(()=>openScanResultOutbox(linked),/symlink/);
 }finally{t.cleanup()}
});

test("hard-cap stops new events but never deletes existing pending results",()=>{
 const t=temp();const box=openScanResultOutbox(t.file);
 try{
  const input=manifest();
  for(let i=0;i<MAX_SCAN_RESULT_EVENTS;i++){
   box.enqueue({...lease,task_id:randomUUID()},target,input,
    "Listing_001",clock);
  }
  assert.equal(box.count(),MAX_SCAN_RESULT_EVENTS);
  assert.throws(()=>box.enqueue({...lease,task_id:randomUUID()},target,input,
   "Listing_001",clock),/scan_result_outbox_full/);
  assert.equal(box.count(),MAX_SCAN_RESULT_EVENTS);
  assert.equal(box.batch().length,10);
 }finally{box.close();t.cleanup()}
});

test("invalid agent config prevents submission before any network request",async()=>{
 const t=temp();const box=openScanResultOutbox(t.file);
 try{
  box.enqueue(lease,target,manifest(),"Listing_001",clock);
  let calls=0;
  for(const url of ["http://localhost:7777","https://evil.test/path",
                    "https://user:pass@evil.test","https://example.com:443"]){
   await assert.rejects(()=>flushScanResultsOnce(box,{...config,url},async()=>{
    calls++;throw Error("unexpected_network");
   }),/invalid_result_cam_origin/);
  }
  assert.equal(calls,0);
  assert.equal(box.count(),1);
 }finally{box.close();t.cleanup()}
});
