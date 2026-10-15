import test from "node:test";
import assert from "node:assert/strict";
import {resolveSettings,heartbeatOnce} from "../src/heartbeat.mjs";
const settings={
 BRANDING_DNA_SERVER_URL:"https://creative-assets.ddns.net",
 BRANDING_DNA_AGENT_ID:"12345678-1111-4111-8111-123456789012",
 BRANDING_DNA_AGENT_TOKEN:"a".repeat(44)
};
test("validates server and token",()=>{
 assert.equal(resolveSettings(settings).agentId,settings.BRANDING_DNA_AGENT_ID);
 assert.throws(()=>resolveSettings({...settings,BRANDING_DNA_SERVER_URL:"http://localhost:8000"}));
 assert.throws(()=>resolveSettings({...settings,BRANDING_DNA_AGENT_TOKEN:"too-short"}));
});
test("heartbeat never fetches an external shop page and must remain scan-disabled",async()=>{
 let called="";
 const config=resolveSettings(settings);
 const res=await heartbeatOnce(config,async(url,options)=>{
  called=url;
  assert.equal(options.method,"POST");
  assert.equal(options.headers.Authorization,"Bearer "+settings.BRANDING_DNA_AGENT_TOKEN);
  return {ok:true,status:200,json:async()=>({status:"accepted",scan_enabled:false})};
 });
 assert.equal(res.scan_enabled,false);
 assert.ok(called.endsWith("/heartbeat"));
 assert.ok(called.startsWith("https://creative-assets.ddns.net/api/"));
});
test("rejects unexpected dispatch readiness",async()=>{
 await assert.rejects(()=>heartbeatOnce(resolveSettings(settings),async()=>({
  ok:true,status:200,json:async()=>({scan_enabled:true})
 })),/unexpected_scanning_enabled/);
});
