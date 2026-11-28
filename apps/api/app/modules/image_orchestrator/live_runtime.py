"""Live image-only runtime adapter selection and queue descriptors.

Execution stays fenced by the independent operator-owned capability/resource
and approved-plan ledger. No worker-controlled readiness or synthetic receipt.
"""
from __future__ import annotations

import time
from sqlalchemy import select
from . import policy as p,admission,approvals
from .model import ImageFlowControl,ImageCapabilityState,ImagePack,ImageCommand,ImageRole,ImageBatch,ImagePlanApproval
from app.modules.render_resources.model import RenderResource
from app.modules.etsy_spy.model import EtsySpyListing,EtsySpyListingImage,EtsySpyListingImageDeletion
from .commands import gallery_pack_id
from .readiness import schema_evidence

WORKER="muse-image-01"
RESOURCE="muse-native-imagegen-01"


def resource_state(db,identity):
    if identity.kind!="muse" or identity.worker!=WORKER:
        return None,None
    if not schema_evidence(db)["centralSchemaReady"]:
        return None,None
    cap=db.get(ImageCapabilityState,(identity.tenant,identity.worker))
    if cap is None or cap.skill_sha256!=p.SKILL_SHA256 or cap.resource_id!=RESOURCE:
        return None,None
    resource=db.get(RenderResource,(identity.tenant,cap.resource_id))
    # No dependency on, or lock against, the separate Muse Video resource.
    if resource is None or not resource.integration_verified or resource.shared_with_video:
        return cap,None
    return cap,resource


def active_approval(db,identity):
    rows=db.execute(select(ImagePlanApproval,ImagePack).join(ImagePack,
        (ImagePlanApproval.tenant_id==ImagePack.tenant_id)&
        (ImagePlanApproval.pack_id==ImagePack.pack_id))
        .where(ImagePlanApproval.tenant_id==identity.tenant,
               ImagePlanApproval.revoked.is_(False)).limit(100)).all()
    for approval,pack in rows:
        if (approval.plan_sha256==pack.plan_sha256
            and approvals._plan_evidence(pack,approval.evidence)
            and approvals.execution_evidence_valid(approval.evidence.get("executionEvidence"))
            and pack.owner in (None,identity.worker)):
            return approval
    return None


def can_bind(db,identity):
    cap,resource=resource_state(db,identity)
    return cap is not None and resource is not None and active_approval(db,identity) is not None


def runtime_plan_authorized(db,identity,pack):
    if identity.worker!=WORKER or identity.kind!="muse" or not approvals.authorize_plan(db,identity,pack):
        return False
    approval=db.get(ImagePlanApproval,(identity.tenant,pack.pack_id))
    if (pack.owner not in (None,identity.worker) or not approval or
        not approvals.execution_evidence_valid(approval.evidence.get("executionEvidence"))):
        return False
    # Current adapter is restricted to new, front C1717 packs. Old images
    # need receipt reconciliation, not a second gallery pack.
    alias=gallery_pack_id(pack.pack_id)
    if db.scalar(select(EtsySpyListingImage.slot).where(
            EtsySpyListingImage.tenant_id==identity.tenant,
            EtsySpyListingImage.listing_key==pack.listing_key,
            EtsySpyListingImage.pack_id!=alias).limit(1)) is not None:
        return False
    if db.scalar(select(EtsySpyListingImageDeletion.slot).where(
            EtsySpyListingImageDeletion.tenant_id==identity.tenant,
            EtsySpyListingImageDeletion.listing_key==pack.listing_key).limit(1)) is not None:
        return False
    from .lite_api import job_query,Job
    lite=db.scalar(job_query(identity.tenant).where(Job.entity_id==pack.listing_key,
        Job.status.in_(("pending","processing"))).limit(1))
    return lite is None


def admission_ready(db,identity,pack):
    # Read-only version of the claim-time approval check; do not lock plans
    # during discovery. Claim itself rechecks every invariant under lock.
    approval=db.get(ImagePlanApproval,(identity.tenant,pack.pack_id))
    if (approval is None or approval.revoked or approval.plan_sha256!=pack.plan_sha256
            or not approvals._plan_evidence(pack,approval.evidence)
            or not approvals.execution_evidence_valid(approval.evidence.get("executionEvidence"))):
        return False
    try:
        admission.validate_plan(pack.approved_plan,pack)
        admission.inspect_current(db,identity.tenant,pack)
    except (p.Conflict,TypeError,ValueError):
        return False
    return pack.owner in (None,identity.worker)


def readiness(db,identity):
    cap,res=resource_state(db,identity)
    approved=active_approval(db,identity) if res is not None else None
    if not res or not approved:
        return {"runtimeAdaptersBound":False,"resourceVerified":res is not None,
                "claimAllowed":False,"flowRunning":False}
    flow=db.get(ImageFlowControl,identity.tenant)
    running=bool(flow and flow.running)
    allowed=bool(cap.claim_allowed and cap.cooldown_until<=int(time.time()) and running
                 and (res.owner_flow is None or
                      (res.owner_flow=="image" and res.owner_worker==identity.worker)))
    evidence=approved.evidence["executionEvidence"]
    return {"runtimeAdaptersBound":True,"resourceVerified":True,
            "claimAllowed":allowed,"flowRunning":running,
            "activationScope":"single_pack",
            "fullRestoreVerified":not evidence["backupOnlyCanary"],
            "legacyClientDrainVerified":evidence["legacyClientDrainVerified"],
            "previewOnlyCanary":evidence["previewOnlyCanary"],
            "resourceId":res.resource_id,"cooldownUntilEpoch":cap.cooldown_until}


def descriptors(db,identity,limit=20):
    state=readiness(db,identity)
    if not state["claimAllowed"]: return []
    cap,res=resource_state(db,identity)
    if res.owner_flow is not None: return []
    rows=db.execute(select(ImageCommand,ImagePack).join(ImagePack,
        (ImageCommand.tenant_id==ImagePack.tenant_id)&(ImageCommand.pack_id==ImagePack.pack_id))
        .where(ImageCommand.tenant_id==identity.tenant,ImageCommand.status=="pending",
            ImagePack.garment_sku=="C1717")
        .order_by(ImageCommand.wave,ImagePack.listing_key,ImageCommand.command_id)
        .limit(100)).all()
    result=[]
    for cmd,pack in rows:
        if not admission_ready(db,identity,pack) or pack.lease_state is not None:
            continue
        roles=list(db.scalars(select(ImageRole).where(ImageRole.tenant_id==identity.tenant,
              ImageRole.pack_id==pack.pack_id).order_by(ImageRole.role)))
        if [x.role for x in roles]!=list(range(1,14)):
            continue
        if any(x.status in ("dispatch_uncertain","upload_pending","reconcile_required") for x in roles):
            continue
        wave1=all(x.status=="verified" for x in roles[:4])
        batch=db.get(ImageBatch,(identity.tenant,pack.batch_id))
        if batch is None or pack.pack_id not in batch.pack_ids:
            continue
        from .adaptive_v61 import barrier_snapshot,PROTOCOL
        barrier=barrier_snapshot(identity.tenant,pack.pack_id,pack.owner,wave1,batch.release_decision)
        if cmd.wave==2 and not barrier["wave2Open"]:continue
        missing=[x.role for x in roles if x.role in p.WAVES[cmd.wave]
                 and x.status=="pending" and x.attempts<p.MAX_ROLE_ATTEMPTS]
        if not missing: continue
        title=db.scalar(select(EtsySpyListing.title).where(
            EtsySpyListing.tenant_id==identity.tenant,EtsySpyListing.listing_key==pack.listing_key)
            .order_by(EtsySpyListing.observed_at.desc().nullslast()).limit(1))
        if not title:continue
        plan=pack.approved_plan
        result.append({"protocol":PROTOCOL,"jobType":"image","action":"create_listing_images",
            "commandId":cmd.command_id,"wave":cmd.wave,"listingId":pack.listing_key,
            "title":title,"packId":pack.pack_id,"packRevision":pack.pack_revision,
            "garmentSku":pack.garment_sku,"garment":{"sku":pack.garment_sku},
            "garmentColor":plan["roleColors"]["01"],"roleColors":plan["roleColors"],
            "artworkSha256":pack.artwork_sha256,"sourceSha256":pack.source_sha256,
            "placementLock":{**plan["placementLock"],"placementLockSha256":pack.placement_sha256},
            "planSha256":pack.plan_sha256,"skillRegistry":{"skillSha256":p.SKILL_SHA256},
            "imageJobClaim":{"body":{"wave":cmd.wave}},
            "imageOriginalPath":f"/api/v1/muse-bridge/jobs/{pack.listing_key}/image-original?artworkSha256={pack.artwork_sha256}",
            "allowedRoles":missing,"barrier":barrier,"publicationMinimumPx":p.PUBLICATION_MIN_NATIVE})
        if len(result)>=limit:break
    return result