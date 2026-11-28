"""Single-pack extra-attempt repair: isolated fixtures, no provider calls."""
from copy import deepcopy
from unittest.mock import patch
from sqlalchemy.orm import Session
import pytest
import test_commands_api as fixture
from app.modules.image_orchestrator import policy as p,repository as repo,worn_repair,adaptive_v61
from app.modules.image_orchestrator.identity import ImageIdentity
from app.modules.image_orchestrator.model import ImageBatch,ImagePack,ImageCommand,ImageRole,ImageCapabilityState,ImageUploadIntent,ImageQAApproval
from app.modules.image_orchestrator.qa_submission import ImageQARecordSubmission,record_hash
from app.modules.render_resources.model import RenderResource
from app.modules.image_orchestrator.live_runtime import WORKER,RESOURCE

T=fixture.TENANT
NOW=fixture.NOW
EVIDENCE="e"*64
OLD="isolated-worn"
DECISION={"open":True,"wave2_eligible":[],"waiting":[],"releasedAt":NOW}

@pytest.fixture
def ready():
    f=fixture.CommandApiTests()
    f.setUp()
    f.principal=ImageIdentity(T,WORKER,"muse")
    ImageQAApproval.__table__.create(f.engine)
    ImageQARecordSubmission.__table__.create(f.engine)
    try:
        with Session(f.engine) as db,db.begin():
            db.add(RenderResource(tenant_id=T,resource_id=RESOURCE,
                       integration_verified=True,shared_with_video=False))
            db.add(ImageCapabilityState(tenant_id=T,worker_id=WORKER,resource_id=RESOURCE,
                       skill_sha256=p.SKILL_SHA256,claim_allowed=True))
            db.add(ImageBatch(tenant_id=T,batch_id=OLD,created_at=NOW-100,
                       pack_ids=[f.pack.pack_id],release_decision={**DECISION,"isolated":[f.pack.pack_id]}))
            db.flush()
            pack=db.get(ImagePack,(T,f.pack.pack_id))
            pack.batch_id=OLD
            pack.owner=WORKER
            pack.epoch=1
            old_ids=[]
            for n in (1,3):
                role=db.get(ImageRole,(T,f.pack.pack_id,n))
                ids=[f"old-{n}-{i}" for i in range(1,4)]
                role.status="hold";role.attempts=3;role.attempt_id=ids[-1]
                role.failure_code="local_qa_failed_other"
                old_ids.extend(ids)
                for i,aid in enumerate(ids):
                    qa={"role":n,"artworkFidelity":"pass","collarGapApproxMm":40,
                        "majorDefects":["print_too_close_to_collar"]}
                    record={"role":n,"localRejection":{"candidateSha256":p.digest(aid),
                        "qaRecordSha256":record_hash(qa,n),"reason":"qa_failed_other","record":qa}}
                    db.add(ImageQARecordSubmission(tenant_id=T,receipt_id=p.digest("qa-"+aid),
                           pack_id=f.pack.pack_id,role=n,attempt_id=aid,epoch=1,
                           worker_id=WORKER,plan_sha256=pack.plan_sha256,
                           batch_id="a"*32,record_sha256=record_hash(record,n),record=record,
                           received_at=NOW+i))
            from dataclasses import asdict
            for n in (2,4):
                aid=f"saved-{n}"
                old_ids.append(aid)
                sha=p.digest(aid)
                r=db.get(ImageRole,(T,f.pack.pack_id,n))
                r.status="verified";r.attempts=1;r.attempt_id=aid
                r.receipt_state=asdict(p.StoredReceipt(sha,1600,1600,"fixture_drive",sha,"pass",
                              True,f.pack.placement_sha,preview_only_canary=True))
                db.add(ImageUploadIntent(tenant_id=T,pack_id=f.pack.pack_id,role=n,attempt_id=aid,
                        epoch=1,image_sha256=sha,qa_receipt_id=p.digest("qa-"+aid),
                        width=1600,height=1600,status="verified"))
            pack.used_attempt_ids=old_ids
            c=db.get(ImageCommand,(T,f.cid))
            c.status="hold";c.claim_epoch=1;c.started=True;c.resource_id=RESOURCE
        yield f
    finally:
        f.tearDown()

def authorize(f):
    with patch.dict(adaptive_v61.REGISTRY,{"wornRepairBundleVerified":True,
                                           "wornRepairEvidenceSha256":EVIDENCE}), \
         patch.object(worn_repair.approvals,"preview_canary",return_value=True), \
         patch.object(worn_repair,"runtime_plan_authorized",return_value=True):
        with Session(f.engine) as db,db.begin():
            return worn_repair.grant(db,T,f.pack.listing,f.pack.pack_id,1,
                 adaptive_v61.BUNDLE_SHA256,EVIDENCE,NOW,test_sqlite=True)

def test_old_bundle_fails_closed(ready):
    f=ready
    with Session(f.engine) as db,db.begin():
        with pytest.raises(p.Conflict,match="corrected_worn_runner_not_verified"):
            worn_repair.grant(db,T,f.pack.listing,f.pack.pack_id,1,
                adaptive_v61.BUNDLE_SHA256,EVIDENCE,NOW,test_sqlite=True)
        assert db.get(ImageCommand,(T,f.cid)).status=="hold"

def test_repair_keeps_receipts_history_and_old_barrier(ready):
    f=ready
    with Session(f.engine) as db:
        snapshots={n:deepcopy(db.get(ImageRole,(T,f.pack.pack_id,n)).receipt_state) for n in (2,4)}
        original=deepcopy(db.get(ImageBatch,(T,OLD)).release_decision)
    result=authorize(f)
    assert result["status"]=="repair_authorized"
    assert result["preservedRoles"]==[2,4]
    assert authorize(f)["status"]=="already_authorized"
    with Session(f.engine) as db:
        row=db.get(ImagePack,(T,f.pack.pack_id))
        assert row.epoch==1 and row.batch_id!=OLD
        assert db.get(ImageBatch,(T,OLD)).release_decision==original
        assert len(row.used_attempt_ids)==8
        for n in (1,3):
            r=db.get(ImageRole,(T,f.pack.pack_id,n))
            assert r.status=="pending" and r.attempts==3
            assert p.attempt_stats(r.status,r.attempts,row.ack_state[p.REPAIR_METADATA_KEY],n)=={
                "baseAttemptCount":3,"attemptCount":3,"attemptLimit":4,"retryAllowed":True}
        for n in (2,4):
            assert db.get(ImageRole,(T,f.pack.pack_id,n)).receipt_state==snapshots[n]

def test_repair_attempts_complete_and_release_new_wave(ready):
    f=ready
    result=authorize(f)
    c,g=f.acquire()
    assert c["epoch"]==2
    for n in (1,3):
        aid=f"repair-{n}"
        start=f.start(g,role=n,attempt=aid)
        assert start.status_code==200,start.text
        assert start.json()["sendAllowed"] is True
        assert f.start(g,role=n,attempt=aid).json()["sendAllowed"] is False
        with Session(f.engine) as db,db.begin():
            sha=p.digest(aid)
            receipt=p.StoredReceipt(sha,1600,1600,"fixture_drive",sha,"pass",True,
                                    f.pack.placement_sha,preview_only_canary=True)
            repo.transition(db,T,result["batchId"],f.pack.pack_id,"C1717",
                lambda pack,_:p.verify_role(pack,WORKER,c["epoch"],g["imageLease"],
                        NOW,n,aid,receipt),test_sqlite=True)
    response=f.command("complete",{"lease":c["lease"],"epoch":2,"outcome":"success",
                         "roles":{str(n):"verified" for n in range(1,5)}})
    assert response.status_code==200,response.text
    with Session(f.engine) as db:
        row=db.get(ImagePack,(T,f.pack.pack_id))
        grant=row.ack_state[p.REPAIR_METADATA_KEY]
        assert db.get(ImageBatch,(T,result["batchId"])).release_decision["wave2_eligible"]==[f.pack.pack_id]
        assert len(row.used_attempt_ids)==10
        for n in (1,3):
            r=db.get(ImageRole,(T,f.pack.pack_id,n))
            assert r.attempts==3 and r.status=="verified"
            assert grant["roles"][str(n)]["repairAttemptId"]==f"repair-{n}"
            assert p.attempt_stats(r.status,r.attempts,grant,n)["attemptCount"]==4

def test_failed_extra_attempt_does_not_restore_budget(ready):
    f=ready
    result=authorize(f)
    c,g=f.acquire()
    assert f.start(g,role=1,attempt="repair-1").json()["sendAllowed"]
    with Session(f.engine) as db,db.begin():
        repo.transition(db,T,result["batchId"],f.pack.pack_id,"C1717",
            lambda pack,_:p.confirmed_failure(pack,1,"repair-1","local_qa_failed_other",
                       terminal=False,proof_of_no_usable_result=True),test_sqlite=True)
    assert f.start(g,role=1,attempt="forbidden").status_code==409
    assert authorize(f)["status"]=="already_authorized"
    with Session(f.engine) as db:
        row=db.get(ImagePack,(T,f.pack.pack_id))
        role=db.get(ImageRole,(T,f.pack.pack_id,1))
        assert role.status=="hold" and role.attempts==3
        assert p.attempt_stats(role.status,role.attempts,row.ack_state[p.REPAIR_METADATA_KEY],1)=={
            "baseAttemptCount":3,"attemptCount":4,"attemptLimit":4,"retryAllowed":False}
