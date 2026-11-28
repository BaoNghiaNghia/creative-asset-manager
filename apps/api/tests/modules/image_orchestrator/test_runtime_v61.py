"""Runtime patch regression: exact QA record, trusted approval, verified status."""
import hashlib
import unittest
from unittest.mock import patch
from sqlalchemy.orm import Session

from app.modules.image_orchestrator import adaptive_v61 as v, policy as p, v6_wire_contract as wire
from app.modules.image_orchestrator import approvals, central_read_api, qa_submission as q
from app.modules.image_orchestrator.model import ImageQAApproval, ImagePack, ImageRole
from app.modules.image_orchestrator.identity import ImageIdentity
from app.modules.image_orchestrator.commands import Runtime
from app.modules.image_orchestrator import write_api
import test_commands_api as fixture
from test_adaptive_v61 import manifest

RECORD={"artworkFidelity":"pass","collarGapApproxMm":150,"garmentCorrect":True,
    "inkContrast":"pass","machine":{"garmentDominantRgb":[37,40,42],
    "inkContrastRatio":5.58,"inkFraction":0.3157,"inkWidthFraction":0.312,
    "nativeDimensions":[1600,1600]},"majorDefects":[],
    "printScaleConsistent":True,"readability":"pass","role":1,"roleCorrect":True}
SHA="d7daf56095aaecd395890fd51f8c9ee552d0df1176883b31b9e90b10545656bc"

class RuntimeV61Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CommandApiTests(); self.f.setUp()
        f=self.f
        q.ImageQARecordSubmission.__table__.create(f.engine)
        ImageQAApproval.__table__.create(f.engine)
        # Literal batch paths precede legacy /images/{role}.
        routes=f.app.router.routes; count=len(routes)
        f.app.include_router(v.router); routes[:]=routes[count:]+routes[:count]
        f.app.include_router(central_read_api.router)
        self.clock=patch.object(v,"now",return_value=fixture.NOW);self.clock.start()
        self.c=f.command("claim").json()
        self.m=manifest(f.pack.listing,roles=(1,))
        response=f.job("batch-claim",{"lease":self.c["lease"],"epoch":self.c["epoch"],
            "batchManifest":self.m,"manifestSha256":wire.manifest_sha256(self.m)})
        self.assertEqual(response.status_code,200,response.text)
        self.a=response.json()
        self.attempt=self.m["calls"][0]["attemptIds"]["1"]
        r=f.job("batch-start",{"imageLease":self.a["imageLease"],"epoch":self.c["epoch"],
            "batchId":self.m["batchId"],"callId":self.m["calls"][0]["callId"],
            "roles":[1],"attemptIds":{"1":self.attempt},"generationMethod":"native_imagegen"})
        self.assertEqual(r.status_code,200,r.text);self.rid=r.json()["qaReceiptIds"]["1"]
        self.headers={**f.headers,"X-Muse-Lease":self.a["imageLease"],
            "X-CAM-Epoch":str(self.c["epoch"]),"X-CAM-Attempt-Id":self.attempt,
            "X-CAM-Batch-Id":self.m["batchId"],"X-CAM-QA-Record-Sha256":SHA}
    def tearDown(self):
        self.clock.stop();self.f.tearDown()
    def submit(self,record=None,**overrides):
        f=self.f
        return f.client.post(f"{fixture.PREFIX}/jobs/{f.pack.listing}/images/1/qa-record",
            params={"commandId":f.cid},headers={**self.headers,**overrides},
            json=RECORD if record is None else record)
    def upload(self):
        f=self.f; data=fixture.png()
        return f.client.post(f"{fixture.PREFIX}/jobs/{f.pack.listing}/images/1",
            params={"commandId":f.cid},content=data,headers={**self.headers,
            "Content-Type":"image/png","X-CAM-Output-Index":"0",
            "X-CAM-Generation-Method":"native_imagegen","X-CAM-Image-Role":"1",
            "X-CAM-Image-Sha256":hashlib.sha256(data).hexdigest(),
            "X-CAM-Artwork-SHA256":f.pack.artwork_sha,"X-CAM-Source-Sha256":f.pack.source_sha,
            "X-CAM-Placement-Lock-Sha256":f.pack.placement_sha,"X-CAM-QA-Receipt-Id":self.rid})
    def test_exact_record_replay_and_wrong_lease(self):
        self.assertEqual(q.record_hash(RECORD,1),SHA)
        self.assertEqual(self.submit(**{"X-Muse-Lease":self.c["lease"]}).status_code,409)
        self.assertEqual(self.submit(record={**RECORD,"role":2}).status_code,409)
        r=self.submit(); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json(),{"status":"qa_record_received","qaRecordSha256":SHA})
        self.assertEqual(self.submit().json(),r.json())
        changed={**RECORD,"readability":"fail"}
        self.assertEqual(self.submit(record=changed,**{"X-CAM-QA-Record-Sha256":q.record_hash(changed,1)}).status_code,409)
        with Session(self.f.engine) as db:
            row=db.get(q.ImageQARecordSubmission,(fixture.TENANT,self.rid))
            self.assertEqual(row.record,RECORD)
            self.assertIsNone(db.get(ImageQAApproval,(fixture.TENANT,self.rid)))
    def test_upload_requires_record_and_independent_approval(self):
        r=self.upload(); self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(r.json()["detail"],"qa_record_not_received")
        self.assertEqual(self.submit().status_code,200)
        r=self.upload(); self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.f.writes,0)
    def test_independent_approval_and_complete_echo(self):
        f=self.f;self.assertEqual(self.submit().status_code,200)
        sha=hashlib.sha256(fixture.png()).hexdigest()
        evidence={"policy":approvals.QA_POLICY,"reviewEvidenceSha256":"a"*64,
            "checks":{key:True for key in approvals.QA_CHECKS},"physicalCalibration":"unverified"}
        with Session(f.engine) as db,db.begin():
            with self.assertRaises(p.Conflict):
                approvals.record_qa(db,fixture.TENANT,f.pack.pack_id,1,self.attempt,sha,"pass",
                    evidence,fixture.WORKER,fixture.NOW,test_sqlite=True,submitted_record_sha=SHA)
            with self.assertRaises(p.Conflict):
                approvals.record_qa(db,fixture.TENANT,f.pack.pack_id,1,self.attempt,sha,"pass",
                    evidence,"reviewer",fixture.NOW,test_sqlite=True)
            row=approvals.record_qa(db,fixture.TENANT,f.pack.pack_id,1,self.attempt,sha,"pass",
                evidence,"reviewer",fixture.NOW,test_sqlite=True,submitted_record_sha=SHA)
            self.assertEqual(row.receipt_id,self.rid)
        f.app.dependency_overrides[write_api.get_adapters]=lambda:write_api.Adapters(
            Runtime(lambda db,identity,row:True,approvals.authorize_qa,test_sqlite=True),f.backend.store)
        r=self.upload();self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(f.writes,1)
        body={"lease":self.c["lease"],"epoch":self.c["epoch"],"outcome":"partial",
              "roles":{"1":"verified","2":"pending","3":"pending","4":"pending"}}
        r=f.command("complete",body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((r.json()["status"],r.json()["commandId"],r.json()["outcome"]),
            ("completed",f.cid,"partial"))
        self.assertTrue(f.command("complete",body).json()["ackReplayed"])
        # Dedicated V6.1 view; test-only principal override, no live credential change.
        with Session(f.engine) as db,db.begin():
            db.get(ImagePack,(fixture.TENANT,f.pack.pack_id)).owner="muse-image-01"
        f.principal=ImageIdentity(fixture.TENANT,"muse-image-01","muse")
        r=f.client.get(f"{fixture.PREFIX}/jobs/{f.pack.listing}/image-status",
            params={"packId":f.pack.pack_id,"sku":f.pack.garment})
        self.assertEqual(r.status_code,200,r.text)
        data=r.json(); entry=data["roles"]["1"]
        self.assertEqual(data["packProgress"],"1/13")
        self.assertEqual(data["waveCommands"]["1"],"pending")
        self.assertEqual(data["waveCommandDetails"]["1"]["commandId"],f.cid)
        self.assertEqual(entry["imageSha256"],sha)
        self.assertTrue(entry["storageVerified"])
        self.assertTrue(p.valid_sha(entry["storageReceiptId"]))
        self.assertTrue(entry["previewOnly"])
        self.assertFalse(data["barrier"]["wave2Open"])
        self.assertFalse(data["claimEnabled"])

    def test_live_review_runs_before_storage(self):
        f=self.f
        self.assertEqual(self.submit().status_code,200)
        calls=[]
        async def reviewer(ctx,png):
            calls.append(ctx.image_sha)
            self.assertEqual(hashlib.sha256(png).hexdigest(),ctx.image_sha)
            self.assertEqual(ctx.qa_record_sha,SHA)
            with Session(f.engine) as db:
                self.assertIsNone(db.get(ImageQAApproval,(fixture.TENANT,self.rid)))
            return "pass",{"policy":approvals.QA_POLICY,"reviewEvidenceSha256":"a"*64,
                "checks":{key:True for key in approvals.QA_CHECKS},
                "physicalCalibration":"unverified"}
        f.app.dependency_overrides[write_api.get_adapters]=lambda:write_api.Adapters(
            Runtime(lambda db,identity,row:True,approvals.authorize_qa,test_sqlite=True),
            f.backend.store,reviewer)
        with patch("app.modules.image_orchestrator.admission.inspect_current",
                   return_value={"jobId":"fixture-job","assetId":"fixture-asset"}):
            result=self.upload()
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(calls),1)
        self.assertEqual(f.writes,1)
        with Session(f.engine) as db:
            self.assertEqual(db.get(ImageQAApproval,(fixture.TENANT,self.rid)).verdict,"pass")

    def test_independent_rejection_requeues_without_drive_write(self):
        f=self.f
        self.assertEqual(self.submit().status_code,200)
        async def reviewer(ctx,png):
            checks={key:True for key in approvals.QA_CHECKS}
            checks["artworkFidelity"]=False
            return "hold",{"policy":approvals.QA_POLICY,"reviewEvidenceSha256":"b"*64,
                "checks":checks,"physicalCalibration":"unverified"}
        f.app.dependency_overrides[write_api.get_adapters]=lambda:write_api.Adapters(
            Runtime(lambda db,identity,row:True,approvals.authorize_qa,test_sqlite=True),
            f.backend.store,reviewer)
        with patch("app.modules.image_orchestrator.admission.inspect_current",
                   return_value={"jobId":"fixture-job","assetId":"fixture-asset"}):
            result=self.upload()
        self.assertEqual(result.status_code,409,result.text)
        self.assertEqual(result.json()["detail"],"image_independent_qa_rejected")
        self.assertEqual(f.writes,0)
        with Session(f.engine) as db:
            r=db.get(ImageRole,(fixture.TENANT,f.pack.pack_id,1))
            self.assertEqual(r.status,"pending")
            self.assertEqual(r.failure_code,"independent_qa_rejected")
            self.assertEqual(db.get(ImageQAApproval,(fixture.TENANT,self.rid)).verdict,"hold")

    def local_reject(self,record=None,**overrides):
        f=self.f
        rec={**RECORD,"artworkFidelity":"fail"} if record is None else record
        payload={"imageLease":self.a["imageLease"],"epoch":self.c["epoch"],
            "batchId":self.m["batchId"],"attemptId":self.attempt,
            "candidateSha256":hashlib.sha256(fixture.png()).hexdigest(),
            "qaRecordSha256":q.record_hash(rec,1),"qaRecord":rec,
            "reason":"qa_failed_fidelity",**overrides}
        return f.job("1/reject-local",payload)

    def test_local_rejection_is_fenced_idempotent_and_consumes_attempt(self):
        f=self.f
        self.assertEqual(self.local_reject(imageLease=self.c["lease"]).status_code,409)
        self.assertEqual(self.local_reject(record=RECORD).status_code,409)
        self.assertEqual(self.local_reject(qaRecordSha256="a"*64).status_code,409)
        r=self.local_reject()
        self.assertEqual(r.status_code,200,r.text)
        result=r.json()
        self.assertEqual(result["status"],"local_rejection_recorded")
        self.assertEqual(result["attemptId"],self.attempt)
        self.assertEqual(result["roleStatus"],"pending")
        self.assertEqual(result["attemptCount"],1)
        self.assertTrue(result["retryAllowed"])
        self.assertEqual(self.local_reject().json(),result)
        self.assertEqual(self.local_reject(candidateSha256="b"*64).status_code,409)
        self.assertEqual(self.submit().status_code,409)
        self.assertEqual(self.upload().status_code,409)
        self.assertEqual(f.writes,0)
        with Session(f.engine) as db:
            row=db.get(ImageRole,(fixture.TENANT,f.pack.pack_id,1))
            self.assertEqual(row.status,"pending")
            self.assertEqual(row.attempts,1)
            self.assertEqual(row.failure_code,"local_qa_failed_fidelity")
            journal=db.get(q.ImageQARecordSubmission,(fixture.TENANT,self.rid))
            self.assertEqual(journal.record_sha256,result["rejectionSha256"])
            self.assertEqual(journal.record["localRejection"]["candidateSha256"],result["candidateSha256"])
            self.assertIsNone(db.get(ImageQAApproval,(fixture.TENANT,self.rid)))
        # Only after the server transition is it permissible to reserve a new attempt.
        m=manifest(f.pack.listing,roles=(1,),bid=30)
        m["calls"][0]["callId"]="a"*32
        body={"lease":self.c["lease"],"epoch":self.c["epoch"],
              "batchManifest":m,"manifestSha256":wire.manifest_sha256(m)}
        self.assertEqual(f.job("batch-claim",body).status_code,200)

    def test_local_reject_cannot_replace_submitted_review(self):
        self.assertEqual(self.submit().status_code,200)
        r=self.local_reject()
        self.assertEqual(r.status_code,409,r.text)
        with Session(self.f.engine) as db:
            row=db.get(ImageRole,(fixture.TENANT,self.f.pack.pack_id,1))
            self.assertEqual(row.status,"dispatch_uncertain")
            self.assertEqual(db.get(q.ImageQARecordSubmission,(fixture.TENANT,self.rid)).record,RECORD)
