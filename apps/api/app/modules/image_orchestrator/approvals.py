"""Trusted CAM-side approval ledger.

Internal calls only. Reviewer/legacy-drain integrations must establish
authentic provenance before invoking record_*; there is deliberately no
worker-facing endpoint that can create approval records. No claim flag is
changed by recording evidence. No generated image bytes stored on VPS.
"""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import policy as p,repository as repo,admission,native_first as native
from .identity import ImageIdentity
from .model import ImagePack,ImageRole,ImagePlanApproval,ImageQAApproval

PLAN_PROOFS=("originalBytesSha256","sourceVisualReviewSha256",
             "placementEvidenceSha256","legacyDrainEvidenceSha256","paletteEvidenceSha256")
QA_CHECKS=("artworkFidelity","flatInk","garmentAndShade","roleFraming",
           "placementVisual","poseAndOcclusion","lighting","occasion")
QA_POLICY="CAM_IMAGE_QA_V1"


def _locked(db,model,**conditions):
    return db.scalar(select(model).where(*(getattr(model,key)==value
        for key,value in conditions.items())).with_for_update()
        .execution_options(populate_existing=True))


def execution_evidence_valid(value):
    """Operator-only, immutable canary scope; never a worker assertion."""
    keys={"scope","resourceEvidenceSha256","cronNativeEvidenceSha256",
        "driveEvidenceSha256","backupReceiptSha256","restoreEvidenceSha256",
        "backupOnlyCanary","serverOnlyLegacyFence","legacyClientDrainVerified",
        "nativeOutputPx","previewOnlyCanary"}
    if not isinstance(value,dict) or set(value)!=keys or value["scope"]!="single_pack":
        return False
    if not all(p.valid_sha(value[k]) for k in ("resourceEvidenceSha256",
            "cronNativeEvidenceSha256","driveEvidenceSha256","backupReceiptSha256")):
        return False
    if any(type(value[k]) is not bool for k in ("backupOnlyCanary","serverOnlyLegacyFence",
            "legacyClientDrainVerified","previewOnlyCanary")):
        return False
    if value["backupOnlyCanary"]:
        if value["restoreEvidenceSha256"] is not None:return False
    elif not p.valid_sha(value["restoreEvidenceSha256"]):return False
    if not (value["serverOnlyLegacyFence"] or value["legacyClientDrainVerified"]):return False
    size=value["nativeOutputPx"]
    return (type(size) is int and 1600<=size<=4000 and
            (size>=2000 or value["previewOnlyCanary"]))


def _plan_evidence(pack,evidence):
    try:
        profile=native.profile_from_plan(pack.approved_plan)
    except (p.Conflict,TypeError,ValueError):
        return False
    required=set(PLAN_PROOFS)
    if profile is not None:
        required.add("operatorPolicyReceiptSha256")
    return (isinstance(evidence,dict) and
        set(evidence) in (required,required|{"executionEvidence"}) and
        ("executionEvidence" not in evidence or execution_evidence_valid(evidence["executionEvidence"])) and
        all(p.valid_sha(evidence[k]) for k in required) and
        evidence["originalBytesSha256"]==pack.artwork_sha256 and
        evidence["paletteEvidenceSha256"]==admission.PALETTE_SHA and
        (profile is None or evidence["operatorPolicyReceiptSha256"]==profile.operator_receipt_sha))


def record_plan(db: Session, tenant: str, pack_id: str, evidence: dict,
                reviewer_id: str, now: int, *, test_sqlite: bool=False):
    """Called by trusted admission service after actual evidence verification."""
    repo._transaction(db,test_sqlite=test_sqlite)
    pack=_locked(db,ImagePack,tenant_id=tenant,pack_id=pack_id)
    p.require(pack is not None,"image_pack_not_found")
    admission.validate_plan(pack.approved_plan,pack)
    p.require(_plan_evidence(pack,evidence) and isinstance(reviewer_id,str) and p._ID.fullmatch(reviewer_id),
              "plan_evidence_unverified")
    previous=_locked(db,ImagePlanApproval,tenant_id=tenant,pack_id=pack_id)
    if previous:
        p.require(not previous.revoked and previous.plan_sha256==pack.plan_sha256
                  and previous.evidence==evidence,"immutable_plan_approval")
        return previous
    row=ImagePlanApproval(tenant_id=tenant,pack_id=pack_id,plan_sha256=pack.plan_sha256,
        evidence=dict(evidence),approved_by=reviewer_id,approved_at=now,revoked=False)
    db.add(row);db.flush()
    return row


def authorize_plan(db: Session, identity: ImageIdentity, pack: ImagePack) -> bool:
    """Production runtime guard; validates current source every time."""
    if pack.tenant_id!=identity.tenant:
        return False
    approval=_locked(db,ImagePlanApproval,tenant_id=identity.tenant,pack_id=pack.pack_id)
    if (not approval or approval.revoked or approval.plan_sha256!=pack.plan_sha256
        or not _plan_evidence(pack,approval.evidence)):
        return False
    try:
        admission.validate_plan(pack.approved_plan,pack)
        admission.inspect_current(db,identity.tenant,pack)
    except p.Conflict:
        return False
    return True


def _qa_evidence(role,verdict,evidence):
    if not isinstance(evidence,dict) or set(evidence)!={
            "policy","reviewEvidenceSha256","checks","physicalCalibration"}:
        return False
    checks=evidence["checks"]
    required=set(QA_CHECKS)
    if role==4:
        required.add("neckTag")
    if role==13:
        required.add("approvedPhotoSources")
    if (evidence["policy"]!=QA_POLICY or not p.valid_sha(evidence["reviewEvidenceSha256"])
        or evidence["physicalCalibration"] not in ("verified","unverified")
        or not isinstance(checks,dict) or set(checks)!=required
        or any(type(v) is not bool for v in checks.values())):
        return False
    # Visual pilot pass is not physical measurement proof.
    return verdict!="pass" or all(checks.values())


def _bound_qa(pack, role, attempt_id, image_sha, verdict, evidence, reviewer):
    """Rebuild the native record from authoritative pack/attempt identity.

    The actual reviewer still must be authenticated and inspect exact PNG
    bytes and native-origin evidence before invoking record_qa. This is
    binding validation, not a pixel-review implementation.
    """
    try:
        profile=native.profile_from_plan(pack.approved_plan)
        if profile is None:
            return _qa_evidence(role,verdict,evidence)
        if not isinstance(evidence,dict) or evidence.get("generationMethod")!=native.NATIVE:
            return False
        identity=p.PackIdentity(pack.tenant_id,pack.listing_key,pack.artwork_sha256,
            pack.garment_sku,pack.pack_revision,pack.placement_sha256,
            pack.source_sha256,pack.skill_sha256)
        if identity.pack_id!=pack.pack_id:
            return False
        expected=native.assess(profile,identity,role=role,epoch=pack.epoch,
            attempt_id=attempt_id,plan_sha=pack.plan_sha256,method=native.NATIVE,
            image_sha=image_sha,reviewed_sha=evidence["imageSha256"],
            width=evidence["width"],height=evidence["height"],
            native_verified=evidence["nativeOriginVerified"],upscaled=evidence["upscaled"],
            checks=evidence["checks"],reviewer=reviewer,worker=pack.owner,
            evidence_sha=evidence["reviewEvidenceSha256"],
            prompt_sha=evidence["promptTemplateSha256"],
            physical_calibration=evidence["physicalCalibrationVerified"])
        return expected==evidence and verdict==("pass" if expected["qaPass"] else "hold")
    except (p.Conflict,KeyError,ValueError,TypeError,AttributeError):
        return False


def preview_canary(db,pack):
    """The immutable operator approval, not a worker-supplied preview flag."""
    approval=db.get(ImagePlanApproval,(pack.tenant_id,pack.pack_id))
    return bool(approval is not None and not approval.revoked
        and approval.plan_sha256==pack.plan_sha256
        and _plan_evidence(pack,approval.evidence)
        and execution_evidence_valid(approval.evidence.get("executionEvidence"))
        and approval.evidence["executionEvidence"]["previewOnlyCanary"])


def publication_scope(db,identity,pack,role,image_sha,qa_id,width,height):
    """Resolve publication policy from the trusted immutable QA ledger."""
    profile=native.profile_from_plan(pack.approved_plan)
    if profile is None:
        return (p.PUBLICATION_MIN_NATIVE,None,None,None)
    p.require(authorize_qa(db,identity,pack,role,image_sha,qa_id),"native_qa_unverified")
    qa=_locked(db,ImageQAApproval,tenant_id=identity.tenant,receipt_id=qa_id)
    record=qa.evidence
    p.require(record.get("eligibleForStorageAdmission") is True
              and (record.get("width"),record.get("height"))==(width,height),
              "native_qa_publication_ineligible")
    return (profile.minimum_px,native.NATIVE,record["qaRecordSha256"],profile.operator_receipt_sha)


def record_qa(db: Session, tenant: str, pack_id: str, role: int,
              attempt_id: str, image_sha: str, verdict: str, evidence: dict,
              reviewer_id: str, now: int, *, test_sqlite: bool=False,
              submitted_record_sha: str|None=None):
    """Record an authentic text-only review of the exact saved candidate.

    The trusted reviewer service has inspected the candidate pixels and
    verified their SHA before calling this function. A renderer self-report
    does not qualify. A revoked or held attempt is never silently approved.
    """
    repo._transaction(db,test_sqlite=test_sqlite)
    p.require(type(role) is int and 1<=role<=13 and p.valid_sha(image_sha)
              and isinstance(attempt_id,str) and p._ID.fullmatch(attempt_id)
              and isinstance(reviewer_id,str) and p._ID.fullmatch(reviewer_id)
              and verdict in ("pass","hold","unverified"),"qa_evidence_invalid")
    pack=_locked(db,ImagePack,tenant_id=tenant,pack_id=pack_id)
    p.require(pack is not None,"image_pack_not_found")
    r=_locked(db,ImageRole,tenant_id=tenant,pack_id=pack_id,role=role)
    p.require(r is not None and r.attempt_id==attempt_id and pack.owner
              and r.status in ("dispatch_uncertain","upload_pending","reconcile_required","verified"),
              "qa_attempt_mismatch")
    p.require(reviewer_id!=pack.owner,"qa_self_approval_denied")
    p.require(_bound_qa(pack,role,attempt_id,image_sha,verdict,evidence,reviewer_id),
              "qa_evidence_invalid")
    rid=p.canonical_sha(["CAM_IMAGE_QA_APPROVAL_V1",tenant,pack_id,role,attempt_id,
                         pack.epoch,pack.plan_sha256,image_sha])
    # A worker record is only input to an independent exact-PNG review.
    if submitted_record_sha is not None or (pack.lease_state or {}).get('adaptive_batches'):
        from .qa_submission import require_submission
        p.require(p.valid_sha(submitted_record_sha),'qa_record_review_binding_required')
        rid=p.canonical_sha(['CAM_IMAGE_V61_QA_RESERVATION',tenant,pack_id,pack.epoch,
                             role,attempt_id,pack.plan_sha256])
        require_submission(db,pack,role,attempt_id,pack.epoch,rid,submitted_record_sha)
    previous=_locked(db,ImageQAApproval,tenant_id=tenant,receipt_id=rid)
    if previous:
        p.require(not previous.revoked and previous.evidence==evidence
                  and previous.verdict==verdict and previous.image_sha256==image_sha,
                  "immutable_qa_approval")
        return previous
    p.require(r.status!="verified","role_already_verified")
    row=ImageQAApproval(tenant_id=tenant,receipt_id=rid,pack_id=pack_id,role=role,
        attempt_id=attempt_id,epoch=pack.epoch,worker_id=pack.owner,
        plan_sha256=pack.plan_sha256,image_sha256=image_sha,verdict=verdict,
        evidence=evidence,reviewed_by=reviewer_id,reviewed_at=now,revoked=False)
    db.add(row);db.flush()
    return row


def authorize_qa(db: Session, identity: ImageIdentity, pack: ImagePack,
                 role: int, image_sha: str, qa_id: str) -> bool:
    if (pack.tenant_id!=identity.tenant or pack.owner!=identity.worker
        or not p.valid_sha(qa_id) or not p.valid_sha(image_sha)):
        return False
    r=db.get(ImageRole,(identity.tenant,pack.pack_id,role))
    qa=_locked(db,ImageQAApproval,tenant_id=identity.tenant,receipt_id=qa_id)
    return bool(r and qa and not qa.revoked and qa.verdict=="pass"
        and qa.pack_id==pack.pack_id and qa.role==role and qa.attempt_id==r.attempt_id
        and qa.epoch==pack.epoch and qa.worker_id==identity.worker
        and qa.plan_sha256==pack.plan_sha256 and qa.image_sha256==image_sha
        and qa.reviewed_by!=identity.worker
        and _bound_qa(pack,role,r.attempt_id,image_sha,"pass",qa.evidence,qa.reviewed_by))