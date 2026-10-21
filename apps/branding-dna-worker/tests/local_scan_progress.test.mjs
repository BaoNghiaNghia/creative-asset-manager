import test from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtempSync,mkdirSync,writeFileSync,rmSync,symlinkSync} from "node:fs";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {openShopProgress} from "../src/local_scan_progress.mjs";

const shop={platform:"amazon",url:"https://www.amazon.com/stores/AuthorizedCaps"};
const filename="branding-dna-20261017-124545321Z-a1b2c3d4.json";
const sha=value=>createHash("sha256").update(value).digest("hex");
function report(shopUrl=shop.url,items=[{
 listing_key:"amazon:B012345678",title:"Canvas Cap",
 source_kind:"browser_dom_observation",source_verification:"unverified",
}]){
 return {
  status:"preview_completed",platform:"amazon",shop_url_sha256:sha(shopUrl),
  scan_enabled:false,evidence_committed:false,rights_verified:false,sales_verified:false,
  mode:"local_browser_dom_only",items,observed_at:"2026-10-17T12:45:45Z",
 };
}

test("local checkpoints survive restart and are isolated per shop even in shared report folder",()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-progress-"));
 try{
  const body=report();
  const bytes=Buffer.from(JSON.stringify(body));
  writeFileSync(join(dir,filename),bytes);
  let db=openShopProgress(dir,shop);
  assert.deepEqual(db.completedKeys(),[]);
  assert.equal(db.reconcile().completed,1);
  assert.deepEqual(db.completedKeys(),["amazon:B012345678"]);
  db.close();

  db=openShopProgress(dir,shop);
  assert.equal(db.reconcile().completed,1);
  assert.deepEqual(db.completedKeys(),["amazon:B012345678"]);
  db.close();

  const other=openShopProgress(dir,{...shop,url:"https://www.amazon.com/stores/OtherCaps"});
  assert.deepEqual(other.completedKeys(),[]);
  assert.equal(other.reconcile().completed,0);
  other.close();
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("partial/failed details are never checkpointed and rights are never inferred",()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-progress-"));
 try{
  const db=openShopProgress(dir,shop);
  const body=report(shop.url,[{
    listing_key:"amazon:B012345678",source_kind:"browser_dom_observation",
    source_verification:"unverified",error_code:"detail_parser_review_required",
  }]);
  const bytes=Buffer.from(JSON.stringify(body));
  assert.equal(db.record(filename,bytes,body),0);
  assert.equal(db.count(),0);
  assert.throws(()=>db.record("bad.json",bytes,body),/progress_report_not_trusted/);
  assert.throws(()=>db.record(filename,bytes,report("https://www.amazon.com/stores/OtherCaps")),/progress_report_not_trusted/);
  db.close();
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("recover report after crash between durable report write and database commit",()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-progress-"));
 try {
  writeFileSync(join(dir,filename),JSON.stringify(report()));
  const db=openShopProgress(dir,shop);
  assert.equal(db.count(),0);
  assert.equal(db.reconcile().completed,1);
  db.close();
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("tampered same-name report is rejected rather than resetting seen products",()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-progress-"));
 try{
  writeFileSync(join(dir,filename),JSON.stringify(report()));
  let db=openShopProgress(dir,shop);
  db.reconcile();
  db.close();
  writeFileSync(join(dir,filename),JSON.stringify(report(shop.url,[])));
  db=openShopProgress(dir,shop);
  assert.throws(()=>db.reconcile(),/progress_report_immutable_violation/);
  db.close();
 }finally{rmSync(dir,{recursive:true,force:true})}
});

test("symlinked checkpoint folder is refused",()=>{
 const dir=mkdtempSync(join(tmpdir(),"bdna-progress-"));
 const external=mkdtempSync(join(tmpdir(),"bdna-external-"));
 try{
  // Windows junctions do not require the Developer Mode/admin privilege
  // needed for directory symlinks, but are still reparse points that must
  // be rejected. Keep the exact symlink path test on other platforms.
  symlinkSync(external,join(dir,".bdna-local-progress"),process.platform==="win32"?"junction":"dir");
  assert.throws(()=>openShopProgress(dir,shop),/progress_directory_untrusted/);
 }finally{rmSync(dir,{recursive:true,force:true});rmSync(external,{recursive:true,force:true})}
});