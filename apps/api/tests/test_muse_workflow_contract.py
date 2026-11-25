"""CAM-owned QA state and scoped execution contract, no live renderer access."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.modules.muse_bridge import router as m, workflow_contract as w

TENANT = "tenant-a"
LID = "123456789"
SHA = "a" * 64

def job(verdict="pass", sha=SHA):
    qa = {"verdict": verdict, "artwork_sha256": sha,
          "reviewed_at": "2026-11-25T00:00:00+00:00",
          "confidence": 0.98, "checks": {key: True for key in w.CHECKS}}
    return SimpleNamespace(listing_key=LID, artwork_sha256=SHA, status="pending",
                           source_state="ready_for_muse",
                           output_receipts={"visual_qa":qa} if verdict else {})

class FakeDB:
    def __init__(self, row):
        self.row = row
    def get(self, model, key):
        return self.row if key == (TENANT, LID) else None
    def scalars(self, statement):
        return SimpleNamespace(all=lambda: [])

class WorkflowTests(TestCase):
    def test_authoritative_existing_pass_no_duplicate_review(self):
        row = job()
        with patch.object(m, "_fresh_action_story_v1", return_value=False):
            x = m._execution_contract(row, "create_video")
        self.assertEqual(x["nextAction"], "create_video")
        self.assertEqual(x["artworkQa"]["status"], "pass")
        self.assertFalse(x["freshArtworkReviewRequired"])
        self.assertFalse(x["artworkQa"]["duplicateLocalReviewRequired"])
        self.assertTrue(x["artworkQa"]["statusIsNotLeaseOrRendererPermission"])
        self.assertEqual(x["outputGarment"], "C1717")
        self.assertFalse(x["sourceGarmentMustBeC1717"])
        self.assertNotIn("X-CAM-Placement-Proof-Contract", x["commandClaimHeaders"])

    def test_legacy_only_exact_header_requirements(self):
        with patch.object(m, "_fresh_action_story_v1", return_value=True):
            x = m._execution_contract(job(), "create_video")
        self.assertEqual(x["commandClaimHeaders"]["X-CAM-Story-Action-Contract"],m.STORY_ACTION_VERSION)
        self.assertEqual(x["commandClaimHeaders"]["X-CAM-Placement-Proof-Contract"],m.PLACEMENT_PROOF_VERSION)

    def test_source_garment_is_not_output_garment(self):
        scope=w.review_scope()
        self.assertFalse(scope["sourceGarmentMustBeC1717"])
        self.assertFalse(scope["sourceGarmentMismatchAloneIsFailure"])
        self.assertEqual(scope["outputGarmentValidationStage"],"generated_hero_and_final_video")
        self.assertEqual(scope["minimumConfidence"],0.95)
        self.assertEqual(set(scope["requiredArtworkChecks"]),set(w.CHECKS))
        x=m._execution_contract(job(None),"review_artwork")
        self.assertTrue(x["freshArtworkReviewRequired"])
        self.assertEqual(x["artworkQa"]["status"],"missing")

    def test_no_failed_or_stale_record_becomes_pass(self):
        for row, status in [(job("fail"),"fail"),(job("pass","b"*64),"stale"),
                            (job(None),"missing"),(job("unknown"),"invalid")]:
            with self.subTest(status=status):
                self.assertEqual(w.artwork_qa(row)["status"],status)

    def test_empty_review_queue_exposes_existing_qa(self):
        row=job()
        result=m.visual_review_queue(listing_id=LID,limit=1,tenant=TENANT,db=FakeDB(row))
        self.assertEqual(result["items"],[])
        self.assertTrue(result["pendingReviewQueueOnly"])
        self.assertEqual(result["artworkQa"]["status"],"pass")
        self.assertFalse(result["visualQaRequired"])
        for verdict in ("fail",None):
            result=m.visual_review_queue(listing_id=LID,limit=1,tenant=TENANT,db=FakeDB(job(verdict)))
            self.assertTrue(result["visualQaRequired"])

    def test_status_read_only_auth_and_tenant(self):
        app=FastAPI()
        app.include_router(m.router)
        app.dependency_overrides[get_db]=lambda: FakeDB(job())
        with patch.object(m,"_configured",return_value=("t"*64,TENANT)):
            client=TestClient(app)
            base="/api/v1/muse-bridge/jobs/"
            self.assertEqual(client.get(base+LID+"/artwork-qa").status_code,401)
            h={"Authorization":"Bearer "+"t"*64}
            r=client.get(base+LID+"/artwork-qa",headers=h)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.headers["cache-control"],"private, no-store")
            self.assertEqual(r.json()["artworkQa"]["status"],"pass")
            self.assertTrue(r.json()["claimStillRequired"])
            self.assertNotIn("lease",r.json())
            self.assertEqual(client.get(base+"987654321/artwork-qa",headers=h).status_code,404)
            client.close()

    def test_compact_keeps_server_action_and_qa(self):
        with patch.object(m,"_fresh_action_story_v1",return_value=False):
            x=m._execution_contract(job(),"create_video")
        payload={"listingId":LID,"commandId":"id","phase":"create_video",
                 "nextAction":"create_video","artworkQa":x["artworkQa"],
                 "executionContract":x,"artworkQaStatusPath":x["artworkQa"]["statusPath"]}
        compact=m._compact_discovery_item(payload)
        for key in ("executionContract","artworkQa","artworkQaStatusPath","nextAction"):
            self.assertEqual(compact[key],payload[key])

    def test_claim_gates_remain_separate(self):
        # QA status reports evidence, not readiness or a lease.
        x=w.artwork_qa(job())
        self.assertNotIn("claimAllowed",x)
        self.assertNotIn("lease",x)
        self.assertTrue(w.config()["claimRevalidatesAllEligibility"])
