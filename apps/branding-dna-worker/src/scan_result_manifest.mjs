/** Future scan-result manifest builder and strict client-side validation.
 * Not imported by the live worker, heartbeat or browser pilot.
 * Hashes received here are caller-provided and are NOT provider provenance.
 */
const KEY = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$/;
const REV = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SHA = /^[0-9a-f]{64}$/;
const ROLES = new Set(["hero","detail","lifestyle","ugc","information","variation","unknown"]);
const MAX_ITEMS = 20;
const MAX_PAGES = 5;
const CAPTURE_WINDOW_MS = 30 * 24 * 60 * 60 * 1000;
const FUTURE_SKEW_MS = 5 * 60 * 1000;
const ISO_WITH_OFFSET = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;

function exactKeys(obj,keys) {
  return obj !== null && typeof obj === "object" && !Array.isArray(obj) &&
    (Object.getPrototypeOf(obj) === Object.prototype || Object.getPrototypeOf(obj) === null) &&
    Object.keys(obj).sort().join("|") === [...keys].sort().join("|");
}

export function validateFutureResultManifest(payload, expectedListingKey, now=new Date()) {
  if(!exactKeys(payload,["listing_key","observed_revision","captured_at","page_count","images"]))
    throw Error("invalid_result_manifest_shape");
  if(typeof expectedListingKey !== "string" || !KEY.test(expectedListingKey) ||
     payload.listing_key !== expectedListingKey)
    throw Error("result_listing_identity_mismatch");
  if(typeof payload.observed_revision !== "string" || !REV.test(payload.observed_revision))
    throw Error("invalid_result_revision");
  if(!Number.isInteger(payload.page_count) ||
     payload.page_count < 1 || payload.page_count > MAX_PAGES)
    throw Error("invalid_result_page_count");
  if(typeof payload.captured_at !== "string" ||
     !ISO_WITH_OFFSET.test(payload.captured_at))
    throw Error("invalid_result_timestamp");
  const observed=new Date(payload.captured_at);
  if(!Number.isFinite(observed.valueOf()) || !(now instanceof Date) ||
     !Number.isFinite(now.valueOf()) ||
     observed.getTime() < now.getTime()-CAPTURE_WINDOW_MS ||
     observed.getTime() > now.getTime()+FUTURE_SKEW_MS)
    throw Error("result_timestamp_outside_window");
  if(!Array.isArray(payload.images) || payload.images.length < 1 ||
     payload.images.length > MAX_ITEMS)
    throw Error("invalid_result_gallery_size");
  const images=payload.images.map((item,i)=>{
    if(!exactKeys(item,["sequence_index","asset_sha256","source_url_sha256","role","rights_state"]) ||
       !Number.isInteger(item.sequence_index) || item.sequence_index!==i ||
       typeof item.asset_sha256!=="string" || !SHA.test(item.asset_sha256) ||
       typeof item.source_url_sha256!=="string" || !SHA.test(item.source_url_sha256) ||
       !ROLES.has(item.role) || item.rights_state!=="unknown")
      throw Error("invalid_result_gallery_metadata");
    // Canonical DTO contains only hashes, safe role and index, never URLs/data.
    return {
      sequence_index:i,
      asset_sha256:item.asset_sha256,
      source_url_sha256:item.source_url_sha256,
      role:item.role,
      rights_state:"unknown",
    };
  });
  return {
    listing_key:payload.listing_key,
    observed_revision:payload.observed_revision,
    captured_at:observed.toISOString(),
    page_count:payload.page_count,
    images,
  };
}

export const FUTURE_RESULT_LIMITS=Object.freeze({
  maxPages:MAX_PAGES,maxGalleryItems:MAX_ITEMS,
  automatedDeliveryEnabled:false,
  resultsVerified:false,
});
