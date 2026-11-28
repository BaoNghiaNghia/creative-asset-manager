"""No-migration Muse Image transport. Independent single-role C1717 pilot.

The existing external-job table is the journal; never use video queue rows.
One nonterminal pilot per tenant. New Send requires a current Extension
reservation, independent operator plan approval, verified original bytes,
and a separately verified image-only renderer. Unknown sends are not replayed.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import secrets
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from PIL import Image, UnidentifiedImageError

from app.core.database import get_db
from app.domain.processing.types import JOB_TYPES
from app.modules.processing.model import ProcessingJobModel as Job
from app.modules.etsy_spy.model import EtsySpyPicked as Picked, EtsySpyListingImage as Gallery, EtsySpyListingImageDeletion as Deleted, EtsySpyAgent as Agent
from app.modules.etsy_spy.service import IMAGE_AGENT_NAME_PREFIX
from app.modules.muse_bridge.router import _operator
from .identity import ImageIdentity, resolve_identity
from . import admission, policy as p, print_box

TYPE = "cam_muse_image_lite"
ENTITY = "cam_muse_image"
PROTOCOL = "CAM_MUSE_IMAGE_LITE_V1"
PREFIX = "/api/v1/muse-bridge/image-lite"
FENCE_TTL = 120
LEASE_TTL = 3600
MAX_BYTES = 32*1024*1024
assert TYPE not in JOB_TYPES

class RedactedRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422,"invalid_image_request") from None
        return handler


router = APIRouter(prefix=PREFIX, tags=["muse-image-lite"],route_class=RedactedRoute)
Db = Annotated[Session, Depends(get_db)]
def image_worker_configured():
    return (bool(re.fullmatch(r"[a-f0-9]{64}",os.environ.get("CAM_MUSE_IMAGE_LITE_WORKER_TOKEN_SHA256","")))
            and bool(os.environ.get("CAM_MUSE_TENANT_ID","")))


def lite_identity(request:Request,authorization:str|None=Header(default=None),
                  x_cam_image_agent_id:str|None=Header(default=None),
                  db:Session=Depends(get_db)):
    if x_cam_image_agent_id is not None:
        return resolve_identity(request,authorization,x_cam_image_agent_id,db)
    if not image_worker_configured():
        raise HTTPException(503,"image_worker_credential_not_configured")
    m=re.fullmatch(r"Bearer ([A-Za-z0-9_-]{48,128})",authorization or "")
    expected=os.environ["CAM_MUSE_IMAGE_LITE_WORKER_TOKEN_SHA256"]
    if not m or not hmac.compare_digest(p.digest(m.group(1)),expected):
        raise HTTPException(401,"image_worker_authentication_required")
    return ImageIdentity(os.environ["CAM_MUSE_TENANT_ID"],"muse-image-01","muse")


Identity = Annotated[ImageIdentity, Depends(lite_identity)]
Operator = Annotated[str, Depends(_operator)]
SHA = r"^[a-f0-9]{64}$"
LISTING = r"^[0-9]{5,20}$"
ATTEMPT = r"^[A-Za-z0-9_-]{1,128}$"


def now():
    return int(datetime.now(timezone.utc).timestamp())


def fail(code, status=409):
    raise HTTPException(status, code)


def check(condition, code, status=409):
    if not condition:
        fail(code, status)


def private(response):
    response.headers["Cache-Control"] = "private, no-store"


def lock_tenant(db, tenant):
    # Separate transaction-scoped key namespace, not a video resource lease.
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(("cam-muse-image-lite:"+tenant).encode()).digest()[:8],
                             "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key":key})
    else:
        check(db.get_bind().dialect.name=="sqlite" and db.info.get("muse_image_lite_test") is True,
              "image_journal_unavailable",503)


def job_query(tenant):
    return select(Job).where(Job.tenant_id==tenant,Job.job_type==TYPE,Job.entity_type==ENTITY)


def get_job(db, tenant, cid, *, lock=False):
    try:
        check(str(UUID(cid))==cid,"image_command_not_found",404)
    except (TypeError, ValueError):
        fail("image_command_not_found",404)
    stmt=job_query(tenant).where(Job.id==cid)
    if lock:
        stmt=stmt.with_for_update().execution_options(populate_existing=True)
    job=db.scalar(stmt)
    check(job is not None,"image_command_not_found",404)
    check(job.payload_json.get("protocol")==PROTOCOL,"image_journal_invalid",503)
    return job


def active(db, tenant):
    return list(db.scalars(job_query(tenant).where(Job.status.in_(("pending","processing")))
                           .order_by(Job.created_at).limit(2)))


def set_payload(job, **values):
    job.payload_json={**job.payload_json,**values}


def pack_view(payload, tenant):
    plan=payload["plan"]
    return SimpleNamespace(tenant_id=tenant, listing_key=payload["listingId"],
        garment_sku="C1717", pack_id=payload["packId"], plan_sha256=payload["planSha256"],
        skill_sha256=p.SKILL_SHA256, artwork_sha256=plan["artworkSha256"],
        source_sha256=plan["sourceSha256"], placement_sha256=plan["placementLock"]["lock_sha256"],
        approved_plan=plan)


def current_asset(db, job):
    pack=pack_view(job.payload_json,job.tenant_id)
    try:
        admission.validate_plan(pack.approved_plan,pack)
        return admission.inspect_current(db,job.tenant_id,pack)
    except (p.Conflict,ValueError,KeyError,TypeError):
        fail("current_artwork_or_print_plan_changed")


def reservation_blocks_legacy(db, tenant, listing):
    rows=list(db.scalars(job_query(tenant).where(Job.entity_id==listing,
            Job.status.in_(("pending","processing"))).limit(2)))
    if any(isinstance(j.payload_json.get("legacyFence"),dict) for j in rows):
        return True
    # The central operator admission holds this same tenant advisory lock.
    # Preserve the old Extension endpoint and protect only admitted listings.
    if db.get_bind().dialect.name=="postgresql":
        from .readiness import schema_evidence
        if schema_evidence(db)["centralSchemaReady"]:
            from .model import ImagePack,ImagePlanApproval
            from .approvals import execution_evidence_valid
            for approval in db.scalars(select(ImagePlanApproval).join(ImagePack,
                    (ImagePlanApproval.tenant_id==ImagePack.tenant_id)&
                    (ImagePlanApproval.pack_id==ImagePack.pack_id))
                    .where(ImagePack.tenant_id==tenant,ImagePack.listing_key==listing,
                           ImagePlanApproval.revoked.is_(False))):
                if execution_evidence_valid(approval.evidence.get("executionEvidence")):
                    return True
    return False


def no_gallery(db, job):
    # This intentionally supports only a NEW listing pack for canary Role 01.
    # Never replace, resurrect a deletion, or mix with legacy pack geometry.
    where=(Gallery.tenant_id==job.tenant_id,Gallery.listing_key==job.entity_id)
    check(db.scalar(select(Gallery.slot).where(*where).limit(1)) is None,"existing_gallery_requires_reconciliation")
    check(db.scalar(select(Deleted.slot).where(Deleted.tenant_id==job.tenant_id,
          Deleted.listing_key==job.entity_id).limit(1)) is None,"existing_deletion_requires_reconciliation")


def current_fence(payload, t):
    fence=payload.get("legacyFence")
    return (isinstance(fence,dict) and fence.get("mode")=="listing_exclusive"
            and type(fence.get("expiresAt")) is int and fence["expiresAt"]>t
            and p.valid_sha(fence.get("ledgerSha256")))


def sole_image_agent(db, tenant):
    rows=list(db.scalars(select(Agent.id).where(Agent.tenant_id==tenant,
        Agent.active.is_(True),Agent.name.startswith(IMAGE_AGENT_NAME_PREFIX)).limit(2)))
    return "chatgpt-ext-"+rows[0] if len(rows)==1 else None


def fence_verified(db, job, t):
    d=job.payload_json
    return (current_fence(d,t) and
            (d.get("legacyFence") or {}).get("agent")==sole_image_agent(db,job.tenant_id))


def ready(db, job, identity, t):
    d=job.payload_json
    return (job.status=="pending" and not job.cancellation_requested
            and d.get("phase")=="prepared" and d.get("runAllowed") is True
            and identity.kind=="muse" and d.get("workerId")==identity.worker
            and d.get("operatorAttestedImageResource") is True
            and fence_verified(db,job,t) and d.get("cooldownUntilEpoch",0)<=t
            and runner_ready(d,t) and lite_p0.capability(d)
            and job.attempt_count<lite_p0.MAX_ATTEMPTS and d["epoch"]<3)


def runner_ready(d,t):
    beat=d.get("imageRunnerHeartbeat")
    return (isinstance(beat,dict) and beat.get("runnerId")==d.get("runnerId")
            and beat.get("workflowSha256")==d.get("workflowSha256")
            and beat.get("resourceId")==d.get("resourceId")
            and type(beat.get("expiresAt")) is int and beat["expiresAt"]>t)


def descriptor(job):
    d=job.payload_json
    return {"protocol":PROTOCOL,"jobType":"image","commandId":job.id,
        "listingId":job.entity_id,"packId":d["packId"],"garmentSku":"C1717",
        "role":1,"allowedRoles":[1],"wave":1,"skillSha256":p.SKILL_SHA256,
        "artworkSha256":d["plan"]["artworkSha256"],"sourceSha256":d["plan"]["sourceSha256"],
        "placementLockSha256":d["plan"]["placementLock"]["lock_sha256"],
        "planSha256":d["planSha256"],"status":d["phase"],
        "imageOnly":True,"runnerId":d["runnerId"],"workflowSha256":d["workflowSha256"],
        "resourceId":d["resourceId"],
        "nativeOutputSize":d.get("nativeOutputSize"),
        "nativeCapabilityEvidenceSha256":d.get("nativeCapabilityEvidenceSha256"),
        "claimEpoch":d["epoch"],"attemptCount":job.attempt_count,
        "lastRejectionReceiptId":((d.get("attemptHistory") or [{}])[-1].get("rejection") or {}).get("rejectionReceiptId")}


def muse(identity):
    check(identity.kind=="muse","image_worker_required",403)


def own(job, identity, body, *, phases, allow_expired=False):
    muse(identity)
    d=job.payload_json
    check(job.status=="processing" and job.claimed_by==identity.worker
          and d["epoch"]==body.epoch and d["phase"] in phases
          and isinstance(d.get("leaseHash"),str)
          and hmac.compare_digest(d["leaseHash"],p.digest(body.lease.get_secret_value())),
          "image_lease_invalid")
    if not allow_expired:
        check(type(d.get("expiresAt")) is int and d["expiresAt"]>now(),"image_lease_expired")


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")


class Prepare(Strict):
    listingId: str=Field(pattern=LISTING)
    plan: dict
    workerId: str=Field(pattern=r"^muse-image-01$")
    runnerId: str=Field(pattern=r"^muse-image-[A-Za-z0-9_-]{1,48}$")
    workflowSha256: str=Field(pattern=SHA)
    resourceId: str=Field(pattern=ATTEMPT)
    independentImageResourceEvidenceSha256: str=Field(pattern=SHA)
    sourceVisualReviewSha256: str=Field(pattern=SHA)
    placementEvidenceSha256: str=Field(pattern=SHA)
    nativeCapabilityEvidenceSha256: str=Field(pattern=SHA)
    nativeOutputSize: int=Field(ge=2000,le=4000,strict=True)


class Fence(Strict):
    ledgerSha256: str=Field(pattern=SHA)
    # Authenticated paired Extension asserts this listing has been reserved
    # durably, not present in any local pack/job, with admission fenced.
    listingId: str=Field(pattern=LISTING)
    mode: str=Field(pattern=r"^listing_exclusive$")


class RunnerHeartbeat(Strict):
    runnerId: str=Field(pattern=r"^muse-image-[A-Za-z0-9_-]{1,48}$")
    workflowSha256: str=Field(pattern=SHA)
    resourceId: str=Field(pattern=ATTEMPT)
    videoJobsEnabled: StrictBool


class Lease(Strict):
    epoch: int=Field(ge=1,strict=True)
    lease: SecretStr=Field(min_length=48,max_length=128)


class Cooldown(Lease):
    providerStatus: int=Field(ge=429,le=429,strict=True)
    retryAfterSeconds: int=Field(ge=60,le=86400,strict=True)


class Start(Lease):
    attemptId: str=Field(pattern=ATTEMPT)


class Qa(Strict):
    attemptId: str=Field(pattern=ATTEMPT)
    epoch: int=Field(ge=1,strict=True)
    imageSha256: str=Field(pattern=SHA)
    reviewEvidenceSha256: str=Field(pattern=SHA)
    width: int=Field(ge=1600,le=4000,strict=True)
    height: int=Field(ge=1600,le=4000,strict=True)
    checks: dict[str,StrictBool]


QA_CHECKS=frozenset(("artworkFidelity","flatInk","garmentAndShade","roleFraming",
                      "placementVisual","poseAndOcclusion","lighting","occasion"))


@router.get("/work")
def work(response:Response,identity:Identity,db:Db):
    private(response)
    rows=active(db,identity.tenant)
    # Never return a lease or plan to an Extension/nonassigned Muse account.
    items=[descriptor(j) for j in rows if ready(db,j,identity,now())]
    return {"protocol":PROTOCOL,"jobType":"image","queueBackend":"processing_jobs",
            "schemaMigrationRequired":False,"pilotOnly":True,"maxConcurrent":1,
            "imageWorkerCredentialConfigured":image_worker_configured(),
            "imageClaimAllowed":len(rows)==1 and len(items)==1,
            "status":"PILOT_READY" if len(rows)==1 and len(items)==1 else "PREPARED_DISABLED",
            "items":items if len(rows)==1 else [],"videoQueueTouched":False,
            "activeImageCommands":len(rows),
            "qaProtocol":lite_p0.QA_PROTOCOL,"stageRequired":True,"minNativePixels":lite_p0.MIN_NATIVE,
            "prepared":[descriptor(j) for j in rows if identity.kind=="muse" and
                        j.payload_json.get("workerId")==identity.worker]}


@router.post("/operator/prepare",status_code=201)
def prepare(body:Prepare,tenant:Operator,db:Db):
    # Only existing independent CAM operator authorization can prepare;
    # a renderer bearer is never allowed to authorize itself.
    check(len(str(body.plan))<32768,"plan_invalid",422)
    lock_tenant(db,tenant)
    check(not active(db,tenant),"another_image_pilot_active")
    plan=body.plan
    try:
        fields={"skillSha256","garmentSku","artworkSha256","sourceSha256","storagePlacement",
                "placementLock","mockupSize","paletteSha256","roleColors","infographicSwatches",
                "infographicSourceRoles"}
        check(fields.issubset(plan) and set(plan)<=fields|{"smsAdvisoryWidthMm"},
              "image_plan_fields_invalid",422)
        ident=p.PackIdentity(tenant,body.listingId,plan["artworkSha256"],"C1717",1,
                            plan["placementLock"]["lock_sha256"],plan["sourceSha256"])
        payload={"protocol":PROTOCOL,"listingId":body.listingId,"packId":ident.pack_id,
             "plan":plan,"planSha256":p.canonical_sha(plan),"phase":"prepared","epoch":0,
             "workerId":body.workerId,"runAllowed":False,"operatorAttestedImageResource":True,
             "runnerId":body.runnerId,"workflowSha256":body.workflowSha256,"resourceId":body.resourceId,
             "independentImageResourceEvidenceSha256":body.independentImageResourceEvidenceSha256,
             "sourceVisualReviewSha256":body.sourceVisualReviewSha256,
             "placementEvidenceSha256":body.placementEvidenceSha256,
             "nativeCapabilityEvidenceSha256":body.nativeCapabilityEvidenceSha256,
             "nativeOutputSize":body.nativeOutputSize}
        admission.validate_plan(plan,pack_view(payload,tenant))
    except (p.Conflict,KeyError,ValueError,TypeError):
        fail("image_plan_invalid",422)
    check(db.get(Picked,(tenant,body.listingId)) is not None,"listing_not_picked")
    # Invariant: one immutable pilot for a listing/artwork; no automatic reset.
    key="cam-image-lite:"+ident.role_key(1)
    check(db.scalar(select(Job.id).where(Job.tenant_id==tenant,Job.idempotency_key==key)) is None,
          "image_pilot_already_exists")
    job=Job(tenant_id=tenant,job_type=TYPE,entity_type=ENTITY,entity_id=body.listingId,
            idempotency_key=key,payload_json=payload,status="pending",provider_key="muse_image_lite",
            provider_scope="external",concurrency_accounted=False,max_attempts=5,attempt_count=0)
    current_asset(db,job)
    no_gallery(db,job)
    db.add(job);db.commit()
    return {**descriptor(job),"imageClaimAllowed":False,"requiresExtensionFence":True,
            "requiresOperatorEnable":True}


@router.get("/reservations")
def reservations(response:Response,identity:Identity,db:Db):
    private(response)
    check(identity.kind=="chatgpt_extension","image_agent_required",403)
    rows=active(db,identity.tenant)
    return {"protocol":PROTOCOL,"reservations":[{"commandId":j.id,"listingId":j.entity_id,
                "phase":j.payload_json["phase"]} for j in rows],"authoritative":True}


@router.post("/commands/{cid}/legacy-fence")
def fence(cid:str,body:Fence,identity:Identity,db:Db):
    check(identity.kind=="chatgpt_extension","image_agent_required",403)
    lock_tenant(db,identity.tenant)
    check(identity.worker==sole_image_agent(db,identity.tenant),"single_image_agent_required")
    job=get_job(db,identity.tenant,cid,lock=True);d=job.payload_json
    check(job.status in ("pending","processing") and body.listingId==job.entity_id,
          "legacy_fence_scope_mismatch")
    previous=d.get("legacyFence")
    check(not previous or previous.get("agent")==identity.worker,"legacy_fence_owner_mismatch")
    set_payload(job,legacyFence={"mode":body.mode,"agent":identity.worker,
        "ledgerSha256":body.ledgerSha256,"expiresAt":now()+FENCE_TTL})
    db.commit()
    return {"commandId":cid,"fenced":True,"expiresAt":job.payload_json["legacyFence"]["expiresAt"]}


@router.post("/commands/{cid}/runner-ready")
def runner_heartbeat(cid:str,body:RunnerHeartbeat,identity:Identity,db:Db):
    muse(identity);lock_tenant(db,identity.tenant)
    job=get_job(db,identity.tenant,cid,lock=True);d=job.payload_json
    check(job.status in ("pending","processing") and d["workerId"]==identity.worker
          and body.videoJobsEnabled is False and body.runnerId==d["runnerId"]
          and body.workflowSha256==d["workflowSha256"] and body.resourceId==d["resourceId"],
          "image_runner_binding_mismatch")
    set_payload(job,imageRunnerHeartbeat={"runnerId":body.runnerId,
                "workflowSha256":body.workflowSha256,"resourceId":body.resourceId,
                "expiresAt":now()+FENCE_TTL})
    db.commit()
    return {"commandId":cid,"runnerBound":True,"imageOnly":True,"videoQueueTouched":False}


@router.post("/operator/commands/{cid}/cancel")
def cancel(cid:str,tenant:Operator,db:Db):
    lock_tenant(db,tenant);job=get_job(db,tenant,cid,lock=True)
    check(job.status=="pending" and job.payload_json["phase"]=="prepared" and
          len(job.payload_json.get("attemptHistory",[]))==job.attempt_count,
          "image_reconciliation_required")
    set_payload(job,phase="cancelled",runAllowed=False)
    job.status="failed";job.completed_at=datetime.now(timezone.utc);db.commit()
    return {**descriptor(job),"legacyReservationRequired":False}


@router.post("/commands/{cid}/cooldown")
def cooldown(cid:str,body:Cooldown,identity:Identity,db:Db):
    lock_tenant(db,identity.tenant);job=get_job(db,identity.tenant,cid,lock=True)
    own(job,identity,body,phases=("claimed","started","awaiting_qa","qa_pass","upload_pending"))
    until=now()+body.retryAfterSeconds
    d=job.payload_json
    if d["phase"]=="claimed":
        job.status="pending";job.claimed_by=None;job.claimed_at=None;job.lease_expires_at=None
        set_payload(job,phase="prepared",runAllowed=False,cooldownUntilEpoch=until,
                    leaseHash=None,expiresAt=None,originalServedSha256=None)
    else:
        set_payload(job,runAllowed=False,cooldownUntilEpoch=until)
    db.commit()
    return {"commandId":cid,"cooldownUntilEpoch":until,"imageOnly":True,
            "videoQueueTouched":False,"attemptPreserved":job.attempt_count>0}


@router.post("/operator/commands/{cid}/enable")
def enable(cid:str,tenant:Operator,db:Db):
    lock_tenant(db,tenant);job=get_job(db,tenant,cid,lock=True)
    d=job.payload_json
    check(image_worker_configured() or db.info.get("muse_image_lite_test") is True,
          "image_worker_credential_not_configured",503)
    check(job.status=="pending" and d["phase"]=="prepared" and fence_verified(db,job,now()) and
          runner_ready(d,now()) and d.get("cooldownUntilEpoch",0)<=now() and lite_p0.capability(d)
          and job.attempt_count<lite_p0.MAX_ATTEMPTS and d["epoch"]<3,
          "verified_extension_reservation_required")
    current_asset(db,job);no_gallery(db,job)
    set_payload(job,runAllowed=True);db.commit()
    return {**descriptor(job),"imageClaimAllowed":True}


@router.post("/operator/commands/{cid}/stop")
def stop(cid:str,tenant:Operator,db:Db):
    lock_tenant(db,tenant);job=get_job(db,tenant,cid,lock=True)
    # STOP only removes new Send permission. Existing attempts and receipts remain.
    set_payload(job,runAllowed=False);db.commit()
    return {**descriptor(job),"imageClaimAllowed":False,"inFlightPreserved":True}


@router.post("/commands/{cid}/claim")
def claim(cid:str,identity:Identity,db:Db,
          skill:Annotated[str,Header(alias="X-CAM-Skill-Sha256")]):
    muse(identity);lock_tenant(db,identity.tenant)
    job=get_job(db,identity.tenant,cid,lock=True)
    check(len(active(db,identity.tenant))==1 and ready(db,job,identity,now()),"image_claim_not_allowed")
    check(skill==p.SKILL_SHA256,"image_skill_changed")
    current_asset(db,job);no_gallery(db,job)
    token=secrets.token_urlsafe(36);t=now()
    job.status="processing";job.claimed_by=identity.worker
    job.claimed_at=datetime.now(timezone.utc);job.lease_expires_at=datetime.fromtimestamp(t+LEASE_TTL,timezone.utc)
    # One canary Send only. Generic exhausted-job maintenance never owns this row.
    epoch=job.payload_json["epoch"]+1
    check(epoch<=3,"image_pilot_claim_limit")
    set_payload(job,phase="claimed",epoch=epoch,leaseHash=p.digest(token),expiresAt=t+LEASE_TTL,
                originalServedSha256=None)
    db.commit()
    return {**descriptor(job),"epoch":epoch,"lease":token,"expiresAtEpoch":t+LEASE_TTL,
            "plan":job.payload_json["plan"],"artworkUrl":f"{PREFIX}/commands/{cid}/artwork",
            "imageOnly":True,"maxOutputs":1}


@router.post("/commands/{cid}/artwork")
async def artwork(cid:str,body:Lease,identity:Identity,db:Db):
    job=get_job(db,identity.tenant,cid)
    own(job,identity,body,phases=("claimed",))
    source=current_asset(db,job)
    from app.modules.muse_bridge.router import _read_original_artwork
    result=await _read_original_artwork(db,identity.tenant,source["jobId"],source["assetId"],
            source["artworkSha256"],"muse-image-native.png")
    # Re-read and bind exact source serving to the still-current lease.
    lock_tenant(db,identity.tenant);job=get_job(db,identity.tenant,cid,lock=True)
    own(job,identity,body,phases=("claimed",))
    current_asset(db,job)
    set_payload(job,originalServedSha256=source["artworkSha256"]);db.commit()
    return result


@router.post("/commands/{cid}/heartbeat")
def heartbeat(cid:str,body:Lease,identity:Identity,db:Db):
    lock_tenant(db,identity.tenant);job=get_job(db,identity.tenant,cid,lock=True)
    own(job,identity,body,phases=("claimed","started","awaiting_qa","qa_pass","upload_pending"))
    t=now()+LEASE_TTL
    job.lease_expires_at=datetime.fromtimestamp(t,timezone.utc)
    set_payload(job,expiresAt=t);db.commit()
    return {"commandId":cid,"epoch":body.epoch,"expiresAtEpoch":t,
            "runAllowed":job.payload_json["runAllowed"]}


@router.post("/commands/{cid}/start")
def start(cid:str,body:Start,identity:Identity,db:Db):
    lock_tenant(db,identity.tenant);job=get_job(db,identity.tenant,cid,lock=True)
    own(job,identity,body,phases=("claimed","started"))
    d=job.payload_json
    if d["phase"]=="started":
        check(d.get("attemptId")==body.attemptId,"image_attempt_mismatch")
        return {"sendAllowed":False,"originalSendPreserved":True}
    check(d["runAllowed"] is True and fence_verified(db,job,now()) and runner_ready(d,now())
          and lite_p0.capability(d) and d.get("originalServedSha256")==d["plan"]["artworkSha256"],"image_start_not_allowed")
    current_asset(db,job);no_gallery(db,job)
    check(job.attempt_count<lite_p0.MAX_ATTEMPTS and
          body.attemptId not in [x["rejection"]["attemptId"] for x in d.get("attemptHistory",[])],
          "image_pilot_retry_limit")
    job.attempt_count+=1
    set_payload(job,phase="started",attemptId=body.attemptId)
    db.commit()
    return {"sendAllowed":True,"role":1,"attemptId":body.attemptId,"maxOutputs":1}


@router.post("/operator/commands/{cid}/qa")
def qa(cid:str,body:Qa,tenant:Operator,db:Db):
    lock_tenant(db,tenant);job=get_job(db,tenant,cid,lock=True);d=job.payload_json
    check(job.status=="processing" and d["phase"] in ("awaiting_qa","qa_pass")
          and body.epoch==d["epoch"] and body.attemptId==d["attemptId"],"image_attempt_mismatch")
    check(set(body.checks)==QA_CHECKS and all(v is True for v in body.checks.values())
          and body.width==body.height,"image_qa_not_accepted")
    stage,_=lite_p0.require_stage(job,body.imageSha256,body.width,body.height)
    check(body.width>=lite_p0.MIN_NATIVE and not stage["previewOnly"],"image_native_publication_size_not_met")
    current_asset(db,job)
    receipt=p.canonical_sha([job.id,d["epoch"],d["attemptId"],body.imageSha256,
                             body.reviewEvidenceSha256,d["planSha256"],body.width,body.height,stage["stageReceiptId"]])
    proof={"receiptId":receipt,"imageSha256":body.imageSha256,"width":body.width,
           "height":body.height,"reviewEvidenceSha256":body.reviewEvidenceSha256,
           "checks":body.checks,"stageReceiptId":stage["stageReceiptId"]}
    check(d.get("qa") in (None,proof),"immutable_image_qa")
    set_payload(job,phase="qa_pass",qa=proof);db.commit()
    return {"qaReceiptId":receipt,"status":"pass","imageSha256":body.imageSha256}


@router.get("/commands/{cid}/qa-status")
def qa_status(cid:str,response:Response,identity:Identity,db:Db,
              attemptId:Annotated[str,Query(pattern=ATTEMPT)],
              imageSha256:Annotated[str,Query(pattern=SHA)]):
    private(response);muse(identity);job=get_job(db,identity.tenant,cid);d=job.payload_json
    check(d["workerId"]==identity.worker,"image_attempt_not_found",404)
    rejected=lite_p0.rejection_status(job,attemptId,imageSha256)
    if rejected is not None:return rejected
    check(job.claimed_by==identity.worker and d.get("attemptId")==attemptId,"image_attempt_not_found",404)
    proof=d.get("qa")
    ok=isinstance(proof,dict) and proof["imageSha256"]==imageSha256
    return {"status":"pass" if ok else "pending",
            "qaReceiptId":proof["receiptId"] if ok else None,"imageSha256":imageSha256,
            "stageReceiptId":proof.get("stageReceiptId") if ok else None}


def inspect_png(data, sha):
    check(57<=len(data)<=MAX_BYTES and hashlib.sha256(data).hexdigest()==sha
          and data.startswith(b"\x89PNG\r\n\x1a\n"),"invalid_image_png",422)
    try:
        with Image.open(io.BytesIO(data)) as im:
            check(im.format=="PNG" and im.width==im.height and 1600<=im.width<=4000
                  and im.width*im.height<=16_000_000 and getattr(im,"n_frames",1)==1,
                  "invalid_image_dimensions",422)
            im.verify()
            size=im.size
        with Image.open(io.BytesIO(data)) as im:
            im.load()
    except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError):
        fail("invalid_image_png",422)
    return size


@router.put("/commands/{cid}/image")
async def upload(cid:str,request:Request,identity:Identity,db:Db,
        epoch:Annotated[int,Header(alias="X-CAM-Epoch",ge=1)],
        lease:Annotated[str,Header(alias="X-CAM-Image-Lease",pattern=r"^[A-Za-z0-9_-]{48,128}$")],
        sha:Annotated[str,Header(alias="X-CAM-Image-Sha256",pattern=SHA)],
        qa_receipt:Annotated[str,Header(alias="X-CAM-QA-Receipt",pattern=SHA)]):
    body=Lease(epoch=epoch,lease=lease)
    job=get_job(db,identity.tenant,cid)
    own(job,identity,body,phases=("qa_pass","upload_pending"))
    check(request.headers.get("content-type","").split(";")[0].strip()=="image/png","image_png_required",415)
    # No database lock while receiving or decoding the bounded PNG.
    db.rollback()
    data=bytearray()
    async for chunk in request.stream():
        check(len(data)+len(chunk)<=MAX_BYTES,"image_png_too_large",413)
        data.extend(chunk)
    width,height=inspect_png(data,sha)
    check(width>=lite_p0.MIN_NATIVE,"image_native_publication_size_not_met",422)
    lock_tenant(db,identity.tenant);job=get_job(db,identity.tenant,cid,lock=True)
    own(job,identity,body,phases=("qa_pass","upload_pending"))
    d=job.payload_json;proof=d["qa"]
    staged,_=lite_p0.require_stage(job,sha,width,height)
    check(proof.get("stageReceiptId")==staged["stageReceiptId"],"image_qa_stage_receipt_mismatch")
    check(proof["receiptId"]==qa_receipt and proof["imageSha256"]==sha
          and (proof["width"],proof["height"])==(width,height),"image_qa_receipt_mismatch")
    # A lost/uncertain storage call must be reconciled by operator, not replayed.
    check(d["phase"]=="qa_pass","image_upload_reconciliation_required")
    current_asset(db,job);no_gallery(db,job)
    gallery_id="sp-"+p.digest("cam-image-lite-gallery:"+d["packId"])[:40]
    set_payload(job,phase="upload_pending",uploadIntentSha256=sha,galleryPackId=gallery_id)
    listing=job.entity_id
    db.commit()
    from app.modules.etsy_spy.imagegen_drive import store_listing_output_to_drive, read_design_from_drive
    plan=d["plan"]
    remote=await store_listing_output_to_drive(tenant_id=identity.tenant,pack_id=gallery_id,slot=1,
        digest=sha,png=bytes(data),listing_id=listing,design_sha256=plan["artworkSha256"],
        placement="front",garment_code="C1717",expected_slots=13,role_contract=p.ROLE_CONTRACT)
    check((remote["native_width"],remote["native_height"])==(width,height),"drive_native_dimensions_mismatch")
    for label,id_key,hash_value,limit in (
        ("original","drive_file_id",sha,MAX_BYTES),
        ("thumbnail","drive_thumbnail_id",remote["thumbnail_sha256"],2*1024*1024+1)):
        saved=await read_design_from_drive(tenant_id=identity.tenant,
            asset_id=f"{gallery_id}:listing-01:{label}",remote_id=remote[id_key],
            sha256=hash_value,max_bytes=limit)
        check(hashlib.sha256(saved).hexdigest()==hash_value,"drive_checksum_mismatch")
    # Reconcile final receipt and gallery atomically in the existing database.
    lock_tenant(db,identity.tenant);job=get_job(db,identity.tenant,cid,lock=True)
    own(job,identity,body,phases=("upload_pending",))
    d=job.payload_json
    check(d["uploadIntentSha256"]==sha and d["qa"]["receiptId"]==qa_receipt,
          "image_upload_identity_changed")
    check(db.scalar(select(Picked).where(Picked.tenant_id==identity.tenant,
                Picked.listing_key==listing).with_for_update()) is not None,"listing_not_picked")
    current_asset(db,job);no_gallery(db,job)
    from app.modules.etsy_spy.sms_mockup_sync import metadata as sms_color_metadata
    color_name = admission.PALETTE[plan["roleColors"]["01"]][0]
    color_meta = sms_color_metadata(sku="C1717",slot=1,design_sha=plan["artworkSha256"],
        image_sha=sha,color_name=color_name,source="verified_muse_plan")
    db.add(Gallery(tenant_id=identity.tenant,listing_key=listing,pack_id=gallery_id,slot=1,
        garment_code="C1717",design_sha256=plan["artworkSha256"],source_sha256=plan["sourceSha256"],
        placement="front",output_sha256=sha,thumbnail_sha256=remote["thumbnail_sha256"],
        drive_file_id=remote["drive_file_id"],drive_thumbnail_id=remote["drive_thumbnail_id"],
        drive_folder_id=remote["drive_folder_id"],drive_thumbnail_folder_id=remote["drive_thumbnail_folder_id"],
        native_width=width,native_height=height,source_job_id=job.id,sms_color_metadata=color_meta))
    receipt={"imageSha256":sha,"width":width,"height":height,"storageVerified":True,
             "qaReceiptId":qa_receipt,"publicationReady":True,"previewOnly":False,
             "galleryPackId":gallery_id,"role":1}
    set_payload(job,phase="complete",receipt=receipt,runAllowed=False,leaseHash=None)
    job.status="completed";job.completed_at=datetime.now(timezone.utc);job.lease_expires_at=None
    db.commit()
    return receipt


@router.get("/commands/{cid}/status")
def status(cid:str,response:Response,identity:Identity,db:Db):
    private(response);job=get_job(db,identity.tenant,cid);d=job.payload_json
    check(identity.kind=="muse" and d["workerId"]==identity.worker or
          identity.kind=="chatgpt_extension" and (d.get("legacyFence") or {}).get("agent")==identity.worker,
          "image_command_not_found",404)
    return {**descriptor(job),"receipt":d.get("receipt"),
            "reconciliationRequired":job.status=="processing" and
                (d["phase"]=="upload_pending" or (d.get("expiresAt") or 0)<=now()),
            "legacyReservationRequired":job.status in ("pending","processing"),
            "videoQueueTouched":False}

# Register isolated image QA staging/recovery routes only after definitions.
from . import lite_p0