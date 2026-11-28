"""P2a image-only, authenticated read API (no claim, render or storage writes).

The candidate registry can be compared before enabling the actual scheduler.
Do not interpret disabled discovery as an empty production queue. Counts are
central-store counts only, never claims about the legacy Extension backlog.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from . import policy as p
from .identity import ImageIdentity, image_identity
from .model import ImageBatch, ImageFlowControl, ImagePack, ImageRole
from .readiness import schema_evidence

REGISTRY = Path(__file__).with_name("skill_registry_candidate.json")
BLOCKERS = (
    "FULL_PRODUCTION_MIGRATION_AND_RESTORE_NOT_VERIFIED",
    "TRUSTED_ARTWORK_AND_QA_REVIEWER_NOT_CONNECTED",
    "LIVE_RESOURCE_INTEGRATION_NOT_VERIFIED",
    "LIVE_DRIVE_AND_RECONCILIATION_NOT_VERIFIED",
    "IMAGE_CLAIM_API_NOT_ENABLED",
    "LEGACY_EXTENSION_CUTOVER_NOT_VERIFIED",
)


def is_read_api_enabled() -> bool:
    # P2D: expose authenticated discovery after additive schema upgrade.
    # Explicit false is a read-only rollback switch; never grants execution.
    return os.environ.get("CAM_IMAGE_READ_API_ENABLED", "true").lower() in ("1", "true")


def read_api_enabled() -> None:
    if not is_read_api_enabled():
        raise HTTPException(404, "image_api_not_enabled")


router = APIRouter(
    prefix="/api/v1/muse-bridge", tags=["image-orchestrator"],
    dependencies=[Depends(read_api_enabled)],
)
Identity = Annotated[ImageIdentity, Depends(image_identity)]
Database = Annotated[Session, Depends(get_db)]
ListingId = Annotated[str, ApiPath(pattern=r"^[0-9]{5,20}$")]
PackId = Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]
Sku = Annotated[str, Query(pattern=r"^(C1717|G500|G180|G185)$")]


def _no_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _registry() -> dict:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        valid = (
            data["protocol"] == p.PROTOCOL
            and data["skillName"] == "print-variant-model-10-image"
            and data["skillVersion"] == p.SKILL_VERSION
            and data["skillSha256"] == p.SKILL_SHA256
            and data["roleContract"] == p.ROLE_CONTRACT
            and data["placementPolicy"] == p.PLACEMENT_CONTRACT
            and data["qaPolicy"] == p.QA_POLICY
            and data["neckTagRole"] == 4
            and data["nativePilotMinimumPx"] == p.PILOT_MIN_NATIVE
            and data["publicationMinimumPx"] == p.PUBLICATION_MIN_NATIVE
            and data["claimEnabled"] is False
            and isinstance(data["files"], dict)
            and len(data["files"]) == 19
            and all(isinstance(k, str) and k.startswith("print-variant-model-10-image/")
                    and ".." not in k.split("/") and p.valid_sha(v)
                    for k, v in data["files"].items())
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(503, "image_registry_invalid")
    return data


def _gate(identity: ImageIdentity, db: Session) -> dict:
    evidence = schema_evidence(db)
    blockers = list(BLOCKERS)
    if not evidence["centralSchemaReady"]:
        blockers.insert(0, "CENTRAL_SCHEMA_NOT_MIGRATED")
    from .live_runtime import readiness
    state=readiness(db,identity) if evidence["centralSchemaReady"] else {}
    allowed=state.get("claimAllowed") is True
    # Capability rows are writable only by trusted CAM operators. They must
    # not be enabled until the actual recovery, provider and cutover checks
    # have been completed. An authenticated worker is not that authority.
    if allowed:
        blockers=[]
    return {**evidence,**state,
        "protocol": ("CAM_MUSE_IMAGE_ADAPTIVE_BATCH_V6_1"
                     if identity.worker=="muse-image-01" else p.PROTOCOL),
        "jobType": "image",
        "workerId": identity.worker,
        "workerKind": identity.kind,
        "claimEnabled": allowed,
        "imageClaimAllowed": allowed,
        "liveClaimEnabled": allowed,
        "rendererCalled": False,
        "rendererAllowed": allowed,
        "status": "READY" if allowed else "PREPARED_DISABLED",
        "blockers": blockers,
    }


@router.get("/skill-registry")
def skill_registry(response: Response, identity: Identity, db: Database):
    _no_cache(response)
    candidate={}
    if identity.worker=="muse-image-01":
        from .adaptive_v61 import REGISTRY, BUNDLE_SHA256
        candidate={**REGISTRY,"protocol":"CAM_MUSE_IMAGE_ADAPTIVE_BATCH_V6_1",
                   "resourceId":"muse-native-imagegen-01","bundleSha256":BUNDLE_SHA256,
                   "creativePlanProtocol":"CAM_IMAGE_CREATIVE_PLAN_V7",
                   "creativePlanPreviewAllowed":True,"creativePlanRenderAllowed":False,
                   "controlledHybridStatus":"PREVIEW_ONLY_UNVALIDATED",
                   "garmentGeometryProtocol":"CAM_SMS_GARMENT_GEOMETRY_V1",
                   "garmentGeometryMetadataOnly":True}
        from .garment_geometry import SNAPSHOT_SHA256
        candidate["garmentGeometrySnapshotSha256"]=SNAPSHOT_SHA256
    return {**_registry(), **candidate, **_gate(identity, db),
            **({"protocol":candidate["protocol"]} if candidate else {}),
            "skillProtocol":p.PROTOCOL,
            "registryAudience":"dedicated_image_worker" if candidate else "legacy_worker",
            "nativeOutputAspectRatio":"1:1",
            "nativeOutputMinimumSquarePx":p.PILOT_MIN_NATIVE,
            "registryStatus": "candidate",
            "paletteVerified": False,
            "printBoxAuthority": "CAM_PRINT_BOX_SINGLE_AUTHORITY_V1",
            "waveBarrier": "global_wave1_before_wave2_per_batch",
            "holdIsolationSeconds": p.BARRIER_SECONDS,
            "leaseSeconds": p.LEASE_SECONDS, "heartbeatSeconds": p.HEARTBEAT_SECONDS}


def _require_candidate_reader(db,identity):
    if identity.kind!="muse" or identity.worker!="muse-image-01":
        raise HTTPException(403,"dedicated_image_worker_required")
    if not schema_evidence(db)["centralSchemaReady"]:
        raise HTTPException(503,"image_central_schema_unverified")


@router.get("/image-candidates")
def image_candidates(response: Response, identity: Identity, db: Database,
                     after: Annotated[str,Query(pattern=r"^([0-9]{5,20})?$")]="",
                     limit: Annotated[int,Query(ge=1,le=100)]=50):
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .candidate_discovery import discover
    return discover(db,identity.tenant,after,limit)


@router.get("/jobs/{listing_id}/image-source-proof")
async def image_source_proof(response: Response,identity: Identity,db: Database,
                            listing_id: ListingId,
                            artworkSha256: Annotated[str,Query(pattern=r"^[a-f0-9]{64}$")]):
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .candidate_discovery import verify_original
    return await verify_original(db,identity.tenant,listing_id,artworkSha256)


@router.post("/jobs/{listing_id}/image-runtime-preflight")
async def image_runtime_preflight(response: Response,identity: Identity,db: Database,
                                  listing_id: ListingId,
                                  artworkSha256: Annotated[str,Query(pattern=r"^[a-f0-9]{64}$")]):
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .live_preflight import preflight,PreflightIncomplete,retry_headers
    try:
        return await preflight(db,identity.tenant,listing_id,artworkSha256)
    except PreflightIncomplete as exc:
        raise HTTPException(503,exc.detail,headers=retry_headers(exc.detail)) from None
    except HTTPException:
        raise
    except p.Conflict as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,"image_preflight_provider_unavailable") from None


@router.get("/jobs/{listing_id}/image-original")
async def image_original(identity: Identity,db: Database,listing_id: ListingId,
                         artworkSha256: Annotated[str,Query(pattern=r"^[a-f0-9]{64}$")]):
    _require_candidate_reader(db,identity)
    from .candidate_discovery import load_original
    response,_=await load_original(db,identity.tenant,listing_id,artworkSha256)
    return response


@router.get("/image-commands")
def image_commands(response: Response, identity: Identity, db: Database,
                   limit: Annotated[int, Query(ge=1, le=20)] = 20):
    _no_cache(response)
    gate=_gate(identity,db)
    if gate["imageClaimAllowed"]:
        from .live_runtime import descriptors
        items=descriptors(db,identity,limit)
        return {**gate,"items":items,"commands":items,"queueAuthoritative":True,
                "counterScope":"admitted_central_packs_only","limit":limit}
    return {**gate, "items": [], "commands": [],
            "queueAuthoritative": False, "reason": "IMAGE_CLAIMS_DISABLED",
            "limit": limit}


@router.get("/image-work")
def image_work(response: Response, identity: Identity, db: Database,
               limit: Annotated[int, Query(ge=1, le=20)] = 20):
    _no_cache(response)
    gate = _gate(identity, db)
    if db.get_bind().dialect.name == "postgresql" and not gate["centralSchemaReady"]:
        return {**gate, "flowRunning":False, "items":[], "limit":limit,
                "centralPackCount":None,"centralRoleCounts":None,
                "remainingWithoutImages":None,"queueAuthoritative":False,
                "reason":"CENTRAL_SCHEMA_NOT_MIGRATED"}
    tenant = identity.tenant
    flow = db.get(ImageFlowControl, tenant)
    packs = db.scalar(select(func.count()).select_from(ImagePack).where(
        ImagePack.tenant_id == tenant)) or 0
    roles = dict(db.execute(select(ImageRole.status, func.count()).where(
        ImageRole.tenant_id == tenant).group_by(ImageRole.status)).all())
    from .live_runtime import descriptors
    items=descriptors(db,identity,limit) if gate["imageClaimAllowed"] else []
    return {**gate,
            "flowRunningRequested": bool(flow and flow.running),
            "flowRunning": gate["imageClaimAllowed"], "items": items, "limit": limit,
            "centralPackCount": packs,
            "centralRoleCounts": {key: int(roles.get(key, 0)) for key in (
                "pending", "dispatch_uncertain", "upload_pending",
                "reconcile_required", "verified", "hold")},
            "remainingWithoutImages": None, "queueAuthoritative": False,
            "counterScope": "central_only_legacy_not_reconciled"}


def _owned_pack(db: Session, identity: ImageIdentity, listing: str,
                pack_id: str, sku: str) -> ImagePack:
    # In this read-only phase no discovery of unowned or other-worker packs.
    if db.get_bind().dialect.name == "postgresql" and not schema_evidence(db)["centralSchemaReady"]:
        raise HTTPException(503,"image_central_schema_unverified")
    row = db.scalar(select(ImagePack).where(
        ImagePack.tenant_id == identity.tenant,
        ImagePack.listing_key == listing,
        ImagePack.pack_id == pack_id,
        ImagePack.garment_sku == sku))
    permitted=bool(row and row.owner==identity.worker)
    if row is not None and row.owner is None and identity.worker=="muse-image-01":
        from .live_runtime import can_bind,admission_ready
        permitted=can_bind(db,identity) and admission_ready(db,identity,row)
    if not permitted:
        raise HTTPException(404, "image_pack_not_found")
    return row


def _receipt(row: ImageRole, pack: ImagePack) -> dict | None:
    raw = row.receipt_state
    if row.status != "verified" or not isinstance(raw, dict):
        return None
    # Allowlist fields, not a raw database JSON dump. Never expose lease hashes
    # or arbitrary worker notes. These are historical stored receipts, not
    # fresh Drive verification or a claim grant.
    try:
        item = p.StoredReceipt(**raw)
        if not (
            item.storage_verified is True
            and item.qa_status == "pass"
            and item.qa_image_sha == item.image_sha
            and p.valid_sha(item.image_sha)
            and item.placement_sha == pack.placement_sha256
            and isinstance(item.drive_file_id, str)
            and p._ID.fullmatch(item.drive_file_id)
            and type(item.width) is int and type(item.height) is int
            and item.width == item.height
            and p.PILOT_MIN_NATIVE <= item.width <= 8192
        ):
            return None
        from . import native_first as native
        profile=native.profile_from_plan(pack.approved_plan)
        if profile is None:
            if item.publication_scope!=(p.PUBLICATION_MIN_NATIVE,None,None,None):
                return None
        elif (p.canonical_sha(pack.approved_plan)!=pack.plan_sha256
                or item.generation_method!=native.NATIVE
                or item.publication_minimum_px!=profile.minimum_px
                or item.operator_policy_receipt_sha!=profile.operator_receipt_sha
                or not item.publication_ready):
            return None
    except (p.Conflict,TypeError, ValueError, AttributeError):
        return None
    result = {
        "imageSha256": item.image_sha,
        "storageVerified": True,
        "driveFileId": item.drive_file_id,
        "dimensions": {"width": item.width, "height": item.height, "aspect": "1:1"},
        "qa": {"policy": p.QA_POLICY, "status": "pass", "imageSha256": item.qa_image_sha},
        "previewOnly": not item.publication_ready,
        "publicationReady": item.publication_ready,
        "status": "verified",
    }
    if profile is not None:
        result.update(generationMethod=item.generation_method,
            minimumPublicationPx=item.publication_minimum_px,
            qaRecordSha256=item.qa_record_sha,
            physicalCalibrationVerified=False)
        result["qa"]["policy"]="CAM_IMAGE_NATIVE_FIRST_V5_CANDIDATE"
    return result


@router.get("/jobs/{listing_id}/image-status")
def image_status(response: Response, identity: Identity, db: Database,
                 listing_id: ListingId, packId: PackId, sku: Sku):
    _no_cache(response)
    pack = _owned_pack(db, identity, listing_id, packId, sku)
    batch = db.get(ImageBatch, (identity.tenant, pack.batch_id))
    if batch is None or packId not in batch.pack_ids:
        raise HTTPException(503, "image_pack_state_unavailable")
    rows = list(db.scalars(select(ImageRole).where(
        ImageRole.tenant_id == identity.tenant,
        ImageRole.pack_id == packId).order_by(ImageRole.role)))
    if [r.role for r in rows] != list(range(1, 14)):
        raise HTTPException(503, "image_pack_state_unavailable")
    verified = []
    roles = []
    for row in rows:
        receipt = _receipt(row, pack)
        status = row.status
        if status == "verified" and receipt is None:
            status = "reconcile_required"
        if receipt is not None:
            verified.append(row.role)
        roles.append({"role": row.role, "status": status,
                      "attempts": row.attempts,
                      "receipt": receipt})
    lease = pack.lease_state or {}
    expiry = lease.get("expires_at")
    active = None
    if type(expiry) is int and expiry > int(time.time()) and lease.get("owner") == identity.worker:
        active = {"worker": identity.worker, "expiresAtEpoch": expiry,
                  "wave": lease.get("wave"), "epoch": pack.epoch}
    wave1 = all(i in verified for i in p.WAVES[1])
    wave2 = all(i in verified for i in p.WAVES[2])
    payload={**_gate(identity, db), "listingId": listing_id, "packId": packId,
            "garmentSku": sku, "packRevision": pack.pack_revision,
            "batchId": pack.batch_id, "artworkSha256": pack.artwork_sha256,
            "placementLockSha256": pack.placement_sha256,
            "skillSha256": pack.skill_sha256,
            "artworkRevisionChanged": None,  # not checked against latest artwork yet
            "artworkRevisionVerified": False,
            "packComplete": wave1 and wave2, "wave1Complete": wave1,
            "wave2Complete": wave2,
            "wave2Allowed": bool(batch.release_decision and
                                 packId in batch.release_decision["wave2_eligible"]),
            "verifiedRoles": verified,
            "completedRoles": len(verified),
            "totalRoles": 13,
            "missingRoles": [i for i in range(1,14) if i not in verified],
            "roles": roles, "activeLease": active,
            "publicationReady": len(verified) == 13 and
                                all(r["receipt"]["publicationReady"] for r in roles),
            "contractReady": False}
    if identity.worker=="muse-image-01":
        from .adaptive_v61 import barrier_snapshot
        from .qa_submission import storage_receipt_id,ImageQARecordSubmission
        import re
        # The renderer needs the actual rejected defects after restart,
        # not a generic retry prompt. Only bounded machine-readable codes
        # from this tenant/pack's immutable local-rejection ledger leave CAM.
        defects={}
        for submission in db.scalars(select(ImageQARecordSubmission).where(
            ImageQARecordSubmission.tenant_id==identity.tenant,
            ImageQARecordSubmission.pack_id==packId,
            ImageQARecordSubmission.plan_sha256==pack.plan_sha256
        ).order_by(ImageQARecordSubmission.received_at.desc()).limit(52)):
            rejected=submission.record.get("localRejection",{})
            record=rejected.get("record") if isinstance(rejected,dict) else None
            if isinstance(record,dict) and isinstance(record.get("majorDefects"),list):
                target=defects.setdefault(submission.role,set())
                target.update(d for d in record["majorDefects"]
                              if isinstance(d,str) and re.fullmatch(r"[a-z0-9_]{1,80}",d))
        mapped={}
        repair=(pack.ack_state or {}).get(p.REPAIR_METADATA_KEY)
        for row,item in zip(rows,roles):
            receipt=item["receipt"]
            stats=p.attempt_stats(item["status"],row.attempts,repair,row.role)
            entry={"status":item["status"],"attempts":stats["attemptCount"],"storageVerified":False,
                   "attemptId":row.attempt_id,"qaStatus":None,"lastOutcome":row.failure_code,
                   "lastQaDefects":sorted(defects.get(row.role,set()))[:32],**stats}
            if receipt is not None:
                entry.update(receipt)
                entry["qaStatus"]="pass"
                entry["storageReceiptId"]=storage_receipt_id(pack,row)
            mapped[str(row.role)]=entry
        from .model import ImageCommand
        commands=list(db.scalars(select(ImageCommand).where(
            ImageCommand.tenant_id==identity.tenant,ImageCommand.pack_id==packId)))
        wave_commands={str(cmd.wave):{"commandId":cmd.command_id,"status":cmd.status,
            "epoch":cmd.claim_epoch} for cmd in commands}
        active_batch=None
        if active is not None:
            journal=lease.get("adaptive_batches") or {}
            bid=journal.get("active")
            info=(journal.get("batches") or {}).get(bid)
            if info is not None:
                active_batch={"batchId":bid,"manifestSha256":info["sha"],
                    "epoch":pack.epoch,"wave":lease.get("wave"),
                    "calls":[{"callId":v["callId"],"role":v["role"],
                        "attemptId":v["attempt"],"generationMethod":v["method"]}
                        for v in info["calls"].values()]}
        payload.update(roles=mapped,repairGrant=repair,packEpoch=pack.epoch,
            waveCommands={wave:detail["status"] for wave,detail in wave_commands.items()},
            waveCommandDetails=wave_commands,activeBatch=active_batch,
            reconciliationRequired=any(r["status"] in
                ("dispatch_uncertain","upload_pending","reconcile_required") for r in roles),
            batchWave2Allowed=payload["wave2Allowed"],
            packProgress=f"{len(verified)}/13",
            currentWave=2 if wave1 else 1,
            barrier=barrier_snapshot(identity.tenant,packId,pack.owner,wave1,batch.release_decision))
    return payload

@router.get("/jobs/{listing_id}/image-contract")
def image_contract(response: Response, identity: Identity, db: Database,
                   listing_id: ListingId, packId: PackId, sku: Sku):
    """Pinned metadata preflight; not permission to render or fetch source bytes."""
    from . import admission, approvals
    from .model import ImagePlanApproval
    _no_cache(response)
    pack=_owned_pack(db,identity,listing_id,packId,sku)
    approved=db.get(ImagePlanApproval,(identity.tenant,packId))
    try:
        metadata=admission.validate_plan(pack.approved_plan,pack)
    except p.Conflict:
        metadata=None
    pinned=bool(metadata and approved and not approved.revoked and
                approved.plan_sha256==pack.plan_sha256 and
                approvals._plan_evidence(pack,approved.evidence))
    return {**_gate(identity, db),"listingId":listing_id,"packId":packId,
            "garmentSku":sku,"packRevision":pack.pack_revision,
            "planSha256":pack.plan_sha256,"sourceSha256":pack.source_sha256,
            "artworkSha256":pack.artwork_sha256,
            "placementLockSha256":pack.placement_sha256,
            "skillSha256":pack.skill_sha256,
            "metadataValidated":metadata is not None,
            "pinnedApprovalPresent":pinned,
            "planMetadata":metadata if pinned else None,
            "printBoxAuthority": "CAM_PRINT_BOX_SINGLE_AUTHORITY_V1",
            "placementLock":metadata["placementLock"] if pinned else None,
            "smsProductionMaxWidthMm":metadata["smsProductionMaxWidthMm"] if pinned else None,
            "contractReady":False,
            "currentSourceVerified":False,
            "rendererAllowed":False}


@router.get("/jobs/{listing_id}/images/{role}/qa-status")
def image_qa_status(response: Response, identity: Identity, db: Database,
                    listing_id: ListingId, packId: PackId, sku: Sku,
                    role: Annotated[int, ApiPath(ge=1,le=13)],
                    attemptId: Annotated[str,Query(pattern=r"^[A-Za-z0-9_-]{1,128}$")],
                    imageSha256: Annotated[str,Query(pattern=r"^[a-f0-9]{64}$")]):
    """Owner can poll an independent review result; cannot create one."""
    from .model import ImageQAApproval
    from . import approvals
    _no_cache(response)
    pack=_owned_pack(db,identity,listing_id,packId,sku)
    r=db.get(ImageRole,(identity.tenant,packId,role))
    if not r or r.attempt_id!=attemptId:
        raise HTTPException(404,"image_attempt_not_found")
    qa=db.scalar(select(ImageQAApproval).where(
        ImageQAApproval.tenant_id==identity.tenant,ImageQAApproval.pack_id==packId,
        ImageQAApproval.role==role,ImageQAApproval.attempt_id==attemptId,
        ImageQAApproval.image_sha256==imageSha256,ImageQAApproval.epoch==pack.epoch,
        ImageQAApproval.plan_sha256==pack.plan_sha256,
        ImageQAApproval.worker_id==identity.worker))
    status="unverified"
    receipt_id=None
    native_record=None
    if qa and not qa.revoked and qa.reviewed_by!=identity.worker and approvals._bound_qa(
            pack,role,attemptId,imageSha256,qa.verdict,qa.evidence,qa.reviewed_by):
        from . import native_first as native
        if native.profile_from_plan(pack.approved_plan) is not None:
            native_record=qa.evidence
        status=qa.verdict
        if status=="pass":
            receipt_id=qa.receipt_id
    result={**_gate(identity, db),"packId":packId,"garmentSku":sku,
            "role":role,"attemptId":attemptId,"imageSha256":imageSha256,
            "qaStatus":status,"qaReceiptId":receipt_id,
            "uploadAllowed":False,"reason":"IMAGE_CLAIMS_DISABLED"}
    if native_record is not None:
        result.update(generationMethod=native_record["generationMethod"],
            qaRecordSha256=native_record["qaRecordSha256"],
            minimumPublicationPx=native_record["minimumPublicationPx"],
            previewOnly=native_record["previewOnly"],
            physicalCalibrationVerified=False)
    return result

@router.get("/jobs/{listing_id}/creative-plan")
def creative_plan_preview(response: Response,identity: Identity,db: Database,
                          listing_id: ListingId,packId: PackId,sku: Sku):
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .creative_v7_preview import preview
    try:
        return preview(db,identity,listing_id,packId,sku)
    except p.Conflict as exc:
        raise HTTPException(409,str(exc)) from None

@router.post("/jobs/{listing_id}/creative-plan/inspect")
async def creative_plan_inspect(response: Response,identity: Identity,db: Database,
                                 listing_id: ListingId,packId: PackId,sku: Sku):
    """Explicit, one-off source metadata analysis. Never poll this route."""
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .creative_v7_preview import inspect_artwork
    from .live_preflight import failure_metadata,retry_headers
    try:
        return await inspect_artwork(db,identity,listing_id,packId,sku)
    except p.Conflict as exc:
        raise HTTPException(409,str(exc)) from None
    except HTTPException:
        raise
    except Exception as exc:
        detail={"code":"creative_artwork_profile_unavailable",**failure_metadata(exc)}
        raise HTTPException(503,detail,headers=retry_headers(detail)) from None

@router.get("/garment-geometry/catalog")
def garment_geometry_catalog(response: Response,identity: Identity,db: Database):
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .garment_geometry import catalog
    return catalog()

@router.get("/garment-geometry")
def garment_geometry(response: Response,identity: Identity,db: Database,
                     sku: Annotated[str,Query(pattern=r"^(C1717|G500|G180|G185)$")],
                     size: Annotated[str,Query(pattern=r"^(XS|S|M|L|XL|2XL|3XL|4XL|5XL)$")],
                     position: Annotated[str,Query(max_length=32)]="Front",
                     color: Annotated[str|None,Query(max_length=64)]=None):
    _no_cache(response)
    _require_candidate_reader(db,identity)
    from .garment_geometry import describe
    try:
        return describe(sku,size,position,color)
    except p.Conflict as exc:
        raise HTTPException(409,str(exc)) from None
