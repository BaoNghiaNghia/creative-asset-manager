import test from "node:test";
import assert from "node:assert/strict";
import {
  validateFutureResultManifest, FUTURE_RESULT_LIMITS,
} from "../src/scan_result_manifest.mjs";

const CLOCK=new Date("2026-10-18T04:00:00.000Z");
function manifest(overrides={}){
 return {
  listing_key:"Listing_001",observed_revision:"revision-2",
  captured_at:"2026-10-18T03:55:00Z",page_count:1,
  images:[{sequence_index:0,asset_sha256:"a".repeat(64),
           source_url_sha256:"b".repeat(64),role:"hero",rights_state:"unknown"}],
  ...overrides,
 };
}

test("metadata-only result builder returns independent, bounded normalized manifest",()=>{
 const submitted=manifest();
 const validated=validateFutureResultManifest(submitted,"Listing_001",CLOCK);
 assert.deepEqual(Object.keys(validated),
  ["listing_key","observed_revision","captured_at","page_count","images"]);
 assert.equal(validated.captured_at,"2026-10-18T03:55:00.000Z");
 assert.deepEqual(validated.images,submitted.images);
 assert.notEqual(validated.images,submitted.images);
 assert.equal(FUTURE_RESULT_LIMITS.maxPages,5);
 assert.equal(FUTURE_RESULT_LIMITS.maxGalleryItems,20);
 assert.equal(FUTURE_RESULT_LIMITS.automatedDeliveryEnabled,false);
 assert.equal(FUTURE_RESULT_LIMITS.resultsVerified,false);
});

test("forged image rights, raw URLs, secrets and unsupported content are refused",()=>{
 for(const change of [
   {source_url:"https://example.com/listing"},
   {marketplace_authorization_verified:true},
   {task_id:"12345678-1111-4111-8111-123456789012"},
   {command:"launch Chrome"},
   {images:[{...manifest().images[0],rights_state:"verified"}]},
   {images:[{...manifest().images[0],media_bytes:"iVBORw0KGgo"}]},
   {images:[{...manifest().images[0],source_url:"http://127.0.0.1/private"}]},
   {images:[{...manifest().images[0],role:"seller_verified"}]},
   {images:[{...manifest().images[0],asset_sha256:"C".repeat(64)}]},
   {images:[{...manifest().images[0],sequence_index:1}]},
   {images:[]},
   {images:Array.from({length:21},(_,i)=>({...manifest().images[0],sequence_index:i}))},
   {page_count:0},{page_count:6},{page_count:"1"},
   {observed_revision:"../path"},
   {listing_key:"OtherListing"},
   {captured_at:"2026-10-18"},{captured_at:"2026-10-18T00:00:00"},
   {captured_at:"2026-09-01T04:00:00Z"},
   {captured_at:"2026-10-18T04:10:00Z"},
 ]){
  assert.throws(
    ()=>validateFutureResultManifest(manifest(change),"Listing_001",CLOCK),
    undefined,
    "Must reject "+Object.keys(change).join(","),
  );
 }
});

test("rejects prototype or accessor-derived unexpected contract values",()=>{
 const dto=manifest();
 Object.defineProperty(dto,"evil",{enumerable:true,value:"unexpected"});
 assert.throws(()=>validateFutureResultManifest(dto,"Listing_001",CLOCK),
               /invalid_result_manifest_shape/);
 const proto=Object.create({listing_key:"Listing_001"});
 Object.assign(proto,manifest());
 assert.throws(()=>validateFutureResultManifest(proto,"Listing_001",CLOCK),
               /invalid_result_manifest_shape/);
});

test("accepts explicit timezone and sorts no invented gallery positions",()=>{
 const submitted=manifest({captured_at:"2026-10-18T10:55:00+07:00",
  images:[...manifest().images,
   {...manifest().images[0],sequence_index:1,role:"detail"}]});
 const checked=validateFutureResultManifest(submitted,"Listing_001",CLOCK);
 assert.equal(checked.captured_at,"2026-10-18T03:55:00.000Z");
 assert.equal(checked.images.length,2);
 const reordered=manifest({images:[submitted.images[1],submitted.images[0]]});
 assert.throws(()=>validateFutureResultManifest(reordered,"Listing_001",CLOCK),
               /invalid_result_gallery_metadata/);
});
