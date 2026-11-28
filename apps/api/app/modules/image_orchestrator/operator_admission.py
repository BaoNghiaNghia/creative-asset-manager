"""Operator-only one-pack production admission.

The image worker may supply evidence to the operator, but cannot enroll
itself. The server independently re-downloads Original, checks Drive owner,
source QA, palette and immutable placement plan before granting commands.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from sqlalchemy import select
from app.modules.muse_bridge.router import _operator
from app.modules.render_resources.model import RenderResource
from app.modules.etsy_spy.model import EtsySpyAgent,EtsySpyListingImage
from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX
from . import policy as p,admission,approvals,repository as repo
from .model import ImagePack,ImageRole,ImageFlowControl,ImageCapabilityState,ImagePlanApproval
from .commands import ensure_commands
from .live_runtime import WORKER,RESOURCE
from .central_read_api import Database,read_api_enabled
from .readiness import schema_evidence
from .lite_api import lock_tenant,image_worker_configured,job_query,Job

SHA=r"^[a-f0-9]{64}$"
router=APIRouter(prefix="/api/v1/muse-bridge/image-runtime/operator",
    tags=["image-orchestrator"],dependencies=[Depends(read_api_enabled)])
Operator=Annotated[str,Depends(_operator)]

class Admit(BaseModel):
    model_config=ConfigDict(extra="forbid")
    listingId:str=Field(pattern=r"^[0-9]{5,20}$")
    plan:dict
    resourceEvidenceSha256:str=Field(pattern=SHA)
    cronNativeEvidenceSha256:str=Field(pattern=SHA)
    nativeOutputPx:int=Field(ge=1600,le=4000,strict=True)
    restoreEvidenceSha256:str|None=Field(default=None,pattern=SHA)
    backupOnlyCanary:StrictBool=False
    legacyCutoverEvidenceSha256:str|None=Field(default=None,pattern=SHA)
    serverOnlyLegacyFence:StrictBool=False
    previewOnlyCanary:StrictBool=False
    activate:StrictBool=False

def backup_receipt():
    from app.core.config import get_settings
    from app.modules.database_backup.verified_receipt import has_recent_verified_receipt,RECEIPT_NAME
    directory=get_settings().DATABASE_BACKUP_STAGING_DIRECTORY
    p.require(has_recent_verified_receipt(directory,max_age_hours=24),"recent_backup_receipt_required")
    raw=(Path(directory)/RECEIPT_NAME).read_bytes()
    p.require(len(raw)<=1024 and has_recent_verified_receipt(directory,max_age_hours=24),
              "recent_backup_receipt_required")
    return hashlib.sha256(raw).hexdigest()

def validate_request(body):
    p.require(body.backupOnlyCanary != bool(body.restoreEvidenceSha256),
              "explicit_restore_or_backup_only_scope_required")
    p.require(body.serverOnlyLegacyFence != bool(body.legacyCutoverEvidenceSha256),
              "explicit_legacy_fence_scope_required")
    p.require(body.nativeOutputPx>=p.PUBLICATION_MIN_NATIVE or body.previewOnlyCanary,
              "native_publication_resolution_not_verified")
    fields={"skillSha256","garmentSku","artworkSha256","sourceSha256",
        "storagePlacement","placementLock","mockupSize","paletteSha256",
        "roleColors","infographicSwatches","infographicSourceRoles"}
    p.require(fields.issubset(body.plan) and set(body.plan)<=fields|{"smsAdvisoryWidthMm"},
              "image_plan_fields_invalid")

def legacy_proof(db,tenant,body):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=15)
    recent=db.scalar(select(EtsySpyAgent.id).where(EtsySpyAgent.tenant_id==tenant,
        EtsySpyAgent.active.is_(True),EtsySpyAgent.name.startswith(IMAGE_AGENT_NAME_PREFIX),
        EtsySpyAgent.last_seen_at>=cutoff).limit(1))
    if body.serverOnlyLegacyFence:
        p.require(recent is None,"legacy_agent_recently_active")
    # Server-only reservation is not a claim that the remote browser drained.
    return p.canonical_sha({"scope":"listing_exclusive_server",
        "listingId":body.listingId,"tenant":tenant,"serverOnly":body.serverOnlyLegacyFence,
        "clientDrainEvidenceSha256":body.legacyCutoverEvidenceSha256,
        "recentAgentObserved":recent is not None})

@router.post("/admit")
async def admit(body:Admit,tenant:Operator,db:Database):
    from .candidate_discovery import inspect_listing
    from .live_preflight import preflight
    if not schema_evidence(db)["centralSchemaReady"] or not image_worker_configured():
        raise HTTPException(503,"image_runtime_not_configured")
    try:
        validate_request(body)
        plan=body.plan
        ident=p.PackIdentity(tenant,body.listingId,plan["artworkSha256"],"C1717",1,
            plan["placementLock"]["lock_sha256"],plan["sourceSha256"])
        plan_sha=p.canonical_sha(plan)
        candidate=db.get(ImagePack,(tenant,ident.pack_id))
        if candidate is not None:
            approval=db.get(ImagePlanApproval,(tenant,ident.pack_id))
            p.require(approval is not None and not approval.revoked and
                candidate.plan_sha256==plan_sha,"image_pack_requires_reconciliation")
            # Replay is idempotent; activation must use the exact approved
            # operator evidence, never rewrite an admitted plan.
            e=approval.evidence.get("executionEvidence",{})
            p.require(approvals.execution_evidence_valid(e) and
                e["resourceEvidenceSha256"]==body.resourceEvidenceSha256 and
                e["cronNativeEvidenceSha256"]==body.cronNativeEvidenceSha256 and
                e["nativeOutputPx"]==body.nativeOutputPx and
                e["restoreEvidenceSha256"]==body.restoreEvidenceSha256 and
                e["backupOnlyCanary"]==body.backupOnlyCanary and
                e["serverOnlyLegacyFence"]==body.serverOnlyLegacyFence and
                e["legacyClientDrainVerified"]==bool(body.legacyCutoverEvidenceSha256) and
                e["previewOnlyCanary"]==body.previewOnlyCanary,"immutable_activation_evidence")
            cap=db.get(ImageCapabilityState,(tenant,WORKER))
            if body.activate and not (cap and cap.claim_allowed):
                backup_receipt()
                lock_tenant(db,tenant)
                legacy_proof(db,tenant,body)
                admission.inspect_current(db,tenant,candidate)
                resource=db.get(RenderResource,(tenant,RESOURCE))
                flow=repo.lock_flow(db,tenant)
                p.require(cap is not None and resource is not None and
                    resource.owner_flow is None and not resource.shared_with_video and
                    candidate.epoch==0,"existing_pack_requires_reconciliation")
                resource.integration_verified=True
                cap.claim_allowed=True;flow.running=True;flow.revision+=1
                db.commit()
            return {"status":"already_admitted","packId":ident.pack_id,
                    "imageClaimAllowed":bool(cap and cap.claim_allowed)}
        proof=await preflight(db,tenant,body.listingId,plan["artworkSha256"])
        p.require(proof["reason"]=="NEW_PACK_AWAITING_SOURCE_PLAN_AND_FENCE" and
            proof["sourceVisualQaVerified"] and proof["driveDestinationVerified"],
            "candidate_source_not_ready")
        p.require(proof["sourceSha256"]==plan["sourceSha256"] and
            plan["placementLock"]["ink_aspect_ratio"]==proof["inkAspectRatio"],
            "candidate_print_box_source_mismatch")
        p.require(set(plan["roleColors"].values())<=set(proof["eligibleColorIds"]),
                  "candidate_palette_unsafe")
        backup_sha=backup_receipt()
        # The provider preflight rolled back before network calls; do not
        # retain its read snapshot while acquiring the admission fence.
        db.rollback()
        lock_tenant(db,tenant)
        fresh=inspect_listing(db,tenant,body.listingId)
        p.require(fresh["reason"]=="NEW_PACK_AWAITING_SOURCE_PLAN_AND_FENCE" and
            fresh["artworkSha256"]==plan["artworkSha256"] and
            fresh["sourceSha256"]==plan["sourceSha256"],"candidate_changed")
        p.require(db.scalar(select(ImagePack.pack_id).join(ImageRole,
            (ImagePack.tenant_id==ImageRole.tenant_id)&(ImagePack.pack_id==ImageRole.pack_id))
            .where(ImagePack.tenant_id==tenant,ImageRole.status!="verified").limit(1)) is None,
            "another_image_pack_incomplete")
        p.require(db.scalar(job_query(tenant).where(Job.entity_id==body.listingId,
            Job.status.in_(("pending","processing"))).limit(1)) is None,"legacy_image_attempt_unresolved")
        legacy_sha=legacy_proof(db,tenant,body)
        now=int(time.time())
        flow=db.get(ImageFlowControl,tenant)
        if flow is None:
            flow=ImageFlowControl(tenant_id=tenant,running=False)
            db.add(flow)
        resource=db.get(RenderResource,(tenant,RESOURCE))
        if resource is None:
            resource=RenderResource(tenant_id=tenant,resource_id=RESOURCE,
                integration_verified=False,shared_with_video=False)
            db.add(resource)
        p.require(resource.owner_flow is None and not resource.shared_with_video,
                  "image_resource_busy_or_shared")
        cap=db.get(ImageCapabilityState,(tenant,WORKER))
        if cap is None:
            cap=ImageCapabilityState(tenant_id=tenant,worker_id=WORKER,
                skill_sha256=p.SKILL_SHA256,resource_id=RESOURCE,claim_allowed=False)
            db.add(cap)
        p.require(cap.skill_sha256==p.SKILL_SHA256 and cap.resource_id==RESOURCE,
                  "image_capability_binding_mismatch")
        db.flush()
        repo.lock_flow(db,tenant)
        repo.create_batch(db,p.Batch("pilot-"+ident.pack_id[:40],tenant,(ident.pack_id,),now),
            [(ident,plan_sha)])
        row=db.get(ImagePack,(tenant,ident.pack_id))
        row.approved_plan=plan
        admission.validate_plan(plan,row)
        db.flush()
        evidence={"scope":"single_pack","resourceEvidenceSha256":body.resourceEvidenceSha256,
            "cronNativeEvidenceSha256":body.cronNativeEvidenceSha256,
            "driveEvidenceSha256":p.canonical_sha({"artwork":proof["artworkSha256"],
                "sourceReview":proof["sourceVisualReviewSha256"],"destinationVerified":True}),
            "backupReceiptSha256":backup_sha,"restoreEvidenceSha256":body.restoreEvidenceSha256,
            "backupOnlyCanary":body.backupOnlyCanary,
            "serverOnlyLegacyFence":body.serverOnlyLegacyFence,
            "legacyClientDrainVerified":bool(body.legacyCutoverEvidenceSha256),
            "nativeOutputPx":body.nativeOutputPx,"previewOnlyCanary":body.previewOnlyCanary}
        approvals.record_plan(db,tenant,ident.pack_id,{
            "originalBytesSha256":proof["artworkSha256"],
            "sourceVisualReviewSha256":proof["sourceVisualReviewSha256"],
            "placementEvidenceSha256":ident.placement_sha,
            "legacyDrainEvidenceSha256":legacy_sha,
            "paletteEvidenceSha256":admission.PALETTE_SHA,
            "executionEvidence":evidence},"cam-operator-image-v61",now)
        ensure_commands(db,tenant,ident.pack_id)
        # Resource evidence is an explicit trusted operator attestation, not
        # a worker bearer claim. The actual live PNG/Drive preflight ran above.
        resource.integration_verified=body.activate
        cap.claim_allowed=body.activate
        flow.running=body.activate
        flow.revision+=1
        db.commit()
        return {"status":"admitted","packId":ident.pack_id,"listingId":body.listingId,
            "imageClaimAllowed":body.activate,"roles":13,"commands":2,
            "activationScope":"single_pack","fullRestoreVerified":not body.backupOnlyCanary,
            "legacyClientDrainVerified":bool(body.legacyCutoverEvidenceSha256),
            "publicationMinimumPx":p.PUBLICATION_MIN_NATIVE,
            "previewOnlyCanary":body.previewOnlyCanary,"videoQueueTouched":False}
    except (p.Conflict,KeyError,ValueError,TypeError) as exc:
        db.rollback()
        code=str(exc) if isinstance(exc,p.Conflict) else "image_admission_invalid"
        raise HTTPException(409,code) from None
    except HTTPException:
        db.rollback();raise
    except Exception:
        db.rollback()
        raise HTTPException(503,"image_admission_provider_unavailable") from None