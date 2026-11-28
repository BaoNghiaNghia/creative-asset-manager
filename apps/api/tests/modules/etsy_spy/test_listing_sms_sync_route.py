"""Use in-memory SQLite only; never contact live SMS, Drive or CAM database."""
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.etsy_spy.model import EtsySpyPicked, EtsySpyListing, EtsySpyListingImage
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.etsy_spy.tiktok_skill import JOB_TYPE
from app.modules.etsy_spy.router import router
from app.modules.etsy_spy.design_artifacts import design_media_path
from app.modules.etsy_spy.listing_sms_sync import ROOT, SHOP, RECEIPT_KEY
from app.modules.processing.model import ProcessingJobModel
from app.modules.muse_bridge.model import CamMuseJob

KEY = "4380517795"
TENANT = "test-tenant"
MASTER = "b" * 64
ASSET = "a" * 24
JOB = "12345678-1234-1234-1234-123456789abc"
FILE = "exampleVertical123"
URL = "https://drive.google.com/file/d/" + FILE + "/view"
BASE = "/api/v1/etsy-spy"
PREFLIGHT = BASE + "/listings/" + KEY + "/listing-idea-preflight"
REPORT = BASE + "/listings/" + KEY + "/sms-sync-readback"
AUDIT = BASE + "/listing-media-audit"


@pytest.fixture
def scenario():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    tables = (EtsySpyPicked, EtsySpyListing, EtsySpyListingImage, ProcessingJobModel,
              CamMuseJob, AuthAuditEventModel)
    Base.metadata.create_all(engine, tables=[x.__table__ for x in tables])
    db = Session(engine)
    now = datetime.now(timezone.utc)
    db.add(EtsySpyPicked(tenant_id=TENANT, listing_key=KEY, picked_at=now))
    db.add(EtsySpyListing(tenant_id=TENANT, agent_id="agent", source_session_id="session",
        keyword="Holiday tee", source_kind="search", listing_key=KEY,
        title="Holiday graphic tee", observed_at=now, updated_at=now))
    db.add(ProcessingJobModel(id=JOB, tenant_id=TENANT, entity_id=KEY,
        entity_type="etsy_listing", job_type=EXTRACTION_JOB_TYPE, idempotency_key="extract",
        status="completed", payload_json={"design_assets":[{
            "id":ASSET,"output_sha256":MASTER,"position":"unknown",
            "method":"imagegen","variant":1,"artwork_kind":"native_master_v2",
            "storage_provider":"google_drive_managed","storage_folder_id":ROOT,
            "drive_file_id":"exampleOriginal123","drive_thumbnail_id":"exampleThumb123"}]}))
    db.add(ProcessingJobModel(tenant_id=TENANT, entity_id=KEY,
        entity_type="etsy_listing", job_type=JOB_TYPE, idempotency_key="seo",
        status="completed", payload_json={"result":{"title":"Holiday graphic tee",
        "description":"Draft description","highlights":["one","two","three","four",
        "Printed and shipped in the USA – no import customs fees"],
        "search_terms":["a","b","c","d","e","f","g","h"]}}))
    db.add(CamMuseJob(tenant_id=TENANT, listing_key=KEY, picked_at=now,
        title="Holiday graphic tee", source_state="ready_for_muse", status="qa_hold",
        artwork_sha256=MASTER, artwork_asset_path=design_media_path(JOB,ASSET),
        product_image_url="https://i.etsystatic.com/shop/photo.jpg",
        output_receipts={"drive_uploads":{"9:16":{"storageVerified":True,
            "storage_provider":"google_drive_managed","drive_file_id":FILE,
            "drive_folder_id":"exampleFolder123","sha256":"c"*64}}}))
    db.add(EtsySpyListingImage(tenant_id=TENANT, listing_key=KEY, pack_id="sp-"+"a"*40,
        slot=2, garment_code="C1717", design_sha256=MASTER, source_sha256="d"*64,
        placement="front_center", drive_folder_id="exampleImageFolder",
        drive_thumbnail_folder_id="exampleThumbFolder", output_sha256="e"*64,
        thumbnail_sha256="f"*64, drive_file_id="exampleImage123",
        drive_thumbnail_id="exampleImageThumb123", native_width=1254, native_height=1254))
    db.commit()
    principal = SimpleNamespace(user_id="actor-test",active_tenant_id=TENANT,
        effective_permissions=frozenset({"etsy_spy.read","etsy_spy.manage"}),
        platform_admin=False,navigation_modules=frozenset({"etsy-spy"}))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    with TestClient(app) as client:
        yield client,db,principal
    db.close()
    engine.dispose()


def payload(candidate, **kw):
    return dict(confirm=True,candidate_id=candidate,idea_code="I1671",
                idea_id="12345678-1234-1234-1234-123456789abd",
                target_id="12345678-1234-1234-1234-123456789abe",shop=SHOP,
                reference_images=2,target_videos=1,listings_made=0,listings_live=0,
                etsy_id=KEY,vertical_field_url=URL)|kw


def test_preflight_separates_idea_and_marketplace_and_reports(scenario):
    client,db,principal=scenario
    r=client.get(PREFLIGHT)
    assert r.status_code==200,r.text
    result=r.json()
    plan=result["sms_sync_v2"]
    assert plan["ready_for_idea_review"] and plan["blockers"]==[]
    assert plan["payload"]["targets"][0]["video_urls"]==[URL]
    assert len(plan["payload"]["targets"][0]["highlights"])==4
    assert "sms_template_selection_required" in result["marketplace_blockers"]
    assert result["ready_for_sms_submit"] is False
    media=result["media_integrity"]
    assert media["missing_main"] and media["preview_only_count"]==1
    assert media["matching_video_master_pack_count"]==1
    assert media["manifest_ready_count"]==0
    assert plan["readback_state"]=="not_reported"
    audit=client.get(AUDIT)
    assert audit.status_code==200 and audit.headers["cache-control"]=="no-store"
    assert audit.json()["items"][0]["listing_key"]==KEY
    assert audit.json()["items"][0]["ready_for_idea_review"] is True
    assert client.get(AUDIT,params={"limit":51}).status_code==422
    assert client.get(AUDIT,params={"after":"invalid"}).status_code==422
    data=payload(plan["candidate_id"])
    r=client.post(REPORT,json=data)
    assert r.status_code==200,r.text
    assert r.json()["changed"] is True
    assert r.json()["live_sms_verified_by_cam"] is False
    assert client.post(REPORT,json=data).json()["changed"] is False
    assert client.get(PREFLIGHT).json()["sms_sync_v2"]["readback_state"]=="reported_counts_match"
    records=db.scalars(select(AuthAuditEventModel)).all()
    assert len(records)==1 and records[0].actor_id=="actor-test"
    assert records[0].action=="sms_sync_v2_readback_reported"
    assert client.post(REPORT,json={**data,"target_id":JOB}).status_code==409
    # Ordinary draft PUT cannot forge, erase or modify a readback receipt.
    assert client.put(PREFLIGHT,json={RECEIPT_KEY:{}}).status_code==422
    assert client.put(PREFLIGHT,json={"brief":"changed draft"}).status_code==200
    view=client.get(PREFLIGHT).json()["sms_sync_v2"]
    assert view["readback_state"]=="stale_report"
    assert client.post(REPORT,json=data).status_code==409
    assert db.get(EtsySpyPicked,(TENANT,KEY)).listing_idea_draft[RECEIPT_KEY]["idea_code"]=="I1671"


def test_readback_permission_tenant_and_payload(scenario):
    client,db,principal=scenario
    candidate=client.get(PREFLIGHT).json()["sms_sync_v2"]["candidate_id"]
    data=payload(candidate)
    assert client.post(REPORT,json={**data,"target_videos":0}).status_code==409
    assert client.post(REPORT,json=data,headers={"Sec-Fetch-Site":"cross-site"}).status_code==415
    principal.effective_permissions=frozenset({"etsy_spy.read"})
    assert client.post(REPORT,json=data).status_code==403
    principal.effective_permissions=frozenset({"etsy_spy.read","etsy_spy.manage"})
    principal.active_tenant_id="other-tenant"
    assert client.get(PREFLIGHT).status_code==404
    assert client.post(REPORT,json=data).status_code==404
    assert client.get(AUDIT).json()["items"]==[]
    assert db.scalars(select(AuthAuditEventModel)).all()==[]

def test_stage_is_tenant_scoped_manage_only_and_excludes_recorded(scenario, tmp_path, monkeypatch):
    from app.modules.etsy_spy import sms_sync_stage as stage
    import json
    client, db, principal = scenario
    monkeypatch.setattr(stage, "ROOT", tmp_path)
    candidate = client.get(PREFLIGHT).json()["sms_sync_v2"]["candidate_id"]
    data = payload(candidate)
    stage.stage_path(TENANT).write_text(json.dumps({
        "schema": stage.SCHEMA, "tenant_id": TENANT, "shop": SHOP,
        "observed_at": "2026-11-28T03:08:21Z", "receipts": [data],
        "held": [{"etsy_id":"4321886411","state":"SMS_DAILY_LIMIT"}],
        "quota":{"state":"SMS_DAILY_LIMIT","limit":20,"reset_at":None,
                 "observed_at":"2026-11-28T03:08:21Z"}
    }))
    result = client.get(BASE + "/sms-sync-stage")
    assert result.status_code == 200 and result.headers["cache-control"] == "private, no-store"
    body = result.json()
    assert body["sms_observed_count"] == 1 and body["pending_count"] == 1
    assert body["recorded_count"] == 0 and body["held_count"] == 1
    assert body["receipts"] == [data] and body["live_sms_verified_by_cam"] is False
    assert db.scalars(select(AuthAuditEventModel)).all() == []
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.get(BASE + "/sms-sync-stage").status_code == 403
    principal.effective_permissions = frozenset({"etsy_spy.read","etsy_spy.manage"})
    principal.active_tenant_id = "other-tenant"
    assert client.get(BASE + "/sms-sync-stage").json()["state"] == "not_prepared"
    principal.active_tenant_id = TENANT
    assert client.post(REPORT,json=data).status_code == 200
    body = client.get(BASE + "/sms-sync-stage").json()
    assert body["recorded_count"] == 1 and body["pending_count"] == 0
    assert body["receipts"] == []
    saved = db.get(EtsySpyPicked,(TENANT,KEY))
    old = dict(saved.listing_idea_draft[RECEIPT_KEY])
    old["idea_code"] = "I999"
    saved.listing_idea_draft = {RECEIPT_KEY:old}
    db.commit()
    body = client.get(BASE + "/sms-sync-stage").json()
    assert body["review_count"] == 1 and body["receipts"] == []


def test_invalid_stage_fails_closed(scenario, tmp_path, monkeypatch):
    from app.modules.etsy_spy import sms_sync_stage as stage
    import json
    client, db, principal = scenario
    monkeypatch.setattr(stage,"ROOT",tmp_path)
    candidate = client.get(PREFLIGHT).json()["sms_sync_v2"]["candidate_id"]
    data = payload(candidate)
    doc = {"schema":stage.SCHEMA,"tenant_id":TENANT,"shop":SHOP,
           "observed_at":"2026-11-28T03:08:21Z","receipts":[data],
           "held":[],"quota":{"state":"UNKNOWN"}}
    for bad in [{"tenant_id":"other-tenant"},{"receipts":[data,data]},
                {"receipts":[{**data,"vertical_field_url":"https://evil.test/file"}]},
                {"receipts":[{**data,"reference_images":True}]},
                {"held":[{"etsy_id":KEY,"state":"SMS_DAILY_LIMIT"}]},
                {"quota":{"state":"SMS_DAILY_LIMIT","limit":20,"reset_at":"2026-11-29T00:00:00Z"}}]:
        stage.stage_path(TENANT).write_text(json.dumps(doc | bad))
        assert client.get(BASE+"/sms-sync-stage").status_code==503
    assert db.scalars(select(AuthAuditEventModel)).all()==[]


def test_v4_operator_color_metadata_and_prepared_target(scenario,monkeypatch):
    from app.modules.etsy_spy import sms_mockup_sync as v
    from app.modules.etsy_spy import sms_mockup_share as share
    client,db,principal=scenario
    endpoint=BASE+"/listings/"+KEY+"/sms-mockups-color-evidence"
    prepare=BASE+"/listings/"+KEY+"/sms-mockups-prepare"
    pack="sp-"+"a"*40
    existing=db.get(EtsySpyListingImage,(TENANT,KEY,pack,2))
    existing.native_width=existing.native_height=2000
    existing.output_sha256=f"{2:064x}"
    for i in range(1,14):
        if i==2:continue
        db.add(EtsySpyListingImage(tenant_id=TENANT,listing_key=KEY,pack_id=pack,slot=i,
            garment_code="C1717",design_sha256=MASTER,source_sha256="d"*64,placement="front_center",
            drive_folder_id="exampleImageFolder",drive_thumbnail_folder_id="exampleThumbFolder",
            output_sha256=f"{i:064x}",thumbnail_sha256=f"{100+i:064x}",
            drive_file_id=f"exampleOriginal{i:03}",drive_thumbnail_id=f"exampleThumb{i:03}",
            native_width=2000,native_height=2000))
    db.commit()
    view=client.get(PREFLIGHT).json()
    projected=view["sms_mockups_v4"]["packs"][0]
    assert projected["color_metadata_count"]==0 and not projected["ready_for_mockup_review"]
    names=["Bay","Ivory","White","Pepper","Chambray","Blue Jean","Moss","Denim","Blossom"]
    evidence={"confirm":True,"pack_id":pack,"garment_code":"C1717",
              "expected_pack_sha256":projected["review_sha256"],"role_contract":v.CONTRACT,
              "images":[{"slot":i,"output_sha256":f"{i:064x}",
                         "color_name":None if i==13 else names[0 if i<=4 else i-4]} for i in range(1,14)]}
    principal.effective_permissions=frozenset({"etsy_spy.read"})
    assert client.post(endpoint,json=evidence).status_code==403
    principal.effective_permissions=frozenset({"etsy_spy.read","etsy_spy.manage"})
    assert client.post(endpoint,json=evidence,headers={"Sec-Fetch-Site":"cross-site"}).status_code==415
    bad={**evidence,"images":[{**evidence["images"][0],"output_sha256":"f"*64}]}
    assert client.post(endpoint,json=bad).status_code==409
    assert client.post(endpoint,json=evidence).json()["updated"]==13
    view=client.get(PREFLIGHT).json()
    current=view["sms_mockups_v4"]["packs"][0]
    assert current["ready_for_mockup_review"] and current["unique_color_count"]==9
    assert client.post(endpoint,json=evidence).status_code==409
    sms=payload(view["sms_sync_v2"]["candidate_id"])
    body={"confirm":True,"pack_id":pack,"garment_code":"C1717","expected_pack_sha256":current["review_sha256"],
          "target_id":sms["target_id"],"shop":SHOP,"color_choices":names,"current_mockups":0}
    assert client.post(prepare,json=body).status_code==409
    assert client.post(REPORT,json=sms).status_code==200
    called=[]
    async def fake_prepare(pack,rows,choices,*,target_id,shop):
        called.append(len(rows))
        assert choices==names and target_id==sms["target_id"]
        return {"policy":v.POLICY,"chunks":[],"live_sms_verified_by_cam":False}
    monkeypatch.setattr(share,"prepare",fake_prepare)
    assert client.post(prepare,json={**body,"current_mockups":1}).status_code==422
    assert client.post(prepare,json={**body,"target_id":JOB}).status_code==409
    result=client.post(prepare,json=body)
    assert result.status_code==200,result.text
    assert called==[13] and result.json()["sms_written"] is False
    assert result.json()["idea_code"]==sms["idea_code"]
    assert client.get(PREFLIGHT).json()["sms_sync_v2"]["readback_state"]=="reported_counts_match"
