"""No live renderer or production database access."""
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.modules.muse_bridge import (
    router as m, queue_recovery as q, source_qa_recheck as s,
    receipt_ack as a, native_placement_review as p, workflow_contract as w,
)
from app.modules.muse_bridge.model import CamMuseJob as J, CamMuseCommand as C

NOW = datetime(2026, 11, 25, 3, tzinfo=timezone.utc)
TENANT = "test-tenant"
LID = "123456789"
SHA = "a" * 64
CID = str(uuid4())
SOURCE = "https://example.invalid/source.jpg"
NATIVE = p.NativeMatch("job-1", "b"*24, "front")
LEASE = "t"*48


class RecoveryTests(TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        from app.modules.processing.model import ProcessingJobModel
        for model in (ProcessingJobModel,J,C): model.__table__.create(engine)
        self.db = Session(engine)
        self.addCleanup(engine.dispose)
        self.addCleanup(self.db.close)
        self.db.add(J(tenant_id=TENANT, listing_key=LID, picked_at=NOW,
            title="sample", artwork_sha256=SHA, product_image_url=SOURCE,
            source_state="qa_hold", status="qa_hold", attempts=0,
            output_receipts={"visual_qa":{
                "verdict":"fail","reviewer":"muse","artwork_sha256":SHA,
                "confidence":0.98,"checks":{k:True for k in w.CHECKS},
                "notes":"GARMENT_MISMATCH_HOLD: source garment sweatshirt, not C1717. Artwork matches.",
                "reviewed_at":NOW.isoformat(),
            }}))
        self.db.add(C(tenant_id=TENANT,command_id=CID,listing_key=LID,
            action="review_artwork",phase="review_artwork",request_id="prior-test-review",
            artwork_sha256=SHA,status="failed",attempts=1,
            details={"notes":"GARMENT_MISMATCH_HOLD: source garment not C1717."}))
        self.db.commit()

    def job(self): return self.db.get(J,(TENANT,LID))
    def prior(self): return self.db.get(C,(TENANT,CID))
    def payload(self, **changes):
        return s.Approval(**{"artworkSha256":SHA,
            "priorReviewSha256":s.fingerprint(self.job().output_receipts["visual_qa"]),
            "priorCommandId":CID,"reason":"Correct source-only garment QA scope after operator review.",**changes})
    def approve(self, payload=None):
        return s.approve(self.db,TENANT,LID,payload or self.payload(),NOW,lambda *_:NATIVE)

    def sweep(self,cap=12):
        with patch.object(m,"_review_native_asset",return_value=NATIVE):
            return q.reconcile_queue(self.db,TENANT,NOW,cap=cap)

    def test_unapproved_failure_stays_held(self):
        report=self.sweep()
        self.assertEqual(report["sourceScopeRechecksQueued"],0)
        self.assertEqual(self.job().output_receipts["visual_qa"]["verdict"],"fail")

    def test_approved_scope_recheck_preserves_history_and_requires_fresh_verdict(self):
        original=dict(self.job().output_receipts["visual_qa"])
        payload=self.payload()
        self.assertEqual(self.approve(payload)["state"],"approved_pending_slot")
        self.assertTrue(self.approve(payload)["reused"])
        self.assertEqual(self.job().output_receipts["visual_qa"],original)
        report=self.sweep(cap=1)
        self.assertEqual(report["sourceScopeRechecksQueued"],1)
        new_id=report["newCommands"][0]["commandId"]
        command=self.db.get(C,(TENANT,new_id))
        job=self.job()
        self.assertEqual(job.status,"qa_hold")
        self.assertEqual(job.source_state,"qa_hold")
        self.assertIsNone(m._qa_record(job))
        self.assertEqual(job.output_receipts[s.KEY]["priorReview"],original)
        self.assertEqual(self.prior().status,"failed")
        self.assertEqual(self.prior().attempts,1)
        self.assertEqual(command.attempts,0)
        self.assertEqual(s.claim_limit(command),4)
        self.assertEqual(command.phase,"review_artwork")
        with patch.object(m,"_review_native_asset",return_value=NATIVE):
            self.assertEqual(m._eligible_phase(self.db,TENANT,job,command),"review_artwork")
            self.assertIsNone(m._eligible_phase(self.db,TENANT,job,SimpleNamespace(
                action="create_video",artwork_sha256=SHA,command_id="wrong")))
        self.assertEqual(self.sweep(cap=1)["sourceScopeRechecksQueued"],0)
        command.attempts=4
        self.db.commit()
        self.sweep(cap=1)
        self.assertEqual(command.status,"failed")
        self.assertEqual(self.sweep(cap=1)["newReviewCommands"],0)

    def test_wrong_fingerprint_or_sha_does_not_approve(self):
        for kw in ({"priorReviewSha256":"c"*64},{"artworkSha256":"c"*64},
                   {"priorCommandId":str(uuid4())}):
            with self.assertRaises(HTTPException) as ctx:self.approve(self.payload(**kw))
            self.assertEqual(ctx.exception.status_code,409)
            self.db.rollback()
        self.assertFalse(s.pending(self.job()))

    def test_genuine_hold_or_failed_check_not_eligible(self):
        old=self.job().output_receipts["visual_qa"]
        for change in ({"checks":{**old["checks"],"designMatchesSource":False}},
                       {"notes":"GARMENT_MISMATCH_HOLD: provider refused, source not C1717."},
                       {"review_scope_policy":w.QA_SCOPE}):
            self.job().output_receipts={"visual_qa":{**old,**change}}
            self.db.commit()
            with self.assertRaises(HTTPException):self.approve()
            self.db.rollback()
        self.job().output_receipts={"visual_qa":old,"videoCapabilityHold":{"code":"blocked"}}
        self.db.commit()
        with self.assertRaises(HTTPException):self.approve()

    def test_changed_source_after_approval_not_queued(self):
        self.approve()
        self.job().product_image_url=SOURCE+"?changed=1"
        self.db.commit()
        self.assertEqual(self.sweep()["sourceScopeRechecksQueued"],0)
        self.assertEqual(self.job().output_receipts["visual_qa"]["verdict"],"fail")

    def test_active_lease_or_other_history_not_eligible(self):
        self.prior().status="claimed"
        self.prior().lease_hash="c"*64
        self.prior().lease_expires_at=NOW+timedelta(minutes=10)
        self.db.commit()
        with self.assertRaises(HTTPException):self.approve()
        self.db.rollback()
        self.assertEqual(self.prior().status,"claimed")

    def test_verdict_marker_does_not_discard_prior_review(self):
        self.approve()
        self.sweep()
        receipts=self.job().output_receipts
        after=s.verdict_marker({**receipts,"visual_qa":{"verdict":"fail"}},"fail",NOW)
        self.assertEqual(after[s.KEY]["state"],"reviewed")
        self.assertEqual(after[s.KEY]["priorReview"]["verdict"],"fail")
        self.assertEqual(after["visual_qa"]["verdict"],"fail")

    def test_unknown_placement_needs_bound_source(self):
        unknown=p.NativeMatch("job-1","b"*24,"unknown")
        self.assertFalse(s.check_candidate(self.job(),[self.prior()],unknown))
        qa=self.job().output_receipts["visual_qa"]
        self.job().output_receipts={"visual_qa":{**qa,"placement_review":{
            "policy":p.POLICY,"observedPlacement":"front_center",
            "placementReviewId":p.review_id(unknown,SHA,SOURCE),
            "jobId":unknown.job_id,"assetId":unknown.asset_id}}}
        self.db.commit()
        self.assertTrue(s.check_candidate(self.job(),[self.prior()],unknown))
        self.job().product_image_url=SOURCE+"?new=1"
        self.assertFalse(s.check_candidate(self.job(),[self.prior()],unknown))


class AckTests(TestCase):
    def setUp(self):
        self.job=SimpleNamespace(listing_key=LID,artwork_sha256=SHA,
            output_receipts={"drive_uploads":{"9:16":{
                "sha256":"d"*64,"storageVerified":True,
                "storage_provider":"google_drive_managed",
                "relativeFolder":f"listing_{LID}/design_{SHA[:16]}_front/video_ugc",
                "drive_file_id":"test-video","drive_folder_id":"test-folder"}}})
        self.cmd=SimpleNamespace(phase="create_video",status="receipt_submitted",
            artwork_sha256=SHA,lease_hash=None,lease_expires_at=None,details={})
        self.payload=m.CommandAck(lease=LEASE,outcome="success",notes="Required vertical receipt saved.")
        self.cmd.details[a.KEY]=a.record(self.cmd,self.job,self.payload,"muse-01",
            ["9:16"],m._verified_video_ratios,NOW)

    def replay(self,payload=None,worker="muse-01",now=NOW):
        return a.matches(self.cmd,self.job,payload or self.payload,worker,m._verified_video_ratios,now)

    def test_exact_replay_only(self):
        self.assertTrue(self.replay())
        self.assertFalse(self.replay(worker="muse-02"))
        self.assertFalse(self.replay(now=NOW+timedelta(hours=25)))
        self.assertFalse(self.replay(m.CommandAck(lease="u"*48,outcome="success",notes=self.payload.notes)))
        self.assertFalse(self.replay(m.CommandAck(lease=LEASE,outcome="failed",notes=self.payload.notes)))
        self.assertNotIn(LEASE,str(self.cmd.details))

    def test_changed_or_missing_receipt_rejected(self):
        self.job.output_receipts["drive_uploads"]["9:16"]["sha256"]="e"*64
        self.assertFalse(self.replay())
        self.job.output_receipts={}
        self.assertFalse(self.replay())

    def test_no_record_for_failure_or_missing_required_aspect(self):
        self.assertIsNone(a.record(self.cmd,self.job,self.payload,"muse-01",
            ["9:16","16:9"],m._verified_video_ratios,NOW))
        self.cmd.status="failed"
        self.assertIsNone(a.record(self.cmd,self.job,self.payload,"muse-01",
            ["9:16"],m._verified_video_ratios,NOW))
