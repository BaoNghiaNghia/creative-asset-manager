"""Read-only contract tests with isolated DB; never call live worker auth or storage."""
import os
import sys
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.modules.image_orchestrator import policy as p, repository as repo
from app.modules.image_orchestrator.identity import ImageIdentity, image_identity, resolve_identity
from app.modules.image_orchestrator.model import ImageBatch, ImageFlowControl, ImagePack, ImageRole, ImagePlanApproval, ImageQAApproval
from app.modules.image_orchestrator import approvals
from app.modules.image_orchestrator import central_read_api as api

TENANT = "read-api-test"
OWNER = "muse-01"
NOW = 100000
PREFIX = "/api/v1/muse-bridge"


class ReadApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        @event.listens_for(self.engine, "connect")
        def fk(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine,tables=[
            ImageBatch.__table__, ImageFlowControl.__table__, ImagePack.__table__, ImageRole.__table__,
            ImagePlanApproval.__table__,ImageQAApproval.__table__])
        self.pack = p.PackIdentity(TENANT,"123456789","a"*64,"C1717",1,"b"*64,"c"*64)
        self.other = p.PackIdentity("foreign","123456789","a"*64,"C1717",1,"b"*64,"c"*64)
        with Session(self.engine) as db,db.begin():
            db.add(ImageFlowControl(tenant_id=TENANT,running=True))
            for identity in (self.pack,self.other):
                repo.create_batch(db,p.Batch("pilot",identity.tenant,(identity.pack_id,),NOW),
                                  [(identity,"d"*64)],test_sqlite=True)
            self.grant=repo.transition(db,TENANT,"pilot",self.pack.pack_id,"C1717",
                lambda pack,_:p.claim(pack,OWNER,1,NOW,flow_running=True,
                                      capability_verified=True,resource_granted=True),
                test_sqlite=True)
        with Session(self.engine) as db,db.begin():
            g=self.grant
            def verified(pack,_):
                p.role_start(pack,OWNER,g.epoch,g.image_token,NOW,1,"attempt-1",
                             flow_running=True,resource_granted=True)
                sha="e"*64
                p.verify_role(pack,OWNER,g.epoch,g.image_token,NOW,1,"attempt-1",
                    p.StoredReceipt(sha,1600,1600,"drive_test",sha,"pass",True,"b"*64))
            repo.transition(db,TENANT,"pilot",self.pack.pack_id,"C1717",verified,test_sqlite=True)
        self.app=FastAPI()
        self.app.include_router(api.router)
        def db_override():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db]=db_override
        self.principal=ImageIdentity(TENANT,OWNER,"muse")
        self.app.dependency_overrides[image_identity]=lambda:self.principal
        self.client=TestClient(self.app)
        self.env=patch.dict(os.environ,{"CAM_IMAGE_READ_API_ENABLED":"true"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.client.close()
        self.engine.dispose()

    def status(self,pack=None,sku="C1717",listing=None):
        identity=pack or self.pack
        return self.client.get(PREFIX+f"/jobs/{listing or identity.listing}/image-status",
                               params={"packId":identity.pack_id,"sku":sku})

    def test_registry_pinned_and_disabled(self):
        r=self.client.get(PREFIX+"/skill-registry")
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers["Cache-Control"],"private, no-store")
        data=r.json()
        self.assertEqual(data["skillSha256"],p.SKILL_SHA256)
        self.assertEqual(len(data["files"]),19)
        self.assertEqual(data["neckTagRole"],4)
        self.assertFalse(data["claimEnabled"])
        self.assertFalse(data["paletteVerified"])
        self.assertEqual(data["nativePilotMinimumPx"],1600)
        self.assertEqual(data["publicationMinimumPx"],2000)

    def test_disabled_discovery_not_queue_empty(self):
        r=self.client.get(PREFIX+"/image-commands")
        self.assertEqual(r.status_code,200)
        data=r.json()
        self.assertEqual(data["items"],[])
        self.assertEqual(data["reason"],"IMAGE_CLAIMS_DISABLED")
        self.assertFalse(data["queueAuthoritative"])
        self.assertFalse(data["imageClaimAllowed"])
        self.assertEqual(self.client.get(PREFIX+"/image-commands?limit=21").status_code,422)

    def test_work_tenant_scoped_even_if_control_requests_run(self):
        data=self.client.get(PREFIX+"/image-work").json()
        self.assertTrue(data["flowRunningRequested"])
        self.assertFalse(data["flowRunning"])
        self.assertEqual(data["centralPackCount"],1)
        self.assertEqual(data["centralRoleCounts"]["verified"],1)
        self.assertEqual(data["centralRoleCounts"]["pending"],12)
        self.assertIsNone(data["remainingWithoutImages"])
        self.assertFalse(data["queueAuthoritative"])

    def test_owner_status_redacted_and_preview(self):
        r=self.status()
        self.assertEqual(r.status_code,200)
        data=r.json()
        self.assertEqual(data["verifiedRoles"],[1])
        self.assertEqual(data["missingRoles"],list(range(2,14)))
        self.assertFalse(data["packComplete"])
        self.assertFalse(data["publicationReady"])
        self.assertFalse(data["artworkRevisionVerified"])
        self.assertTrue(data["roles"][0]["receipt"]["previewOnly"])
        self.assertFalse(data["contractReady"])
        self.assertNotIn(self.grant.image_token,r.text)
        self.assertNotIn(self.grant.command_token,r.text)
        self.assertNotIn(p.digest(self.grant.image_token),r.text)
        self.assertNotIn(p.digest(self.grant.command_token),r.text)

    def test_scope_and_wrong_pack_identifiers(self):
        self.assertEqual(self.status(self.other).status_code,404)
        self.assertEqual(self.status(sku="G500").status_code,404)
        self.assertEqual(self.status(listing="123456790").status_code,404)
        self.principal=ImageIdentity(TENANT,"muse-02","muse")
        self.assertEqual(self.status().status_code,404)
        self.principal=ImageIdentity("foreign",OWNER,"muse")
        self.assertEqual(self.status().status_code,404)
        self.assertEqual(self.status(self.other).status_code,404) # unowned
        self.assertEqual(self.client.get(PREFIX+"/jobs/123456789/image-status",
             params={"packId":"img-legacy","sku":"C1717"}).status_code,422)

    def test_corrupt_receipt_does_not_report_verified(self):
        with Session(self.engine) as db,db.begin():
            row=db.get(ImageRole,(TENANT,self.pack.pack_id,1))
            row.receipt_state={**row.receipt_state,"qa_image_sha":"f"*64}
        data=self.status().json()
        self.assertEqual(data["verifiedRoles"],[])
        self.assertEqual(data["roles"][0]["status"],"reconcile_required")

    def test_disabled_feature_and_no_mutation_paths(self):
        with patch.dict(os.environ,{"CAM_IMAGE_READ_API_ENABLED":"false"}):
            self.assertEqual(self.client.get(PREFIX+"/skill-registry").status_code,404)
            self.assertEqual(self.status().status_code,404)
        self.assertEqual(self.client.post(PREFIX+"/image-commands/abc/claim",json={}).status_code,404)
        self.assertEqual(self.client.post(PREFIX+"/jobs/123456789/images/01",
                                         content=b"test").status_code,404)
        self.assertEqual(self.client.get(PREFIX+"/commands").status_code,404)

    def test_contract_metadata_only_when_execution_disabled(self):
        url=PREFIX+"/jobs/123456789/image-contract"
        params={"packId":self.pack.pack_id,"sku":"C1717"}
        r=self.client.get(url,params=params)
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()["contractReady"])
        self.assertFalse(r.json()["rendererAllowed"])
        self.assertIsNone(r.json()["planMetadata"])
        self.principal=ImageIdentity(TENANT,"muse-02","muse")
        self.assertEqual(self.client.get(url,params=params).status_code,404)

    def test_qa_poll_is_exact_attempt_sha_and_owner_bound(self):
        url=PREFIX+"/jobs/123456789/images/01/qa-status"
        params={"packId":self.pack.pack_id,"sku":"C1717",
                "attemptId":"attempt-1","imageSha256":"e"*64}
        self.assertEqual(self.client.get(url,params=params).json()["qaStatus"],"unverified")
        rid="a"*64
        with Session(self.engine) as db,db.begin():
            db.add(ImageQAApproval(tenant_id=TENANT,receipt_id=rid,pack_id=self.pack.pack_id,
                role=1,attempt_id="attempt-1",epoch=self.grant.epoch,worker_id=OWNER,
                plan_sha256="d"*64,image_sha256="e"*64,verdict="pass",
                evidence={"policy":p.QA_POLICY,"reviewEvidenceSha256":"b"*64,
                    "checks":{k:True for k in approvals.QA_CHECKS},
                    "physicalCalibration":"unverified"},
                reviewed_by="trusted-review-fixture",reviewed_at=NOW,revoked=False))
        result=self.client.get(url,params=params).json()
        self.assertEqual(result["qaReceiptId"],rid)
        self.assertFalse(result["uploadAllowed"])
        result=self.client.get(url,params={**params,"imageSha256":"f"*64}).json()
        self.assertIsNone(result["qaReceiptId"])
        with Session(self.engine) as db,db.begin():
            db.get(ImageQAApproval,(TENANT,rid)).revoked=True
        self.assertEqual(self.client.get(url,params=params).json()["qaStatus"],"unverified")
        self.assertEqual(self.client.get(url,params={**params,"attemptId":"wrong"}).status_code,404)
        self.principal=ImageIdentity(TENANT,"muse-02","muse")
        self.assertEqual(self.client.get(url,params=params).status_code,404)
        self.assertEqual(self.client.post(url,params=params,json={"qaStatus":"pass"}).status_code,405)

    def test_native_qa_poll_and_receipt_are_bound_to_plan(self):
        from app.modules.image_orchestrator import native_first as n
        plan={"nativeFirst":{"promptTemplateSha256":"1"*64,"minimumPublicationPx":1600,
            "visualEstimateQa":True,"fallbackAllowed":False,"operatorPolicyReceiptSha256":"2"*64}}
        profile=n.profile_from_plan(plan)
        plan_sha=p.canonical_sha(plan)
        record=n.assess(profile,self.pack,role=1,epoch=self.grant.epoch,attempt_id="attempt-1",
            plan_sha=plan_sha,method=n.NATIVE,image_sha="e"*64,reviewed_sha="e"*64,
            width=1600,height=1600,native_verified=True,upscaled=False,
            checks={k:True for k in n.CHECKS},reviewer="review-fixture",worker=OWNER,
            evidence_sha="3"*64,prompt_sha=profile.prompt_sha)
        with Session(self.engine) as db,db.begin():
            row=db.get(ImagePack,(TENANT,self.pack.pack_id))
            row.approved_plan=plan;row.plan_sha256=plan_sha
            role=db.get(ImageRole,(TENANT,self.pack.pack_id,1))
            role.receipt_state={**role.receipt_state,"publication_minimum_px":1600,
                "generation_method":n.NATIVE,"qa_record_sha":record["qaRecordSha256"],
                "operator_policy_receipt_sha":"2"*64}
            db.add(ImageQAApproval(tenant_id=TENANT,receipt_id="4"*64,pack_id=self.pack.pack_id,
                role=1,attempt_id="attempt-1",epoch=self.grant.epoch,worker_id=OWNER,
                plan_sha256=plan_sha,image_sha256="e"*64,verdict="pass",evidence=record,
                reviewed_by="review-fixture",reviewed_at=NOW,revoked=False))
        url=PREFIX+"/jobs/123456789/images/01/qa-status"
        params={"packId":self.pack.pack_id,"sku":"C1717","attemptId":"attempt-1","imageSha256":"e"*64}
        result=self.client.get(url,params=params).json()
        self.assertEqual(result["qaReceiptId"],"4"*64)
        self.assertEqual(result["qaRecordSha256"],record["qaRecordSha256"])
        self.assertFalse(result["uploadAllowed"])
        self.assertFalse(result["previewOnly"])
        data=self.status().json()
        self.assertEqual((data["completedRoles"],data["totalRoles"]),(1,13))
        self.assertTrue(data["roles"][0]["receipt"]["publicationReady"])
        self.assertEqual(data["roles"][0]["receipt"]["generationMethod"],n.NATIVE)
        with Session(self.engine) as db,db.begin():
            row=db.get(ImageRole,(TENANT,self.pack.pack_id,1))
            row.receipt_state={**row.receipt_state,"operator_policy_receipt_sha":"5"*64}
            qa=db.get(ImageQAApproval,(TENANT,"4"*64))
            qa.evidence={**qa.evidence,"attemptId":"wrong"}
        self.assertEqual(self.status().json()["completedRoles"],0)
        self.assertEqual(self.client.get(url,params=params).json()["qaStatus"],"unverified")

    def test_missing_bearer_rejected_before_auth_adapter(self):
        self.app.dependency_overrides.pop(image_identity)
        self.assertEqual(self.client.get(PREFIX+"/skill-registry").status_code,401)


class IdentityAdapterTests(unittest.TestCase):
    def test_muse_existing_identity_and_legacy_denied(self):
        request=Request({"type":"http","headers":[]})
        module=types.ModuleType("app.modules.muse_bridge.router")
        module._muse=lambda request,auth:"existing-tenant"
        module._muse_identity=lambda request:"muse-01"
        with patch.dict(sys.modules,{"app.modules.muse_bridge.router":module}):
            self.assertEqual(resolve_identity(request,"Bearer fixture",None,None),
                             ImageIdentity("existing-tenant","muse-01","muse"))
            module._muse_identity=lambda request:"legacy"
            with self.assertRaises(HTTPException) as exc:
                resolve_identity(request,"Bearer fixture",None,None)
            self.assertEqual(exc.exception.status_code,403)

    def test_agent_auth_role_and_no_fallback(self):
        request=Request({"type":"http","headers":[]})
        module=types.ModuleType("app.modules.etsy_spy.service")
        agent_id="11111111-2222-4333-8444-555555555555"
        calls=[]
        def auth(db,aid,bearer,*,image_only=False):
            calls.append((aid,image_only))
            if bearer!="Bearer fixture":
                raise HTTPException(401,"invalid")
            return SimpleNamespace(tenant_id="agent-tenant")
        module.authenticate_agent=auth
        with patch.dict(sys.modules,{"app.modules.etsy_spy.service":module}):
            result=resolve_identity(request,"Bearer fixture",agent_id,None)
            self.assertEqual(result,ImageIdentity("agent-tenant","chatgpt-ext-"+agent_id,"chatgpt_extension"))
            self.assertEqual(calls,[(agent_id,True)])
            with self.assertRaises(HTTPException) as exc:
                resolve_identity(request,"Bearer invalid",agent_id,None)
            self.assertEqual(exc.exception.status_code,401)
            with self.assertRaises(HTTPException) as exc:
                resolve_identity(request,"Bearer fixture","bad-uuid",None)
            self.assertEqual(exc.exception.status_code,401)
