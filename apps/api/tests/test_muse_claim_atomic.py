"""Exercise actual command/job claim HTTP routes on an isolated SQLite DB.

No renderer, production credentials, Drive writes, or production job claims.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.modules.processing.model import ProcessingJobModel as P
from app.modules.muse_bridge import router as m
from app.modules.muse_bridge.model import CamMuseCommand as C, CamMuseJob as J

TENANT = "test-tenant"
LID = "123456789"
SHA = "a"*64
CID = str(uuid4())
PREFIX = "/api/v1/muse-bridge"
NOW = datetime(2026,11,25,4,tzinfo=timezone.utc)


class AtomicClaimTests(TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread":False},poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        for model in (P,J,C):
            model.__table__.create(self.engine)
        with Session(self.engine) as db:
            db.add(J(tenant_id=TENANT,listing_key=LID,picked_at=NOW,
                title="Mountain Hiking T Shirt",artwork_sha256=SHA,
                artwork_asset_path="/api/v1/etsy-spy/image-agents/designs/12345678-1234-1234-1234-123456789abc/"+("b"*24)+"/original",
                source_state="ready_for_muse",status="pending",attempts=0,
                output_receipts={"visual_qa":{"verdict":"pass","artwork_sha256":SHA}}))
            db.add(C(tenant_id=TENANT,command_id=CID,listing_key=LID,
                action="create_video",phase="create_video",request_id="test-atomic-claim",
                artwork_sha256=SHA,status="pending",attempts=0,details={}))
            db.add(P(id="12345678-1234-1234-1234-123456789abc",
                tenant_id=TENANT,job_type="etsy_design_extract",entity_type="etsy_listing",entity_id=LID,
                idempotency_key="initial-source",status="completed",created_at=NOW,
                payload_json={"source":{"url":"https://i.etsystatic.com/test.jpg"},
                              "design_assets":[{"id":"b"*24,"position":"front","variant":1,
                                "method":"imagegen","artwork_kind":"native_master_v2","output_sha256":SHA}]}))
            db.commit()
        app=FastAPI()
        app.include_router(m.router)
        def test_auth(request:Request):
            worker=request.headers.get("X-Test-Worker")
            if worker not in ("muse-01","muse-02"):
                raise HTTPException(401)
            request.state.muse_worker_id=worker
            return TENANT
        def session():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[m._muse]=test_auth
        app.dependency_overrides[get_db]=session
        self.client=TestClient(app,raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        for context in (
            patch.dict("os.environ",{
                "CAM_MUSE_STORY_V33_ENABLED":"true",
                "CAM_MUSE_STORY_V33_BULK_ENABLED":"true",
                "CAM_MUSE_STORY_LITE_ENABLED":"true",
                "CAM_MUSE_CONTINUOUS_GEN_ENABLED":"true",
                "CAM_MUSE_ASPECT_PHASE":"vertical_first",
                "CAM_MUSE_LATEST_ARTWORK_ENABLED":"true",
            }),
            patch.object(m,"_muse_dual_enabled",return_value=True),
            patch.object(m,"_assert_muse_executor_ready",return_value=None),
            patch.object(m,"_utc",return_value=NOW),
        ):
            context.start()
            self.addCleanup(context.stop)

    def headers(self,worker="muse-01"):
        return {"X-Test-Worker":worker,"X-CAM-Muse-Policy-Version":m.POLICY_VERSION}

    def command_claim(self):
        return self.client.post(PREFIX+"/commands/"+CID+"/claim",headers=self.headers())

    def video_claim(self,body,worker="muse-01"):
        return self.client.post(PREFIX+"/jobs/"+LID+"/claim",json=body,
                                headers=self.headers(worker))

    def claimed_command_body(self):
        response=self.command_claim()
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(result["nextAction"],"create_video")
        self.assertEqual(result["videoJobClaim"]["creativePolicy"],m.LITE_POLICY)
        self.assertEqual(result["artworkSource"]["expectedSha256"],SHA)
        self.assertEqual(result["videoJobClaim"]["body"]["worker"],"muse-01")
        with Session(self.engine) as db:
            cmd=db.get(C,(TENANT,CID))
            self.assertEqual(cmd.lease_hash,hashlib.sha256(result["lease"].encode()).hexdigest())
            self.assertEqual(cmd.status,"claimed")
        return result["videoJobClaim"]["body"]

    def assert_job_unclaimed(self):
        with Session(self.engine) as db:
            job=db.get(J,(TENANT,LID))
            self.assertEqual(job.status,"pending")
            self.assertEqual(job.attempts,0)
            self.assertIsNone(job.lease_hash)
            self.assertIsNone(job.lease_expires_at)
            self.assertEqual(job.output_receipts,{"visual_qa":{"verdict":"pass","artwork_sha256":SHA}})
            self.assertEqual(db.get(C,(TENANT,CID)).status,"claimed")

    def assert_video_response(self,response):
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(result["job"]["artworkSource"]["assetVariant"],"original")
        self.assertEqual(result["job"]["artworkSource"]["expectedSha256"],SHA)
        self.assertEqual(result["job"]["videoSpec"]["creativePolicy"],m.LITE_POLICY)
        self.assertEqual(result["job"]["videoSpec"]["targetDurationSeconds"],6)
        self.assertEqual(result["job"]["videoSpec"]["requiredVideoAspectRatios"],["9:16"])
        with Session(self.engine) as db:
            job=db.get(J,(TENANT,LID))
            self.assertEqual(job.status,"claimed")
            self.assertEqual(job.attempts,1)
            self.assertEqual(job.lease_hash,hashlib.sha256(result["lease"].encode()).hexdigest())
            self.assertEqual(job.output_receipts["museWorkerOwner"],"muse-01")
            self.assertEqual(job.output_receipts["story_contract_v33"]["creativePolicy"],m.LITE_POLICY)

    def add_new_original(self):
        with Session(self.engine) as db:
            db.add(P(id=str(uuid4()),tenant_id=TENANT,job_type="etsy_design_extract",
                entity_type="etsy_listing",entity_id=LID,idempotency_key="new-source",
                status="completed",created_at=NOW+timedelta(seconds=1),
                payload_json={"retry":{"source_job_id":"prior"},
                              "source":{"url":"https://i.etsystatic.com/test.jpg"},
                              "design_assets":[{"id":"c"*24,"position":"front","variant":1,
                                  "method":"imagegen","artwork_kind":"native_master_v2",
                                  "output_sha256":"d"*64}]}))
            db.commit()

    def test_new_original_blocks_stale_command_claim(self):
        self.add_new_original()
        response=self.command_claim()
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()["detail"]["code"],"ARTWORK_VERSION_CHANGED")
        with Session(self.engine) as db:
            self.assertEqual(db.get(C,(TENANT,CID)).status,"pending")
            self.assertEqual(db.get(C,(TENANT,CID)).attempts,0)

    def test_new_original_between_claims_rolls_back_job_attempt(self):
        body=self.claimed_command_body()
        self.add_new_original()
        response=self.video_claim(body)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()["detail"]["code"],"ARTWORK_VERSION_CHANGED")
        self.assert_job_unclaimed()

    def test_actual_two_step_story_lite_claim(self):
        body=self.claimed_command_body()
        self.assert_video_response(self.video_claim(body))
        self.assertEqual(self.video_claim(body).status_code,409)

    def test_view_failure_rolls_back_entire_video_claim(self):
        body=self.claimed_command_body()
        with patch.object(m,"_row_view",side_effect=RuntimeError("test response construction")):
            self.assertEqual(self.video_claim(body).status_code,500)
        self.assert_job_unclaimed()
        self.assert_video_response(self.video_claim(body))

    def test_encoding_failure_rolls_back_entire_video_claim(self):
        body=self.claimed_command_body()
        with patch.object(m,"_row_view",return_value={"notJson":float("nan")}):
            self.assertEqual(self.video_claim(body).status_code,500)
        self.assert_job_unclaimed()
        self.assert_video_response(self.video_claim(body))

    def test_command_contract_failure_rolls_back_claim(self):
        with patch.object(m,"_video_job_claim_contract",side_effect=RuntimeError("test contract construction")):
            self.assertEqual(self.command_claim().status_code,500)
        with Session(self.engine) as db:
            cmd=db.get(C,(TENANT,CID))
            self.assertEqual(cmd.status,"pending")
            self.assertEqual(cmd.attempts,0)
            self.assertIsNone(cmd.lease_hash)
            self.assertIsNone(cmd.lease_expires_at)
        body=self.claimed_command_body()
        self.assert_video_response(self.video_claim(body))

    def test_wrong_worker_cannot_take_job_lease(self):
        body=self.claimed_command_body()
        self.assertEqual(self.video_claim({**body,"worker":"muse-02"},"muse-02").status_code,403)
        self.assert_job_unclaimed()
        self.assert_video_response(self.video_claim(body))

    def test_source_contract_available_for_non_lite_view(self):
        with Session(self.engine) as db, patch.dict("os.environ",{"CAM_MUSE_STORY_LITE_ENABLED":"false"}):
            result=m._row_view(db.get(J,(TENANT,LID)))
            self.assertEqual(result["artworkSource"]["expectedSha256"],SHA)
            self.assertNotEqual(result["videoSpec"].get("creativePolicy"),m.LITE_POLICY)