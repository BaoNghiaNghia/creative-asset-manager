import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,writeFileSync,rmSync} from "node:fs";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {parseArgs,checkOnce,main} from "../src/start_cam_paired_agent.mjs";
import {settings} from "../src/agent_cam.mjs";

const fakeToken="non-secret-unit-test-token-valid-length-only-00001";
const agentId="72680eb2-3205-4b27-8a1f-66140db12f5b";
const config=settings({RRUGC_AGENT_ID:agentId,RRUGC_SCOUT_TOKEN:fakeToken});
const blocked={state:"disabled",reason:"scan_task_dispatch_not_deployed",active_scan_jobs:0};
const shop={platform:"etsy",storefront_url:"https://www.etsy.com/shop/TestShop"};
const preview={version:1,scan_enabled:false,dispatch:{...blocked,marketplace_authorization_verified:false},
 shop_count:1,blocked_count:1,ready_count:0,items:[{
 shop_id:agentId,platform:"etsy",status:"blocked",staged_tasks:0,
 blockers:["scan_task_dispatch_not_deployed","marketplace_authorization_not_verified"],
 }]};

function mockCam({unsafe=false}={}){
 const paths=[];
 return {paths,request:async(url,options)=>{
  assert.equal(options.headers.Authorization,"Bearer "+fakeToken);
  paths.push(new URL(url).pathname);
  if(url.endsWith("/shops"))return {status:200,ok:true,json:async()=>({
   items:[shop],scan_enabled:unsafe,dispatch:blocked})};
  if(url.endsWith("/dispatch-preview"))return {status:200,ok:true,json:async()=>preview};
  if(url.endsWith("/branding-heartbeat"))return {status:200,ok:true,json:async()=>({
   accepted:true,state:"connected_waiting",scan_enabled:false,active_scan_jobs:0,shop_count:1})};
  throw new Error("unexpected_call");
 }};
}

test("CLI accepts only explicit real-once or connected-run mode",()=>{
 assert.equal(parseArgs(["--once","--env-file","../scout.local.env"]).mode,"once");
 assert.equal(parseArgs(["--run"]).mode,"run");
 for(const argv of [[],["--scan"],["--once","--run"],["--run","--shop","other"]])
  assert.throws(()=>parseArgs(argv),/usage:/);
});

test("one-shot pairing makes three authenticated CAM requests and no marketplace request",async()=>{
 const http=mockCam();
 const result=await checkOnce(config,http.request);
 assert.deepEqual(result,{
  event:"cam_live_check",connected:true,state:"connected_waiting",shop_count:1,
  preview_available:true,blocked_shops:1,staged_tasks:0,dispatch_enabled:false,
  active_scan_jobs:0,marketplace_requests:0,
 });
 assert.equal(http.paths.length,3);
 assert.ok(http.paths.every(path=>path.startsWith("/api/v1/branding-dna/")));
 assert.equal(JSON.stringify(result).includes(fakeToken),false);
});

test("CAM unexpected scanning flag causes hard refusal before heartbeat",async()=>{
 const http=mockCam({unsafe:true});
 await assert.rejects(()=>checkOnce(config,http.request),/dispatch_contract_not_audited/);
 assert.equal(http.paths.length,1);
});

test("read pairing from disk, perform real protocol check and never log credentials",async()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-cam-paired-"));
 const file=join(dir,"scout.local.env");
 try{
  writeFileSync(file,`RRUGC_AGENT_ID=${agentId}\nRRUGC_SCOUT_TOKEN=${fakeToken}\nBRANDING_DNA_SERVER_URL=https://creative-assets.ddns.net\n`,{mode:0o600});
  const printed=[],http=mockCam();
  const result=await main(["--once","--env-file",file],{request:http.request,log:line=>printed.push(line)});
  assert.equal(result.shop_count,1);
  assert.equal(printed.length,1);
  assert.equal(JSON.parse(printed[0]).active_scan_jobs,0);
  assert.equal(printed[0].includes(fakeToken),false);
 }finally{rmSync(dir,{recursive:true,force:true})}
});
