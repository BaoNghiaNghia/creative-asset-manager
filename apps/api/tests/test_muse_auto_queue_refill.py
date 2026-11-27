"""Isolated queue refill regression; never calls a renderer or Drive."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.modules.processing.model import ProcessingJobModel
from app.modules.muse_bridge import router as m, queue_recovery as q
from app.modules.muse_bridge.model import CamMuseJob as J, CamMuseCommand as C

NOW = datetime(2026, 11, 27, 9, tzinfo=timezone.utc)
TENANT = "test-refill"
SHA = "a"*64

@pytest.fixture
def db(monkeypatch):
    engine=create_engine("sqlite://")
    for model in (ProcessingJobModel,J,C):
        model.__table__.create(engine)
    for name, value in (("_utc",NOW),("smart_queue_enabled",True),
                         ("_story_v33_enabled",False),("_continuous_gen_enabled",True),
                         ("_video_aspect_phase","vertical_first")):
        monkeypatch.setattr(m,name,lambda v=value:v)
    with Session(engine) as session:
        yield session
    engine.dispose()

def job(db,i,vertical=False,attempts=0,**extra):
    lid=str(1234567800+i)
    uploads={"9:16":{"storageVerified":True,"storage_provider":"google_drive_managed",
        "relativeFolder":f"listing_{lid}/design_{SHA[:16]}_front/video_ugc",
        "drive_file_id":"video-"+lid,"drive_folder_id":"folder-"+lid,"sha256":"b"*64}} if vertical else {}
    j=J(tenant_id=TENANT,listing_key=lid,picked_at=NOW+timedelta(seconds=i),
        title="Test shirt",artwork_sha256=SHA,artwork_asset_path="/verified",
        source_state="ready_for_muse",status="pending",attempts=attempts,
        output_receipts={"visual_qa":{"verdict":"pass","artwork_sha256":SHA},
            "drive_uploads":uploads,"videoContentQa":"pending_review",
            "videoGarmentQa":"pending_independent_visual_review","camLinked":False,**extra})
    db.add(j);db.flush()
    return j

def command(db,j,status="failed",phase="create_video",scope=None,age=20):
    uid=str(uuid4()); c=C(tenant_id=TENANT,command_id=uid,listing_key=j.listing_key,
        action=phase,phase=phase,request_id=uid,artwork_sha256=SHA,status=status,
        attempts=1,created_at=NOW-timedelta(minutes=age),updated_at=NOW-timedelta(minutes=age),
        details={"origin":q.POLICY,**({"requiredVideoAspectRatios":scope} if scope else {})})
    db.add(c);db.flush()
    return c

def test_bounded_vertical_first_then_horizontal_and_idempotent(db):
    h=[job(db,i,True) for i in range(12)]
    v=[job(db,i) for i in range(20,22)]
    before={j.listing_key:deepcopy(j.output_receipts) for j in h+v}
    db.commit()
    report=q.reconcile_queue(db,TENANT,NOW)
    assert report["newVideoCommands"]==8
    assert [c["listingId"] for c in report["newCommands"][:2]]==[j.listing_key for j in v]
    for c in db.scalars(select(C).where(C.status=="pending")).all():
        j=db.get(J,(TENANT,c.listing_key))
        assert c.details["requiredVideoAspectRatios"]==(["16:9"] if j in h else ["9:16"])
        assert m._eligible_phase(db,TENANT,j,c)=="create_video"
        assert j.output_receipts==before[j.listing_key] and j.attempts==0
    assert q.reconcile_queue(db,TENANT,NOW)["newVideoCommands"]==0

def test_successful_previous_phases_not_charged_but_caps_preserved(db):
    h=job(db,1,True,2)
    for n in range(2):command(db,h,"reviewed","review_artwork",age=40+n)
    command(db,h,"receipt_submitted",scope=["9:16"],age=30)
    b=job(db,2,True,2)
    for n in range(3):command(db,b,scope=["16:9"],age=30+n)
    capped=job(db,3,True,5)
    held=job(db,4,True,videoCapabilityHold={"code":"blocked"})
    v=job(db,5,attempts=3)
    for n in range(3):command(db,v,scope=["9:16"],age=30+n)
    db.commit()
    assert [c["listingId"] for c in q.reconcile_queue(db,TENANT,NOW)["newCommands"]]==[h.listing_key]
    assert [j.attempts for j in (h,b,capped,v)]==[2,2,5,3]
    assert held.output_receipts["videoCapabilityHold"]=={"code":"blocked"}

def test_completed_vertical_command_not_repurposed(db):
    j=job(db,1,True);c=command(db,j,"pending",scope=["9:16"])
    db.commit()
    assert m._eligible_phase(db,TENANT,j,c) is None
    report=q.reconcile_queue(db,TENANT,NOW)
    assert c.status=="receipt_submitted"
    assert report["newVideoCommands"]==1
    fresh=db.get(C,(TENANT,report["newCommands"][0]["commandId"]))
    assert fresh.details["requiredVideoAspectRatios"]==["16:9"]

def test_live_scope_and_finished_repair_protected(db):
    j=job(db,1,True)
    j.status="claimed";j.lease_expires_at=NOW+timedelta(minutes=10)
    j.output_receipts={**j.output_receipts,"videoWorkScope":{"requiredVideoAspectRatios":["9:16"]}}
    assert m._job_required_aspects(j)==["9:16"]
    assert m._unleased_job_required_aspects(j)==["16:9"]
    j.status="pending"
    j.output_receipts={**j.output_receipts,"videoRegeneration":{"requiredVideoAspectRatios":["16:9"]}}
    assert m._video_job_claim_contract(j)["forceFreshVideoAspectRatios"]==["16:9"]
    j.output_receipts={**j.output_receipts,"videoRegeneration":{"requiredVideoAspectRatios":["9:16"]}}
    assert m._video_job_claim_contract(j)["forceFreshVideoAspectRatios"]==[]
