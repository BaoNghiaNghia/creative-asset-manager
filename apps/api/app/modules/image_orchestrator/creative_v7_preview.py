"""Read-only V7 previews. Explicit artwork inspection costs one metadata call.

Not part of V6 claim, renderer, admission or retry state. Do not invoke the
visual classifier on every polling tick. Caller may retain the SHA-bound
preview as evidence; production plans need a separate approved protocol.
"""
from __future__ import annotations
import hashlib
from sqlalchemy import select
from . import policy as p,admission,approvals,creative_v7 as v7
from .model import ImagePack,ImageRole,ImagePlanApproval
from app.modules.etsy_spy.model import EtsySpyListing

def snapshot(db,identity,listing,pack_id,sku):
    from .central_read_api import _owned_pack,_receipt
    row=_owned_pack(db,identity,listing,pack_id,sku)
    approval=db.get(ImagePlanApproval,(identity.tenant,pack_id))
    p.require(approval is not None and not approval.revoked
              and approval.plan_sha256==row.plan_sha256
              and approvals._plan_evidence(row,approval.evidence),
              "creative_product_plan_unapproved")
    admission.validate_plan(row.approved_plan,row)
    source=admission.inspect_current(db,identity.tenant,row)
    title=db.scalar(select(EtsySpyListing.title).where(
        EtsySpyListing.tenant_id==identity.tenant,EtsySpyListing.listing_key==listing)
        .order_by(EtsySpyListing.observed_at.desc().nullslast()).limit(1))
    roles=list(db.scalars(select(ImageRole).where(ImageRole.tenant_id==identity.tenant,
        ImageRole.pack_id==pack_id).order_by(ImageRole.role)))
    p.require([r.role for r in roles]==list(range(1,14)),"creative_role_state_invalid")
    verified=[]
    for role in roles:
        if role.status=="verified":
            p.require(_receipt(role,row) is not None,"creative_receipt_unverified")
            verified.append(role.role)
    failures=[r.role for r in roles if r.role in v7.WORN and
              (r.failure_code or r.status in ("hold","reconcile_required"))]
    return {"listing":listing,"pack_id":pack_id,"plan_sha":row.plan_sha256,
            "product":row.approved_plan,"title":title or "",
            "verified_roles":verified,"failed_worn_roles":failures},source

def preview(db,identity,listing,pack_id,sku):
    data,_=snapshot(db,identity,listing,pack_id,sku)
    return v7.plan_roles(**data)

async def inspect_artwork(db,identity,listing,pack_id,sku):
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.providers.ai.factory import build_ai_provider_registry
    from app.domain.providers.contracts import AiMetadataAnalysisInput
    from .candidate_discovery import load_original
    from .live_preflight import source_board
    from starlette.concurrency import run_in_threadpool
    before,source=snapshot(db,identity,listing,pack_id,sku)
    sha=before["product"]["artworkSha256"]
    response,proof=await load_original(db,identity.tenant,listing,sha)
    p.require(proof["sourceAssetId"]==source["assetId"] and
              proof["sourceJobId"]==source["jobId"],"creative_artwork_changed")
    db.rollback()
    board,bbox,_,_=await run_in_threadpool(source_board,response.body,sha)
    registry=build_ai_provider_registry(get_settings(),session_factory=SessionLocal)
    try:
        reviewer=registry.require("gemini")
        result=await reviewer.analyze_single(AiMetadataAnalysisInput(
            tenant_id=identity.tenant,asset_id=proof["sourceAssetId"],
            prompt=v7.PROFILE_PROMPT,image_bytes=board,image_mime_type="image/png",
            image_width=bbox[2]-bbox[0],image_height=bbox[3]-bbox[1],
            metadata_profile="cam_image_creative_profile",metadata_profile_version="v7",
            json_schema=v7.ArtworkProfile.model_json_schema()))
        profile=v7.ArtworkProfile.model_validate(dict(result.metadata))
        evidence={"artworkSha256":sha,"sourceSha256":before["product"]["sourceSha256"],
                  "boardSha256":hashlib.sha256(board).hexdigest(),
                  "provider":result.provider,"model":result.model,
                  "profile":profile.model_dump(),"protocol":v7.PROTOCOL}
    finally:
        await registry.aclose()
    after,fresh=snapshot(db,identity,listing,pack_id,sku)
    p.require(after["plan_sha"]==before["plan_sha"] and fresh==source,
              "creative_artwork_changed")
    return v7.plan_roles(**after,profile=profile,profile_evidence=p.canonical_sha(evidence))
