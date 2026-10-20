import test from "node:test";
import assert from "node:assert/strict";
import {fetchConfiguredShop,runCamShopPilot,summarizeCamShopPreflight,readPairingFile} from "../src/cam_shop_pilot.mjs";
import {mkdtempSync,writeFileSync,rmSync} from "node:fs";
import {join} from "node:path";
import {tmpdir} from "node:os";

const id="12345678-1234-1234-1234-123456789abc";
const config={origin:"https://creative-assets.ddns.net",id,token:"fake-local-test-only-token"};
const shop={id,platform:"etsy",storefront_url:"https://www.etsy.com/shop/Embroly",
 status:"active",access_mode:"owned"};
const library={scan_enabled:false,dispatch:{state:"disabled",active_scan_jobs:0},
 items:[shop],count:1};
const request=async(url,options)=>{
 assert.equal(options.method,"GET");
 assert.match(url,/\/scout-agents\/.*\/shops$/);
 assert.equal(options.headers.Authorization,"Bearer "+config.token);
 return {status:200,ok:true,json:async()=>library};
};
const approved={platform:"etsy",shop_url:shop.storefront_url,browser_access_approved:true,
 source_rights_reviewed:true,platform_terms_reviewed:true,robots_policy_reviewed:true,
 approved_by:"CAM operator",approved_at:new Date().toISOString(),
 etsy_written_authorization:true,etsy_authorization_reference:"Written consent ABC123"};


test("read-only preflight summarizes CAM shops without ever opening a browser",()=>{
 const summary=summarizeCamShopPreflight([
  shop,{...shop,id:"12345678-1234-1234-1234-123456789abd",
   platform:"amazon",storefront_url:"https://www.amazon.com/stores/Acme",
   status:"draft",access_mode:"competitor_research"},
  {...shop,id:"12345678-1234-1234-1234-123456789abe",
   storefront_url:"http://localhost/shop/Bad",status:"draft",access_mode:"unknown"},
 ]);
 assert.deepEqual(summary,{shops:3,amazon:1,etsy:2,inactive:2,
  not_owned_or_authorized:2,invalid_shop_url:1,browser_jobs_ready:0,
  reason:"approval_and_dispatch_not_deployed"});
});
test("uses existing on-machine Scout pairing file, never writes or prints secrets",()=>{
 const dir=mkdtempSync(join(tmpdir(),"cam-pairing-test-"));
 try{
  const file=join(dir,"scout.local.env");
  writeFileSync(file,"# comment\nRRUGC_AGENT_ID="+id+"\nRRUGC_SCOUT_TOKEN=\"test-secret-foo\"\nBRANDING_DNA_SERVER_URL=https://creative-assets.ddns.net\nIGNORED_KEY=123\n");
  assert.deepEqual(readPairingFile(file),{
   RRUGC_AGENT_ID:id,RRUGC_SCOUT_TOKEN:"test-secret-foo",
   BRANDING_DNA_SERVER_URL:"https://creative-assets.ddns.net",
  });
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("uses only CAM Shop Library instead of local duplicated shop URLs",async()=>{
 const value=await fetchConfiguredShop(config,id,request);
 assert.deepEqual(value,{id,platform:"etsy",shopUrl:shop.storefront_url});
});
test("rejects undeclared, inactive, foreign, malformed, and incompatible shop metadata",async()=>{
 for(const bad of [
  {...library,items:[{...shop,status:"draft"}]},
  {...library,items:[{...shop,access_mode:"competitor_research"}]},
  {...library,items:[{...shop,storefront_url:"https://127.0.0.1/admin"}]},
  {...library,items:[{...shop,storefront_url:"https://www.etsy.com/shop/Embroly?secret=1"}]},
  {...library,items:[shop,shop],count:2},
  {...library,scan_enabled:true},
  {...library,dispatch:{state:"enabled",active_scan_jobs:1}},
 ])await assert.rejects(()=>fetchConfiguredShop(config,id,async()=>({
   ok:true,status:200,json:async()=>bad,
 })));
 await assert.rejects(()=>fetchConfiguredShop(config,"not-a-uuid",request),/invalid_cam_shop_id/);
 await assert.rejects(()=>fetchConfiguredShop(config,id,async()=>({status:401,ok:false})),/scout_pairing_revoked/);
});
test("does not launch browser without exact reviewed platform approval",async()=>{
 let launches=0;
 const pilot=async()=>{launches++;return {status:"preview_completed"}};
 for(const bad of [null,{...approved,etsy_written_authorization:false},
   {...approved,shop_url:"https://www.etsy.com/shop/OtherShop"},
   {...approved,approved_at:"2023-01-01T00:00:00Z"}]){
  await assert.rejects(()=>runCamShopPilot(config,{shopId:id,approval:bad,profileDir:"C:\\pilot"},{
   request,pilot,
  }));
 }
 assert.equal(launches,0);
});
test("bounded browser pilot runs only on CAM shop with per-shop approval and no CAM upload",async()=>{
 const calls=[];
 const result=await runCamShopPilot(config,{shopId:id,approval:approved,
   profileDir:"C:\\PilotStore",maxProducts:2},{
  request,
  pilot:async(opts)=>{calls.push(opts);return {status:"preview_completed",observed_count:2,
   evidence_committed:false,scan_enabled:false};},
 });
 assert.equal(calls.length,1);
 assert.equal(calls[0].shopUrl,shop.storefront_url);
 assert.equal(calls[0].maxProducts,2);
 assert.equal(calls[0].interactionMode,"visible_click");
 assert.equal(calls[0].authorization.etsy_written_authorization,true);
 assert.deepEqual(result,{status:"preview_completed",observed_count:2,
  evidence_committed:false,scan_enabled:false});
});
