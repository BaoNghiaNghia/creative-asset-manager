"""P2b API lifecycle, simulated approved QA/storage and isolated SQLite only."""
import hashlib
import io
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base,get_db
from app.modules.image_orchestrator import policy as p,repository as repo
from app.modules.image_orchestrator.commands import (Runtime,command_id,ensure_commands,
    gallery_pack_id)
from app.modules.image_orchestrator.identity import ImageIdentity,image_identity
from app.modules.image_orchestrator.model import (ImageBatch,ImagePack,ImageRole,ImageFlowControl,
    ImageCommand,ImageCapabilityState,ImageUploadIntent)
from app.modules.image_orchestrator import write_api as api
from app.modules.render_resources.model import RenderResource

TENANT="fixture-tenant"
WORKER="muse-01"
NOW=100000
PREFIX="/api/v1/muse-bridge"
SKU="C1717"


def png(width=1600,height=1600):
    buffer=io.BytesIO()
    Image.new("RGB",(width,height),"white").save(buffer,format="PNG")
    return buffer.getvalue()


class CommandApiTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite://",poolclass=StaticPool,
                                  connect_args={"check_same_thread":False})
        @event.listens_for(self.engine,"connect")
        def fk(conn,_):
            conn.execute("PRAGMA foreign_keys=ON")
        tables=[ImageBatch,ImagePack,ImageRole,ImageFlowControl,ImageCommand,
                ImageCapabilityState,ImageUploadIntent,RenderResource]
        Base.metadata.create_all(self.engine,tables=[m.__table__ for m in tables])
        self.pack=p.PackIdentity(TENANT,"123456789","a"*64,SKU,1,"b"*64,"c"*64)
        self.other=p.PackIdentity(TENANT,"123456790","a"*64,SKU,1,"b"*64,"c"*64)
        self.cid=command_id(self.pack.pack_id,1)
        self.cid2=command_id(self.pack.pack_id,2)
        self.plan={"fixture":"approved","storagePlacement":"front"}
        self.plan_ok=True
        self.qa_ok=True
        self.store_ok=True
        self.writes=0
        self.principal=ImageIdentity(TENANT,WORKER,"muse")
        with Session(self.engine) as db,db.begin():
            db.add(ImageFlowControl(tenant_id=TENANT,running=True))
            db.add(RenderResource(tenant_id=TENANT,resource_id="shared",integration_verified=True))
            for worker in (WORKER,"muse-02"):
                db.add(ImageCapabilityState(tenant_id=TENANT,worker_id=worker,
                    claim_allowed=True,skill_sha256=p.SKILL_SHA256,resource_id="shared"))
            repo.create_batch(db,p.Batch("pilot",TENANT,
                (self.pack.pack_id,self.other.pack_id),NOW),
                [(x,p.canonical_sha(self.plan)) for x in (self.pack,self.other)],test_sqlite=True)
            for identity in (self.pack,self.other):
                db.get(ImagePack,(TENANT,identity.pack_id)).approved_plan=self.plan
                db.flush()
                ensure_commands(db,TENANT,identity.pack_id,test_sqlite=True)
        async def store(ticket,body):
            self.writes+=1
            # Assert upload journal was committed before external I/O.
            with Session(self.engine) as db:
                journal=db.get(ImageUploadIntent,(TENANT,ticket.pack_id,ticket.role,ticket.attempt_id))
                self.assertEqual(journal.status,"pending")
            if not self.store_ok:
                raise OSError("simulated storage timeout")
            self.assertEqual(ticket.gallery_id,gallery_pack_id(ticket.pack_id))
            self.assertEqual(hashlib.sha256(body).hexdigest(),ticket.image_sha)
            from app.modules.image_orchestrator.storage import SavedOutput
            return SavedOutput(p.StoredReceipt(ticket.image_sha,ticket.width,ticket.height,
                "fixture_drive_id",ticket.image_sha,"pass",True,ticket.placement_sha),None)
        self.backend=api.Adapters(Runtime(
            lambda db,identity,row:self.plan_ok,
            lambda db,identity,row,role,sha,qa:self.qa_ok and qa=="qa-approved",
            test_sqlite=True),store)
        self.app=FastAPI()
        self.app.include_router(api.router)
        def session():
            with Session(self.engine) as db:
                yield db
        self.app.dependency_overrides[get_db]=session
        self.app.dependency_overrides[image_identity]=lambda:self.principal
        self.app.dependency_overrides[api.get_adapters]=lambda:self.backend
        self.client=TestClient(self.app)
        self.env=patch.dict(os.environ,{"CAM_IMAGE_READ_API_ENABLED":"true"})
        self.clock=patch.object(api,"now",return_value=NOW)
        self.env.start();self.clock.start()
        self.headers={"X-CAM-Pack-Id":self.pack.pack_id,"X-CAM-Garment-Sku":SKU,
                      "X-CAM-Skill-Sha256":p.SKILL_SHA256}

    def tearDown(self):
        self.client.close();self.clock.stop();self.env.stop();self.engine.dispose()

    def command(self,action,body=None,cid=None,headers=None):
        return self.client.post(f"{PREFIX}/image-commands/{cid or self.cid}/{action}",
                                headers=headers or self.headers,json=body)

    def job(self,tail,body=None):
        return self.client.post(f"{PREFIX}/jobs/{self.pack.listing}/images/{tail}",
                                params={"commandId":self.cid},headers=self.headers,json=body)

    def acquire(self):
        response=self.command("claim")
        self.assertEqual(response.status_code,200,response.text)
        c=response.json()
        response=self.job("claim",{"lease":c["lease"],"epoch":c["epoch"]})
        self.assertEqual(response.status_code,200,response.text)
        return c,response.json()

    def start(self,g,role=1,attempt="attempt-1"):
        return self.job(f"{role:02d}/start",
                        {"lease":g["imageLease"],"epoch":g["epoch"],"attemptId":attempt})

    def upload(self,g,data=None,**overrides):
        data=data if data is not None else png()
        headers={**self.headers,"Content-Type":"image/png","X-Muse-Lease":g["imageLease"],
                 "X-CAM-Epoch":str(g["epoch"]),"X-CAM-Attempt-Id":"attempt-1",
                 "X-CAM-Image-Sha256":hashlib.sha256(data).hexdigest(),
                 "X-CAM-Artwork-SHA256":self.pack.artwork_sha,
                 "X-CAM-Placement-Lock-Sha256":self.pack.placement_sha,
                 "X-CAM-Source-Sha256":self.pack.source_sha,
                 "X-CAM-Image-Role":"01","X-CAM-QA-Receipt-Id":"qa-approved",**overrides}
        return self.client.post(f"{PREFIX}/jobs/{self.pack.listing}/images/01",
                                params={"commandId":self.cid},headers=headers,content=data)

    def test_default_adapter_and_feature_fail_closed(self):
        self.app.dependency_overrides.pop(api.get_adapters)
        self.assertEqual(self.command("claim").status_code,503)
        with patch.dict(os.environ,{"CAM_IMAGE_READ_API_ENABLED":"false"}):
            self.assertEqual(self.command("claim").status_code,404)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(RenderResource,(TENANT,"shared")).owner_flow)

    def test_claim_requires_approved_plan_and_skill(self):
        self.plan_ok=False
        self.assertEqual(self.command("claim").status_code,409)
        self.plan_ok=True
        self.assertEqual(self.command("claim",headers={**self.headers,"X-CAM-Skill-Sha256":"f"*64}).status_code,409)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(RenderResource,(TENANT,"shared")).owner_flow)
        self.acquire()

    def test_full_claim_role_upload_partial_replay(self):
        c,g=self.acquire()
        self.assertNotEqual(c["lease"],g["imageLease"])
        response=self.start(g)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["sendAllowed"])
        self.assertFalse(self.start(g).json()["sendAllowed"])
        r=self.upload(g)
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()["previewOnly"])
        self.assertEqual(self.writes,1)
        self.assertEqual(self.upload(g).status_code,409)
        self.assertEqual(self.writes,1)
        partial={"lease":c["lease"],"epoch":c["epoch"],"outcome":"partial"}
        r=self.command("complete",partial)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()["missingRoles"],[2,3,4])
        c2,g2=self.acquire()
        self.assertGreater(c2["epoch"],c["epoch"])
        r=self.command("complete",partial)
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()["ackReplayed"])
        with Session(self.engine) as db:
            self.assertEqual(db.get(RenderResource,(TENANT,"shared")).owner_operation,self.cid)
            self.assertEqual(db.get(ImageRole,(TENANT,self.pack.pack_id,1)).status,"verified")
        self.assertEqual(self.start(g2,1,"second").status_code,409)

    def test_adaptive_one_role_slices_complete_all_13_for_two_packs(self):
        # Real API transactions with fake approved QA/storage. The worker
        # chooses its slice size by completing after each individual PNG.
        # This is not a renderer or production-storage test.
        packs=(self.pack,self.other)
        data=png()
        sha=hashlib.sha256(data).hexdigest()
        sent=set()
        for wave in (1,2):
            for identity in packs:
                self.pack=identity
                self.cid=command_id(identity.pack_id,wave)
                self.headers={**self.headers,"X-CAM-Pack-Id":identity.pack_id}
                for role in p.WAVES[wave]:
                    c,g=self.acquire()
                    attempt=f"adaptive-{role}"
                    response=self.start(g,role,attempt)
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertTrue(response.json()["sendAllowed"])
                    self.assertFalse(self.start(g,role,attempt).json()["sendAllowed"])
                    self.assertNotIn((identity.pack_id,role),sent)
                    sent.add((identity.pack_id,role))
                    headers={**self.headers,"Content-Type":"image/png",
                             "X-Muse-Lease":g["imageLease"],"X-CAM-Epoch":str(g["epoch"]),
                             "X-CAM-Attempt-Id":attempt,"X-CAM-Image-Sha256":sha,
                             "X-CAM-Artwork-SHA256":identity.artwork_sha,
                             "X-CAM-Placement-Lock-Sha256":identity.placement_sha,
                             "X-CAM-Source-Sha256":identity.source_sha,
                             "X-CAM-Image-Role":f"{role:02d}","X-CAM-QA-Receipt-Id":"qa-approved"}
                    r=self.client.post(f"{PREFIX}/jobs/{identity.listing}/images/{role:02d}",
                        params={"commandId":self.cid},headers=headers,content=data)
                    self.assertEqual(r.status_code,200,r.text)
                    outcome="success" if role==p.WAVES[wave][-1] else "partial"
                    body={"lease":c["lease"],"epoch":c["epoch"],"outcome":outcome}
                    r=self.command("complete",body)
                    self.assertEqual(r.status_code,200,r.text)
                    self.assertTrue(r.json()["productiveSlice"])
                    self.assertEqual(r.json()["remainingNoProgressClaims"],p.MAX_WAVE_CLAIMS)
                    self.assertTrue(self.command("complete",body).json()["ackReplayed"])
                if wave==1 and identity==packs[0]:
                    self.assertEqual(self.command("claim",cid=command_id(identity.pack_id,2)).status_code,409)
            if wave==1:
                with Session(self.engine) as db:
                    decision=db.get(ImageBatch,(TENANT,"pilot")).release_decision
                    self.assertTrue(decision["open"])
                    self.assertEqual(set(decision["wave2_eligible"]),{x.pack_id for x in packs})
        self.assertEqual(len(sent),26)
        self.assertEqual(self.writes,26)
        with Session(self.engine) as db:
            for identity in packs:
                row=db.get(ImagePack,(TENANT,identity.pack_id))
                self.assertEqual(row.wave_claims,{"1":0,"2":0})
                for role in range(1,14):
                    r=db.get(ImageRole,(TENANT,identity.pack_id,role))
                    self.assertEqual((r.status,r.attempts),("verified",1))

    def test_resource_and_wave_exclusion(self):
        c,g=self.acquire()
        self.principal=ImageIdentity(TENANT,"muse-02","muse")
        other_headers={**self.headers,"X-CAM-Pack-Id":self.other.pack_id}
        self.assertEqual(self.command("claim",cid=command_id(self.other.pack_id,1),
                                     headers=other_headers).status_code,409)
        self.assertEqual(self.start(g).status_code,409)
        self.principal=ImageIdentity(TENANT,WORKER,"muse")
        self.assertEqual(self.command("yield",{"lease":c["lease"],"epoch":c["epoch"]}).status_code,200)
        self.assertEqual(self.command("claim",cid=self.cid2).status_code,409)

    def test_stop_prevents_new_start_not_heartbeat_or_receipt(self):
        c,g=self.acquire();self.assertTrue(self.start(g).json()["sendAllowed"])
        with Session(self.engine) as db,db.begin():
            db.get(ImageFlowControl,TENANT).running=False
        r=self.command("heartbeat",{"lease":c["lease"],"epoch":c["epoch"],"state":"uploading"})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.upload(g).status_code,200)
        self.assertEqual(self.start(g,2,"attempt-2").status_code,409)
        self.assertEqual(self.command("complete",{"lease":c["lease"],"epoch":c["epoch"],
                                                 "outcome":"partial"}).status_code,200)

    def test_lost_image_grant_rotation_only_before_send(self):
        c,g=self.acquire()
        again=self.job("claim",{"lease":c["lease"],"epoch":c["epoch"]}).json()
        self.assertNotEqual(g["imageLease"],again["imageLease"])
        self.assertEqual(self.start(g).status_code,409)
        self.assertTrue(self.start(again).json()["sendAllowed"])
        self.assertEqual(self.job("claim",{"lease":c["lease"],"epoch":c["epoch"]}).status_code,409)
        self.assertEqual(self.command("yield",{"lease":c["lease"],"epoch":c["epoch"]}).status_code,409)

    def test_upload_timeout_does_not_rewrite_or_regenerate(self):
        c,g=self.acquire();self.start(g)
        self.store_ok=False
        self.assertEqual(self.upload(g).status_code,503)
        self.store_ok=True
        self.assertEqual(self.upload(g).status_code,409)
        self.assertEqual(self.writes,1)
        self.assertEqual(self.command("complete",{"lease":c["lease"],"epoch":c["epoch"],
                                                 "outcome":"failed"}).status_code,409)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ImageRole,(TENANT,self.pack.pack_id,1)).status,"upload_pending")
            self.assertIsNotNone(db.get(RenderResource,(TENANT,"shared")).owner_flow)

    def test_png_and_qa_validation_before_storage(self):
        c,g=self.acquire();self.start(g)
        self.assertEqual(self.upload(g,png(1600,1601)).status_code,422)
        self.assertEqual(self.upload(g,png(1024,1024)).status_code,422)
        self.assertEqual(self.upload(g,**{"X-CAM-Image-Sha256":"f"*64}).status_code,422)
        self.qa_ok=False
        self.assertEqual(self.upload(g).status_code,409)
        self.assertEqual(self.writes,0)

    def test_identity_and_redacted_validation(self):
        c,g=self.acquire()
        wrong={**self.headers,"X-CAM-Garment-Sku":"G500"}
        r=self.command("heartbeat",{"lease":c["lease"],"epoch":c["epoch"],"state":"started"},
                       headers=wrong)
        self.assertEqual(r.status_code,404)
        r=self.command("heartbeat",{"lease":c["lease"],"epoch":"bad","state":"started"})
        self.assertEqual(r.status_code,422)
        self.assertNotIn(c["lease"],r.text)
        self.principal=ImageIdentity("foreign",WORKER,"muse")
        self.assertEqual(self.command("claim").status_code,404)

    def test_capability_disable_blocks_new_role(self):
        c,g=self.acquire()
        with Session(self.engine) as db,db.begin():
            db.get(ImageCapabilityState,(TENANT,WORKER)).claim_allowed=False
        self.assertEqual(self.start(g).status_code,409)
        self.assertEqual(self.command("heartbeat",{"lease":c["lease"],"epoch":c["epoch"],
                                                  "state":"started"}).status_code,200)
