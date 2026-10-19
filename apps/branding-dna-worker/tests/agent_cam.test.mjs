import test from "node:test";
import assert from "node:assert/strict";
import {readCamControl,readDispatchPreview,sendStandbyHeartbeat,settings,run,AGENT_VERSION} from "../src/agent_cam.mjs";
const env={RRUGC_AGENT_ID:"72680eb2-3205-4b27-8a1f-66140db12f5b",
 RRUGC_SCOUT_TOKEN:"fake-test-token-for-local-only-unit-test-not-a-secret",
 BRANDING_DNA_SERVER_URL:"https://creative-assets.ddns.net"};
const config=settings(env);
const disabled={state:"disabled",reason:"scan_task_dispatch_not_deployed",active_scan_jobs:0};
const shopsResponse={scan_enabled:false,dispatch:disabled,
 items:[{platform:"amazon",storefront_url:"https://www.amazon.com/stores/Test"},
 {platform:"etsy",storefront_url:"https://www.etsy.com/shop/Test"}]};
const previewResponse={version:1,scan_enabled:false,
 dispatch:{...disabled,marketplace_authorization_verified:false},
 shop_count:2,blocked_count:2,ready_count:0,items:[
  {shop_id:"72680eb2-3205-4b27-8a1f-66140db12f5b",platform:"amazon",status:"blocked",staged_tasks:2,
   blockers:["scan_task_dispatch_not_deployed","marketplace_authorization_not_verified"]},
  {shop_id:"72680eb2-3205-4b27-8a1f-66140db12f5c",platform:"etsy",status:"blocked",staged_tasks:0,
   blockers:["scan_task_dispatch_not_deployed","marketplace_authorization_not_verified"]},
 ]};
const heartbeatResponse={accepted:true,state:"connected_waiting",
 scan_enabled:false,active_scan_jobs:0,shop_count:2};

test("agent reads server Shop Library with paired credentials and no local shop JSON",async()=>{
 let calls=0;
 const result=await readCamControl(config,async(url,options)=>{
  calls++; assert.match(url,/branding-dna\/scout-agents\/.*\/shops$/);
  assert.equal(options.method,"GET"); assert.equal(options.redirect,"error");
  assert.equal(options.headers.Authorization,"Bearer "+config.token);
  return {ok:true,status:200,json:async()=>shopsResponse};
 });
 assert.equal(calls,1); assert.deepEqual(result,{shopCount:2,dispatchEnabled:false});
});

test("dispatch preview is strictly read-only and never includes executable work",async()=>{
 const outcome=await readDispatchPreview(config,async(url,options)=>{
  assert.match(url,/\/dispatch-preview$/);
  assert.equal(options.method,"GET");
  assert.equal(options.headers.Authorization,"Bearer "+config.token);
  return {ok:true,status:200,json:async()=>previewResponse};
 });
 assert.deepEqual(outcome,{available:true,blocked:2,stagedTasks:2});
 assert.deepEqual(await readDispatchPreview(config,async()=>({ok:false,status:404})),
  {available:false,blocked:0,stagedTasks:0});
 for(const bad of [
  {...previewResponse,scan_enabled:true},
  {...previewResponse,ready_count:1},
  {...previewResponse,items:[{...previewResponse.items[0],blockers:[]}]},
 ])await assert.rejects(()=>readDispatchPreview(config,async()=>({
  ok:true,status:200,json:async()=>bad})),/dispatch_preview_cannot_enable_scan/);
});

test("paired standby heartbeat posts no task, URLs or credentials in body",async()=>{
 const result=await sendStandbyHeartbeat(config,async(url,options)=>{
  assert.match(url,/\/branding-heartbeat$/);
  assert.equal(options.method,"POST"); assert.equal(options.redirect,"error");
  assert.equal(options.headers.Authorization,"Bearer "+config.token);
  assert.deepEqual(JSON.parse(options.body),{
   version:AGENT_VERSION,state:"connected_waiting",scan_enabled:false,active_scan_jobs:0,
  });
  assert.ok(!options.body.includes(config.token));
  assert.ok(!options.body.includes("amazon.com"));
  return {ok:true,status:200,json:async()=>heartbeatResponse};
 });
 assert.deepEqual(result,{shopCount:2});
});

test("agent fails closed on unreviewed dispatch, revoked pairing and invalid heartbeat",async()=>{
 for(const data of [
  {...shopsResponse,scan_enabled:true}, {...shopsResponse,dispatch:{...disabled,active_scan_jobs:1}},
  {...shopsResponse,items:[{platform:"other",storefront_url:"https://x"}]},
 ])await assert.rejects(()=>readCamControl(config,async()=>({
   ok:true,status:200,json:async()=>data})),
  /dispatch_contract_not_audited|invalid_shop_metadata/);
 await assert.rejects(()=>readCamControl(config,async()=>({ok:false,status:401})),
  /scout_pairing_revoked/);
 await assert.rejects(()=>sendStandbyHeartbeat(config,async()=>({
  ok:true,status:200,json:async()=>({...heartbeatResponse,scan_enabled:true})})),
  /invalid_cam_heartbeat_contract/);
 await assert.rejects(()=>sendStandbyHeartbeat(config,async()=>({ok:false,status:403})),
  /scout_pairing_revoked/);
 assert.throws(()=>settings({...env,BRANDING_DNA_SERVER_URL:"http://creative-assets.ddns.net"}),/invalid_cam_origin/);
});

test("connection loop polls and heartbeats without any marketplace requests",async()=>{
 let stopped=false;const reports=[];let calls=0;const paths=[];
 await run(config,{
  request:async(url,options)=>{
   calls++;paths.push(new URL(url).pathname);
   if(options.method==="GET" && url.endsWith("/shops"))return {ok:true,status:200,json:async()=>({...shopsResponse,items:[]})};
   if(options.method==="GET" && url.endsWith("/dispatch-preview"))return {ok:true,status:200,json:async()=>({...previewResponse,items:[],shop_count:0,blocked_count:0})};
   assert.equal(options.method,"POST");
   return{ok:true,status:200,json:async()=>({...heartbeatResponse,shop_count:0})};
  },
  wait:async delay=>{assert.equal(delay,60000);stopped=true;},
  onStop:()=>stopped,log:line=>reports.push(JSON.parse(line)),
 });
 assert.equal(calls,3);
 assert.equal(paths.every(x=>x.startsWith("/api/v1/branding-dna/")),true);
 assert.equal(reports[0].dispatch_enabled,false);
 assert.equal(reports[0].state,"connected_waiting");
 assert.equal(reports[0].shops,0);
 assert.equal(reports[0].preview_available,true);
 assert.equal(reports[0].blocked_shops,0);
 assert.ok(!JSON.stringify(reports).includes(config.token));
});