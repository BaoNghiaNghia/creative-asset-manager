import test from "node:test";
import assert from "node:assert/strict";
import {fetchConfiguredShop,runCamShopPilot,runCamShopPilotWithProgress,summarizeCamShopPreflight,readPairingFile,prepareCamObservationReport} from "../src/cam_shop_pilot.mjs";
import {mkdtempSync,writeFileSync,rmSync,readFileSync,readdirSync} from "node:fs";
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
  paused:0,invalid_shop_url:1,browser_jobs_ready:0,
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
  {...library,items:[{...shop,status:"paused"}]},
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
test("CAM-shop pilot resumes from immutable local per-shop checkpoints without rescanning",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-cam-progress-"));
 try{
  const approvalFile=join(dir,"approval.json"),reportDir=join(dir,"reports");
  writeFileSync(approvalFile,JSON.stringify(approved));
  const keys=["etsy:123456","etsy:789123"],visited=[];
  const args={shopId:id,approvalFile,profileDir:join(dir,"chrome"),reportDir,maxProducts:2,
    minimumDelayMs:10000,maximumDelayMs:18000};
  const pilot=async opts=>{
    visited.push({shop:opts.shopUrl,skip:[...opts.skipProductKeys]});
    const key=keys[visited.length-1];
    return {status:"preview_completed",platform:"etsy",observed_at:new Date().toISOString(),
      scan_enabled:false,items:[{
        listing_key:key,title:"Visible embroidered trucker hat",
        source_kind:"browser_dom_observation",source_verification:"unverified",
        rights_state:"unknown",sales_state:"unknown",
      }]};
  };
  const first=await runCamShopPilotWithProgress(config,args,{request,pilot});
  assert.equal(first.totalObserved,1);assert.equal(first.previouslySeen,0);
  assert.equal(first.status,"preview_completed");assert.equal(first.count,1);
  const second=await runCamShopPilotWithProgress(config,args,{request,pilot});
  assert.equal(second.totalObserved,2);assert.equal(second.previouslySeen,1);
  assert.deepEqual(visited.map(x=>x.skip),[[],["etsy:123456"]]);
  assert.equal(visited[0].shop,shop.storefront_url);
  const reports=readdirSync(reportDir).filter(name=>name.endsWith(".json"));
  assert.equal(reports.length,2);
  for(const filename of reports){
    const content=readFileSync(join(reportDir,filename),"utf8");
    assert.ok(!content.includes(config.token));
    assert.ok(!content.includes(shop.storefront_url));
    const saved=JSON.parse(content);
    assert.equal(saved.scan_enabled,false);
    assert.equal(saved.rights_verified,false);
    assert.equal(saved.evidence_committed,false);
  }
 }finally{rmSync(dir,{recursive:true,force:true})}
});
test("CAM shop pilot rejects missing approval before browser and preserves local state",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-cam-deny-"));let calls=0;
 try{
  await assert.rejects(()=>runCamShopPilotWithProgress(config,{
   shopId:id,approvalFile:join(dir,"missing.json"),profileDir:join(dir,"chrome"),
   reportDir:join(dir,"reports"),
  },{request,pilot:async()=>{calls++;return {};}}),/approval_file_not_present_or_untrusted/);
  assert.equal(calls,0);
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("prepares replay-stable quarantined DOM observation payload, no URLs or media",()=>{
 const shaA="a".repeat(64),shaB="b".repeat(64),shaC="c".repeat(64);
 const report={
  status:"preview_completed",platform:"etsy",shop_url_sha256:shaA,
  observed_at:"2026-10-20T10:10:00.000Z",coverage_state:"partial",
  rights_verified:false,sales_verified:false,evidence_committed:false,scan_enabled:false,
  observed_count:1,note:"ignore this nonfinal note",items:[{
   listing_key:"etsy:123456",title:"Embroidered Cap",observed_page_sha256:shaB,
   image_url_sha256:[shaC],image_count:1,source_kind:"browser_dom_observation",
   source_verification:"unverified",sales_state:"unknown",rights_state:"unknown",
   coverage_state:"partial",
  }],
 };
 const payload=prepareCamObservationReport(report);
 assert.equal(payload.report_key,prepareCamObservationReport(report).report_key);
 assert.match(payload.report_key,/^pilot:[0-9a-f]{64}$/);
 assert.equal(payload.rights_verified,false);
 assert.equal(payload.evidence_committed,false);
 assert.equal(payload.observed_count,1);
 assert.deepEqual(Object.keys(payload.items[0]),[
  "listing_key","title","observed_page_sha256","image_url_sha256",
  "image_count","source_kind","rights_state","sales_state",
  "coverage_state","source_verification",
 ]);
 assert.equal(JSON.stringify(payload).includes("note"),false);
 for(const changed of [
  {...report,status:"needs_operator"},
  {...report,rights_verified:true},
  {...report,items:[{...report.items[0],source_verification:"verified"}]},
  {...report,items:[{...report.items[0],image_url_sha256:["bad"]}]},
  {...report,items:[{...report.items[0],title:""}]},
  {...report,items:[{...report.items[0],listing_key:"amazon:ABCD123456"}]},
  {...report,items:report.items.concat(report.items),observed_count:2},
 ])assert.throws(()=>prepareCamObservationReport(changed));
});