"""Throughput regression: isolated data, no renderer or production I/O."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from app.modules.muse_bridge import router as m, observability as o, regeneration as regen

NOW = datetime(2026,11,25,9,0,tzinfo=timezone.utc)

def job(saved=(),requested=None,status="pending",pinned=None):
    r = {}
    if requested is not None:
        r["videoRegeneration"]={"requiredVideoAspectRatios":list(requested)}
    if pinned is not None:
        r["videoWorkScope"]={"requiredVideoAspectRatios":list(pinned)}
    return SimpleNamespace(status=status, lease_expires_at=NOW+timedelta(minutes=20)
        if status=="claimed" else None, output_receipts=r, saved=set(saved))

def row(typ,mins,payload):
    return SimpleNamespace(event_type=typ,occurred_at=NOW-timedelta(minutes=mins),payload_json=payload)

def api(mins,route,status=200,new=False):
    return row("CAM_API_RESULT",mins,{"origin":"cam_observed","method":"POST",
         "route":route,"httpStatus":status,"newVideoStorageVerified":new})

class AspectPriorityTests(TestCase):
    def setUp(self):
        for ctx in (patch.object(m,"_utc",return_value=NOW),
                    patch.object(m,"_verified_video_ratios",side_effect=lambda j:j.saved),
                    patch.dict("os.environ",{"CAM_MUSE_ASPECT_PHASE":"vertical_first"})):
            ctx.start()
            self.addCleanup(ctx.stop)

    def test_two_ratio_explicit_regeneration_is_split_without_mutation(self):
        j=job(requested=("9:16","16:9"))
        self.assertEqual(m._job_required_aspects(j),["9:16"])
        self.assertEqual(regen.requested_aspects(j),["9:16","16:9"])
        j.saved.add("9:16")
        self.assertEqual(m._job_required_aspects(j),["16:9"])
        self.assertEqual(j.output_receipts["videoRegeneration"]["requiredVideoAspectRatios"],["9:16","16:9"])

    def test_horizontal_only_explicit_regeneration_remains_eligible(self):
        self.assertEqual(m._job_required_aspects(job(saved=("9:16",),requested=("16:9",))),["16:9"])

    def test_existing_active_lease_retains_both_aspects(self):
        j=job(saved=("9:16",),requested=("9:16","16:9"),status="claimed",pinned=("9:16","16:9"))
        self.assertEqual(m._job_required_aspects(j),["9:16","16:9"])
        self.assertEqual(m._unleased_job_required_aspects(j),["16:9"])

    def test_both_mode_keeps_original_contract(self):
        with patch.dict("os.environ",{"CAM_MUSE_ASPECT_PHASE":"both"}):
            self.assertEqual(m._job_required_aspects(job(requested=("9:16","16:9"))),["9:16","16:9"])
            self.assertEqual(m._video_aspect_priority(["16:9"]),0)

    def test_discovery_prioritizes_vertical_even_when_horizontal_is_partial(self):
        items=[{"phase":"create_video","listingId":"123456780","savedVideoAspectRatios":["9:16"],
                "missingVideoAspectRatios":["16:9"]},
               {"phase":"review_artwork","listingId":"123456781"},
               {"phase":"create_video","listingId":"123456782","missingVideoAspectRatios":["9:16"]},
               {"phase":"create_video","listingId":"123456783","missingVideoAspectRatios":["9:16"]}]
        db=SimpleNamespace(scalars=lambda _:SimpleNamespace(all=lambda:[]))
        result=m._fair_muse_discovery_order(items,"test",db)
        self.assertEqual([i["listingId"] for i in result],
                         ["123456782","123456783","123456781","123456780"])

class ThroughputTests(TestCase):
    def test_fresh_storage_not_upload_replay_and_observed_transition(self):
        rows=[api(170,"jobs_videos_vertical",new=True),
              api(169,"jobs_videos_vertical",new=False),
              api(168,"jobs_claim"),
              api(160,"jobs_videos_vertical",new=True),
              api(159,"jobs_videos_horizontal",503),
              api(190,"jobs_videos_vertical",new=True)]
        result=o.summarize_video_throughput(rows,NOW)
        self.assertEqual(result["newVerifiedVideoCount"],2)
        self.assertEqual(result["videosPerHour"],.67)
        self.assertEqual(result["medianBetweenNewVideosSeconds"],600)
        self.assertEqual(result["medianUploadToNextClaimSeconds"],120)
        self.assertEqual(result["uploadHttpRequests"],4)
        self.assertEqual(result["uploadHttpFailureRate"],.25)
        self.assertIsNone(result["rendererFailureRate"])

    def test_exact_worker_attempt_pairs_exclude_unknown_and_conflicts(self):
        rows=[]
        def attempt(types,aspect="9:16",owner="muse-01"):
            aid=str(uuid4())
            cid=str(uuid4())
            for i,typ in enumerate(types):
                rows.append(row(typ,10-i,{"origin":"worker_reported","workerId":owner,
                    "renderAttemptId":aid,"commandId":cid,"aspectRatio":aspect}))
        attempt(["RENDER_STARTED","RENDER_SUCCEEDED"])
        attempt(["RENDER_STARTED","RENDER_FAILED"])
        attempt(["RENDER_STARTED"])
        attempt(["RENDER_STARTED","RENDER_SUCCEEDED","RENDER_FAILED"])
        rows.append(row("RENDER_FAILED",8,{"origin":"worker_reported","workerId":"muse-01"}))
        report=o.summarize_video_throughput(rows,NOW)
        self.assertEqual(report["rendererMatchedAttempts"],2)
        self.assertEqual(report["rendererMatchedFailures"],1)
        self.assertEqual(report["rendererFailureRate"],.5)
        self.assertEqual(report["rendererUnmatchedAttempts"],2)
        self.assertEqual(report["rendererUnpairedEvents"],1)

    def test_no_denominator_is_unknown_not_zero(self):
        result=o.summarize_video_throughput([],NOW)
        self.assertIsNone(result["rendererFailureRate"])
        self.assertIsNone(result["uploadHttpFailureRate"])
        self.assertIsNone(result["medianBetweenNewVideosSeconds"])

    def test_optional_attempt_uuid_and_success_event(self):
        e=o.WorkerEvent(eventId=uuid4(),eventType="RENDER_SUCCEEDED",occurredAtUtc=NOW,
                        renderAttemptId=uuid4(),commandId=uuid4(),aspectRatio="9:16")
        self.assertEqual(e.eventType,"RENDER_SUCCEEDED")
        self.assertIsNotNone(e.renderAttemptId)
