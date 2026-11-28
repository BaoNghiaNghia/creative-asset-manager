"""V6.1 candidate batch routes over existing transactional command/resource policy.

No production route registration or capability enablement. The existing
write_api.get_adapters remains fail-closed. QA reservations are identifiers,
NOT approvals. The independent exact-PNG reviewer and storage adapter must
still be connected before enabling any production claims.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import policy as p, v6_wire_contract as wire
from .model import ImageBatch
from .write_api import (Backend, CommandQuery, ListingPath, PackHeader, SkuHeader, RolePath,
                        Identity, Database, RedactedRoute, read_api_enabled,
                        perform, service, scope, now, private, check_token)

PROTOCOL = "CAM_MUSE_IMAGE_ADAPTIVE_BATCH_V6_1"
BUNDLE_SHA256 = "da10307e9e6b3e04a9e19daa68d888033c9678ca30bdae8a8f40deed4a2038ea"
REGISTRY = {
    # Exact 245691-byte CAM-built worn-repair ZIP, CRC and 57/57 payload hashes verified.
    "bundlePinEvidence": "CAM_VERIFIED_ZIP_MANIFEST_20261128",
    "bundleBytesVerifiedByCAM": True,
    "localRejectionProtocol": "CAM_IMAGE_LOCAL_REJECT_V1",
    "wornRepairProtocol": p.REPAIR_PROTOCOL,
    # Verified offline code/prompt evidence, not proof of installation or live render.
    "wornRepairBundleVerified": True,
    "wornRepairEvidenceSha256": "0d14ea3d7265c534cdc317276d087e1100c929f19aa7fc25ed0551dc471e9882",
    "wornRepairModuleSha256": "898f49c8f2fac987fa553004d39983d847b6322143b810c459e29003769d9c07",
    "preflightSha256": "df3d8831f5357f7b2131e8b573649a3ff220d5e811d35cfa70be14b1fce3665e",
    "bundleReportedBytes": 245691,
    "bundleManifestSha256": "cf3154d3c8ba3850caf2697e091d6bbe88c144c8ac304cead18f4c8a6eb763b7",
    "bundleRegistrySha256": "25931d0e879627d0ebe318bab5915f9ba9206d2fdb5220c049df2aae6210e8a4",
    "recoveryStateSha256": "dcca2bc15ab281a87250ba200a3ed1d53af22b98797d5add7b8169cd86434ec4",
    "previewCanaryStoragePolicy": "CENTRAL_RECEIPTS_ONLY_NO_GALLERY_V1",
    "adaptiveBatchPolicy": PROTOCOL, "batchPlannerVersion": PROTOCOL,
    "batchPlannerSha256": "7225588b73ef4ca2534933aaa8a1034978bc5034b6e67e8e7a0c1b3798809a8d",
    "packRunnerSha256": "424569953ec16ff7c9555cebf7700c2a401b8c628d03b08f403c0d98e1d84a40",
    "nativeRunnerVersion": "CAM_MUSE_IMAGE_NATIVE_V1",
    "nativeRunnerSha256": "996ce0a8b653d767864c5af60faab7e7756b13d5fbd3be0400043c377e368731",
    "nativePromptTemplateSha256": "455ee7cdc86e6bcbe62e4a68baad566908b98082bfce486f178c1303269db8df",
    "clientSha256": "f7bc830604f99abda49c64743f4f44bf0af1b4bf391439d5d219e4d575988f04",
    "transportSha256": "27a2b9c2f53a1213c575107bae1b8709206d36290d10eef89ea362bf0a1fce9f",
    "rendererSha256": "21b7faa15a122867873d71dc54f1c6143be42e8a740febe3080d78bb7390977e",
    "qaSha256": "c01150f5c88e864411c53800df53d7fed8cd55cbdab42355044f4e51e738cc06",
    "outboxSha256": "0c9017ba566e1c6a0245c322083782a40ec261be892aadbf5c19ec76cd214476",
    "maxBatchRoles": 13, "multiOutputSupported": False,
    "multiOutputEvidenceSha256": None, "nativeMinimumPx": 1600,
    "publicationMinimumPx": 2000, "claimEnabled": False,
    "rendererAllowed": False, "autoWaveBarrier": True,
    "status": "PREPARED_DISABLED",
}


def barrier_view(pack, decision):
    """Server-owned, pack/owner-scoped monotonic snapshot, not bearer auth.

    The existing command claim independently requires Wave 1 receipts and
    resource ownership. A grant is a freshness fence, not a bypass.
    """
    return barrier_snapshot(pack.identity.tenant,pack.identity.pack_id,pack.owner,
                            pack.wave_complete(1),decision)


def barrier_snapshot(tenant,pack_id,owner,wave1_complete,decision):
    opened = bool(decision and decision.get("open") is True
                  and pack_id in decision.get("wave2_eligible", [])
                  and wave1_complete)
    revision = 2 if opened else 1
    grant = p.canonical_sha([PROTOCOL, tenant, pack_id,
                             owner, revision, decision if opened else None])
    return {"wave1Open": not wave1_complete, "wave2Open": opened,
            "autoAdvance": True, "barrierGrant": grant, "barrierRevision": revision}


def qa_reservation(pack, role, attempt, plan_sha):
    return p.canonical_sha(["CAM_IMAGE_V61_QA_RESERVATION", pack.identity.tenant,
                            pack.identity.pack_id, pack.epoch, role, attempt, plan_sha])


def claim_manifest(pack, worker, command_token, epoch, ts, manifest, sha,
                   *, wave, barrier, grant=None, revision=None):
    lease = p.authorized(pack, worker, epoch, command_token, ts, kind="command")
    p.require(lease.wave == wave, "v61_wave_mismatch")
    rows = wire.validate_manifest(manifest, sha, listing_id=pack.identity.listing,
                                  wave=wave, expected_protocol=PROTOCOL)
    if wave == 2:
        p.require(barrier["wave2Open"] and type(revision) is int
                  and revision == barrier["barrierRevision"]
                  and isinstance(grant, str)
                  and hmac.compare_digest(grant, barrier["barrierGrant"]),
                  "v61_stale_barrier")
    else:
        p.require(grant is None and revision is None, "v61_unexpected_barrier")
    bid = manifest["batchId"]
    state = lease.adaptive_batches
    history = state.get("batches", {})
    old = history.get(bid)
    image_token = hmac.new(command_token.encode(), (PROTOCOL + ":" + bid).encode(),
                           hashlib.sha256).hexdigest()
    if old is not None:
        p.require(state.get("active") == bid and old["sha"] == sha
                  and hmac.compare_digest(lease.image_hash, p.digest(image_token)),
                  "v61_batch_replay_conflict")
        accepted = old["roles"]
    else:
        p.require(not pack.uncertain and len(history) < 39, "v61_reconciliation_required")
        # Each batch contains only eligible roles. No partial subset is silently
        # authorized; refresh image-status and replan on a conflict.
        accepted = [role for _,role,_,_ in rows]
        p.require(all(p.role_can_start(pack,role)
                      and attempt not in pack.used_attempt_ids
                      for _,role,attempt,_ in rows), "v61_role_not_claimable")
        # No second manifest may reuse a previous batch's attempt or call ID.
        reserved = [entry for item in history.values() for entry in item["calls"].values()]
        p.require(not ({x["attempt"] for x in reserved} & {r[2] for r in rows})
                  and not ({x["callId"] for x in reserved} & {r[0] for r in rows}),
                  "v61_manifest_identifier_reused")
        history[bid] = {"sha":sha, "roles":accepted,
            "calls":{cid:{"callId":cid,"role":role,"attempt":attempt,"method":method}
                     for cid,role,attempt,method in rows}}
        lease.adaptive_batches = {"active":bid,"batches":history}
        lease.image_hash = p.digest(image_token)
    return {"batchAccepted":True, "batchId":bid, "imageLease":image_token,
            "epoch":epoch, "rolesAccepted":accepted, "rolesRejected":[],
            **({"barrierGrant":grant,"barrierRevision":revision} if wave==2 else {})}


def start_manifest_call(pack, worker, image_token, epoch, ts, *, batch_id,
                        call_id, roles, attempts, method, plan_sha, flow_running):
    lease = p.authorized(pack, worker, epoch, image_token, ts)
    state = lease.adaptive_batches
    p.require(state.get("active") == batch_id, "v61_batch_scope_mismatch")
    entry = state.get("batches", {}).get(batch_id, {}).get("calls", {}).get(call_id)
    p.require(entry is not None, "v61_call_not_reserved")
    role, attempt = entry["role"], entry["attempt"]
    p.require(roles == [role] and attempts == {str(role):attempt}
              and method == entry["method"] == "native_imagegen",
              "v61_call_mapping_mismatch")
    sent = p.role_start(pack,worker,epoch,image_token,ts,role,attempt,
                        flow_running=flow_running,resource_granted=True)
    # First grant only. Lost HTTP ACK must reconcile, never re-render.
    return {"sendAllowed":sent,"batchId":batch_id,"callId":call_id,
            "outputs":[{"outputIndex":0,"role":role,"attemptId":attempt}],
            "generationMethod":method,
            "qaReceiptIds":{str(role):qa_reservation(pack,role,attempt,plan_sha)}}


def require_upload_binding(pack, lease, *, role, attempt, batch_id, output_index,
                           method, qa_id, qa_record_sha, plan_sha):
    """Scope guard only. Exact independent QA still required by prepare_upload."""
    if not lease.adaptive_batches:
        p.require(all(x is None for x in (batch_id,output_index,method,qa_record_sha)),
                  "v61_batch_not_claimed")
        return
    state = lease.adaptive_batches
    p.require(state.get("active")==batch_id and type(output_index) is int
              and output_index==0 and method=="native_imagegen"
              and p.valid_sha(qa_record_sha), "v61_upload_headers_invalid")
    entries = state["batches"][batch_id]["calls"].values()
    p.require(any(e["role"]==role and e["attempt"]==attempt and e["method"]==method
                  for e in entries)
              and pack.roles[role].attempt_id==attempt
              and qa_id==qa_reservation(pack,role,attempt,plan_sha),
              "v61_upload_scope_mismatch")


def claim_batch(svc, sc, ts, skill, body):
    cap=svc._capability(ts,skill)
    row,resource,flow=svc._lock_existing(sc)
    p.require(cap.resource_id==resource.resource_id and flow.running, "image_claim_disabled")
    def apply(pack,_):
        cmd=svc._command(sc);svc._fence(cmd,resource,body.epoch,ts)
        svc.db.refresh(row)
        p.require(svc.runtime.authorize_plan(svc.db,svc.identity,row), "image_plan_unverified")
        batch=svc.db.get(ImageBatch,(svc.tenant,row.batch_id))
        p.require(batch is not None,"image_batch_not_found")
        result=claim_manifest(pack,svc.worker,check_token(body.lease.get_secret_value()),
                              body.epoch,ts,body.batchManifest,body.manifestSha256,
                              wave=cmd.wave,barrier=barrier_view(pack,batch.release_decision),
                              grant=body.barrierGrant,revision=body.barrierRevision)
        cmd.image_granted=True
        return result
    return svc._transition(sc,row,apply)


def start_batch(svc,sc,ts,body):
    cap=svc._capability(ts,p.SKILL_SHA256)
    row,resource,flow=svc._lock_existing(sc)
    p.require(cap.resource_id==resource.resource_id,"resource_binding_changed")
    def apply(pack,_):
        cmd=svc._command(sc);svc._fence(cmd,resource,body.epoch,ts)
        p.require(cmd.image_granted,"image_grant_required")
        svc.db.refresh(row)
        p.require(svc.runtime.authorize_plan(svc.db,svc.identity,row),"image_plan_unverified")
        result=start_manifest_call(pack,svc.worker,check_token(body.imageLease.get_secret_value()),
            body.epoch,ts,batch_id=body.batchId,call_id=body.callId,roles=body.roles,
            attempts=body.attemptIds,method=body.generationMethod,plan_sha=row.plan_sha256,
            flow_running=flow.running)
        if result["sendAllowed"]: cmd.started=True
        return result
    return svc._transition(sc,row,apply)


class BatchClaimBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    lease:SecretStr
    epoch:int=Field(ge=1,strict=True)
    batchManifest:dict
    manifestSha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    barrierGrant:str|None=Field(default=None,max_length=128)
    barrierRevision:int|None=Field(default=None,ge=1,strict=True)


class BatchStartBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    imageLease:SecretStr
    epoch:int=Field(ge=1,strict=True)
    batchId:str=Field(pattern=r"^[a-f0-9]{32}$")
    callId:str=Field(pattern=r"^[a-f0-9]{32}$")
    roles:list[int]=Field(min_length=1,max_length=1)
    attemptIds:dict[str,str]
    generationMethod:Literal["native_imagegen"]


router=APIRouter(prefix="/api/v1/muse-bridge",tags=["image-orchestrator-v61-candidate"],
                 dependencies=[Depends(read_api_enabled)],route_class=RedactedRoute)


@router.post("/jobs/{listing_id}/images/batch-claim")
def batch_claim(listing_id:ListingPath,commandId:CommandQuery,pack:PackHeader,sku:SkuHeader,
                body:BatchClaimBody,response:Response,identity:Identity,db:Database,backend:Backend,
                skill:Annotated[str,Header(alias="X-CAM-Skill-Sha256")]):
    private(response)
    return perform(db,lambda:claim_batch(service(db,identity,backend),
        scope(commandId,pack,sku,listing_id),now(),skill,body))


@router.post("/jobs/{listing_id}/images/batch-start")
def batch_start(listing_id:ListingPath,commandId:CommandQuery,pack:PackHeader,sku:SkuHeader,
                body:BatchStartBody,response:Response,identity:Identity,db:Database,backend:Backend):
    private(response)
    return perform(db,lambda:start_batch(service(db,identity,backend),
        scope(commandId,pack,sku,listing_id),now(),body))

@router.post("/jobs/{listing_id}/images/{role}/qa-record")
async def qa_record(listing_id:ListingPath,role:RolePath,commandId:CommandQuery,
                    pack:PackHeader,sku:SkuHeader,request:Request,response:Response,
                    identity:Identity,db:Database,backend:Backend,
                    token:Annotated[str,Header(alias="X-Muse-Lease")],
                    epoch:Annotated[int,Header(alias="X-CAM-Epoch",ge=1)],
                    attempt:Annotated[str,Header(alias="X-CAM-Attempt-Id",pattern=r"^[a-f0-9]{32}$")],
                    batch_id:Annotated[str,Header(alias="X-CAM-Batch-Id",pattern=r"^[a-f0-9]{32}$")],
                    sha:Annotated[str,Header(alias="X-CAM-QA-Record-Sha256",pattern=r"^[a-f0-9]{64}$")]):
    private(response)
    if request.headers.get("content-type","").split(";")[0].lower()!="application/json":
        raise HTTPException(415,"qa_record_json_required")
    raw=bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>65536:
            raise HTTPException(413,"qa_record_too_large")
        raw.extend(chunk)
    try:
        record=json.loads(raw)
    except (ValueError,TypeError,RecursionError):
        raise HTTPException(422,"qa_record_invalid") from None
    from .qa_submission import receive
    return perform(db,lambda:receive(service(db,identity,backend),
        scope(commandId,pack,sku,listing_id),now(),role=int(role),epoch=epoch,
        token=check_token(token),attempt=attempt,batch_id=batch_id,sha=sha,record=record))

class LocalRejectBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    imageLease:SecretStr
    epoch:int=Field(ge=1,strict=True)
    batchId:str=Field(pattern=r"^[a-f0-9]{32}$")
    attemptId:str=Field(pattern=r"^[a-f0-9]{32}$")
    candidateSha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    qaRecordSha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    qaRecord:dict
    reason:Literal["qa_failed_fidelity","qa_failed_other"]


@router.post("/jobs/{listing_id}/images/{role}/reject-local")
def reject_local(listing_id:ListingPath,role:RolePath,commandId:CommandQuery,
                pack:PackHeader,sku:SkuHeader,body:LocalRejectBody,response:Response,
                identity:Identity,db:Database,backend:Backend):
    private(response)
    from .qa_submission import reject_local as transition
    return perform(db,lambda:transition(service(db,identity,backend),
        scope(commandId,pack,sku,listing_id),now(),role=int(role),
        epoch=body.epoch,token=check_token(body.imageLease.get_secret_value()),
        attempt=body.attemptId,batch_id=body.batchId,candidate_sha=body.candidateSha256,
        qa_sha=body.qaRecordSha256,qa_record=body.qaRecord,reason=body.reason))