"""Isolated SQLite maintenance tests. No renderer, storage or live scheduler."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.image_orchestrator import policy as p, repository as repo, maintenance
from app.modules.image_orchestrator.model import ImageFlowControl, ImageBatch, ImagePack, ImageRole

def test_deadline_tick_is_bounded_tenant_scoped_and_preserves_inflight():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[x.__table__ for x in
        (ImageFlowControl,ImageBatch,ImagePack,ImageRole)])
    now=100000
    def ident(tenant,listing):
        return p.PackIdentity(tenant,listing,"a"*64,"C1717",1,"b"*64,"c"*64)
    a,b,c,d=(ident("tenant",str(n)) for n in (123456789,123456790,123456791,123456792))
    foreign=ident("foreign","123456789")
    with Session(engine) as db,db.begin():
        db.add(ImageFlowControl(tenant_id="tenant",running=True))
        db.add(ImageFlowControl(tenant_id="foreign",running=True))
        for tenant,bid,created,packs in (
            ("tenant","old",now-p.BARRIER_SECONDS,(a,b)),
            ("tenant","old2",now-p.BARRIER_SECONDS,(c,)),
            ("tenant","fresh",now,(d,)),
            ("foreign","old",now-p.BARRIER_SECONDS,(foreign,))):
            repo.create_batch(db,p.Batch(bid,tenant,tuple(x.pack_id for x in packs),created),
                [(x,"d"*64) for x in packs],test_sqlite=True)
        def verify(pack,_):
            g=p.claim(pack,"muse-01",1,now,flow_running=True,
                capability_verified=True,resource_granted=True)
            for role in p.WAVES[1]:
                attempt="role-"+str(role); sha=p.digest(attempt)
                p.role_start(pack,"muse-01",g.epoch,g.image_token,now,role,attempt,
                    flow_running=True,resource_granted=True)
                p.verify_role(pack,"muse-01",g.epoch,g.image_token,now,role,attempt,
                    p.StoredReceipt(sha,2000,2000,"drive_id",sha,"pass",True,"b"*64))
            p.complete(pack,"muse-01",g.epoch,g.command_token,now,"success")
        repo.transition(db,"tenant","old",a.pack_id,"C1717",verify,test_sqlite=True)
        def start(pack,_):
            g=p.claim(pack,"muse-02",1,now,flow_running=True,
                capability_verified=True,resource_granted=True)
            p.role_start(pack,"muse-02",g.epoch,g.image_token,now,1,"inflight",
                flow_running=True,resource_granted=True)
        repo.transition(db,"tenant","old",b.pack_id,"C1717",start,test_sqlite=True)
    with Session(engine) as db,db.begin():
        snapshot=dict(db.get(ImagePack,("tenant",b.pack_id)).lease_state)
        result=maintenance.advance_due_barriers(db,"tenant",now,limit=1,test_sqlite=True)
        assert result=={"flowRunning":True,"evaluated":1,"moreDue":True,
                        "released":[{"batchId":"old","wave2Eligible":[a.pack_id],"isolated":[b.pack_id]}]}
        assert db.get(ImagePack,("tenant",b.pack_id)).lease_state==snapshot
        assert db.get(ImageRole,("tenant",b.pack_id,1)).status=="dispatch_uncertain"
        assert db.get(ImageBatch,("foreign","old")).release_decision is None
        assert db.get(ImageBatch,("tenant","fresh")).release_decision is None
        result=maintenance.advance_due_barriers(db,"tenant",now,limit=1,test_sqlite=True)
        assert result["released"][0]["batchId"]=="old2" and not result["moreDue"]
        assert maintenance.advance_due_barriers(db,"tenant",now,test_sqlite=True)["evaluated"]==0
        db.get(ImageFlowControl,"tenant").running=False
        db.flush()
        assert maintenance.advance_due_barriers(db,"tenant",now+p.BARRIER_SECONDS,
            test_sqlite=True)["flowRunning"] is False
        assert db.get(ImageBatch,("tenant","fresh")).release_decision is None
    engine.dispose()
