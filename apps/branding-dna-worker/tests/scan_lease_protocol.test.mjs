import test from "node:test";
import assert from "node:assert/strict";
import {checkpointLease,failLease} from "../src/scan_lease_protocol.mjs";

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

test("checkpoint posts an opaque packet only to CAM, never a marketplace",async()=>{
 let count=0;
 const received=await checkpointLease(config,lease,7,async (url,opts)=>{
  count++;
  assert.equal(url,config.url+"/api/v1/branding-dna/workers/"+config.agentId+
      "/scan-tasks/"+lease.task_id+"/checkpoint");
  assert.equal(opts.method,"POST");
  assert.equal(opts.redirect,"error");
  assert.equal(opts.headers.Authorization,"Bearer "+config.token);
  assert.deepEqual(JSON.parse(opts.body),{
   lease_id:lease.lease_id,lease_generation:2,checkpoint_seq:7,
  });
  return {ok:true,status:200,json:async()=>({
   accepted:true,scan_enabled:false,task_id:lease.task_id,lease_generation:2,checkpoint_seq:7,
  })};
 });
 assert.equal(count,1);
 assert.equal(received.checkpoint_seq,7);
});

test("only fixed failure codes can be submitted",async()=>{
 let calls=0;
 const requester=async(url,opts)=>{
  calls++;
  assert.ok(url.endsWith("/failure"));
  assert.equal(JSON.parse(opts.body).error_code,"manual_check_required");
  return {ok:true,status:200,json:async()=>({
   accepted:true,scan_enabled:false,task_id:lease.task_id,status:"blocked",retry_after:null,
  })};
 };
 assert.equal((await failLease(config,lease,"manual_check_required",requester)).status,"blocked");
 await assert.rejects(()=>failLease(config,lease,"run_shell",requester),/invalid_error_code/);
 assert.equal(calls,1);
});

test("rejects arbitrary URLs, caller payloads and invalid lease metadata without making calls",async()=>{
 let calls=0;
 const requester=async()=>{calls++;throw Error("unexpected_network")};
 for(const bad of [
  {...lease,source_url:"https://example.com"},
  {...lease,lease_generation:0},
  {...lease,lease_generation:"1"},
  {...lease,task_id:"https://example.com"},
  {...lease,lease_id:"../../traversal"},
 ]) await assert.rejects(()=>checkpointLease(config,bad,0,requester));
 for(const url of ["http://localhost:3000","https://evil.test/path",
                   "https://evil.test/?x=1","https://user:pass@evil.test"])
  await assert.rejects(()=>checkpointLease({...config,url},lease,0,requester));
 await assert.rejects(()=>checkpointLease(config,lease,-1,requester));
 await assert.rejects(()=>checkpointLease(config,lease,1.5,requester));
 assert.equal(calls,0);
});

test("rejects revoked credentials, redirect responses and invalid acknowledgements",async()=>{
 await assert.rejects(()=>checkpointLease(config,lease,1,async()=>({
  ok:false,status:401,
 })),/enrollment_revoked/);
 await assert.rejects(()=>checkpointLease(config,lease,1,async()=>({
  ok:false,status:409,
 })),/scan_callback_http_409/);
 await assert.rejects(()=>checkpointLease(config,lease,1,async()=>({
  ok:true,status:200,json:async()=>({accepted:true,scan_enabled:true,
   task_id:lease.task_id,lease_generation:2,checkpoint_seq:1}),
 })),/invalid_scan_callback_ack/);
 await assert.rejects(()=>checkpointLease(config,lease,1,async()=>({
  ok:true,status:200,json:async()=>({accepted:true,scan_enabled:false,
   task_id:lease.task_id,lease_generation:2,checkpoint_seq:0}),
 })),/invalid_scan_callback_ack/);
 await assert.rejects(()=>failLease(config,lease,"rate_limited",async()=>({
  ok:true,status:200,json:async()=>({accepted:true,scan_enabled:false,
   task_id:lease.task_id,status:"completed",retry_after:null}),
 })),/invalid_scan_callback_ack/);
});
