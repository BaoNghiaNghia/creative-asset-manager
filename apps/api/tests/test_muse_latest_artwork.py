"""Version-aware recovery on isolated DB; no Drive or renderer calls."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.modules.processing.model import ProcessingJobModel as P
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.muse_bridge import latest_artwork as v,router as m,queue_recovery as q
from app.modules.muse_bridge.model import CamMuseJob as J,CamMuseCommand as C

NOW=datetime(2026,11,25,5,tzinfo=timezone.utc)
TENANT="test-tenant"
LID="123456789"
OLD="a"*64
NEW="b"*64
SOURCE="https://i.etsystatic.com/test.jpg"

def asset(sha,position="front",aid="c"*24,kind="native_master_v2"):
    return {"id":aid,"position":position,"variant":1,"method":"imagegen",
            "artwork_kind":kind,"output_sha256":sha}

def saved(sha):
    return {"visual_qa":{"verdict":"pass","artwork_sha256":sha},
            "drive_uploads":{"9:16":{"storageVerified":True,
                "storage_provider":"google_drive_managed",
                "relativeFolder":f"listing_{LID}/design_{sha[:16]}_front/video_ugc",
                "drive_file_id":"video-old","drive_folder_id":"folder-old","sha256":"d"*64}},
            "hero_sources":{"9:16":{"artworkSha256":sha}},
            "story_contract_v33":{"creativePolicy":"legacy"},
            "videoContentQa":"pass","videoGarmentQa":"pass","camLinked":True}

class LatestTests(TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite+pysqlite:///:memory:")
        for model in (P,EtsySpyPicked,J,C):model.__table__.create(self.engine)
        self.db=Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        env=patch.dict("os.environ",{"CAM_MUSE_LATEST_ARTWORK_ENABLED":"true"})
        env.start();self.addCleanup(env.stop)
        self.old=self.add_source(OLD,age=20)
        self.db.add(EtsySpyPicked(tenant_id=TENANT,listing_key=LID,picked_at=NOW))
        self.db.add(J(tenant_id=TENANT,listing_key=LID,picked_at=NOW,
            title="sample shirt",artwork_sha256=OLD,
            artwork_asset_path=v.design_media_path(self.old.id,"c"*24),
            product_image_url=SOURCE,status="completed",source_state="ready_for_muse",
            attempts=2,output_receipts=saved(OLD),completed_at=NOW))
        self.db.add(C(tenant_id=TENANT,command_id=str(uuid4()),listing_key=LID,
            action="create_video",phase="create_video",request_id="previous-video",
            artwork_sha256=OLD,status="receipt_submitted",attempts=2,details={}))
        self.db.commit()

    def add_source(self,sha=NEW,age=0,tenant=TENANT,status="completed",
                   position="front",retry=True,assets=None,cancelled=False):
        uid=str(uuid4())
        payload={"source":{"url":SOURCE},"design_assets":
                 (assets if assets is not None else [asset(sha,position)])}
        if retry:payload["retry"]={"source_job_id":"prior"}
        r=P(id=uid,tenant_id=tenant,entity_type="etsy_listing",entity_id=LID,
            job_type=v.EXTRACTION_JOB_TYPE,idempotency_key=uid,payload_json=payload,
            status=status,cancellation_requested=cancelled,created_at=NOW-timedelta(minutes=age))
        self.db.add(r);self.db.commit()
        return r

    def job(self):
        return self.db.get(J,(TENANT,LID))

    def latest(self):
        return v.load(self.db,TENANT,[LID])[LID]

    def sweep(self):
        return q.reconcile_queue(self.db,TENANT,NOW,cap=4)

    def commands(self):
        return self.db.scalars(select(C).where(C.tenant_id==TENANT,C.listing_key==LID)).all()

    def test_completed_job_new_original_is_reviewed_and_old_media_archived(self):
        old=dict(self.job().output_receipts)
        new=self.add_source()
        result=self.sweep()
        self.assertEqual(result["artworkVersionsAdvanced"],1)
        self.assertEqual(result["newReviewCommands"],1)
        j=self.job()
        self.assertEqual(j.artwork_sha256,NEW)
        self.assertEqual(j.status,"qa_hold")
        self.assertIsNone(j.artwork_asset_path)
        self.assertEqual(j.attempts,0)
        self.assertFalse(m._verified_video_ratios(j))
        self.assertIsNone(m._qa_record(j))
        self.assertEqual(j.output_receipts[v.HISTORY][OLD]["receipts"],old)
        self.assertEqual(j.output_receipts[v.HISTORY][OLD]["attempts"],2)
        self.assertEqual(m._review_native_asset(self.db,TENANT,j).job_id,new.id)
        cmds=[c for c in self.commands() if c.status=="pending"]
        self.assertEqual(len(cmds),1)
        self.assertEqual(cmds[0].phase,"review_artwork")
        self.assertEqual(cmds[0].artwork_sha256,NEW)
        self.assertEqual(self.sweep()["newReviewCommands"],0)
        self.assertEqual(self.sweep()["artworkVersionsAdvanced"],0)

    def test_same_sha_does_not_regenerate_or_reset(self):
        old=dict(self.job().output_receipts)
        self.add_source(OLD)
        result=self.sweep()
        self.assertEqual(result["artworkVersionsAdvanced"],0)
        self.assertEqual(self.job().output_receipts,old)
        self.assertEqual(self.job().attempts,2)
        self.assertEqual(m._verified_video_ratios(self.job()),{"9:16"})

    def test_thumbnail_metadata_change_does_not_generate(self):
        p=dict(self.old.payload_json)
        p["design_assets"]=[{**asset(OLD),"thumbnail_sha256":"e"*64}]
        self.old.payload_json=p
        self.db.commit()
        self.assertEqual(self.sweep()["artworkVersionsAdvanced"],0)

    def test_pending_retry_blocks_stale_claim_until_saved(self):
        r=self.add_source(status="pending",assets=[])
        self.assertEqual(self.latest().state,"awaiting_original")
        with self.assertRaises(HTTPException) as ctx:v.require_current(self.db,TENANT,self.job())
        self.assertEqual(ctx.exception.detail["code"],"ARTWORK_VERSION_CHANGED")
        self.assertEqual(self.sweep()["artworkVersionsAwaitingOriginal"],1)
        self.assertEqual(self.job().artwork_sha256,OLD)
        r.status="completed";r.payload_json={**r.payload_json,"design_assets":[asset(NEW)]}
        self.db.commit()
        self.assertEqual(self.sweep()["artworkVersionsAdvanced"],1)

    def test_failed_or_cancelled_retry_never_supersedes(self):
        self.add_source(status="failed")
        self.add_source(cancelled=True)
        self.assertEqual(self.latest().digest,OLD)

    def test_non_front_new_generation_no_old_fallback(self):
        self.add_source(position="back")
        self.assertEqual(self.latest().state,"unsupported_placement_or_master")
        self.assertFalse(v.current(self.db,TENANT,self.job(),fresh=True))
        self.assertEqual(self.sweep()["artworkVersionsAdvanced"],0)

    def test_active_lease_stays_pinned(self):
        j=self.job();j.status="claimed";j.lease_hash="f"*64
        j.lease_expires_at=NOW+timedelta(minutes=30)
        self.db.add(C(tenant_id=TENANT,command_id=str(uuid4()),listing_key=LID,
            action="create_video",phase="create_video",request_id="active",
            artwork_sha256=OLD,status="claimed",attempts=1,
            lease_hash="e"*64,lease_expires_at=NOW+timedelta(minutes=30),details={}))
        self.db.commit();self.add_source()
        self.assertEqual(self.sweep()["artworkVersionsDeferred"],1)
        self.assertEqual(self.job().artwork_sha256,OLD)
        self.assertEqual(self.job().lease_hash,"f"*64)

    def test_terminal_hold_never_bypassed(self):
        j=self.job();j.output_receipts={**j.output_receipts,
            "videoCapabilityHold":{"code":"RENDERER_CAPABILITY_BLOCKED"}}
        self.db.commit();self.add_source()
        self.assertEqual(self.sweep()["artworkVersionsHeld"],1)
        self.assertEqual(self.job().artwork_sha256,OLD)
        self.assertFalse(v.current(self.db,TENANT,j,fresh=True))

    def test_stale_pending_command_superseded(self):
        j=self.job();j.status="pending"
        self.db.add(C(tenant_id=TENANT,command_id=str(uuid4()),listing_key=LID,
            action="create_video",phase="create_video",request_id="stale",
            artwork_sha256=OLD,status="pending",attempts=1,details={}))
        self.db.commit();self.add_source();self.sweep()
        stale=next(c for c in self.commands() if c.request_id=="stale")
        self.assertEqual(stale.status,"failed")
        self.assertEqual(stale.attempts,1)
        self.assertEqual(stale.details["queueRecovery"]["reason"],"artwork_version_superseded")

    def test_returning_sha_restores_prior_verified_output_and_attempts(self):
        self.add_source();self.sweep()
        self.add_source(OLD,age=-1)
        self.assertEqual(self.sweep()["artworkVersionsRestored"],1)
        self.assertEqual(self.job().artwork_sha256,OLD)
        self.assertEqual(self.job().attempts,2)
        self.assertEqual(m._verified_video_ratios(self.job()),{"9:16"})
        self.assertIn(NEW,self.job().output_receipts[v.HISTORY])

    def test_unknown_new_generation_never_uses_old_placement_approval(self):
        r=self.add_source(position="unknown")
        self.sweep()
        j=self.job()
        self.assertEqual(m._review_native_asset(self.db,TENANT,j).position,"unknown")
        self.assertIsNone(m._stored_native_asset(self.db,TENANT,LID,NEW))
        self.assertEqual(m._review_placement_view(self.db,TENANT,j)["reviewOnly"],True)
        self.assertEqual(j.output_receipts[v.CURRENT]["sourceJobId"],r.id)

    def test_other_tenant_extraction_ignored(self):
        self.add_source(tenant="other")
        self.assertEqual(self.latest().digest,OLD)

    def test_stale_agent_sync_cannot_undo_rotated_original(self):
        self.add_source();self.sweep()
        request=m.SyncRequest(items=[m.SyncItem(listingId=LID,pickedAt=NOW,
            title="old stale sync",status="ready_for_muse",artworkSha256=OLD,
            artworkAssetPath=v.design_media_path(self.old.id,"c"*24),productImageUrl=SOURCE)])
        with patch.object(m,"authenticate_agent",return_value=SimpleNamespace(tenant_id=TENANT)):
            result=m.sync_agent("test-agent",request,authorization="test",db=self.db)
        self.assertEqual(result["updated"],0)
        self.assertEqual(self.job().artwork_sha256,NEW)
        self.assertEqual(self.job().status,"qa_hold")
