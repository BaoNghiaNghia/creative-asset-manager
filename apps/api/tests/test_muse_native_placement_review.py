"""Unknown Native Master placement is a QA lane, not a render bypass."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from fastapi import HTTPException

from app.modules.muse_bridge import native_placement_review as p, router as muse

SHA = "a" * 64
ASSET = "b" * 24
SOURCE = "https://example.invalid/actual-product.jpg"
TENANT = "test-tenant"
LISTING = "123456789"

def asset(position="unknown", **extra):
    return {"id": ASSET, "position": position, "artwork_kind": "native_master_v2",
            "method": "imagegen", "output_sha256": SHA, **extra}

def stored(*assets):
    return SimpleNamespace(id="test-job", payload_json={"design_assets": list(assets)})

def job():
    return SimpleNamespace(listing_key=LISTING, artwork_sha256=SHA,
                           product_image_url=SOURCE, output_receipts={},
                           status="qa_hold", source_state="qa_hold",
                           artwork_asset_path=None)

class FakeDb:
    def __init__(self, row):
        self.row = row
        self.commits = 0
    def scalar(self, _):
        return self.row
    def get(self, *_):
        return self.row
    def commit(self):
        self.commits += 1

class NativePlacementReviewTests(TestCase):
    def test_only_exact_native_unknown_is_review_candidate(self):
        front, unknown = p.choose_matches([stored(asset())], SHA)
        self.assertIsNone(front)
        self.assertEqual(unknown.position, "unknown")
        for bad in (asset(output_sha256="d"*64), asset(artwork_kind=None),
                    asset(method="manual"), asset("back"), asset("left_chest"),
                    asset(id="bad")):
            self.assertEqual(p.choose_matches([stored(bad)], SHA), (None,None))

    def test_known_nonfront_conflict_cannot_be_reviewed_as_unknown(self):
        self.assertEqual(p.choose_matches([stored(asset(),asset("back"))], SHA),(None,None))
        front, unknown = p.choose_matches([stored(asset(),asset("front"))], SHA)
        self.assertEqual(front.position,"front")

    def test_unknown_not_approved_by_metadata_or_unbound_qa(self):
        row=job()
        _,match=p.choose_matches([stored(asset())],SHA)
        db=FakeDb(row)
        with patch.object(muse,"_native_asset_matches",return_value=(None,match)):
            self.assertEqual(muse._review_native_asset(db,TENANT,row),match)
            self.assertIsNone(muse._stored_native_asset(db,TENANT,LISTING,SHA))
            row.output_receipts={"visual_qa":{"verdict":"pass","artwork_sha256":SHA,"confidence":1}}
            self.assertIsNone(muse._stored_native_asset(db,TENANT,LISTING,SHA))
            row.output_receipts={}
            cmd=SimpleNamespace(artwork_sha256=SHA,action="create_video")
            self.assertEqual(muse._eligible_phase(db,TENANT,row,cmd),"review_artwork")

    def verdict(self, **kwargs):
        data=dict(verdict="pass",artworkSha256=SHA,sourceImageReviewed=True,
                  designMatchesSource=True,originalTextPreserved=True,
                  transparentBackgroundChecked=True,placementChecked=True,
                  notes="Comparative review of the actual product image and exact artwork.",
                  confidence=0.97)
        data.update(kwargs)
        return muse.VisualReviewVerdict(**data)

    def test_unknown_pass_requires_exact_contract_and_front_evidence(self):
        row=job(); db=FakeDb(row)
        _,match=p.choose_matches([stored(asset())],SHA)
        rid=p.review_id(match,SHA,SOURCE)
        request=SimpleNamespace(state=SimpleNamespace())
        with patch.object(muse,"_assert_active_job_command_owner"),patch.object(muse,"_native_asset_matches",return_value=(None,match)):
            for changes in ({},{"observedPlacement":"back","placementReviewId":rid},
                            {"observedPlacement":"front_center","placementReviewId":"f"*64},
                            {"observedPlacement":"front_center","placementReviewId":rid,"confidence":0.5},
                            {"observedPlacement":"front_center","placementReviewId":rid,"placementChecked":False}):
                with self.assertRaises(HTTPException) as ctx:
                    muse.submit_visual_review(LISTING,self.verdict(**changes),request,TENANT,db)
                self.assertEqual(ctx.exception.status_code,422)
                self.assertEqual(row.status,"qa_hold")
                self.assertEqual(db.commits,0)
            result=muse.submit_visual_review(LISTING,self.verdict(observedPlacement="front_center",placementReviewId=rid),request,TENANT,db)
            self.assertEqual(result["status"],"pending")
            self.assertEqual(db.commits,1)
            self.assertFalse(result["videoGenerated"])
            self.assertFalse(result["printReady"])
            self.assertEqual(muse._stored_native_asset(db,TENANT,LISTING,SHA),match.locator)
            row.product_image_url=SOURCE+"?changed=1"
            self.assertIsNone(muse._stored_native_asset(db,TENANT,LISTING,SHA))

    def test_failed_review_not_promoted(self):
        row=job(); db=FakeDb(row)
        _,match=p.choose_matches([stored(asset())],SHA)
        request=SimpleNamespace(state=SimpleNamespace())
        with patch.object(muse,"_assert_active_job_command_owner"),patch.object(muse,"_native_asset_matches",return_value=(None,match)):
            result=muse.submit_visual_review(LISTING,self.verdict(verdict="fail",observedPlacement="back",placementReviewId=p.review_id(match,SHA,SOURCE)),request,TENANT,db)
            self.assertEqual(result["status"],"qa_hold")
            self.assertEqual(row.source_state,"qa_hold")
            self.assertIsNone(row.artwork_asset_path)
            self.assertIsNone(muse._stored_native_asset(db,TENANT,LISTING,SHA))

    def test_front_legacy_review_compatible(self):
        row=job(); db=FakeDb(row)
        match,_=p.choose_matches([stored(asset("front"))],SHA)
        request=SimpleNamespace(state=SimpleNamespace())
        with patch.object(muse,"_assert_active_job_command_owner"),patch.object(muse,"_native_asset_matches",return_value=(match,None)):
            self.assertEqual(muse.submit_visual_review(LISTING,self.verdict(),request,TENANT,db)["status"],"pending")
            self.assertEqual(muse._stored_native_asset(db,TENANT,LISTING,SHA),match.locator)

    def test_compact_preserves_review_and_video_scope(self):
        r={"commandId":"cid","listingId":LISTING,"phase":"review_artwork",
           "placementReview":{"policy":p.POLICY,"reviewOnly":True}}
        self.assertEqual(muse._compact_discovery_item(r)["placementReview"],r["placementReview"])
        v={"commandId":"cid","listingId":LISTING,"phase":"create_video","nextStep":"create_video",
           "requiredVideoAspectRatios":["9:16"],"missingVideoAspectRatios":["9:16"],
           "deferredVideoAspectRatios":["16:9"]}
        for key in ("nextStep","requiredVideoAspectRatios","missingVideoAspectRatios","deferredVideoAspectRatios"):
            self.assertEqual(muse._compact_discovery_item(v)[key],v[key])
