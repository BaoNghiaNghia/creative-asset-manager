from datetime import datetime, timezone, timedelta
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.muse_bridge import skip_reconciliation as s, router as m, queue_recovery as q, observability as obs
from app.modules.muse_bridge.model import CamMuseJob as J, CamMuseCommand as C

NOW=datetime(2026,11,25,1,0,tzinfo=timezone.utc)
TENANT="tenant-a"
SHA="a"*64
LID="123456789"
CID=str(uuid4())
OTHER=str(uuid4())
def body(**kw):
    return s.SkipReport(**{"reportId":str(uuid4()),"listingId":LID,
        "artworkSha256":SHA,"observedAtUtc":NOW.isoformat(),
        "reason":"operator_paused",**kw})

class Tests(TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite+pysqlite:///:memory:")
        from app.modules.processing.model import ProcessingJobModel
        for model in (ProcessingJobModel,J,C): model.__table__.create(self.engine)
        self.db=Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.db.add(J(tenant_id=TENANT,listing_key=LID,picked_at=NOW,title="test",
            artwork_sha256=SHA,artwork_asset_path="/verified",source_state="ready_for_muse",status="pending",
            attempts=2,output_receipts={"visual_qa":{"verdict":"pass","artwork_sha256":SHA},
                "drive_uploads":{"16:9":{"sha256":"b"*64,"storageVerified":True}}}))
        self.db.add(C(tenant_id=TENANT,command_id=CID,listing_key=LID,action="create_video",
            phase="create_video",request_id="test-request-id",artwork_sha256=SHA,status="pending",
            attempts=1,details={"origin":"test"}))
        self.db.commit()

    def apply(self,payload=None,now=NOW,tenant=TENANT,cid=CID):
        return s.report(self.db,tenant,cid,payload or body(),"muse-01",lambda *_:"create_video",now)

    def job(self): return self.db.get(J,(TENANT,LID))
    def cmd(self): return self.db.get(C,(TENANT,CID))

    def test_preserve_media_attempts_and_identity(self):
        p=body()
        before=dict(self.job().output_receipts)
        result=self.apply(p)
        self.assertTrue(result["holdActive"])
        self.assertEqual(self.cmd().status,"pending")
        self.assertEqual(self.cmd().attempts,1)
        self.assertEqual(self.job().attempts,2)
        for k,v in before.items(): self.assertEqual(self.job().output_receipts[k],v)
        self.assertEqual(self.cmd().request_id,"test-request-id")
        self.assertIsNone(self.cmd().lease_hash)
        self.assertEqual(s.summary(self.db,TENANT,NOW)["activeListings"],1)
        self.assertEqual(s.summary(self.db,"other",NOW)["activeListings"],0)
        self.assertIsNone(m._eligible_phase(self.db,TENANT,self.job(),self.cmd()))
        self.assertFalse(q.can_refill(self.job(),[],NOW,m))

    def test_queue_recovery_reuses_capacity_without_resetting_held_command(self):
        self.apply()
        second="987654321"
        self.db.add(J(tenant_id=TENANT,listing_key=second,picked_at=NOW,title="test second",
            artwork_sha256=SHA,artwork_asset_path="/verified",source_state="ready_for_muse",
            status="pending",attempts=0,output_receipts={"visual_qa":{"verdict":"pass","artwork_sha256":SHA}}))
        self.db.commit()
        with patch.object(m,"_story_v33_enabled",return_value=False), patch.object(m,"_job_required_aspects",return_value=["9:16"]):
            report=q.reconcile_queue(self.db,TENANT,NOW,cap=1)
        self.assertEqual(report["heldPendingListings"],1)
        self.assertEqual(report["newVideoCommands"],1)
        self.assertEqual(report["eligibleInflightAfter"],1)
        self.assertEqual(self.cmd().status,"pending")
        self.assertEqual(self.cmd().attempts,1)
        self.assertEqual(report["newCommands"][0]["listingId"],second)

    def test_temporary_expires_without_extending_or_reactivating(self):
        p=body(reason="temporary_blocked",holdSeconds=1800)
        r=self.apply(p)
        self.assertTrue(self.apply(p,NOW+timedelta(minutes=10))["reused"])
        self.assertEqual(self.apply(p,NOW+timedelta(minutes=10))["untilUtc"],r["untilUtc"])
        later=NOW+timedelta(minutes=31)
        self.assertFalse(s.active(self.job(),later))
        self.assertFalse(self.apply(p,later)["holdActive"])
        self.assertEqual(self.cmd().status,"pending")

    def test_terminal_refusal_cannot_be_released(self):
        for reason in ("terminal_hold","provider_refused"):
            with self.subTest(reason=reason):
                p=body(reason=reason)
                self.apply(p)
                self.assertTrue(s.active(self.job(),NOW+timedelta(days=7)))
                with self.assertRaises(HTTPException) as ctx:
                    s.release(self.db,TENANT,CID,s.SkipRelease(reportId=p.reportId,listingId=LID,artworkSha256=SHA),NOW)
                self.assertEqual(ctx.exception.status_code,409)
                self.db.rollback()
                # Independent fixture state for the next reason, not production behavior.
                j=self.job(); j.output_receipts={k:v for k,v in j.output_receipts.items() if k!=s.KEY}
                c=self.cmd(); c.details={"origin":"test"}
                self.db.commit()

    def test_operator_release_and_replay(self):
        p=body()
        self.apply(p)
        req=s.SkipRelease(reportId=p.reportId,listingId=LID,artworkSha256=SHA)
        self.assertFalse(s.release(self.db,TENANT,CID,req,NOW+timedelta(minutes=1))["holdActive"])
        self.assertFalse(self.apply(p,NOW+timedelta(minutes=2))["holdActive"])
        self.assertTrue(s.release(self.db,TENANT,CID,req,NOW+timedelta(minutes=2))["reused"])

    def test_conflicts_and_tenant_boundary(self):
        for payload,tenant,cid in [(body(artworkSha256="c"*64),TENANT,CID),(body(),"other",CID),(body(),TENANT,OTHER)]:
            with self.assertRaises(HTTPException):self.apply(payload,tenant=tenant,cid=cid)
            self.db.rollback()
        self.cmd().status="claimed";self.cmd().lease_hash="x"*64
        self.cmd().lease_expires_at=NOW+timedelta(minutes=30);self.db.commit()
        with self.assertRaises(HTTPException):self.apply()
        self.assertIsNone(s.marker(self.job()))

    def test_stale_and_conflicting_report_never_override(self):
        p=body(observedAtUtc=(NOW-timedelta(hours=1)).isoformat())
        with self.assertRaises(HTTPException):self.apply(p)
        self.db.rollback()
        p=body();self.apply(p)
        with self.assertRaises(HTTPException):self.apply(body(reportId=p.reportId,reason="provider_refused"))
        self.db.rollback()
        self.assertEqual(s.marker(self.job())["reason"],"operator_paused")

    def test_explicit_regeneration_does_not_remove_reconciled_hold(self):
        import asyncio
        from app.modules.muse_bridge import regeneration
        row=self.job()
        row.output_receipts={**row.output_receipts,"drive_uploads":{"16:9":{
            "sha256":"b"*64,"storageVerified":True,"storage_provider":"google_drive_managed",
            "relativeFolder":f"listing_{LID}/design_{SHA[:16]}_front/video_ugc",
            "drive_file_id":"file-existing","drive_folder_id":"folder-existing"}}}
        self.db.commit()
        self.apply(body(reason="provider_refused"))
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(regeneration.regenerate(self.db,TENANT,LID,{"16:9":"b"*64}))
        self.assertEqual(ctx.exception.status_code,409)
        self.assertIn("hold",ctx.exception.detail)
        self.assertTrue(s.active(self.job(),NOW))
        self.assertEqual(self.job().output_receipts["drive_uploads"]["16:9"]["drive_file_id"],"file-existing")

    def test_provider_quota_is_not_listing_skip(self):
        for changes in ({"reason":"provider_rate_limit"},{"reason":"temporary_blocked"},{"holdSeconds":1800},{"lease":"secret"}):
            with self.assertRaises(ValueError):body(**changes)

    def test_route_telemetry_allowlists_reason(self):
        self.assertEqual(obs.route_info("/api/v1/muse-bridge/commands/"+CID+"/skip-report")["route"],"command_skip-report")
        self.assertEqual(obs.safe_business_result({"skipReason":"source_blocked","skipHoldActive":True}),{"skipReason":"source_blocked","skipHoldActive":True})
        self.assertEqual(obs.safe_business_result({"skipReason":"raw notes or token"}),{})

    def test_worker_permission_boundary(self):
        app=FastAPI();app.include_router(m.router)
        app.dependency_overrides[get_db]=lambda:None
        from fastapi import Request
        def standby(request:Request):
            request.state.muse_worker_id="muse-02"
            return TENANT
        app.dependency_overrides[m._muse]=standby
        client=TestClient(app)
        with patch.object(s,"report") as report:
            self.assertEqual(client.post("/api/v1/muse-bridge/commands/"+CID+"/skip-report",json=body().model_dump(mode="json")).status_code,403)
            report.assert_not_called()
