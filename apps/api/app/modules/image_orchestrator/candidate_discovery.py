"""Bounded tenant-local pre-admission inventory, not a render queue.

Use the same latest Native Master selector as CAM Muse. Never infer that
old gallery receipts share a physical print box or are independently QA
verified. This module has no writes and never issues a lease.
"""
from collections import Counter, defaultdict
from sqlalchemy import select
from fastapi import HTTPException

from . import policy as p
from .model import ImagePack
from app.modules.etsy_spy.model import (EtsySpyPicked as Picked,
    EtsySpyListingImage as Gallery, EtsySpyListingImageDeletion as Deleted)
from app.modules.processing.model import ProcessingJobModel as Job
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.muse_bridge.latest_artwork import choose, enabled

MAX_SCAN=100
MAX_HISTORY=64
MAX_GALLERY=260


def inspect_listing(db,tenant,listing):
    if not p._LISTING.fullmatch(listing) or db.get(Picked,(tenant,listing)) is None:
        raise HTTPException(404,"image_candidate_not_found")
    rows=list(db.execute(select(Job.id,Job.status,Job.cancellation_requested,Job.payload_json)
        .where(Job.tenant_id==tenant,Job.entity_type=="etsy_listing",
            Job.entity_id==listing,Job.job_type==EXTRACTION_JOB_TYPE,
            Job.cancellation_requested.is_(False),Job.status!="failed")
        .order_by(Job.created_at.desc(),Job.id.desc()).limit(MAX_HISTORY+1)))
    item={"listingId":listing,"garmentSku":"C1717","claimAllowed":False,
          "sourceMetadataVerified":False,"originalBytesVerified":False,
          "placementLockVerified":False,"legacyDrainVerified":False,
          "reason":"NO_NATIVE_MASTER"}
    if not enabled():
        return {**item,"reason":"LATEST_ARTWORK_POLICY_DISABLED"}
    if len(rows)>MAX_HISTORY:
        return {**item,"reason":"ARTWORK_HISTORY_REQUIRES_RECONCILIATION"}
    selected=choose(rows)
    if selected is None: return item
    if selected.state!="saved" or selected.position!="front":
        return {**item,"reason":"LATEST_MASTER_NOT_VERIFIED_FRONT",
                "latestState":selected.state}
    row=next((x for x in rows if x.id==selected.job_id),None)
    payload=row.payload_json if row and isinstance(row.payload_json,dict) else {}
    assets=payload.get("design_assets")
    exact=[a for a in assets if isinstance(a,dict) and a.get("id")==selected.asset_id
           and a.get("output_sha256")==selected.digest] if isinstance(assets,list) else []
    if len(exact)!=1:
        return {**item,"reason":"NATIVE_MASTER_DESCRIPTOR_INVALID"}
    asset=exact[0]
    from .admission import validate_selected_asset
    from types import SimpleNamespace
    source_sha=asset.get("source_sha256")
    try:
        p.require(p.valid_sha(source_sha),"source_sha_unverified")
        source=validate_selected_asset(selected,rows,
            SimpleNamespace(artwork_sha256=selected.digest,source_sha256=source_sha))
    except (p.Conflict,TypeError,ValueError):
        return {**item,"reason":"NATIVE_MASTER_DESCRIPTOR_UNVERIFIED"}
    item.update(sourceMetadataVerified=True,artworkSha256=source["artworkSha256"],
        sourceSha256=source["sourceSha256"],sourceJobId=source["jobId"],
        sourceAssetId=source["assetId"])
    existing=db.scalar(select(ImagePack.pack_id).where(
        ImagePack.tenant_id==tenant,ImagePack.listing_key==listing,
        ImagePack.garment_sku=="C1717",ImagePack.artwork_sha256==selected.digest).limit(1))
    if existing is not None:
        return {**item,"reason":"CENTRAL_PACK_EXISTS","packId":existing}
    gallery=list(db.execute(select(Gallery.pack_id,Gallery.slot,Gallery.design_sha256,
        Gallery.source_sha256,Gallery.placement,Gallery.garment_code)
        .where(Gallery.tenant_id==tenant,Gallery.listing_key==listing)
        .limit(MAX_GALLERY+1)))
    if len(gallery)>MAX_GALLERY:
        return {**item,"reason":"GALLERY_REQUIRES_RECONCILIATION"}
    deleted=db.scalar(select(Deleted.slot).where(Deleted.tenant_id==tenant,
        Deleted.listing_key==listing).limit(1))
    if deleted is not None:
        return {**item,"reason":"DELETION_REQUIRES_RECONCILIATION"}
    if gallery:
        groups=defaultdict(set)
        for g in gallery:
            if (g.garment_code=="C1717" and g.placement=="front"
                and g.design_sha256==selected.digest and g.source_sha256==source_sha
                and type(g.slot) is int and 1<=g.slot<=13):
                groups[g.pack_id].add(g.slot)
        return {**item,"reason":"EXISTING_GALLERY_REQUIRES_RECONCILIATION",
            "legacyCurrentPacks":[{"legacyPackId":key,"storedRoles":sorted(slots),
                "missingRoles":sorted(set(range(1,14))-slots)}
                for key,slots in sorted(groups.items())],
            "legacyReceiptsAreCentralVerified":False}
    return {**item,"reason":"NEW_PACK_AWAITING_SOURCE_PLAN_AND_FENCE",
            "missingRoles":list(range(1,14))}


def discover(db,tenant,after="",limit=50):
    p.require(type(limit) is int and 1<=limit<=MAX_SCAN,"invalid_candidate_limit")
    keys=list(db.scalars(select(Picked.listing_key).where(
        Picked.tenant_id==tenant,Picked.listing_key>after)
        .order_by(Picked.listing_key).limit(limit+1)))
    page=[]
    for listing in keys[:limit]:
        if not p._LISTING.fullmatch(listing):
            page.append({"reason":"INVALID_LISTING_ID","claimAllowed":False})
            continue
        page.append(inspect_listing(db,tenant,listing))
    counts=dict(Counter(x["reason"] for x in page))
    return {"protocol":"CAM_IMAGE_CANDIDATE_DISCOVERY_V1","scanned":len(page),
        "reasonCounts":counts,"candidateItems":page,
        "nextAfter":keys[limit-1] if len(keys)>limit else None,
        "candidateOnly":True,"queueAuthoritative":False,"claimAllowed":False}


async def verify_original(db,tenant,listing,expected_sha):
    item=inspect_listing(db,tenant,listing)
    if (item.get("sourceMetadataVerified") is not True
            or item.get("artworkSha256")!=expected_sha):
        raise HTTPException(409,"candidate_artwork_changed")
    from app.modules.muse_bridge.router import _read_original_artwork
    response=await _read_original_artwork(db,tenant,item["sourceJobId"],
        item["sourceAssetId"],expected_sha,"muse-image-native.png")
    import hashlib,io
    from PIL import Image,UnidentifiedImageError
    raw=response.body
    if (response.headers.get("X-CAM-Artwork-Variant")!="original"
        or response.media_type!="image/png" or hashlib.sha256(raw).hexdigest()!=expected_sha):
        raise HTTPException(409,"original_artwork_proof_invalid")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format!="PNG" or im.width*im.height>40000000:
                raise ValueError("invalid native size")
            w,h=im.size
            im.verify()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise HTTPException(409,"original_artwork_png_invalid") from None
    # Artwork may change while the provider fetch is in progress. Never
    # publish a proof for an obsolete original or return the raw PNG here.
    fresh=inspect_listing(db,tenant,listing)
    if fresh.get("artworkSha256")!=expected_sha or fresh.get("sourceJobId")!=item["sourceJobId"]:
        raise HTTPException(409,"candidate_artwork_changed")
    return {**fresh,"originalBytesVerified":True,"nativeWidth":w,"nativeHeight":h,
            "originalByteCount":len(raw),"sourceVisualQaVerified":False,
            "proofScope":"SHA_BOUND_ORIGINAL_BYTES_ONLY"}
