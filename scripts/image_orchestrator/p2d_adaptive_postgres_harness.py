"""Run adaptive concurrency regression against the disposable PG16 wrapper."""
from pathlib import Path
import runpy
import sys
import unittest

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/"apps/api"))
suite=runpy.run_path(str(root/"apps/api/tests/modules/image_orchestrator/postgres_contention.py"))
base=suite["ContentionTests"]
state=suite["migrate"].__globals__
from sqlalchemy.orm import Session
from app.modules.image_orchestrator import policy as p,maintenance
from app.modules.image_orchestrator.model import ImageBatch,ImagePack,ImageRole
from app.modules.render_resources.model import RenderResource
NOW=suite["NOW"]

class AdaptiveTests(base):
    def prepare_slice(self,worker,identity,wave,role):
        sc=self.scope(identity,wave)
        c=self.claim(worker,identity,wave)
        g=self.run_op(worker,lambda svc,s:svc.claim_image(sc,c["epoch"],c["lease"],NOW))
        attempt=f"adaptive-{role}"
        sha=p.digest(identity.pack_id+attempt)
        self.run_op(worker,lambda svc,s:svc.start(sc,g["epoch"],g["imageLease"],role,attempt,NOW))
        ticket=self.run_op(worker,lambda svc,s:svc.prepare_upload(sc,g["epoch"],g["imageLease"],
            role,attempt,NOW,sha,1600,1600,identity.artwork_sha,identity.placement_sha,
            "fixture-qa",identity.source_sha,p.SKILL_SHA256))["ticket"]
        receipt=p.StoredReceipt(sha,1600,1600,"fixture-drive",sha,"pass",True,identity.placement_sha)
        # Explicit test adapter. Actual storage/gallery I/O is outside this
        # contention test; never change the production projection gate.
        def fixture_project(db, actual_ticket, actual_receipt):
            self.assertTrue(db.in_transaction())
            self.assertEqual(actual_ticket,ticket)
            self.assertEqual(actual_receipt,receipt)
        self.run_op(worker,lambda svc,s:svc.accept_storage(sc,g["imageLease"],NOW,
            ticket,receipt,project=fixture_project))
        outcome="success" if role==p.WAVES[wave][-1] else "partial"
        return lambda:self.run_op(worker,lambda svc,s:svc.finish(sc,c["epoch"],c["lease"],NOW,outcome))

    def test_adaptive_completion_and_barrier_contention(self):
        final=[]
        for worker,identity in (("muse-01",self.a),("ext",self.b)):
            for role in p.WAVES[1]:
                finish=self.prepare_slice(worker,identity,1,role)
                if role==4:
                    final.append(finish)
                else:
                    self.assertTrue(finish()["productiveSlice"])
        results=self.concurrent(*final)
        self.assertEqual([x[0] for x in results],["ok","ok"])
        self.assertTrue(all(x[1]["productiveSlice"] for x in results))
        for worker,identity in (("muse-01",self.a),("ext",self.b)):
            for role in p.WAVES[2]:
                finish=self.prepare_slice(worker,identity,2,role)
                self.assertTrue(finish()["productiveSlice"])
                self.assertTrue(finish()["ackReplayed"])
            with Session(state["ENGINE"]) as s:
                row=s.get(ImagePack,(self.tenant,identity.pack_id))
                self.assertEqual(row.wave_claims,{"1":0,"2":0})
                self.assertTrue(all(s.get(ImageRole,(self.tenant,identity.pack_id,n)).status=="verified"
                                    for n in range(1,14)))

    def test_deadline_maintenance_preserves_live_lease(self):
        self.claim("muse-01",self.a)
        with Session(state["ENGINE"]) as s,s.begin():
            batch=s.get(ImageBatch,(self.tenant,"batch"))
            batch.created_at=NOW-p.BARRIER_SECONDS
            s.flush()
            lease=dict(s.get(ImagePack,(self.tenant,self.a.pack_id)).lease_state)
            result=maintenance.advance_due_barriers(s,self.tenant,NOW)
            self.assertEqual(result["evaluated"],1)
            self.assertEqual(set(result["released"][0]["isolated"]),{self.a.pack_id,self.b.pack_id})
            self.assertEqual(s.get(ImagePack,(self.tenant,self.a.pack_id)).lease_state,lease)
            self.assertEqual(s.get(RenderResource,(self.tenant,"shared")).owner_flow,"image")
            self.assertEqual(maintenance.advance_due_barriers(s,self.tenant,NOW)["evaluated"],0)

if __name__=="__main__":
    if sys.argv[1:]!=["--postgres"]:
        raise SystemExit("use reviewed disposable PostgreSQL wrapper")
    suite["migrate"]()
    try:
        result=unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(AdaptiveTests))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        state["ENGINE"].dispose()
