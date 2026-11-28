"""Operator-only one-extra-attempt repair of an idle, isolated preview pack.

The base counter and all attempt/QA/receipt IDs survive. Re-home to a fresh
batch using the existing recovery convention; never alter an old barrier.
The grant is immutable pack metadata stored separately from expiring ACKs.
Only an independently verified corrected runner pin may activate it.
"""
from __future__ import annotations
from sqlalchemy import select
from app.modules.render_resources import service as res
from . import policy as p,repository as repo,approvals
from .model import ImageBatch,ImagePack,ImageCommand,ImageCapabilityState,ImageUploadIntent,ImageQAApproval
from .qa_submission import ImageQARecordSubmission,record_hash
from .live_runtime import WORKER,RESOURCE,runtime_plan_authorized
from .identity import ImageIdentity

def verify_bundle(bundle_sha,evidence_sha):
    from .adaptive_v61 import REGISTRY,BUNDLE_SHA256
    p.require(REGISTRY.get("wornRepairBundleVerified") is True
              and REGISTRY.get("bundleBytesVerifiedByCAM") is True
              and bundle_sha==BUNDLE_SHA256 and p.valid_sha(bundle_sha)
              and p.valid_sha(evidence_sha)
              and evidence_sha==REGISTRY.get("wornRepairEvidenceSha256"),
              "corrected_worn_runner_not_verified")

def grant(db,tenant,listing,pack_id,expected_epoch,bundle_sha,evidence_sha,now,*,test_sqlite=False):
    verify_bundle(bundle_sha,evidence_sha)
    flags={"test_sqlite":test_sqlite}
    repo._transaction(db,**flags)
    cap=db.scalar(select(ImageCapabilityState).where(
        ImageCapabilityState.tenant_id==tenant,ImageCapabilityState.worker_id==WORKER)
        .with_for_update().execution_options(populate_existing=True))
    p.require(cap is not None and cap.claim_allowed and cap.resource_id==RESOURCE
              and cap.skill_sha256==p.SKILL_SHA256,"repair_resource_not_configured")
    resource=res._lock(db,tenant,RESOURCE,test_sqlite)
    flow=repo.lock_flow(db,tenant,**flags)
    initial=db.get(ImagePack,(tenant,pack_id))
    p.require(initial is not None and initial.listing_key==listing and
              initial.garment_sku=="C1717","repair_pack_not_found")
    old=repo._lock_batch(db,tenant,initial.batch_id)
    row=repo._lock_pack(db,tenant,old.batch_id,pack_id)
    pack,roles=repo._load(db,row)
    existing=pack.repair_grant
    if existing is not None:
        p.require(existing["bundleSha256"]==bundle_sha and
                  existing["promptEvidenceSha256"]==evidence_sha and
                  existing["expectedEpoch"]==expected_epoch,"repair_grant_already_issued")
        return {"status":"already_authorized","packId":pack_id,"repairGrant":existing}
    p.require(resource.integration_verified and not resource.shared_with_video
              and resource.owner_flow is None and resource.expires_at is None
              and flow.running and pack.lease is None and not pack.uncertain
              and pack.owner==WORKER and pack.epoch==expected_epoch,
              "repair_pack_not_idle_or_epoch_changed")
    p.require(approvals.preview_canary(db,row) and
              runtime_plan_authorized(db,ImageIdentity(tenant,WORKER,"muse"),row),
              "repair_preview_plan_unverified")
    p.require(old.pack_ids==[pack_id] and old.release_decision is not None
              and old.release_decision.get("open") is True
              and pack_id in old.release_decision.get("isolated",[])
              and pack_id not in old.release_decision.get("wave2_eligible",[]),
              "repair_pack_not_isolated")
    cmds=list(db.scalars(select(ImageCommand).where(
        ImageCommand.tenant_id==tenant,ImageCommand.pack_id==pack_id)
        .order_by(ImageCommand.wave).with_for_update().execution_options(populate_existing=True)))
    p.require([c.wave for c in cmds]==[1,2] and
              cmds[0].status=="hold" and cmds[0].claim_epoch==expected_epoch
              and not cmds[0].image_granted and cmds[1].status=="pending"
              and cmds[1].claim_epoch is None and not cmds[1].started
              and pack.wave_claims[1]<p.MAX_WAVE_CLAIMS,"repair_command_state_changed")
    p.require(all(pack.roles[n].status=="verified" and pack.roles[n].receipt
                  and pack.roles[n].receipt.storage_verified is True
                  and pack.roles[n].receipt.preview_only_canary is True
                  and not pack.roles[n].receipt.publication_ready for n in (2,4))
              and all(pack.roles[n].status=="pending" and pack.roles[n].attempts==0
                      and pack.roles[n].attempt_id is None for n in range(5,14)),
              "repair_preserved_roles_changed")
    intents=list(db.scalars(select(ImageUploadIntent).where(
        ImageUploadIntent.tenant_id==tenant,ImageUploadIntent.pack_id==pack_id)
        .with_for_update()))
    p.require(len(intents)==2 and {i.role for i in intents}=={2,4}
              and all(i.status=="verified" and i.attempt_id==pack.roles[i.role].attempt_id
                      and i.image_sha256==pack.roles[i.role].receipt.image_sha for i in intents),
              "repair_upload_history_changed")
    slots={}
    for n in (1,3):
        r=pack.roles[n]
        p.require(r.status=="hold" and r.attempts==p.MAX_ROLE_ATTEMPTS
                  and r.receipt is None and r.failure_code in
                  ("local_qa_failed_other","local_qa_failed_fidelity"),"repair_role_not_exhausted")
        p.require(db.scalar(select(ImageQAApproval.receipt_id).where(
            ImageQAApproval.tenant_id==tenant,ImageQAApproval.pack_id==pack_id,
            ImageQAApproval.role==n).limit(1)) is None,"repair_role_has_independent_review")
        records=list(db.scalars(select(ImageQARecordSubmission).where(
            ImageQARecordSubmission.tenant_id==tenant,ImageQARecordSubmission.pack_id==pack_id,
            ImageQARecordSubmission.role==n).order_by(ImageQARecordSubmission.received_at)
            .with_for_update()))
        p.require(len(records)==p.MAX_ROLE_ATTEMPTS,"repair_rejection_history_incomplete")
        ids=[]
        for q in records:
            reject=q.record.get("localRejection",{})
            p.require(q.plan_sha256==row.plan_sha256 and q.worker_id==WORKER
                      and q.epoch<=expected_epoch and q.attempt_id in pack.used_attempt_ids
                      and q.record_sha256==record_hash(q.record,n)
                      and reject.get("reason") in ("qa_failed_other","qa_failed_fidelity")
                      and p.valid_sha(reject.get("candidateSha256"))
                      and reject.get("qaRecordSha256")==record_hash(reject.get("record"),n),
                      "repair_rejection_history_unverified")
            ids.append(q.attempt_id)
        p.require(len(set(ids))==p.MAX_ROLE_ATTEMPTS and r.attempt_id in ids,
                  "repair_rejection_history_unverified")
        slots[str(n)]={"priorAttemptIds":ids,"repairAttemptId":None}
    value={"protocol":p.REPAIR_PROTOCOL,"packId":pack_id,"planSha256":row.plan_sha256,
           "previousBatchId":old.batch_id,"issuedAt":now,"expectedEpoch":expected_epoch,
           "bundleSha256":bundle_sha,"promptEvidenceSha256":evidence_sha,"roles":slots}
    value["grantId"]=p.canonical_sha(value)
    new_id="worn-repair-"+pack_id[:40]
    p.require(db.get(ImageBatch,(tenant,new_id)) is None,"repair_batch_exists")
    db.add(ImageBatch(tenant_id=tenant,batch_id=new_id,created_at=now,
                      pack_ids=[pack_id],release_decision=None))
    db.flush()
    pack.repair_grant=value
    for n in (1,3):pack.roles[n].status="pending"
    repo._store(row,pack,roles)
    row.batch_id=new_id
    cmds[0].status="pending"
    cmds[0].started=False
    # Keep historical epoch/ACK fields. Next claim issues a strictly newer lease.
    db.flush()
    return {"status":"repair_authorized","packId":pack_id,"batchId":new_id,
            "commandId":cmds[0].command_id,"repairGrant":value,
            "preservedRoles":[2,4],"allowedRoles":[1,3],"publicationReady":False}
