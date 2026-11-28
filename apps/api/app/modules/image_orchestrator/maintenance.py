"""Bounded image-only deadline maintenance for a trusted future scheduler.

No admission, renderer, storage, resource release or activation. Transactions
are caller-owned. Periodic deadline evaluation preserves in-flight leases and
the repository's immutable wave release decision. Scheduler installation is
separate from this module.
"""
from sqlalchemy import String, cast, or_, select
from . import policy as p, repository as repo
from .model import ImageBatch

def advance_due_barriers(db, tenant: str, now: int, *, limit: int=20,
                         test_sqlite: bool=False) -> dict:
    p.require(type(now) is int and now>=0 and type(limit) is int
              and 1<=limit<=20, "image_maintenance_input_invalid")
    flags={"test_sqlite":test_sqlite}
    flow=repo.lock_flow(db,tenant,**flags)
    if not flow.running:
        return {"flowRunning":False,"evaluated":0,"released":[],"moreDue":False}
    # JSON null and SQL NULL are both historical unset representations.
    # Bounded, oldest first; released rows fall out of the next tick.
    ids=list(db.scalars(select(ImageBatch.batch_id).where(
        ImageBatch.tenant_id==tenant,
        ImageBatch.created_at<=now-p.BARRIER_SECONDS,
        or_(ImageBatch.release_decision.is_(None),
            cast(ImageBatch.release_decision,String)=="null")
    ).order_by(ImageBatch.created_at,ImageBatch.batch_id).limit(limit+1)))
    released=[]
    for bid in ids[:limit]:
        result=repo.release_barrier(db,tenant,bid,now,**flags)
        p.require(result["open"],"due_barrier_did_not_open")
        released.append({"batchId":bid,"wave2Eligible":result["wave2_eligible"],
                         "isolated":result["isolated"]})
    return {"flowRunning":True,"evaluated":min(limit,len(ids)),
            "released":released,"moreDue":len(ids)>limit}
