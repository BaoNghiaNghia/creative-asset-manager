import asyncio
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.modules.muse_bridge import router as muse, regeneration as regen
from app.modules.muse_bridge.model import CamMuseJob, CamMuseCommand

TENANT = "test-regeneration"
LID = "1234567890"
ART = "a"*64
RATIOS = ("9:16","16:9")
SHAS = dict(zip(RATIOS, ("b"*64,"c"*64)))

@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(muse, "_story_v33_enabled", lambda: True)
    monkeypatch.setattr(muse, "_story_v33_bulk_enabled", lambda: True)
    monkeypatch.setattr(muse, "_story_lite_enabled", lambda: True)
    monkeypatch.setattr(muse, "_continuous_gen_enabled", lambda: True)
    monkeypatch.setattr(muse, "_phase_required_aspects", lambda: ["9:16"])
    remote = AsyncMock()
    monkeypatch.setattr(regen, "delete_drive_videos", remote)
    engine = create_engine("sqlite://")
    CamMuseJob.__table__.create(engine)
    CamMuseCommand.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        job = CamMuseJob(tenant_id=TENANT, listing_key=LID, picked_at=muse._utc(),
                         title="Retro T-shirt", artwork_sha256=ART, artwork_asset_path="native-master.png",
                         source_state="ready_for_muse", status="completed", attempts=2,
                         output_receipts={"visual_qa":{"verdict":"pass","artwork_sha256":ART},
                            "drive_uploads":{r:{"storageVerified":True,"storage_provider":"google_drive_managed",
                            "relativeFolder":f"listing_{LID}/design_{ART[:16]}_front/video_ugc",
                            "drive_file_id":f"test_file_{i}_abcdef","drive_folder_id":"test_folder_abcdef",
                            "sha256":SHAS[r]} for i,r in enumerate(RATIOS)}})
        db.add(job)
        db.commit()
        yield db, job, remote

def call(db, expected):
    return asyncio.run(regen.regenerate(db,TENANT,LID,expected))

def pin_lite(db, job):
    plans = muse._build_job_story_spec(job)["aspectPlans"]
    pin = {"storyVersion":muse.STORY_VERSION,"storyOutputContract":muse.LITE_OUTPUT,
        "storySourceContract":muse.LITE_SOURCE,"creativePolicy":muse.LITE_POLICY,
        "artworkSha256":ART,"garmentSku":"C1717","targetDurationSeconds":muse.LITE_TARGET_SECONDS,
        "heroGenerationPlans":{r:{k:plans[r][k] for k in ("heroRequestId","heroSeed","storyId")} for r in RATIOS}}
    old = job.output_receipts
    job.output_receipts={**old,"story_contract_v33":pin,"drive_uploads":{
        r:{**v,"storyVersion":muse.STORY_VERSION,"storyOutputContract":muse.LITE_OUTPUT}
        for r,v in old["drive_uploads"].items()}}
    db.commit()
    return pin

def test_both_latest_and_duplicate_fence(setup):
    db, job, remote=setup
    before=muse._build_job_story_spec(job)["aspectPlans"]
    result=call(db,SHAS)
    assert result["queued"] and job.status=="pending" and job.attempts==0
    assert result["videoUgc"]["regeneratingAspectRatios"]==list(RATIOS)
    assert muse._verified_video_ratios(job)==set()
    cmd=db.scalar(select(CamMuseCommand))
    assert cmd.command_id==result["commandId"] and cmd.status=="pending"
    assert muse._eligible_phase(db,TENANT,job,cmd)=="create_video"
    assert muse._job_required_aspects(job)==list(RATIOS)
    assert muse._story_candidate_v33(job)["creativePolicy"]==muse.LITE_POLICY
    for r in RATIOS:
        assert muse._build_job_story_spec(job)["aspectPlans"][r]["heroRequestId"]!=before[r]["heroRequestId"]
    with pytest.raises(HTTPException) as exc:
        call(db,SHAS)
    assert exc.value.status_code==409
    remote.assert_awaited_once()
    assert len(db.scalars(select(CamMuseCommand)).all())==1

def test_horizontal_only_preserves_compatible_vertical(setup):
    db,job,remote=setup
    pin=pin_lite(db,job)
    old=job.output_receipts["drive_uploads"]["9:16"].copy()
    result=call(db,{"16:9":SHAS["16:9"]})
    assert result["deletedAspectRatios"]==["16:9"]
    assert muse._verified_video_ratios(job)=={"9:16"}
    assert job.output_receipts["drive_uploads"]["9:16"]==old
    assert muse._job_required_aspects(job)==["16:9"]
    assert muse._pinned_work_aspects({"requiredVideoAspectRatios":["16:9"]})==["16:9"]
    new=job.output_receipts["story_contract_v33"]["heroGenerationPlans"]
    assert new["9:16"]==pin["heroGenerationPlans"]["9:16"]
    assert new["16:9"]["heroRequestId"]!=pin["heroGenerationPlans"]["16:9"]["heroRequestId"]
    assert set(remote.await_args.args[-1])=={"16:9"}

def test_incompatible_sibling_never_deleted(setup):
    db,job,remote=setup
    with pytest.raises(HTTPException) as exc:
        call(db,{"16:9":SHAS["16:9"]})
    assert exc.value.status_code==409
    remote.assert_not_awaited()
    assert muse._verified_video_ratios(job)==set(RATIOS)

def test_failed_drive_rolls_back(setup):
    db,job,remote=setup
    remote.side_effect=HTTPException(503,"Drive unavailable")
    with pytest.raises(HTTPException): call(db,SHAS)
    assert job.status=="completed"
    assert muse._verified_video_ratios(job)==set(RATIOS)
    assert not db.scalars(select(CamMuseCommand)).all()

def test_claimed_job_and_stale_sha(setup):
    db,job,remote=setup
    with pytest.raises(HTTPException) as exc: call(db,{"9:16":"d"*64})
    assert exc.value.status_code==409
    job.status="claimed"
    db.commit()
    with pytest.raises(HTTPException) as exc: call(db,SHAS)
    assert exc.value.status_code==409
    remote.assert_not_awaited()

def test_tenant_scope(setup):
    db,job,remote=setup
    with pytest.raises(HTTPException) as exc:
        asyncio.run(regen.regenerate(db,"wrong-tenant",LID,SHAS))
    assert exc.value.status_code==404
    remote.assert_not_awaited()
