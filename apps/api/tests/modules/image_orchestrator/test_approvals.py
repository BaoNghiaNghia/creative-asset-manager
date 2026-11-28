"""Trusted ledger unit tests, no actual image review or Drive calls."""
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.image_orchestrator import policy as p,repository as repo,admission,approvals,print_box
from app.modules.image_orchestrator.commands import CommandService,Runtime,Scope,ensure_commands,command_id
from app.modules.image_orchestrator.identity import ImageIdentity
from app.modules.image_orchestrator.model import (ImageBatch,ImagePack,ImageRole,ImageFlowControl,
    ImageCommand,ImageCapabilityState,ImageUploadIntent,ImagePlanApproval,ImageQAApproval)
from app.modules.render_resources.model import RenderResource

TENANT="qa-fixture"
WORKER="muse-01"
NOW=100000
ART="a"*64
SOURCE="b"*64
IMG="e"*64


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite:///:memory:")
        @event.listens_for(self.engine,"connect")
        def fk(conn,_):
            conn.execute("PRAGMA foreign_keys=ON")
        tables=[ImageBatch,ImagePack,ImageRole,ImageFlowControl,ImageCommand,
                ImageCapabilityState,ImageUploadIntent,ImagePlanApproval,ImageQAApproval,RenderResource]
        Base.metadata.create_all(self.engine,tables=[m.__table__ for m in tables])
        colors=list(admission.PALETTE)[:9]
        lock={**print_box.FIXED,"artwork_sha256":ART,"placement_side":"front_center",
              "print_width_mm":191.16,"width_source":"sms_preview_advisory","ink_aspect_ratio":1.25}
        lock["lock_sha256"]=print_box.lock_digest(lock)
        self.pack=p.PackIdentity(TENANT,"123456789",ART,"C1717",1,lock["lock_sha256"],SOURCE)
        self.scope=Scope(command_id(self.pack.pack_id,1),self.pack.pack_id,"C1717",self.pack.listing)
        self.plan={"skillSha256":p.SKILL_SHA256,"garmentSku":"C1717",
          "artworkSha256":ART,"sourceSha256":SOURCE,"storagePlacement":"front",
          "placementLock":lock,"mockupSize":"M","paletteSha256":admission.PALETTE_SHA,
          "roleColors":{**{f"{n:02d}":colors[0] for n in range(1,5)},
                        **{f"{n:02d}":colors[n-4] for n in range(5,13)}},
          "infographicSwatches":colors[1:9],"infographicSourceRoles":[1,5,6,7]}
        self.evidence={"originalBytesSha256":ART,"sourceVisualReviewSha256":"c"*64,
          "placementEvidenceSha256":"d"*64,"legacyDrainEvidenceSha256":"f"*64,
          "paletteEvidenceSha256":admission.PALETTE_SHA}
        self.identity=ImageIdentity(TENANT,WORKER,"muse")
        self.runtime=Runtime(approvals.authorize_plan,approvals.authorize_qa,test_sqlite=True)
        with Session(self.engine) as db,db.begin():
            db.add(ImageFlowControl(tenant_id=TENANT,running=True))
            db.add(RenderResource(tenant_id=TENANT,resource_id="shared",integration_verified=True))
            db.add(ImageCapabilityState(tenant_id=TENANT,worker_id=WORKER,
                    claim_allowed=True,skill_sha256=p.SKILL_SHA256,resource_id="shared"))
            repo.create_batch(db,p.Batch("pilot",TENANT,(self.pack.pack_id,),NOW),
                [(self.pack,p.canonical_sha(self.plan))],test_sqlite=True)
            db.get(ImagePack,(TENANT,self.pack.pack_id)).approved_plan=self.plan
            db.flush()
            ensure_commands(db,TENANT,self.pack.pack_id,test_sqlite=True)
        # Simulated current-artwork adapter: no real source or network.
        self.current=patch.object(admission,"inspect_current",return_value={"fixture":True})
        self.current.start()

    def tearDown(self):
        self.current.stop()
        self.engine.dispose()

    def op(self,fn):
        with Session(self.engine) as db,db.begin():
            return fn(CommandService(db,self.identity,self.runtime),db)

    def approve(self):
        return self.op(lambda _,db:approvals.record_plan(db,TENANT,self.pack.pack_id,
            self.evidence,"trusted-review-fixture",NOW,test_sqlite=True).plan_sha256)

    def start(self,role=1):
        self.approve()
        c=self.op(lambda svc,_:svc.claim(self.scope,NOW,p.SKILL_SHA256))
        g=self.op(lambda svc,_:svc.claim_image(self.scope,c["epoch"],c["lease"],NOW))
        self.op(lambda svc,_:svc.start(self.scope,g["epoch"],g["imageLease"],role,"attempt",NOW))
        return g

    def review(self,role=1,verdict="pass",reviewer="review-fixture",evidence=None,image_sha=IMG):
        checks={k:True for k in approvals.QA_CHECKS}
        if role==4: checks["neckTag"]=True
        if role==13: checks["approvedPhotoSources"]=True
        evidence=evidence or {"policy":p.QA_POLICY,"reviewEvidenceSha256":"b"*64,
                            "checks":checks,"physicalCalibration":"unverified"}
        return self.op(lambda _,db:approvals.record_qa(db,TENANT,self.pack.pack_id,role,
            "attempt",image_sha,verdict,evidence,reviewer,NOW,test_sqlite=True).receipt_id)

    def prepare(self,g,rid,role=1):
        return self.op(lambda svc,_:svc.prepare_upload(self.scope,g["epoch"],g["imageLease"],
            role,"attempt",NOW,IMG,1600,1600,ART,self.pack.placement_sha,rid,SOURCE,p.SKILL_SHA256))

    def native_profile(self,minimum=1600):
        from app.modules.image_orchestrator import native_first as n
        self.plan["nativeFirst"]={"promptTemplateSha256":"1"*64,
            "minimumPublicationPx":minimum,"visualEstimateQa":True,
            "fallbackAllowed":False,"operatorPolicyReceiptSha256":"2"*64}
        self.evidence["operatorPolicyReceiptSha256"]="2"*64
        with Session(self.engine) as db,db.begin():
            row=db.get(ImagePack,(TENANT,self.pack.pack_id))
            row.approved_plan=self.plan
            row.plan_sha256=p.canonical_sha(self.plan)
        return n.profile_from_plan(self.plan)

    def native_record(self,g,profile):
        from app.modules.image_orchestrator import native_first as n
        return n.assess(profile,self.pack,role=1,epoch=g["epoch"],attempt_id="attempt",
            plan_sha=p.canonical_sha(self.plan),method=n.NATIVE,image_sha=IMG,reviewed_sha=IMG,
            width=1600,height=1600,native_verified=True,upscaled=False,
            checks={k:True for k in n.CHECKS},reviewer="review-fixture",worker=WORKER,
            evidence_sha="3"*64,prompt_sha=profile.prompt_sha)

    def test_native_policy_approval_is_explicit_and_bound(self):
        self.native_profile()
        self.evidence.pop("operatorPolicyReceiptSha256")
        with self.assertRaisesRegex(p.Conflict,"plan_evidence_unverified"):
            self.approve()
        self.evidence["operatorPolicyReceiptSha256"]="4"*64
        with self.assertRaisesRegex(p.Conflict,"plan_evidence_unverified"):
            self.approve()

    def test_native_1600_trusted_review_storage_scope(self):
        from dataclasses import replace
        profile=self.native_profile()
        g=self.start()
        record=self.native_record(g,profile)
        with self.assertRaisesRegex(p.Conflict,"qa_evidence_invalid"):
            self.review()  # legacy QA cannot approve a Native-first pack
        bad={**record,"role":2}
        with self.assertRaisesRegex(p.Conflict,"qa_evidence_invalid"):
            self.review(evidence=bad)
        rid=self.review(evidence=record)
        ticket=self.prepare(g,rid)["ticket"]
        self.assertEqual(ticket.publication_scope,(1600,"NATIVE_IMAGEGEN",
                         record["qaRecordSha256"],"2"*64))
        receipt=p.StoredReceipt(IMG,1600,1600,"fixture_drive_id",IMG,"pass",True,
                               self.pack.placement_sha,*ticket.publication_scope)
        self.assertTrue(receipt.publication_ready)
        bad=replace(receipt,operator_policy_receipt_sha="7"*64)
        with self.assertRaisesRegex(p.Conflict,"native_publication_scope_mismatch"):
            self.op(lambda svc,_:svc.accept_storage(self.scope,g["imageLease"],NOW,ticket,bad))
        result=self.op(lambda svc,_:svc.accept_storage(self.scope,g["imageLease"],NOW,ticket,receipt))
        self.assertFalse(result["previewOnly"])
        self.assertTrue(result["storageVerified"])

    def test_native_1600_strict_2000_cannot_enter_production_storage(self):
        profile=self.native_profile(2000)
        g=self.start()
        record=self.native_record(g,profile)
        self.assertTrue(record["previewOnly"])
        rid=self.review(evidence=record)
        with self.assertRaisesRegex(p.Conflict,"native_qa_publication_ineligible"):
            self.prepare(g,rid)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ImageUploadIntent,(TENANT,self.pack.pack_id,1,"attempt")))

    def test_no_approval_no_claim_and_no_resource_leak(self):
        with self.assertRaisesRegex(p.Conflict,"image_plan_unverified"):
            self.op(lambda svc,_:svc.claim(self.scope,NOW,p.SKILL_SHA256))
        with Session(self.engine) as db:
            self.assertIsNone(db.get(RenderResource,(TENANT,"shared")).owner_flow)

    def test_approval_rechecks_current_artwork(self):
        self.approve()
        with patch.object(admission,"inspect_current",side_effect=p.Conflict("stale_artwork")):
            with self.assertRaisesRegex(p.Conflict,"image_plan_unverified"):
                self.op(lambda svc,_:svc.claim(self.scope,NOW,p.SKILL_SHA256))

    def test_qa_cannot_self_approve_and_fake_id_cannot_upload(self):
        g=self.start()
        with self.assertRaisesRegex(p.Conflict,"qa_self_approval_denied"):
            self.review(reviewer=WORKER)
        with self.assertRaisesRegex(p.Conflict,"image_qa_or_source_unverified"):
            self.prepare(g,"c"*64)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ImageUploadIntent,(TENANT,self.pack.pack_id,1,"attempt")))

    def test_exact_sha_bound_qa_and_replay(self):
        g=self.start()
        rid=self.review()
        self.assertEqual(self.review(),rid)
        other=self.review(image_sha="f"*64)
        with self.assertRaisesRegex(p.Conflict,"image_qa_or_source_unverified"):
            self.prepare(g,other)
        prepared=self.prepare(g,rid)
        self.assertIsNotNone(prepared["ticket"])
        self.assertTrue(self.prepare(g,rid)["alreadyPrepared"])
        with self.assertRaisesRegex(p.Conflict,"immutable_upload_intent"):
            self.prepare(g,other)
        with Session(self.engine) as db:
            j=db.get(ImageUploadIntent,(TENANT,self.pack.pack_id,1,"attempt"))
            self.assertEqual(j.qa_receipt_id,rid)

    def test_revoked_review_between_write_and_commit_blocks_receipt(self):
        g=self.start()
        rid=self.review()
        ticket=self.prepare(g,rid)["ticket"]
        with Session(self.engine) as db,db.begin():
            db.get(ImageQAApproval,(TENANT,rid)).revoked=True
        receipt=p.StoredReceipt(IMG,1600,1600,"fixture_drive_id",IMG,"pass",True,self.pack.placement_sha)
        with self.assertRaisesRegex(p.Conflict,"image_qa_or_source_unverified"):
            self.op(lambda svc,_:svc.accept_storage(self.scope,g["imageLease"],NOW,ticket,receipt))
        with Session(self.engine) as db:
            self.assertEqual(db.get(ImageUploadIntent,(TENANT,self.pack.pack_id,1,"attempt")).status,"pending")
            self.assertEqual(db.get(ImageRole,(TENANT,self.pack.pack_id,1)).status,"upload_pending")

    def test_folded_neck_tag_hold_cannot_be_changed_to_pass(self):
        g=self.start(4)
        checks={**{k:True for k in approvals.QA_CHECKS},"neckTag":False}
        evidence={"policy":p.QA_POLICY,"reviewEvidenceSha256":"b"*64,
                  "checks":checks,"physicalCalibration":"unverified"}
        with self.assertRaisesRegex(p.Conflict,"qa_evidence_invalid"):
            self.review(role=4,evidence=evidence)
        rid=self.review(role=4,verdict="hold",evidence=evidence)
        with self.assertRaisesRegex(p.Conflict,"immutable_qa_approval"):
            self.review(role=4)
        with self.assertRaisesRegex(p.Conflict,"image_qa_or_source_unverified"):
            self.prepare(g,rid,role=4)

    def test_preview_canary_commits_receipt_without_commercial_gallery(self):
        from dataclasses import replace
        from app.modules.etsy_spy.model import EtsySpyPicked as Picked,EtsySpyListingImage as Gallery,EtsySpyListingImageDeletion as Deleted
        from app.modules.image_orchestrator.storage import DriveEvidence,project_gallery
        Base.metadata.create_all(self.engine,tables=[Picked.__table__,Gallery.__table__,Deleted.__table__])
        with Session(self.engine) as db,db.begin():
            db.add(Picked(tenant_id=TENANT,listing_key=self.pack.listing))
        self.evidence["executionEvidence"]={"scope":"single_pack",
            "resourceEvidenceSha256":"a"*64,"cronNativeEvidenceSha256":"b"*64,
            "driveEvidenceSha256":"c"*64,"backupReceiptSha256":"d"*64,
            "restoreEvidenceSha256":None,"backupOnlyCanary":True,
            "serverOnlyLegacyFence":True,"legacyClientDrainVerified":False,
            "nativeOutputPx":1600,"previewOnlyCanary":True}
        g=self.start()
        rid=self.review()
        ticket=self.prepare(g,rid)["ticket"]
        self.assertTrue(ticket.preview_only_canary)
        receipt=p.StoredReceipt(IMG,1600,1600,"fixture_drive_id",IMG,"pass",True,
            self.pack.placement_sha,preview_only_canary=True)
        self.assertFalse(replace(receipt,width=2000,height=2000).publication_ready)
        ev=DriveEvidence("fixture_drive_id","fixture_thumbnail","fixture_folder_id","fixture_thumb_folder","e"*64)
        with self.assertRaisesRegex(p.Conflict,"preview_canary_scope_mismatch"):
            self.op(lambda svc,_:svc.accept_storage(self.scope,g["imageLease"],NOW,ticket,
                replace(receipt,preview_only_canary=False)))
        result=self.op(lambda svc,_:svc.accept_storage(self.scope,g["imageLease"],NOW,ticket,receipt,
            project=lambda db,t,r:project_gallery(db,t,r,ev)))
        self.assertTrue(result["previewOnly"])
        self.assertTrue(result["storageVerified"])
        with Session(self.engine) as db:
            self.assertIsNone(db.get(Gallery,(TENANT,self.pack.listing,ticket.gallery_id,1)))
            self.assertTrue(db.get(ImageRole,(TENANT,self.pack.pack_id,1)).receipt_state["preview_only_canary"])

    def test_successful_storage_with_trusted_qa_stays_preview(self):
        g=self.start()
        rid=self.review()
        ticket=self.prepare(g,rid)["ticket"]
        receipt=p.StoredReceipt(IMG,1600,1600,"fixture_drive_id",IMG,"pass",True,self.pack.placement_sha)
        result=self.op(lambda svc,_:svc.accept_storage(self.scope,g["imageLease"],NOW,ticket,receipt))
        self.assertTrue(result["previewOnly"])
        self.assertTrue(result["storageVerified"])