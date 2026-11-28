"""V6.1 candidate tests. Synthetic renderer/storage only; no production."""
import copy
import unittest

from sqlalchemy.orm import Session

from app.modules.image_orchestrator import adaptive_v61 as v, policy as p
from app.modules.image_orchestrator import v6_wire_contract as wire
from app.modules.image_orchestrator.model import ImagePack, ImageRole
import test_commands_api as fixture

def manifest(listing, wave=1, roles=(1,2,3,4), bid=5):
    return {"protocol":v.PROTOCOL,"batchId":f"{bid:032x}","listingId":listing,"wave":wave,
        "calls":[{"callId":f"{r:032x}","roles":[r],"outputMapping":{"0":r},
            "methods":{str(r):"native_imagegen"},"attemptIds":{str(r):f"{r+bid:032x}"}}
            for r in roles]}

class AdaptiveV61PolicyTests(unittest.TestCase):
    def setUp(self):
        self.pack=p.Pack(p.PackIdentity("tenant","4603662026","a"*64,"C1717",1,"b"*64,"c"*64))
        self.ts=100
        self.g=p.claim(self.pack,"worker",1,self.ts,flow_running=True,
                       capability_verified=True,resource_granted=True)
        self.m=manifest(self.pack.identity.listing)
        self.plan="f"*64
    def claim(self,m=None,**kwargs):
        m=self.m if m is None else m
        return v.claim_manifest(self.pack,"worker",self.g.command_token,self.g.epoch,self.ts,
            m,wire.manifest_sha256(m),wave=self.pack.lease.wave,
            barrier=v.barrier_view(self.pack,None),**kwargs)
    def start(self,a,role=1,**kwargs):
        params=dict(batch_id=self.m["batchId"],call_id=f"{role:032x}",roles=[role],
            attempts={str(role):f"{role+5:032x}"},method="native_imagegen",plan_sha=self.plan,
            flow_running=True)
        params.update(kwargs)
        return v.start_manifest_call(self.pack,"worker",a["imageLease"],self.g.epoch,self.ts,**params)
    def test_exact_v61_fixture_sha(self):
        self.assertEqual(wire.manifest_sha256(self.m),"fc26ceacf3c72cd52740117043ee19f8d5237a86ee29b24d6898f85c6685ad77")
        a=self.claim()
        self.assertEqual(a["rolesAccepted"],[1,2,3,4])
        self.assertNotEqual(a["imageLease"],self.g.command_token)
        self.assertEqual(a,self.claim())
    def test_replay_changed_manifest_denied(self):
        self.claim()
        m=copy.deepcopy(self.m);m["calls"].pop()
        with self.assertRaises(p.Conflict): self.claim(m)
    def test_wave_one_forbids_barrier(self):
        with self.assertRaises(p.Conflict): self.claim(grant="stale",revision=1)
    def test_command_token_cannot_start(self):
        self.claim()
        with self.assertRaises(p.Conflict): self.start({"imageLease":self.g.command_token})
    def test_mapping_and_replay(self):
        a=self.claim()
        with self.assertRaises(p.Conflict): self.start(a,method="hybrid_compositor")
        with self.assertRaises(p.Conflict): self.start(a,attempts={"1":"f"*32})
        r=self.start(a)
        self.assertTrue(r["sendAllowed"])
        self.assertEqual(r["outputs"],[{"outputIndex":0,"role":1,"attemptId":f"{6:032x}"}])
        self.assertFalse(self.start(a)["sendAllowed"])
        self.assertEqual(self.pack.roles[1].attempts,1)
        self.assertEqual(len(r["qaReceiptIds"]["1"]),64)
        with self.assertRaises(p.Conflict): self.start(a,role=2)
    def test_upload_binding(self):
        a=self.claim();r=self.start(a)
        args=dict(role=1,attempt=f"{6:032x}",batch_id=a["batchId"],output_index=0,
            method="native_imagegen",qa_id=r["qaReceiptIds"]["1"],
            qa_record_sha="e"*64,plan_sha=self.plan)
        v.require_upload_binding(self.pack,self.pack.lease,**args)
        for key,bad in (("output_index",1),("qa_id","a"*64),("batch_id","a"*32),
                        ("method","hybrid_compositor"),("qa_record_sha",None)):
            with self.subTest(key=key),self.assertRaises(p.Conflict):
                v.require_upload_binding(self.pack,self.pack.lease,**{**args,key:bad})
    def test_wave_two_barrier_freshness(self):
        for role in p.WAVES[1]: self.pack.roles[role].status="verified"
        self.pack.lease=None
        decision={"open":True,"wave2_eligible":[self.pack.identity.pack_id],"releasedAt":99}
        self.g=p.claim(self.pack,"worker",2,self.ts,wave2_allowed=True,flow_running=True,
                       capability_verified=True,resource_granted=True)
        b=v.barrier_view(self.pack,decision)
        m=manifest(self.pack.identity.listing,2,(5,),bid=50)
        def claim(grant,revision):
            return v.claim_manifest(self.pack,"worker",self.g.command_token,self.g.epoch,self.ts,m,
                wire.manifest_sha256(m),wave=2,barrier=b,grant=grant,revision=revision)
        with self.assertRaises(p.Conflict): claim(b["barrierGrant"],1)
        with self.assertRaises(p.Conflict): claim("bad",2)
        r=claim(b["barrierGrant"],b["barrierRevision"])
        self.assertEqual(r["barrierGrant"],b["barrierGrant"])
        self.assertEqual(r["barrierRevision"],2)
    def test_new_batch_requires_reconciliation(self):
        a=self.claim();self.start(a)
        with self.assertRaises(p.Conflict):
            self.claim(manifest(self.pack.identity.listing,1,(2,),bid=99))

class AdaptiveV61ApiTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.CommandApiTests()
        self.f.setUp()
        # Literal batch paths must precede the legacy dynamic /images/{role} upload.
        routes=self.f.app.router.routes
        count=len(routes)
        self.f.app.include_router(v.router)
        routes[:]=routes[count:]+routes[:count]
        self.clock=fixture.patch.object(v,"now",return_value=fixture.NOW)
        self.clock.start()
    def tearDown(self):
        self.clock.stop();self.f.tearDown()
    def post(self,tail,body):
        return self.f.job(tail,body)
    def test_transactional_claim_and_first_start(self):
        f=self.f
        c=f.command("claim").json()
        m=manifest(f.pack.listing)
        body={"lease":c["lease"],"epoch":c["epoch"],"batchManifest":m,
              "manifestSha256":wire.manifest_sha256(m)}
        response=self.post("batch-claim",body)
        self.assertEqual(response.status_code,200,response.text)
        a=response.json()
        self.assertEqual(self.post("batch-claim",body).json(),a)
        request={"imageLease":a["imageLease"],"epoch":c["epoch"],"batchId":m["batchId"],
            "callId":f"{1:032x}","roles":[1],"attemptIds":{"1":f"{6:032x}"},
            "generationMethod":"native_imagegen"}
        wrong=self.post("batch-start",{**request,"imageLease":c["lease"]})
        self.assertEqual(wrong.status_code,409,wrong.text)
        r=self.post("batch-start",request)
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()["sendAllowed"])
        self.assertFalse(self.post("batch-start",request).json()["sendAllowed"])
        with Session(f.engine) as db:
            pack=db.get(ImagePack,(fixture.TENANT,f.pack.pack_id))
            role=db.get(ImageRole,(fixture.TENANT,f.pack.pack_id,1))
            self.assertEqual(role.attempts,1)
            self.assertEqual(role.status,"dispatch_uncertain")
            self.assertEqual(pack.lease_state["adaptive_batches"]["active"],m["batchId"])
            self.assertNotIn(a["imageLease"],str(pack.lease_state))
    def test_default_adapters_fail_closed(self):
        self.f.app.dependency_overrides.pop(fixture.api.get_adapters)
        m=manifest(self.f.pack.listing)
        r=self.post("batch-claim",{"lease":"a"*48,"epoch":1,"batchManifest":m,
            "manifestSha256":wire.manifest_sha256(m)})
        self.assertEqual(r.status_code,503,r.text)
