import test from "node:test";
import assert from "node:assert/strict";
import {readCamControl,settings,run} from "../src/agent_cam.mjs";
const env={RRUGC_AGENT_ID:"72680eb2-3205-4b27-8a1f-66140db12f5b",
 RRUGC_SCOUT_TOKEN:"fake-test-token-for-local-only-unit-test-not-a-secret",
 BRANDING_DNA_SERVER_URL:"https://creative-assets.ddns.net"};
test("agent uses paired CAM credentials and never needs local shop JSON",async()=>{
 const cfg=settings(env);let calls=0;
 const result=await readCamControl(cfg,async(url,options)=>{
  calls++;
  assert.match(url,/branding-dna\/scout-agents\/.*\/shops$/);
  assert.equal(options.method,"GET");
  assert.equal(options.redirect,"error");
  assert.equal(options.headers.Authorization,"Bearer "+cfg.token);
  return {ok:true,status:200,json:async()=>({
   scan_enabled:false,items:[{platform:"amazon",storefront_url:"https://www.amazon.com/stores/Test"},{platform:"etsy",storefront_url:"https://www.etsy.com/shop/Test"}]
  })};
 });
 assert.equal(calls,1);assert.deepEqual(result,{shopCount:2,dispatchEnabled:false});
});
test("agent fail-closes server unexpectedly enabling dispatch and invalid auth",async()=>{
 const cfg=settings(env);
 for(const res of [
  {ok:true,status:200,json:async()=>({scan_enabled:true,items:[]})},
  {ok:false,status:401},
  {ok:true,status:200,json:async()=>({scan_enabled:false,items:[{platform:"other",storefront_url:"https://x"}]})},
 ]){
  await assert.rejects(()=>readCamControl(cfg,async()=>res),
    /dispatch_contract_not_audited|scout_pairing_revoked|invalid_shop_metadata/);
 }
 assert.throws(()=>settings({...env,BRANDING_DNA_SERVER_URL:"http://creative-assets.ddns.net"}),/invalid_cam_origin/);
});
test("connection loop waits, reports dispatch disabled and exits without marketplace traffic",async()=>{
 const cfg=settings(env);let stopped=false;const reports=[];let calls=0;
 await run(cfg,{
  request:async()=>{calls++;return{ok:true,status:200,json:async()=>({scan_enabled:false,items:[]})}},
  wait:async delay=>{assert.equal(delay,60000);stopped=true;},
  onStop:()=>stopped,log:line=>reports.push(JSON.parse(line))
 });
 assert.equal(calls,1);
 assert.equal(reports[0].dispatch_enabled,false);
 assert.equal(reports[0].shops,0);
 assert.ok(!JSON.stringify(reports).includes(cfg.token));
});
