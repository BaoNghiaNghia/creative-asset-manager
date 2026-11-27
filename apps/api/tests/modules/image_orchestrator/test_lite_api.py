"""Single-role Lite transport tests; existing schema only, synthetic IO."""
import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.domain.processing.types import JOB_TYPES
from app.modules.etsy_spy.model import EtsySpyAgent, EtsySpyPicked, EtsySpyListingImage, EtsySpyListingImageDeletion
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.image_orchestrator import lite_api as a, policy as p, admission, print_box
from app.modules.image_orchestrator.identity import ImageIdentity, image_identity
from app.modules.muse_bridge.model import CamMuseJob, CamMuseCommand
from app.modules.muse_bridge import original_artwork
from app.modules.processing.model import ProcessingJobModel as Job

TENANT="fixture"
LISTING="1234567890"
AGENT="11111111-1111-4111-8111-111111111111"
EXT=ImageIdentity(TENANT,"chatgpt-ext-"+AGENT,"chatgpt_extension")
MUSE=ImageIdentity(TENANT,"muse-image-01","muse")
SRC="b"*64
WF="c"*64
RUNNER={"runnerId":"muse-image-canary","workflowSha256":WF,
        "resourceId":"independent-fixture","videoJobsEnabled":False}
PREFIX=a.PREFIX


def png(size):
    b=io.BytesIO()
    Image.new("RGBA",(size,size),(15,30,50,255)).save(b,format="PNG")
    return b.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def auth(name):
    return {"Authorization":"Bearer "+name}


@pytest.fixture
def env(monkeypatch):
    if os.environ.get("CAM_LITE_TEST_ISOLATED")=="1":
        port=int(os.environ["CAM_P2C_TEST_PORT"])
        assert port!=5432 and 1024<=port<=65535
        url=URL.create("postgresql+psycopg",username="cam_p2c_test",
            password=os.environ["CAM_P2C_TEST_PASSWORD"],host="127.0.0.1",
            port=port,database="cam_p2c_isolated")
        engine=create_engine(url,connect_args={"connect_timeout":3})
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT current_database()"))=="cam_p2c_isolated"
            assert conn.scalar(text("SELECT current_user"))=="cam_p2c_test"
            assert not inspect(conn).get_table_names(schema="public")
    else:
        engine=create_engine("sqlite://",connect_args={"check_same_thread":False},
                             poolclass=StaticPool)
    tables=[x.__table__ for x in (Job,EtsySpyAgent,EtsySpyPicked,EtsySpyListingImage,
            EtsySpyListingImageDeletion,CamMuseJob,CamMuseCommand)]
    Base.metadata.create_all(engine,tables=tables)
    art=png(256);artsha=sha(art);out=png(2000);thumb=png(192)
    asset="a"*24
    with Session(engine) as db:
        db.add(EtsySpyPicked(tenant_id=TENANT,listing_key=LISTING));db.flush()
        db.add(EtsySpyAgent(id=AGENT,tenant_id=TENANT,name="CAM Image Agent / Fixture",
            token_hash="b"*64,active=True))
        db.add(Job(tenant_id=TENANT,job_type=EXTRACTION_JOB_TYPE,entity_type="etsy_listing",
                   entity_id=LISTING,idempotency_key="native-fixture",status="completed",
                   payload_json={"design_assets":[{"id":asset,"output_sha256":artsha,
                       "artwork_kind":"native_master_v2","method":"imagegen","position":"front",
                       "position_verified":True,"source_sha256":SRC,
                       "storage_provider":"google_drive_managed","drive_file_id":"drive_original_fixture"}]}))
        db.add(CamMuseJob(tenant_id=TENANT,listing_key=LISTING,picked_at=EtsySpyPicked.__table__.c.picked_at.default.arg(None),
                         title="video fixture",source_state="saved",status="claimed",
                         attempts=2,output_receipts={"video":"unchanged"}));db.flush()
        db.add(CamMuseCommand(tenant_id=TENANT,command_id=str(uuid4()),listing_key=LISTING,
               action="create_video",phase="create_video",request_id="fixture-video",
               status="claimed",attempts=2,details={"video":"unchanged"}));db.commit()
    app=FastAPI();app.include_router(a.router)
    def session():
        with Session(engine) as db:
            db.info["muse_image_lite_test"]=True
            yield db
    def identity(authorization:str|None=Header(default=None)):
        who={"Bearer muse":MUSE,"Bearer other":ImageIdentity(TENANT,"muse-02","muse"),
             "Bearer ext":EXT,"Bearer foreign":ImageIdentity("other","muse-01","muse")}.get(authorization)
        if not who:raise HTTPException(401)
        return who
    def operator(authorization:str|None=Header(default=None)):
        if authorization!="Bearer operator":raise HTTPException(401)
        return TENANT
    app.dependency_overrides[get_db]=session
    app.dependency_overrides[a.lite_identity]=identity
    app.dependency_overrides[a._operator]=operator
    async def original(db,tenant,job_id,asset_id,digest,filename):
        assert tenant==TENANT and asset_id==asset and digest==artsha
        db.rollback()
        return original_artwork.verified_response(art,digest,filename)
    writes=[]
    async def save(**kw):
        assert kw["png"]==out and kw["role_contract"]==p.ROLE_CONTRACT
        assert kw["slot"]==1
        writes.append(kw["digest"])
        return {"native_width":2000,"native_height":2000,
                "drive_file_id":"drive_original_fixture1","drive_thumbnail_id":"drive_thumbnail_fixture1",
                "drive_folder_id":"drive_folder_fixture","drive_thumbnail_folder_id":"drive_thumb_folder",
                "thumbnail_sha256":sha(thumb)}
    async def read(**kw):
        return thumb if kw["asset_id"].endswith(":thumbnail") else out
    monkeypatch.setattr("app.modules.muse_bridge.router._read_original_artwork",original)
    monkeypatch.setattr("app.modules.etsy_spy.imagegen_drive.store_listing_output_to_drive",save)
    monkeypatch.setattr("app.modules.etsy_spy.imagegen_drive.read_design_from_drive",read)
    colors=list(admission.PALETTE)[:9]
    lock={**print_box.FIXED,"artwork_sha256":artsha,"placement_side":"front_center",
          "print_width_mm":191.16,"width_source":"sms_preview_advisory","ink_aspect_ratio":1.25}
    lock["lock_sha256"]=print_box.lock_digest(lock)
    plan={"skillSha256":p.SKILL_SHA256,"garmentSku":"C1717","artworkSha256":artsha,
          "sourceSha256":SRC,"storagePlacement":"front","placementLock":lock,"mockupSize":"M",
          "smsAdvisoryWidthMm":169.34,"paletteSha256":admission.PALETTE_SHA,
          "roleColors":{**{f"{n:02d}":colors[0] for n in range(1,5)},
                         **{f"{n:02d}":colors[n-4] for n in range(5,13)}},
          "infographicSwatches":colors[1:9],"infographicSourceRoles":[1,5,6,7]}
    payload={"listingId":LISTING,"plan":plan,"workerId":"muse-image-01",
             **{k:v for k,v in RUNNER.items() if k!="videoJobsEnabled"},
             "independentImageResourceEvidenceSha256":"d"*64,
             "sourceVisualReviewSha256":"e"*64,"placementEvidenceSha256":"f"*64}
    with TestClient(app) as client:
        yield client,engine,payload,art,out,writes
    engine.dispose()


def post(env,path,who,body=None,expected=200,headers=None):
    r=env[0].post(PREFIX+path,headers={**auth(who),**(headers or {})},json=body)
    assert r.status_code==expected,r.text
    return r


def prepare(env):
    return post(env,"/operator/prepare","operator",env[2],201).json()["commandId"]


def fence(env,cid):
    post(env,f"/commands/{cid}/legacy-fence","ext",
         {"listingId":LISTING,"ledgerSha256":"1"*64,"mode":"listing_exclusive"})


def enable(env,cid):
    fence(env,cid)
    post(env,f"/commands/{cid}/runner-ready","muse",RUNNER)
    post(env,f"/operator/commands/{cid}/enable","operator")


def claim(env,cid):
    return post(env,f"/commands/{cid}/claim","muse",headers={"X-CAM-Skill-Sha256":p.SKILL_SHA256}).json()


def lease(grant):
    return {k:grant[k] for k in ("epoch","lease")}


def start(env,cid,grant):
    r=post(env,f"/commands/{cid}/artwork","muse",lease(grant))
    assert r.content==env[3] and r.headers["X-CAM-Artwork-Variant"]=="original"
    body={**lease(grant),"attemptId":"attempt-01"}
    assert post(env,f"/commands/{cid}/start","muse",body).json()["sendAllowed"]
    assert post(env,f"/commands/{cid}/start","muse",body).json()["sendAllowed"] is False


def test_external_only_and_initial_gates(env):
    assert a.TYPE not in JOB_TYPES
    assert env[0].get(PREFIX+"/work").status_code==401
    assert env[0].get(PREFIX+"/work",headers=auth("muse")).json()["imageClaimAllowed"] is False
    post(env,"/operator/prepare","muse",env[2],401)
    cid=prepare(env)
    post(env,"/operator/prepare","operator",env[2],409)
    post(env,f"/commands/{cid}/claim","muse",expected=409,headers={"X-CAM-Skill-Sha256":p.SKILL_SHA256})
    fence(env,cid)
    post(env,f"/operator/commands/{cid}/enable","operator",expected=409)
    post(env,f"/commands/{cid}/runner-ready","muse",{**RUNNER,"videoJobsEnabled":True},409)
    enable(env,cid)
    assert env[0].get(PREFIX+"/work",headers=auth("muse")).json()["imageClaimAllowed"] is True
    assert not env[0].get(PREFIX+"/work",headers=auth("other")).json()["imageClaimAllowed"]
    assert not env[0].get(PREFIX+"/work",headers=auth("ext")).json()["imageClaimAllowed"]
    assert env[0].get(PREFIX+f"/commands/{cid}/status",headers=auth("foreign")).status_code==404


def test_one_shot_native_qa_drive_and_video_unchanged(env):
    cid=prepare(env);enable(env,cid);g=claim(env,cid)
    post(env,f"/commands/{cid}/claim","muse",expected=409,headers={"X-CAM-Skill-Sha256":p.SKILL_SHA256})
    post(env,f"/commands/{cid}/start","muse",{**lease(g),"attemptId":"attempt-01"},409)
    start(env,cid,g)
    sh=sha(env[4])
    qa={"epoch":g["epoch"],"attemptId":"attempt-01","imageSha256":sh,
        "reviewEvidenceSha256":"9"*64,"width":2000,"height":2000,
        "checks":{k:True for k in a.QA_CHECKS}}
    post(env,f"/operator/commands/{cid}/qa","muse",qa,401)
    qr=post(env,f"/operator/commands/{cid}/qa","operator",qa).json()["qaReceiptId"]
    post(env,f"/operator/commands/{cid}/stop","operator")
    headers={**auth("muse"),"Content-Type":"image/png","X-CAM-Epoch":str(g["epoch"]),
             "X-CAM-Image-Lease":g["lease"],"X-CAM-Image-Sha256":sh,"X-CAM-QA-Receipt":qr}
    r=env[0].put(PREFIX+f"/commands/{cid}/image",headers=headers,content=env[4])
    assert r.status_code==200,r.text
    assert r.json()["publicationReady"] and r.json()["storageVerified"]
    assert env[0].put(PREFIX+f"/commands/{cid}/image",headers=headers,content=env[4]).status_code==409
    assert len(env[5])==1
    result=env[0].get(PREFIX+f"/commands/{cid}/status",headers=auth("muse")).json()
    assert result["status"]=="complete" and result["receipt"]["imageSha256"]==sh
    with Session(env[1]) as db:
        job=db.get(Job,cid);assert job.status=="completed"
        assert not a.reservation_blocks_legacy(db,TENANT,LISTING)
        assert db.scalar(select(EtsySpyListingImage)).output_sha256==sh
        video=db.scalar(select(CamMuseJob));command=db.scalar(select(CamMuseCommand))
        assert (video.status,video.attempts,video.output_receipts)==("claimed",2,{"video":"unchanged"})
        assert (command.status,command.attempts,command.details)==("claimed",2,{"video":"unchanged"})


def test_stop_and_expired_fence_prevent_send(env,monkeypatch):
    cid=prepare(env);enable(env,cid);g=claim(env,cid)
    post(env,f"/commands/{cid}/artwork","muse",lease(g))
    with Session(env[1]) as db:
        row=db.get(Job,cid);a.set_payload(row,legacyFence={**row.payload_json["legacyFence"],"expiresAt":0});db.commit()
        assert a.reservation_blocks_legacy(db,TENANT,LISTING)
    post(env,f"/commands/{cid}/start","muse",{**lease(g),"attemptId":"attempt"},409)
    fence(env,cid)
    post(env,f"/operator/commands/{cid}/stop","operator")
    post(env,f"/commands/{cid}/start","muse",{**lease(g),"attemptId":"attempt"},409)


def test_cooldown_only_image_and_no_attempt_charge(env):
    cid=prepare(env);enable(env,cid);g=claim(env,cid)
    r=post(env,f"/commands/{cid}/cooldown","muse",{**lease(g),"providerStatus":429,"retryAfterSeconds":600})
    assert not r.json()["attemptPreserved"]
    post(env,f"/operator/commands/{cid}/enable","operator",expected=409)
    with Session(env[1]) as db:
        row=db.get(Job,cid);assert row.status=="pending" and row.attempt_count==0
        assert row.payload_json["leaseHash"] is None
        assert db.scalar(select(CamMuseCommand)).status=="claimed"


def test_operator_cancel_releases_only_unsent(env):
    cid=prepare(env);fence(env,cid)
    with Session(env[1]) as db:assert a.reservation_blocks_legacy(db,TENANT,LISTING)
    post(env,f"/operator/commands/{cid}/cancel","operator")
    with Session(env[1]) as db:assert not a.reservation_blocks_legacy(db,TENANT,LISTING)


def test_multiple_image_agents_fail_closed(env):
    cid=prepare(env)
    with Session(env[1]) as db:
        db.add(EtsySpyAgent(id=str(uuid4()),tenant_id=TENANT,name="CAM Image Agent / Second",
                            token_hash="c"*64,active=True));db.commit()
    post(env,f"/commands/{cid}/legacy-fence","ext",
         {"listingId":LISTING,"ledgerSha256":"1"*64,"mode":"listing_exclusive"},409)


def test_png_size_and_sha():
    data=png(1600)
    assert a.inspect_png(data,sha(data))==(1600,1600)
    for bad,expected in [(png(1599),None),(data,"0"*64)]:
        with pytest.raises(HTTPException):
            a.inspect_png(bad,expected or sha(bad))
@pytest.mark.skipif(os.environ.get("CAM_LITE_TEST_ISOLATED")!="1",reason="real PostgreSQL only")
def test_concurrent_claim_postgres(env):
    cid=prepare(env);enable(env,cid)
    def compete(_):
        return env[0].post(PREFIX+f"/commands/{cid}/claim",
            headers={**auth("muse"),"X-CAM-Skill-Sha256":p.SKILL_SHA256}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(compete,range(2)))
    assert sorted(results)==[200,409],results
    with Session(env[1]) as db:
        row=db.get(Job,cid)
        assert row.status=="processing" and row.payload_json["epoch"]==1
        assert row.attempt_count==0
        assert db.scalar(select(CamMuseCommand)).status=="claimed"

def test_lite_token_independent_of_video_roster(monkeypatch):
    from fastapi import Request
    token="x"*64
    monkeypatch.setenv("CAM_MUSE_TENANT_ID",TENANT)
    monkeypatch.setenv("CAM_MUSE_IMAGE_LITE_WORKER_TOKEN_SHA256",p.digest(token))
    monkeypatch.setenv("CAM_MUSE_DUAL_WORKERS_ENABLED","false")
    request=Request({"type":"http","method":"GET","path":"/","headers":[]})
    assert a.lite_identity(request,"Bearer "+token,None,None)==MUSE
    with pytest.raises(HTTPException) as exc:
        a.lite_identity(request,"Bearer "+"v"*64,None,None)
    assert exc.value.status_code==401
