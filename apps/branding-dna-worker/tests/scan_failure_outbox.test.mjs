import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  defaultScanFailureOutboxPath, openScanFailureOutbox,
  flushScanFailuresOnce, MAX_SCAN_FAILURE_EVENTS,
} from "../src/scan_failure_outbox.mjs";

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

function temp(){
 const dir=mkdtempSync(join(tmpdir(),"bdna-scan-failure-"));
 return {dir,file:join(dir,"failure.sqlite"),
         cleanup:()=>rmSync(dir,{recursive:true,force:true})};
}

test("outbox is agent-namespaced and never allows arbitrary identifier in storage path",()=>{
 const root=join(tmpdir(),"owned");
 const testEnv={LOCALAPPDATA:root,XDG_STATE_HOME:root};
 const path=defaultScanFailureOutboxPath(testEnv,config.agentId);
 assert.ok(path.endsWith(join(config.agentId,"scan-failure-outbox.sqlite")));
 assert.throws(()=>defaultScanFailureOutboxPath(testEnv,"../../secret"));
});

test("terminal failures commit to SQLite; restart replays exactly once with no shop URL",async()=>{
 const t=temp();try{
  let q=openScanFailureOutbox(t.file);
  assert.equal(q.enqueue(lease,"rate_limited"),true);
  assert.equal(q.enqueue(lease,"rate_limited"),false);
  assert.throws(()=>q.enqueue({...lease,lease_id:"11111111-2222-4222-8222-333333333333"},"rate_limited"),/conflicting/);
  assert.throws(()=>q.enqueue(lease,"parser_changed"),/conflicting/);
  assert.equal(q.count(),1);
  q.close();

  q=openScanFailureOutbox(t.file);
  assert.equal(q.count(),1);
  let calls=0;
  const result=await flushScanFailuresOnce(q,config,async(url,options)=>{
   calls++;
   assert.equal(url,config.url+"/api/v1/branding-dna/workers/"+config.agentId+
     "/scan-tasks/"+lease.task_id+"/failure");
   assert.equal(options.redirect,"error");
   assert.deepEqual(JSON.parse(options.body),{
    lease_id:lease.lease_id,lease_generation:2,error_code:"rate_limited",
   });
   return {ok:true,status:200,json:async()=>({
    accepted:true,scan_enabled:false,task_id:lease.task_id,
    status:"retry",retry_after:"2026-10-18T00:00:00+00:00",replayed:true,
   })};
  });
  assert.deepEqual(result,{acknowledged:1,remaining:0});
  assert.equal(calls,1);
  assert.deepEqual(await flushScanFailuresOnce(q,config,async()=>{throw Error("unexpected")}),
                   {acknowledged:0,remaining:0});
  q.close();
 } finally{t.cleanup();}
});

test("network errors, conflicting ACK, revoked credentials and 409 retain pending receipt",async()=>{
 const t=temp();const q=openScanFailureOutbox(t.file);
 try{
  q.enqueue(lease,"temporary_network_error");
  const outcome=async(res)=>flushScanFailuresOnce(q,config,async()=>res);
  await assert.rejects(()=>flushScanFailuresOnce(q,config,async()=>{throw Error("offline")}),/offline/);
  assert.equal(q.count(),1);
  await assert.rejects(()=>outcome({ok:false,status:409}),/scan_callback_http_409/);
  assert.equal(q.count(),1);
  await assert.rejects(()=>outcome({ok:false,status:401}),/enrollment_revoked/);
  assert.equal(q.count(),1);
  await assert.rejects(()=>outcome({ok:true,status:200,json:async()=>({
    accepted:true,scan_enabled:false,task_id:lease.task_id,
    status:"retry",retry_after:null,
  })}),/invalid_scan_failure_receipt_ack/);
  assert.equal(q.count(),1);
  await assert.rejects(()=>outcome({ok:true,status:200,json:async()=>({
    accepted:true,scan_enabled:true,task_id:lease.task_id,
    status:"blocked",retry_after:null,replayed:false,
  })}),/invalid_scan_callback_ack/);
  assert.equal(q.count(),1);
 }finally{q.close();t.cleanup();}
});

test("rejects arbitrary payload fields, illegal codes, invalid sequences and symlinked db",()=>{
 const t=temp();
 try{
  const q=openScanFailureOutbox(t.file);
  assert.throws(()=>q.enqueue({...lease,source_url:"https://evil.test"},"rate_limited"),/invalid_scan_failure_lease/);
  assert.throws(()=>q.enqueue({...lease,lease_generation:-1},"rate_limited"),/invalid_scan_failure_generation/);
  assert.throws(()=>q.enqueue({...lease,task_id:"https://evil.test"},"rate_limited"),/invalid_scan_failure_identifier/);
  assert.throws(()=>q.enqueue(lease,"eval_code"),/invalid_scan_failure_code/);
  assert.equal(q.count(),0);
  q.close();
  const link=join(t.dir,"linked.sqlite");
  symlinkSync(t.file,link);
  assert.throws(()=>openScanFailureOutbox(link),/symlink/);
  assert.equal(MAX_SCAN_FAILURE_EVENTS,500);
 }finally{t.cleanup();}
});
