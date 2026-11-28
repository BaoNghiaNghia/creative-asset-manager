"""P2b image command lifecycle, transactionally fenced.

Never call a renderer or storage provider inside a database transaction.
Only the trusted service may attest plan readiness and QA/storage proof.
The production admission adapter is not yet installed.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable
from uuid import UUID, uuid5
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.render_resources import service as res
from . import policy as p, repository as repo
from .identity import ImageIdentity
from .model import ImageCapabilityState, ImageCommand, ImagePack, ImageUploadIntent

_NAMESPACE = UUID("72b717d5-17c8-4ac9-869a-ef79d20efba1")


def command_id(pack_id: str, wave: int) -> str:
    return str(uuid5(_NAMESPACE, f"{pack_id}:{wave}"))


def gallery_pack_id(pack_id: str) -> str:
    p.require(p.valid_sha(pack_id), "invalid_pack_id")
    return "sp-" + p.digest("cam-image-orchestrator-gallery-v1:" + pack_id)[:40]


def ensure_commands(db: Session, tenant: str, pack_id: str, *,
                    test_sqlite: bool = False):
    """Internal admission only; called after an approved pack plan is persisted."""
    repo._transaction(db, test_sqlite=test_sqlite)
    row = db.scalar(select(ImagePack).where(ImagePack.tenant_id == tenant,
                    ImagePack.pack_id == pack_id).with_for_update())
    p.require(row is not None, "image_pack_not_found")
    p.require(isinstance(row.approved_plan, dict) and
              p.canonical_sha(row.approved_plan) == row.plan_sha256, "image_plan_unverified")
    for wave in (1, 2):
        cid = command_id(pack_id, wave)
        if db.get(ImageCommand, (tenant,cid)) is None:
            db.add(ImageCommand(tenant_id=tenant, command_id=cid,
                                pack_id=pack_id,wave=wave,status="pending",
                                image_granted=False,started=False))
    db.flush()


@dataclass(frozen=True)
class Scope:
    command_id: str
    pack_id: str
    sku: str
    listing: str | None = None


@dataclass(frozen=True)
class Runtime:
    """Internal adapters only, never constructed from request values.

    authorize_plan checks current Picked/artwork/source/palette/role plan
    and the drained legacy ledger. authorize_qa must resolve an authentic
    approved QA record, not trust the worker's claimed status.
    """
    authorize_plan: Callable[[Session, ImageIdentity, ImagePack], bool]
    authorize_qa: Callable[[Session, ImageIdentity, ImagePack, int, str, str], bool]
    test_sqlite: bool = False


@dataclass(frozen=True)
class UploadTicket:
    tenant: str
    listing: str
    pack_id: str
    gallery_id: str
    sku: str
    role: int
    attempt_id: str
    epoch: int
    artwork_sha: str
    source_sha: str
    placement_sha: str
    storage_placement: str
    image_sha: str
    width: int
    height: int
    publication_minimum_px: int = p.PUBLICATION_MIN_NATIVE
    generation_method: str | None = None
    qa_record_sha: str | None = None
    operator_policy_receipt_sha: str | None = None
    preview_only_canary: bool = False

    @property
    def publication_scope(self):
        return (self.publication_minimum_px,self.generation_method,
                self.qa_record_sha,self.operator_policy_receipt_sha)


class CommandService:
    def __init__(self, db: Session, identity: ImageIdentity, runtime: Runtime):
        self.db, self.identity, self.runtime = db,identity,runtime
        self.flags={"test_sqlite":runtime.test_sqlite}

    @property
    def tenant(self):
        return self.identity.tenant

    @property
    def worker(self):
        return self.identity.worker

    def _snapshot(self, scope: Scope):
        row = self.db.scalar(select(ImageCommand).where(
            ImageCommand.tenant_id == self.tenant,
            ImageCommand.command_id == scope.command_id,
            ImageCommand.pack_id == scope.pack_id))
        pack = self.db.scalar(select(ImagePack).where(
            ImagePack.tenant_id == self.tenant,ImagePack.pack_id == scope.pack_id,
            ImagePack.garment_sku == scope.sku))
        p.require(row is not None and pack is not None and
                  (scope.listing is None or pack.listing_key == scope.listing),
                  "image_command_not_found")
        return row,pack

    def _capability(self, now: int, skill_sha: str):
        cap = self.db.scalar(select(ImageCapabilityState).where(
            ImageCapabilityState.tenant_id == self.tenant,
            ImageCapabilityState.worker_id == self.worker
        ).with_for_update().execution_options(populate_existing=True))
        p.require(cap is not None and cap.claim_allowed and
                  cap.skill_sha256 == skill_sha == p.SKILL_SHA256,
                  "image_capability_unverified")
        p.require(cap.cooldown_until <= now, "image_worker_cooldown")
        return cap

    def _command(self, scope: Scope):
        row = self.db.scalar(select(ImageCommand).where(
            ImageCommand.tenant_id == self.tenant,
            ImageCommand.command_id == scope.command_id,
            ImageCommand.pack_id == scope.pack_id
        ).with_for_update().execution_options(populate_existing=True))
        p.require(row is not None, "image_command_not_found")
        return row

    def _transition(self, scope: Scope, pack_row: ImagePack, fn):
        return repo.transition(self.db,self.tenant,pack_row.batch_id,scope.pack_id,
                               scope.sku,fn,**self.flags)

    def _lock_existing(self, scope: Scope):
        cmd, row = self._snapshot(scope)
        rid = cmd.resource_id
        p.require(rid is not None, "image_command_not_claimed")
        # Lock resource first. Do not rely on a stale ORM snapshot for ownership.
        resource = res._lock(self.db,self.tenant,rid,self.runtime.test_sqlite)
        flow = repo.lock_flow(self.db,self.tenant,**self.flags)
        return row,resource,flow

    def _fence(self, command, resource, epoch: int, now: int):
        p.require(command.status == "claimed" and command.claim_epoch == epoch
                  and command.resource_id == resource.resource_id
                  and command.resource_epoch == resource.epoch,
                  "image_command_epoch_mismatch")
        res._owned(resource,"image",self.worker,command.command_id,resource.epoch)
        p.require(resource.expires_at is not None and resource.expires_at > now,
                  "resource_lease_expired")

    def claim(self, scope: Scope, now: int, skill_sha: str):
        snapshot, row = self._snapshot(scope)
        cap = self._capability(now,skill_sha)
        # Any later rejection rolls this reservation back in the same transaction.
        epoch = res.reserve(self.db,self.tenant,cap.resource_id,"image",self.worker,
                            scope.command_id,now,**self.flags)
        flow = repo.lock_flow(self.db,self.tenant,**self.flags)
        p.require(flow.running, "image_flow_stopped")
        def apply(pack, allowed):
            cmd=self._command(scope)
            p.require(cmd.status == "pending","image_command_not_pending")
            # A hash alone never proves source and role plan readiness.
            self.db.refresh(row)
            p.require(isinstance(row.approved_plan,dict) and
                      p.canonical_sha(row.approved_plan)==row.plan_sha256 and
                      self.runtime.authorize_plan(self.db,self.identity,row),
                      "image_plan_unverified")
            grant=p.claim(pack,self.worker,cmd.wave,now,wave2_allowed=allowed,
                          flow_running=True,capability_verified=True,resource_granted=True)
            cmd.status="claimed";cmd.claim_epoch=grant.epoch
            cmd.resource_id=cap.resource_id;cmd.resource_epoch=epoch
            cmd.image_granted=False;cmd.started=False
            # The initial image token is discarded. Separate image-claim mints
            # its own token after authenticating the command lease.
            return {"commandId":cmd.command_id,"epoch":grant.epoch,"wave":cmd.wave,
                    "lease":grant.command_token,"expiresAtEpoch":grant.expires_at,
                    "packId":scope.pack_id,"garmentSku":scope.sku}
        return self._transition(scope,row,apply)

    def claim_image(self,scope: Scope,epoch: int,token: str,now: int):
        cap=self._capability(now,p.SKILL_SHA256)
        row,resource,flow=self._lock_existing(scope)
        p.require(cap.resource_id==resource.resource_id,'resource_binding_changed')
        p.require(flow.running,"image_flow_stopped")
        def apply(pack,_):
            cmd=self._command(scope);self._fence(cmd,resource,epoch,now)
            lease=p.authorized(pack,self.worker,epoch,token,now,kind="command")
            p.require(not lease.adaptive_batches,"v61_batch_claim_required")
            p.require(not cmd.started and not pack.uncertain,"image_grant_requires_reconciliation")
            # Safe recovery of a lost grant BEFORE any role-start. Old token
            # becomes invalid atomically; no duplicate role can be authorized.
            image_token=secrets.token_urlsafe(36)
            lease.image_hash=p.digest(image_token)
            cmd.image_granted=True
            return {"commandId":cmd.command_id,"imageLease":image_token,
                    "epoch":epoch,"expiresAtEpoch":lease.expires_at,
                    "packId":scope.pack_id,"garmentSku":scope.sku,"wave":cmd.wave}
        return self._transition(scope,row,apply)

    def heartbeat(self,scope: Scope,epoch: int,token: str,now: int):
        row,resource,_=self._lock_existing(scope)
        def apply(pack,_):
            cmd=self._command(scope);self._fence(cmd,resource,epoch,now)
            until=p.heartbeat(pack,self.worker,epoch,token,now)
            resource.expires_at=until
            return {"expiresAtEpoch":until,"epoch":epoch}
        return self._transition(scope,row,apply)

    def start(self,scope: Scope,epoch: int,token: str,role: int,attempt: str,now: int):
        cap=self._capability(now,p.SKILL_SHA256)
        row,resource,flow=self._lock_existing(scope)
        p.require(cap.resource_id==resource.resource_id,'resource_binding_changed')
        def apply(pack,_):
            cmd=self._command(scope);self._fence(cmd,resource,epoch,now)
            p.require(cmd.image_granted,"image_grant_required")
            p.require(pack.lease is not None and not pack.lease.adaptive_batches,
                      "v61_batch_start_required")
            # Retry/replay of the same attempt never sends a second grant.
            result=p.role_start(pack,self.worker,epoch,token,now,role,attempt,
                                flow_running=flow.running,resource_granted=True)
            if result:
                self.db.refresh(row)
                p.require(self.runtime.authorize_plan(self.db,self.identity,row),
                          "image_plan_unverified")
                cmd.started=True
            return {"sendAllowed":result,"attemptId":attempt,"role":role,"epoch":epoch}
        return self._transition(scope,row,apply)

    def finish(self,scope: Scope,epoch: int,token: str,now: int,outcome: str,*,roles=None):
        row,resource,_=self._lock_existing(scope)
        def apply(pack,_):
            cmd=self._command(scope)
            # An ACK replay must not release a newer resource grant.
            if pack.lease is not None and pack.lease.epoch == epoch:
                active=p.authorized(pack,self.worker,epoch,token,now,kind="command")
                if active.adaptive_batches or roles is not None:
                    p.require(isinstance(roles,dict) and set(roles)=={str(n) for n in p.WAVES[cmd.wave]}
                        and all((roles[str(n)]=="verified")== (pack.roles[n].status=="verified")
                                for n in p.WAVES[cmd.wave]),"outcome_receipt_mismatch")
            result=p.complete(pack,self.worker,epoch,token,now,outcome)
            p.require(result['wave']==cmd.wave,'image_command_wave_mismatch')
            if result["ackReplayed"]:
                return result
            self._fence(cmd,resource,epoch,now)
            # complete() forbids unresolved dispatch; only accepted receipts
            # or trusted, reconciled terminal failures can reach this point.
            res.release(self.db,self.tenant,resource.resource_id,"image",self.worker,
                        cmd.command_id,resource.epoch,verified_idle=True,**self.flags)
            missing=result["missingRoles"]
            cmd.status=("completed" if not missing else
                        "pending" if any(pack.roles[n].status=="pending" for n in missing)
                        and pack.wave_claims[cmd.wave]<p.MAX_WAVE_CLAIMS else "hold")
            cmd.image_granted=False
            return result
        result=self._transition(scope,row,apply)
        if not result["ackReplayed"] and result["wave"]==1:
            # Same transaction, existing batch/pack lock order. A completed
            # wave can release wave 2 without a human action; the decision
            # still isolates held/timed-out packs and is monotonic.
            repo.release_barrier(self.db,self.tenant,row.batch_id,now,**self.flags)
        return {**result, "status":"completed", "commandId":scope.command_id, "outcome":outcome}

    def yield_unsent(self,scope: Scope,epoch: int,token: str,now: int):
        row,resource,_=self._lock_existing(scope)
        def apply(pack,_):
            cmd=self._command(scope);self._fence(cmd,resource,epoch,now)
            p.authorized(pack,self.worker,epoch,token,now,kind="command")
            p.require(not cmd.started and not pack.uncertain,"cannot_yield_started_command")
            res.release(self.db,self.tenant,resource.resource_id,"image",self.worker,
                        cmd.command_id,resource.epoch,verified_idle=True,**self.flags)
            pack.lease=None
            # Yield before send does not consume renderer attempts. Keep pack
            # ownership (safe explicit owner transfer is a separate recovery).
            pack.wave_claims[cmd.wave]-=1
            cmd.status="pending";cmd.image_granted=False
            return {"yielded":True,"epoch":epoch}
        return self._transition(scope,row,apply)

    def prepare_upload(self,scope: Scope,epoch: int,token: str,role: int,attempt: str,
                       now: int,image_sha: str,width: int,height: int,
                       artwork_sha: str,placement_sha: str,qa_id: str,
                       source_sha: str,skill_sha: str,*,batch_id=None,output_index=None,
                       generation_method=None,qa_record_sha=None):
        row,resource,_=self._lock_existing(scope)
        p.require(p.valid_sha(image_sha) and type(width) is int and type(height) is int and
                  width==height and 1600<=width<=4096 and width*height<=16000000,
                  "invalid_native_dimensions")
        p.require(isinstance(qa_id,str) and p._ID.fullmatch(qa_id),"invalid_qa_receipt_id")
        def apply(pack,_):
            cmd=self._command(scope);self._fence(cmd,resource,epoch,now)
            lease=p.authorized(pack,self.worker,epoch,token,now)
            p.require(cmd.image_granted and role in p.WAVES[lease.wave],
                      "role_outside_wave")
            from .adaptive_v61 import require_upload_binding
            require_upload_binding(pack,lease,role=role,attempt=attempt,batch_id=batch_id,
                output_index=output_index,method=generation_method,qa_id=qa_id,
                qa_record_sha=qa_record_sha,plan_sha=row.plan_sha256)
            if lease.adaptive_batches:
                from .qa_submission import require_submission
                require_submission(self.db,row,role,attempt,epoch,qa_id,qa_record_sha,batch_id)
            r=pack.roles[role]
            p.require(r.attempt_id==attempt and artwork_sha==pack.identity.artwork_sha
                      and placement_sha==pack.identity.placement_sha
                      and source_sha==pack.identity.source_sha
                      and skill_sha==pack.identity.skill_sha,"upload_identity_mismatch")
            key=(self.tenant,scope.pack_id,role,attempt)
            intent=self.db.get(ImageUploadIntent,key)
            if intent is not None:
                p.require((intent.image_sha256,intent.width,intent.height,intent.epoch,intent.qa_receipt_id)==
                          (image_sha,width,height,epoch,qa_id),"immutable_upload_intent")
                # Even exact replay cannot start another external write.
                return {"alreadyPrepared":True,"status":intent.status,"ticket":None}
            p.require(r.status=="dispatch_uncertain","role_not_in_progress")
            self.db.refresh(row)
            p.require(self.runtime.authorize_plan(self.db,self.identity,row) and
                      self.runtime.authorize_qa(self.db,self.identity,row,role,image_sha,qa_id),
                      "image_qa_or_source_unverified")
            from .approvals import publication_scope,preview_canary
            publication=publication_scope(self.db,self.identity,row,role,image_sha,qa_id,width,height)
            storage_placement=row.approved_plan.get('storagePlacement')
            p.require(storage_placement in ('front','back'),'storage_placement_unverified')
            r.status="upload_pending"
            self.db.add(ImageUploadIntent(tenant_id=self.tenant,pack_id=scope.pack_id,
                        role=role,attempt_id=attempt,epoch=epoch,image_sha256=image_sha,
                        qa_receipt_id=qa_id,width=width,height=height,status="pending"))
            return {"alreadyPrepared":False,"status":"pending","ticket":UploadTicket(
                self.tenant,row.listing_key,scope.pack_id,gallery_pack_id(scope.pack_id),
                scope.sku,role,attempt,epoch,pack.identity.artwork_sha,
                pack.identity.source_sha,pack.identity.placement_sha,storage_placement,image_sha,width,height,
                *publication,preview_only_canary=preview_canary(self.db,row))}
        return self._transition(scope,row,apply)

    def accept_storage(self,scope: Scope,token: str,now: int,ticket: UploadTicket,
                       receipt: p.StoredReceipt, *, project=None):
        """Internal trusted receipt adapter only. Never deserialize from HTTP body."""
        row,resource,_=self._lock_existing(scope)
        p.require(ticket.tenant==self.tenant and ticket.pack_id==scope.pack_id
                  and ticket.sku==scope.sku and ticket.listing==row.listing_key,
                  "upload_identity_mismatch")
        def apply(pack,_):
            cmd=self._command(scope);self._fence(cmd,resource,ticket.epoch,now)
            intent=self.db.get(ImageUploadIntent,(self.tenant,scope.pack_id,
                                                  ticket.role,ticket.attempt_id))
            p.require(intent is not None and
                      (intent.image_sha256,intent.width,intent.height,intent.epoch)==
                      (receipt.image_sha,receipt.width,receipt.height,ticket.epoch),
                      "upload_intent_mismatch")
            self.db.refresh(row)
            p.require(self.runtime.authorize_plan(self.db,self.identity,row) and
                      self.runtime.authorize_qa(self.db,self.identity,row,ticket.role,
                                                receipt.image_sha,intent.qa_receipt_id),
                      "image_qa_or_source_unverified")
            from .approvals import publication_scope,preview_canary
            publication=publication_scope(self.db,self.identity,row,ticket.role,
                receipt.image_sha,intent.qa_receipt_id,receipt.width,receipt.height)
            p.require(ticket.publication_scope==publication==receipt.publication_scope,
                      "native_publication_scope_mismatch")
            p.require(ticket.preview_only_canary==receipt.preview_only_canary==preview_canary(self.db,row),
                      "preview_canary_scope_mismatch")
            if ticket.generation_method is not None:
                p.require(receipt.publication_ready,"native_qa_publication_ineligible")
            result=p.verify_role(pack,self.worker,ticket.epoch,token,now,ticket.role,
                                 ticket.attempt_id,receipt)
            # Production will require a gallery projection in the same DB transaction.
            p.require(project is not None or self.runtime.test_sqlite,'gallery_projection_required')
            if project is not None:
                project(self.db,ticket,receipt)
            intent.status="verified"
            return {"status":result,"packId":scope.pack_id,"role":ticket.role,
                    "imageSha256":receipt.image_sha,"previewOnly":not receipt.publication_ready,
                    "storageVerified":True}
        return self._transition(scope,row,apply)