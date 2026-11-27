"""Progress diagnostics on isolated fixtures; no renderer or production access."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.modules.application_logs.model import ApplicationLogModel, LogApplicationModel
from app.modules.muse_bridge import progress_watch as p, monitor, observability as obs
from app.modules.muse_bridge import router as m, queue_recovery as q
from app.modules.muse_bridge.model import CamMuseJob as J, CamMuseCommand as C

NOW = datetime(2026,11,25,4,tzinfo=timezone.utc)
TENANT = "tenant-a"
SHA = "a"*64
LID = "123456789"


def row(seconds_ago=0, **values):
    return SimpleNamespace(received_at=NOW-timedelta(seconds=seconds_ago),
                           payload_json=values)


def sample(age=0, ready=4, active=0, **kw):
    return row(age, **{"readyVideoListings":ready,"activeVideoListings":active,
                       "skipped":False,"failed":False,**kw})


def event(age=0, route="command_poll", **kw):
    return row(age, **{"origin":"cam_observed","workerId":"muse-01",
                       "route":route,"httpStatus":200,
                       "videoDiscoveryAllowed":True,"videoCandidatesOffered":4,**kw})


def samples():
    return [sample(i) for i in (0,120,240,360)]


def polls(**kw):
    return [event(i,**kw) for i in (10,130,250,350)]


def report():
    return {"queueRecovery":{"enabled":True,"status":"RECENT"},
            "rendererCircuit":{"state":"closed"},
            "workers":[{"workerId":"muse-01","status":"HEARTBEAT_RECENT",
                        "executionReadiness":{"ready":True},
                        "cooldown":{"cooldownActive":False}},
                       {"workerId":"muse-02","status":"API_ACTIVE_HEARTBEAT_UNCONFIRMED",
                        "executionReadiness":{"ready":False},"apiSignalKind":"config_only"}]}


class ProgressTests(TestCase):
    def evaluate(self, r=None, s=None, e=None, preferred="muse-01"):
        return p.evaluate(r if r is not None else report(),
                          s if s is not None else samples(),
                          e if e is not None else polls(),preferred,NOW)

    def test_repeated_real_offers_without_claim(self):
        result=self.evaluate()
        self.assertEqual(result["state"],"READY_QUEUE_POLLING_WITHOUT_VIDEO_PROGRESS")
        self.assertEqual(result["continuousReadyWithoutLeaseSeconds"],360)
        self.assertEqual(result["pollsOfferingVideo"],4)
        self.assertTrue(result["readOnly"])

    def test_empty_discovery_differs_from_ignored_work(self):
        self.assertEqual(self.evaluate(e=polls(videoCandidatesOffered=0))["state"],
                         "READY_QUEUE_DISCOVERY_MISMATCH")

    def test_claim_rejections_are_observed_not_success(self):
        errors=[event(80,route="command_claim",httpStatus=409),
                event(60,route="jobs_claim",httpStatus=422)]
        result=self.evaluate(e=polls()+errors)
        self.assertEqual(result["state"],"READY_QUEUE_CLAIM_REJECTIONS")
        self.assertEqual(result["claimRejections"],2)

    def test_recent_video_claim_or_new_storage_restarts_window(self):
        for progress in (event(15,route="command_claim",phase="create_video"),
                         event(20,route="jobs_claim"),
                         event(15,route="jobs_videos_vertical",newVideoStorageVerified=True)):
            self.assertEqual(self.evaluate(e=polls()+[progress])["state"],
                             "RECENT_OR_UNCONFIRMED_PROGRESS")

    def test_review_claim_and_reused_upload_not_video_progress(self):
        events=polls()+[event(15,route="command_claim",phase="review_artwork"),
                        event(20,route="jobs_videos_vertical",newVideoStorageVerified=False)]
        self.assertEqual(self.evaluate(e=events)["state"],"READY_QUEUE_POLLING_WITHOUT_VIDEO_PROGRESS")

    def test_samples_must_be_continuous_fresh_and_nonempty(self):
        for s in ([],[sample(0),sample(120)], [sample(0),sample(120,skipped=True),sample(360)],
                  [sample(0),sample(360)], [sample(0),sample(120,ready=0),sample(240),sample(360)],
                  [sample(300),sample(420),sample(540),sample(660)]):
            self.assertNotIn(self.evaluate(s=s)["state"],
                             ("READY_QUEUE_POLLING_WITHOUT_VIDEO_PROGRESS","READY_QUEUE_CLAIM_REJECTIONS"))
        self.assertEqual(self.evaluate(s=[sample(0,ready=0)]+samples()[1:])["state"],"NO_READY_VIDEO")

    def test_active_lease_cooldown_and_circuit_suppress_stall(self):
        self.assertEqual(self.evaluate(s=[sample(0,active=1)]+samples()[1:])["state"],"VIDEO_LEASE_ACTIVE")
        r=report()
        r["workers"][0]["cooldown"]["cooldownActive"]=True
        self.assertEqual(self.evaluate(r=r)["state"],"PROVIDER_COOLDOWN")
        for state,expected in (("open","RENDERER_CIRCUIT_OPEN"),("half_open","RENDERER_PROBE_PENDING")):
            r=report();r["rendererCircuit"]["state"]=state
            self.assertEqual(self.evaluate(r=r)["state"],expected)
        self.assertEqual(self.evaluate(preferred="muse-02")["state"],"NO_AVAILABLE_RENDER_WORKER")
        self.assertEqual(self.evaluate(preferred=None)["state"],"NO_AVAILABLE_RENDER_WORKER")

    def test_standby_replay_and_review_only_polls_are_not_offers(self):
        for events in (polls(workerId="muse-02"),polls(origin="worker_reported"),
                       polls(videoDiscoveryAllowed=False)):
            result=self.evaluate(e=events)
            self.assertEqual(result["discoveryPolls"],0)
            self.assertEqual(result["state"],"READY_QUEUE_NO_OBSERVED_DISCOVERY")

    def test_stale_recovery_and_missing_old_telemetry(self):
        r=report();r["queueRecovery"]["status"]="STALE"
        self.assertEqual(self.evaluate(r=r)["state"],"WAITING_FOR_EVIDENCE")
        old=[row(0,eligibleInflight=12,skipped=False,failed=False)]
        self.assertEqual(self.evaluate(s=old)["state"],"WAITING_FOR_EVIDENCE")

    def test_monitor_alert_and_standby_no_heartbeat_warning(self):
        r=report();r["videoProgress"]=self.evaluate()
        alerts=monitor.derive_alerts(r,NOW)
        self.assertIn("video-progress:ready-queue-polling-without-video-progress",{a["id"] for a in alerts})
        self.assertNotIn("muse-02:heartbeat",{a["id"] for a in alerts})
        r["videoProgress"]={"state":"VIDEO_LEASE_ACTIVE"}
        self.assertFalse(any(a["id"].startswith("video-progress:") for a in monitor.derive_alerts(r,NOW)))

    def test_poll_telemetry_scope_and_redaction(self):
        request=SimpleNamespace(state=SimpleNamespace(muse_review_only=False))
        m._record_discovery_progress(request,[{"nextAction":"create_video"},{"phase":"review_artwork"}])
        self.assertEqual(obs.safe_business_result(request.state.muse_business_result),
                         {"videoDiscoveryAllowed":True,"videoCandidatesOffered":1})
        request.state.muse_review_only=True
        m._record_discovery_progress(request,[])
        self.assertFalse(request.state.muse_business_result["videoDiscoveryAllowed"])
        self.assertEqual(obs.safe_business_result({"newVideoStorageVerified":True,"secret":"secret","videoCandidatesOffered":999}),
                         {"newVideoStorageVerified":True})


class DatabaseTests(TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite+pysqlite:///:memory:")
        from app.modules.processing.model import ProcessingJobModel
        for model in (LogApplicationModel,ApplicationLogModel,ProcessingJobModel,J,C):
            model.__table__.create(self.engine)
        self.db=Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def job(self, listing=LID, **kw):
        return J(**{"tenant_id":TENANT,"listing_key":listing,"picked_at":NOW,
                    "title":"test","artwork_sha256":SHA,"artwork_asset_path":"/verified",
                    "source_state":"ready_for_muse","status":"pending","attempts":0,
                    "output_receipts":{"visual_qa":{"verdict":"pass","artwork_sha256":SHA}},**kw})

    def command(self, listing=LID, **kw):
        return C(**{"tenant_id":TENANT,"command_id":str(uuid4()),"listing_key":listing,
                    "action":"create_video","phase":"create_video","request_id":str(uuid4()),
                    "artwork_sha256":SHA,"status":"pending","attempts":0,"details":{},**kw})

    def test_new_active_lease_after_sweep_suppresses_alert_tenant_scoped(self):
        for i,sample_row in enumerate(samples()):
            obs._write(self.db,TENANT,"CAM_RECOVERY_SWEEP","s"+str(i),
                       sample_row.payload_json,sample_row.received_at,sample_row.received_at)
        for i,e in enumerate(polls()):
            obs._write(self.db,TENANT,"CAM_API_RESULT","e"+str(i),
                       e.payload_json,e.received_at,e.received_at)
        self.db.add(self.job(tenant_id="other",status="claimed",lease_expires_at=NOW+timedelta(minutes=30)))
        self.db.commit()
        self.assertEqual(p.diagnostics(self.db,TENANT,report(),"muse-01",NOW)["state"],
                         "READY_QUEUE_POLLING_WITHOUT_VIDEO_PROGRESS")
        self.db.add(self.job(status="claimed",lease_expires_at=NOW+timedelta(minutes=30)))
        self.db.commit()
        result=p.diagnostics(self.db,TENANT,report(),"muse-01",NOW)
        self.assertEqual(result["state"],"VIDEO_LEASE_ACTIVE")
        self.assertTrue(result["activeVideoLeaseObservedSinceSample"])
        self.assertEqual(self.db.get(J,(TENANT,LID)).status,"claimed")

    def test_recovery_counts_ready_and_active_separately(self):
        self.db.add(self.job())
        self.db.add(self.command())
        self.db.add(self.job("987654321",status="claimed",lease_expires_at=NOW+timedelta(minutes=30)))
        self.db.add(self.command("987654321",status="claimed",lease_expires_at=NOW+timedelta(minutes=30)))
        self.db.commit()
        with patch.object(m,"_job_required_aspects",return_value=["9:16"]), patch.object(m,"_story_v33_enabled",return_value=False):
            result=q.reconcile_queue(self.db,TENANT,NOW,cap=2)
        self.assertEqual(result["readyVideoListings"],1)
        self.assertEqual(result["activeVideoListings"],1)
        self.assertEqual(result["eligibleInflightAfter"],2)
