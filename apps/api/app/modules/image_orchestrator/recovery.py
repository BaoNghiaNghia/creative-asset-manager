"""Conservative image-only recovery; no renderer or Drive calls.

Expired leases are not evidence of a failed render. An unsent reservation
can be released automatically only when its exact fence still matches.
Any started command is quarantined; external output reconciliation needs
trusted evidence, not a worker claim or timeout. No video rows are changed.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.render_resources import service as res
from . import policy as p, repository as repo
from .commands import command_id
from .model import ImageBatch, ImageCommand, ImagePack, ImageUploadIntent


def recover_expired(db: Session, tenant: str, cid: str, now: int, *,
                    test_sqlite: bool = False) -> dict:
    repo._transaction(db,test_sqlite=test_sqlite)
    flags={"test_sqlite":test_sqlite}
    initial=db.get(ImageCommand,(tenant,cid))
    p.require(initial is not None,"image_command_not_found")
    row=db.get(ImagePack,(tenant,initial.pack_id))
    p.require(row is not None,"image_pack_not_found")
    if initial.status!="claimed":
        return {"action":"unchanged"}
    p.require(initial.resource_id is not None,"resource_binding_missing")
    resource=res._lock(db,tenant,initial.resource_id,test_sqlite)
    repo.lock_flow(db,tenant,**flags)
    def apply(pack,_):
        cmd=db.scalar(select(ImageCommand).where(
            ImageCommand.tenant_id==tenant,ImageCommand.command_id==cid,
            ImageCommand.pack_id==pack.identity.pack_id
        ).with_for_update().execution_options(populate_existing=True))
        if cmd.status!="claimed":
            return {"action":"unchanged"}
        p.require(pack.lease is not None and pack.lease.epoch==cmd.claim_epoch
                  and pack.lease.wave==cmd.wave and pack.owner==pack.lease.owner
                  and cmd.resource_id==resource.resource_id
                  and cmd.resource_epoch==resource.epoch,"recovery_fence_mismatch")
        res._owned(resource,"image",pack.owner,cid,resource.epoch)
        if (pack.lease.expires_at>now or resource.expires_at is None
                or resource.expires_at>now):
            return {"action":"active"}
        owner=pack.owner
        # Persist expiry before any future claim can be considered.
        p.expire(pack,now)
        cmd.image_granted=False
        if cmd.started or pack.uncertain:
            cmd.status="hold"
            for intent in db.scalars(select(ImageUploadIntent).where(
                    ImageUploadIntent.tenant_id==tenant,
                    ImageUploadIntent.pack_id==pack.identity.pack_id,
                    ImageUploadIntent.epoch==cmd.claim_epoch,
                    ImageUploadIntent.status=="pending").with_for_update()):
                intent.status="reconcile_required"
            return {"action":"reconcile_required","resourceReleased":False}
        # No committed role-start in this command; therefore a conforming
        # runner never received authorization to submit a renderer request.
        res.release(db,tenant,resource.resource_id,"image",owner,cid,resource.epoch,
                    verified_idle=True,**flags)
        cmd.status=("pending" if pack.wave_claims[cmd.wave]<p.MAX_WAVE_CLAIMS
                    else "hold")
        # Preserve prior pack ownership whenever any role was ever attempted.
        if not any(r.attempts for r in pack.roles.values()):
            pack.owner=None
        return {"action":"requeued_unsent" if cmd.status=="pending" else "claim_limit_hold",
                "resourceReleased":True,"ownerRetained":pack.owner is not None}
    return repo.transition(db,tenant,row.batch_id,row.pack_id,row.garment_sku,apply,**flags)


def requeue_isolated(db: Session, tenant: str, pack_id: str, new_batch_id: str,
                     now: int, *, test_sqlite: bool = False) -> dict:
    """Move an idle isolated pack into a new fixed batch, retaining history.

    Old released batch membership and release decision remain immutable.
    Never reset attempts, clear unresolved uploads or change pack IDs.
    """
    flags={"test_sqlite":test_sqlite}
    repo._transaction(db,**flags)
    repo.lock_flow(db,tenant,**flags)
    snapshot=db.get(ImagePack,(tenant,pack_id))
    p.require(snapshot is not None,"image_pack_not_found")
    old=repo._lock_batch(db,tenant,snapshot.batch_id)
    row=repo._lock_pack(db,tenant,old.batch_id,pack_id)
    p.require(old.release_decision is not None and
              pack_id in old.release_decision.get("isolated",[]),"pack_not_isolated")
    pack,_=repo._load(db,row)
    p.require(pack.lease is None and not pack.uncertain,"reconciliation_required")
    cmds=list(db.scalars(select(ImageCommand).where(
        ImageCommand.tenant_id==tenant,ImageCommand.pack_id==pack_id
    ).order_by(ImageCommand.wave).with_for_update().execution_options(populate_existing=True)))
    p.require(len(cmds)==2 and all(c.status!="claimed" for c in cmds),
              "active_or_missing_command")
    p.require(not any(c.status=="hold" for c in cmds),"manual_recovery_required")
    p.require(all(r.status in ("pending","verified") for r in pack.roles.values()),
              "manual_recovery_required")
    if pack.complete:
        return {"action":"already_complete"}
    new=p.Batch(new_batch_id,tenant,(pack_id,),now)
    p.require(new.batch_id!=old.batch_id and
              db.get(ImageBatch,(tenant,new.batch_id)) is None,"recovery_batch_exists")
    db.add(ImageBatch(tenant_id=tenant,batch_id=new.batch_id,created_at=now,
        pack_ids=[pack_id],release_decision=None))
    db.flush()
    row.batch_id=new.batch_id
    # No new renderer or command IDs. Release on the next ordinary barrier
    # evaluation; a complete Wave 1 may immediately qualify in the new batch.
    db.flush()
    return {"action":"requeued_isolated","packId":pack_id,
            "previousBatchId":old.batch_id,"batchId":new.batch_id}
