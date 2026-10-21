import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync,mkdirSync,writeFileSync,rmSync,readdirSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {localShopPaths,checkShopEligibility,runCamShopBatch,parseBatchCli} from "../src/cam_shop_batch.mjs";

const idA="12345678-1234-1234-1234-123456789abc";
const idB="12345678-1234-1234-1234-123456789abd";
const config={origin:"https://creative-assets.ddns.net",id:idA,token:"test-token-only-1234567890123"};
const amazon={id:idA,platform:"amazon",storefront_url:"https://www.amazon.com/stores/AllowedCaps",status:"active",access_mode:"owned"};
const etsy={id:idB,platform:"etsy",storefront_url:"https://www.etsy.com/shop/AllowedShop",status:"active",access_mode:"owned"};
const request=shops=>async(_url,opts)=>{
 assert.equal(opts.headers.Authorization,"Bearer "+config.token);
 return {ok:true,status:200,json:async()=>({scan_enabled:false,
  dispatch:{state:"disabled",active_scan_jobs:0},count:shops.length,items:shops})};
};
const approval=(shop)=>({
 platform:shop.platform,shop_url:shop.storefront_url,browser_access_approved:true,
 source_rights_reviewed:true,platform_terms_reviewed:true,robots_policy_reviewed:true,
 approved_by:"Operator reviewed documents",approved_at:new Date().toISOString(),
 ...(shop.platform==="amazon"?{amazon_agent_policy_reviewed:true}:
 {etsy_written_authorization:true,etsy_authorization_reference:"Written Etsy consent doc 2026-10-21"}),
});
async function withDir(fn){
 const base=mkdtempSync(join(tmpdir(),"cam-shop-batch-"));
 try{return await fn(base)}finally{rmSync(base,{recursive:true,force:true})}
}
function authorize(base,shop){
 const p=localShopPaths(base,shop.id);
 mkdirSync(join(base,"cam-shop-pilots",shop.id),{recursive:true});
 writeFileSync(p.approvalFile,JSON.stringify(approval(shop)));
 return p;
}
test("CLI requires an explicit mode and caps serial work to five shops",()=>{
 assert.deepEqual(parseBatchCli(["--inspect"]),{
  mode:"inspect",shopId:null,maxShops:1,envFile:"scout.local.env"});
 assert.equal(parseBatchCli(["--run-approved","--max-shops","5","--shop-id",idA]).shopId,idA);
 assert.throws(()=>parseBatchCli(["--inspect","--run-approved"]),/batch_mode_duplicate/);
 assert.throws(()=>parseBatchCli(["--run-approved","--max-shops"]),/batch_arg_missing/);
 assert.throws(()=>parseBatchCli(["--go"]),/batch_unknown_arg/);
});
test("unapproved and competitor shops never open browser; no shop URL in summary",async()=>withDir(async base=>{
 let launched=0;
 const result=await runCamShopBatch(config,{mode:"run_approved",maxShops:5,
  baseDir:base,request:request([amazon,etsy,{...etsy,id:"12345678-1234-1234-1234-123456789abe",access_mode:"competitor_research"}]),
  pilot:async()=>{launched++;throw Error("must never run")}});
 assert.equal(launched,0);assert.equal(result.run_count,0);
 assert.equal(result.blocked,3);
 assert.equal(result.items[0].reason,"per_shop_approval_missing");
 assert.equal(result.items[2].reason,"shop_access_not_verified");
 assert.equal(JSON.stringify(result).includes(amazon.storefront_url),false);
}));
test("inspect validates permission per exact CAM shop without browsing",async()=>withDir(async base=>{
 authorize(base,amazon);
 let calls=0;
 const result=await runCamShopBatch(config,{mode:"inspect",baseDir:base,
  request:request([amazon,etsy]),pilot:async()=>{calls++}});
 assert.equal(result.eligible,1,JSON.stringify(result));assert.equal(result.blocked,1);
 assert.equal(result.items[0].state,"locally_approved");
 assert.equal(result.items[1].state,"blocked");assert.equal(calls,0);
}));
test("sequential approved shop creates durable report and resumes without repeating keys",async()=>withDir(async base=>{
 authorize(base,amazon);
 const seen=[];
 const pilot=async options=>{
  seen.push([...options.skipProductKeys]);
  const key=seen.length===1?"amazon:B012345678":"amazon:B012345679";
  return {status:"preview_completed",platform:"amazon",scan_enabled:false,items:[{
    listing_key:key,title:"Five Panel Twill Cap",source_kind:"browser_dom_observation",
    source_verification:"unverified",rights_state:"unknown",sales_state:"unknown",
  }]};
 };
 const args={mode:"run_approved",shopId:idA,maxShops:2,baseDir:base,
  request:request([amazon,etsy]),pilot};
 const first=await runCamShopBatch(config,args);
 assert.equal(first.run_count,1);assert.equal(first.items[0].state,"completed",JSON.stringify(first));
 assert.equal(first.items[0].total_observed,1);
 const second=await runCamShopBatch(config,args);
 assert.equal(second.items[0].total_observed,2);
 assert.deepEqual(seen,[[],["amazon:B012345678"]]);
 assert.equal(readdirSync(localShopPaths(base,idA).reportDir).filter(x=>x.endsWith(".json")).length,2);
}));
test("a blocked browser halts other eligible shops rather than silently retrying",async()=>withDir(async base=>{
 authorize(base,amazon);authorize(base,etsy);
 let runs=0;
 const result=await runCamShopBatch(config,{mode:"run_approved",baseDir:base,
  request:request([amazon,etsy]),maxShops:5,
  pilot:async()=>{runs++;return {status:"needs_operator",reason:"marketplace_access_limited",
   platform:"amazon",items:[],scan_enabled:false}}});
 assert.equal(runs,1);assert.equal(result.run_count,1);
 assert.equal(result.needs_operator,1);
 assert.equal(result.items[0].state,"needs_operator");
 assert.equal(result.items[1].state,"locally_approved");
}));
test("invalid rights declarations cannot silently authorize Etsy or Amazon",()=>withDir(base=>{
 const p=authorize(base,etsy);
 let row=checkShopEligibility(etsy,base);
 assert.equal(row.state,"locally_approved");
 writeFileSync(p.approvalFile,JSON.stringify({...approval(etsy),etsy_written_authorization:false}));
 row=checkShopEligibility(etsy,base);
 assert.equal(row.state,"blocked");
 assert.equal(row.reason,"etsy_written_authorization_required");
 assert.equal(checkShopEligibility({...amazon,access_mode:"competitor_research"},base).state,"blocked");
}));