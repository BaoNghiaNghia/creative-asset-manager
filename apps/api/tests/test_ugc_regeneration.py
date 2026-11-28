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
    monkeypatch.setattr(muse, "_video_aspect_phase", lambda: "vertical_first")
    remote = AsyncMock()
    monkeypatch.setattr(regen, "delete_drive_videos", remote)
    engine = create_engine("sqlite://")
    CamMuseJob.__table__.create(engine)
    CamMuseCommand.__table__.create(engine)
    from app.modules.processing.model import ProcessingJobModel
    ProcessingJobModel.__table__.create(engine)
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
    assert regen.requested_aspects(job)==list(RATIOS)
    assert muse._job_required_aspects(job)==["9:16"]
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

def repair_call(db, ratio, sha, color="Black", corrected="e"*64, oversized=False):
    return asyncio.run(regen.regenerate(db,TENANT,LID,{ratio:sha},
        hero_repair={"colorway":color,"correctedLocalSha256":corrected,
                     "oversizedFlatPrint":oversized}))

def test_contrast_repair_only_horizontal_retains_sibling_and_qa_evidence(setup):
    db,job,remote=setup
    pin=pin_lite(db,job)
    job.output_receipts={**job.output_receipts,"videoContentQa":"pending_review"}
    db.commit()
    before=muse._story_candidate_v33(job)
    old=job.output_receipts["drive_uploads"]["9:16"].copy()
    rejected=job.output_receipts["drive_uploads"]["16:9"].copy()
    result=repair_call(db,"16:9",SHAS["16:9"],color="Navy")
    assert result["queued"] and result["quarantinedAspectRatios"]==["16:9"]
    assert result["videoContentQa"]=="fail"
    assert remote.await_args.kwargs=={"archive":True}
    assert set(remote.await_args.args[-1])=={"16:9"}
    assert job.output_receipts["drive_uploads"]=={"9:16":old}
    history=job.output_receipts["rejectedVideoHistoryByAspect"]
    assert set(history)=={"16:9"} and history["16:9"][-1]["receipt"]==rejected
    assert history["16:9"][-1]["videoContentQa"]=="fail"
    assert job.output_receipts["story_contract_v33"]["heroGenerationPlans"]["9:16"]==pin["heroGenerationPlans"]["9:16"]
    story=muse._story_candidate_v33(job)
    assert story["aspectPlans"]["9:16"]==before["aspectPlans"]["9:16"]
    plan=story["aspectPlans"]["16:9"]
    assert "dark Comfort Colors C1717" in plan["referenceImagePrompt"]
    assert "Navy" in plan["referenceImagePrompt"] and "150 mm" in plan["referenceImagePrompt"]
    assert "flat sticker overlay" in plan["referenceImagePrompt"]
    assert plan["storyId"]==before["aspectPlans"]["16:9"]["storyId"]
    claim=muse._video_job_claim_contract(job)
    assert claim["requiredVideoAspectRatios"]==["16:9"]
    assert claim["savedVideoAspectRatios"]==["9:16"]
    assert claim["forceFreshVideoAspectRatios"]==["16:9"]
    assert claim["body"]["storyOutputContract"]==muse.LITE_OUTPUT
    assert claim["heroRepairByAspect"]["16:9"]["rejectedVideoSha256"]==SHAS["16:9"]

def test_contrast_repair_keeps_lite_pin_without_saved_sibling(setup):
    db,job,remote=setup
    pin=pin_lite(db,job)
    job.output_receipts={**job.output_receipts,"videoContentQa":"pending_review",
        "drive_uploads":{"9:16":job.output_receipts["drive_uploads"]["9:16"]}}
    db.commit()
    result=repair_call(db,"9:16",SHAS["9:16"],oversized=True)
    assert result["quarantinedAspectRatios"]==["9:16"]
    assert muse._pinned_story_v33(job)["storyOutputContract"]==muse.LITE_OUTPUT
    assert muse._pinned_story_v33(job)["heroGenerationPlans"]["16:9"]==pin["heroGenerationPlans"]["16:9"]
    assert muse._video_job_claim_contract(job)["requiredVideoAspectRatios"]==["9:16"]
    assert "Oversized" in job.output_receipts["rejectedVideoHistoryByAspect"]["9:16"][-1]["reason"]

def test_contrast_repair_rejects_light_color_stale_sha_and_missing_pin(setup):
    db,job,remote=setup
    with pytest.raises(HTTPException) as exc:
        repair_call(db,"9:16",SHAS["9:16"],color="White")
    assert exc.value.status_code==422
    with pytest.raises(HTTPException) as exc:
        repair_call(db,"9:16","d"*64)
    assert exc.value.status_code==409
    with pytest.raises(HTTPException) as exc:
        repair_call(db,"9:16",SHAS["9:16"])
    assert exc.value.status_code==409
    remote.assert_not_awaited()

def test_contrast_repair_failed_archive_rolls_back(setup):
    db,job,remote=setup
    pin_lite(db,job)
    job.output_receipts={**job.output_receipts,"videoContentQa":"pending_review"}
    db.commit()
    remote.side_effect=HTTPException(503,"Drive archive unavailable")
    with pytest.raises(HTTPException):
        repair_call(db,"16:9",SHAS["16:9"])
    assert muse._verified_video_ratios(job)==set(RATIOS)
    assert job.output_receipts["videoContentQa"]=="pending_review"
    assert "heroRepairByAspect" not in job.output_receipts
    assert not db.scalars(select(CamMuseCommand)).all()

def test_repair_cannot_supersede_other_ratio_pending(setup):
    from uuid import uuid4
    db,job,remote=setup
    pin_lite(db,job)
    job.output_receipts={**job.output_receipts,"videoContentQa":"pending_review"}
    now=muse._utc()
    cmd=CamMuseCommand(tenant_id=TENANT,command_id=str(uuid4()),listing_key=LID,
        action="create_video",request_id="other-ratio",status="pending",
        phase="create_video",artwork_sha256=ART,attempts=0,created_at=now,
        updated_at=now,details={"requiredVideoAspectRatios":["9:16"]})
    db.add(cmd)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        repair_call(db,"16:9",SHAS["16:9"])
    assert exc.value.status_code==409
    assert cmd.status=="pending"
    assert muse._verified_video_ratios(job)==set(RATIOS)
    remote.assert_not_awaited()

def test_drive_quarantine_never_deletes_and_checks_sha(monkeypatch):
    from types import SimpleNamespace
    from app.modules.etsy_spy import imagegen_drive as drive
    from app.modules.etsy_spy.listing_drive_folders import ROOT_FOLDER_ID
    selected={"16:9":{"drive_file_id":"test_file_abcdef", "drive_folder_id":"test_parent_abcdef",
                      "sha256":SHAS["16:9"]}}
    metadata={"id":"test_file_abcdef","parents":["test_parent_abcdef"],"trashed":False,
        "appProperties":{"cam_tenant_id":TENANT,"cam_content_hash":SHAS["16:9"],
                         "cam_asset_id":f"muse-ugc:{LID}:{ART}:horizontal"}}
    client=AsyncMock()
    client.__aenter__.return_value=client
    client.get.return_value=SimpleNamespace(status_code=200,json=lambda:metadata)
    client.patch.return_value=SimpleNamespace(status_code=200,
        json=lambda:{"id":"test_file_abcdef","trashed":True})
    monkeypatch.setattr(drive,"provider_and_folder",lambda:(SimpleNamespace(
        get_access_token=AsyncMock(return_value="test-only")),ROOT_FOLDER_ID))
    monkeypatch.setattr(drive,"verify_private_drive_destination",AsyncMock())
    monkeypatch.setattr(regen.httpx,"AsyncClient",lambda **kw:client)
    asyncio.run(regen.delete_drive_videos(TENANT,LID,ART,selected,archive=True))
    client.patch.assert_awaited_once()
    assert client.patch.await_args.kwargs["json"]=={"trashed":True}
    client.delete.assert_not_awaited()
    client.patch.reset_mock()
    metadata["appProperties"]["cam_content_hash"]="f"*64
    with pytest.raises(HTTPException) as exc:
        asyncio.run(regen.delete_drive_videos(TENANT,LID,ART,selected,archive=True))
    assert exc.value.status_code==409
    client.patch.assert_not_awaited()

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


def test_compact_print_only_updates_missing_hero_not_saved_receipts(setup, monkeypatch):
    monkeypatch.setattr(muse, "smart_queue_enabled", lambda: True)
    from copy import deepcopy
    from app.modules.muse_bridge.story_lite import PRINT_SCALE_POLICY, worker_policy
    db,job,remote=setup
    pin_lite(db,job)
    old=muse._story_candidate_v33(job)
    job.status="pending"
    job.output_receipts={**job.output_receipts,
        "drive_uploads":{"9:16":job.output_receipts["drive_uploads"]["9:16"]}}
    before=deepcopy(job.output_receipts)
    story=muse._story_candidate_v33(job)
    assert story["aspectPlans"]["9:16"]==old["aspectPlans"]["9:16"]
    plan=story["aspectPlans"]["16:9"]
    policy=plan["heroPrintScalePolicy"]
    assert policy==worker_policy()["heroPrintScalePolicy"]
    assert policy["version"]==PRINT_SCALE_POLICY
    assert policy["targetWidthPercentOfShirtFront"]==[35,40]
    assert policy["oversizedReferenceLinearScalePercent"]==80
    assert policy["firstInkBelowLowerCollarTargetMm"]==150
    assert policy["physicalCalibrationVerified"] is False
    realism=plan["heroRealismPolicy"]
    assert realism==worker_policy()["heroRealismPolicy"]
    assert realism["version"]=="CAM_VIDEO_HERO_REALISM_V1"
    assert plan["headProportionQaPolicy"]==worker_policy()["headProportionQaPolicy"]
    assert "headProportionQaPolicy" not in story["aspectPlans"]["9:16"]
    assert plan["prompt"]==old["aspectPlans"]["16:9"]["prompt"]+" "+realism["motionInstructions"]
    assert "reduce ONLY the ink box once" in plan["referenceImagePrompt"]
    assert "mid-thighs or knees" in plan["referenceImagePrompt"]
    assert "35-40%" in plan["referenceImagePrompt"]
    assert "Never repeatedly shrink" in plan["referenceImagePrompt"]
    assert job.output_receipts==before
    remote.assert_not_awaited()


def test_compact_print_respects_old_and_new_active_lease_scope(setup):
    from datetime import timedelta
    from app.modules.muse_bridge.story_lite import PRINT_SCALE_POLICY
    db,job,remote=setup
    pin_lite(db,job)
    job.status="claimed"
    job.lease_expires_at=muse._utc()+timedelta(minutes=10)
    job.output_receipts={**job.output_receipts,"drive_uploads":{},
        "videoWorkScope":{"referenceFirst":True,"requiredVideoAspectRatios":["16:9"]}}
    old=muse._story_candidate_v33(job)
    assert all("heroPrintScalePolicy" not in p for p in old["aspectPlans"].values())
    job.output_receipts={**job.output_receipts,
        "videoWorkScope":{**job.output_receipts["videoWorkScope"],
            "heroPrintScalePolicyVersion":PRINT_SCALE_POLICY,
            "heroPrintScaleAspects":["16:9"]}}
    story=muse._story_candidate_v33(job)
    assert story["aspectPlans"]["9:16"]==old["aspectPlans"]["9:16"]
    assert story["aspectPlans"]["16:9"]["heroPrintScalePolicy"]["version"]==PRINT_SCALE_POLICY
    assert story["aspectPlans"]["16:9"]["prompt"]==old["aspectPlans"]["16:9"]["prompt"]
    assert all("heroRealismPolicy" not in p for p in story["aspectPlans"].values())
    job.output_receipts={**job.output_receipts,
        "videoWorkScope":{**job.output_receipts["videoWorkScope"],
            "heroRealismPolicyVersion":"CAM_VIDEO_HERO_REALISM_V1",
            "heroRealismAspects":["16:9"]}}
    new=muse._story_candidate_v33(job)
    assert new["aspectPlans"]["9:16"]==old["aspectPlans"]["9:16"]
    assert new["aspectPlans"]["16:9"]["heroPrintScalePolicy"]==story["aspectPlans"]["16:9"]["heroPrintScalePolicy"]
    assert new["aspectPlans"]["16:9"]["heroRealismPolicy"]["additionalServerQaGate"] is False
    assert "Animate only the person" in new["aspectPlans"]["16:9"]["prompt"]
    assert all("headProportionQaPolicy" not in p for p in new["aspectPlans"].values())
    remote.assert_not_awaited()


def test_compact_print_preserves_completed_story(setup):
    db,job,remote=setup
    pin_lite(db,job)
    story=muse._story_candidate_v33(job)
    assert all("heroPrintScalePolicy" not in p for p in story["aspectPlans"].values())
    assert all("heroRealismPolicy" not in p for p in story["aspectPlans"].values())
    assert muse._verified_video_ratios(job)==set(RATIOS)
    remote.assert_not_awaited()


def test_banked_repair_keeps_old_until_verified_and_never_renders(setup, monkeypatch):
    import hashlib
    from copy import deepcopy
    from app.modules.muse_bridge import banked_video_repair as banked
    from app.modules.muse_bridge.video_asset_identity import video_asset_id
    db,job,remote=setup
    pin_lite(db,job)
    job.status="submitted"
    job.output_receipts={**job.output_receipts,"videoContentQa":"pending_review",
        "camLinked":True,"videoLastStoredAtUtc":"prior-render"}
    db.commit()
    monkeypatch.setattr(muse.latest_artwork,"current",lambda *args:True)
    old=deepcopy(job.output_receipts["drive_uploads"])
    payload=banked.StageRequest(expectedVideoSha256=SHAS["16:9"],expectedArtworkSha256=ART,
        workerId="muse-01",reasonCode="HEAD_PROPORTION_DRIFT")
    request=banked.stage(db,TENANT,LID,"horizontal",payload)
    rid=request["requestId"]
    assert request["state"]=="pending_upload" and not request["renderAllowed"]
    assert banked.stage(db,TENANT,LID,"horizontal",payload)["requestId"]==rid
    assert job.output_receipts["drive_uploads"]==old and job.attempts==2
    assert not db.scalars(select(CamMuseCommand)).all()
    with pytest.raises(HTTPException) as err:
        banked.status(db,TENANT,LID,"horizontal","muse-02")
    assert err.value.status_code==404
    with pytest.raises(HTTPException) as err:
        call(db,{"16:9":SHAS["16:9"]})
    assert err.value.status_code==409
    def box(kind,payload):
        return (8+len(payload)).to_bytes(4,"big")+kind+payload
    mvhd=bytes(12)+(1000).to_bytes(4,"big")+(10000).to_bytes(4,"big")+bytes(80)
    matrix=(65536).to_bytes(4,"big")+bytes(12)+(65536).to_bytes(4,"big")+bytes(16)
    tkhd=bytes(40)+matrix+(512*65536).to_bytes(4,"big")+(288*65536).to_bytes(4,"big")
    moov=box(b"mvhd",mvhd)+box(b"trak",box(b"tkhd",tkhd)+box(b"mdia",box(b"hdlr",bytes(8)+b"vide"+bytes(12))))
    mp4=box(b"ftyp",b"isom0000")+box(b"mdat",b"x"*4096)+box(b"moov",moov)
    sha=hashlib.sha256(mp4).hexdigest()
    kwargs=dict(mp4=mp4,video_sha=sha,artwork_sha=ART,qa_attestation="worker_visual_pass",
        output_contract=muse.LITE_OUTPUT,motion_version=muse.STORY_VERSION)
    store=AsyncMock(side_effect=HTTPException(503,"Drive unavailable"))
    monkeypatch.setattr(banked,"store_muse_video",store)
    def upload(**overrides):
        return asyncio.run(banked.upload(db,TENANT,LID,"horizontal",rid,"muse-01",**{**kwargs,**overrides}))
    with pytest.raises(HTTPException) as err:
        upload(video_sha="f"*64)
    assert err.value.status_code==422
    store.assert_not_awaited()
    with pytest.raises(HTTPException) as err:
        upload()
    assert err.value.status_code==503
    assert job.output_receipts["drive_uploads"]==old
    assert job.output_receipts[banked.MARKER]["16:9"]["replacementSha256"]==sha
    with pytest.raises(HTTPException) as err:
        other=mp4.replace(b"x"*4096,b"y"*4096)
        upload(mp4=other,video_sha=hashlib.sha256(other).hexdigest())
    assert err.value.status_code==409
    store.side_effect=None
    store.return_value={**old["16:9"],"sha256":sha,"storageRevision":rid,
        "nativeBytesPreserved":True,"aspectRatio":"16:9","drive_file_id":"new_banked_file_123"}
    result=upload()
    assert result["state"]=="uploaded" and result["storageVerified"] is True
    assert job.output_receipts["drive_uploads"]["9:16"]==old["9:16"]
    assert job.output_receipts["drive_uploads"]["16:9"]["sha256"]==sha
    assert job.output_receipts["rejectedVideoHistoryByAspect"]["16:9"][-1]["receipt"]==old["16:9"]
    assert job.output_receipts["videoLastStoredAtUtc"]=="prior-render"
    assert job.status=="submitted" and job.attempts==2 and job.output_receipts["videoContentQa"]=="pending_review"
    assert not db.scalars(select(CamMuseCommand)).all()
    assert store.await_args.kwargs["mp4"]==mp4 and store.await_args.kwargs["storage_revision"]==rid
    assert upload()["reused"] is True and store.await_count==2
    assert banked.stage(db,TENANT,LID,"horizontal",payload)["requestId"]==rid
    remote.assert_not_awaited()
    assert video_asset_id(LID,ART,"horizontal",rid)!=video_asset_id(LID,ART,"horizontal")
    assert video_asset_id(LID,ART,"horizontal",rid)==video_asset_id(LID,ART,"horizontal",rid)
    result=call(db,{"16:9":sha})
    assert result["queued"]
    assert job.output_receipts["videoStorageRevisionByAspect"]["16:9"]!=rid
    assert job.output_receipts["drive_uploads"]["9:16"]==old["9:16"]
