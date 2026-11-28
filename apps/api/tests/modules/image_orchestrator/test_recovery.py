"""Recovery tests use the same isolated command fixture, no provider calls."""
import unittest
from sqlalchemy.orm import Session
from app.modules.image_orchestrator import policy as p,repository as repo,recovery
from app.modules.image_orchestrator.model import (
    ImageBatch,ImagePack,ImageCommand,ImageRole,ImageUploadIntent)
from app.modules.render_resources.model import RenderResource
import test_commands_api as fixtures
TENANT,WORKER,NOW=fixtures.TENANT,fixtures.WORKER,fixtures.NOW


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        # Reuse fixture setup, not inherited tests.
        self.fx=fixtures.CommandApiTests("test_default_adapter_and_feature_fail_closed")
        self.fx.setUp()

    def tearDown(self):
        self.fx.tearDown()

    def sweep(self,now):
        f=self.fx
        with Session(f.engine) as db,db.begin():
            return recovery.recover_expired(db,TENANT,f.cid,now,test_sqlite=True)

    def test_unsent_expiry_releases_only_exact_resource(self):
        f=self.fx
        c,g=f.acquire()
        self.assertEqual(self.sweep(NOW+3599)["action"],"active")
        result=self.sweep(NOW+3600)
        self.assertEqual(result["action"],"requeued_unsent")
        self.assertTrue(result["resourceReleased"])
        self.assertFalse(result["ownerRetained"])
        with Session(f.engine) as db:
            self.assertIsNone(db.get(RenderResource,(TENANT,"shared")).owner_flow)
            self.assertEqual(db.get(ImageCommand,(TENANT,f.cid)).status,"pending")
            self.assertIsNone(db.get(ImagePack,(TENANT,f.pack.pack_id)).owner)
        self.assertEqual(self.sweep(NOW+3601)["action"],"unchanged")
        with unittest.mock.patch.object(__import__("app.modules.image_orchestrator.write_api",fromlist=["now"]),"now",return_value=NOW+3601):
            c2,g2=f.acquire()
        self.assertGreater(c2["epoch"],c["epoch"])

    def test_started_expiry_keeps_resource_and_attempt(self):
        f=self.fx
        c,g=f.acquire()
        self.assertTrue(f.start(g).json()["sendAllowed"])
        result=self.sweep(NOW+3600)
        self.assertEqual(result["action"],"reconcile_required")
        self.assertFalse(result["resourceReleased"])
        with Session(f.engine) as db:
            self.assertEqual(db.get(ImageCommand,(TENANT,f.cid)).status,"hold")
            role=db.get(ImageRole,(TENANT,f.pack.pack_id,1))
            self.assertEqual(role.status,"reconcile_required")
            self.assertEqual(role.attempts,1)
            self.assertEqual(db.get(RenderResource,(TENANT,"shared")).owner_flow,"image")
        self.assertEqual(f.command("claim").status_code,409)

    def test_upload_timeout_keeps_journal_for_reconciliation(self):
        f=self.fx
        c,g=f.acquire()
        f.start(g)
        f.store_ok=False
        self.assertEqual(f.upload(g).status_code,503)
        self.assertEqual(self.sweep(NOW+3601)["action"],"reconcile_required")
        with Session(f.engine) as db:
            intent=db.get(ImageUploadIntent,(TENANT,f.pack.pack_id,1,"attempt-1"))
            self.assertEqual(intent.status,"reconcile_required")
            self.assertEqual(f.writes,1)

    def test_expiry_fence_mismatch_does_not_touch_resource(self):
        f=self.fx
        c,g=f.acquire()
        with Session(f.engine) as db,db.begin():
            db.get(RenderResource,(TENANT,"shared")).epoch+=1
        with self.assertRaisesRegex(p.Conflict,"recovery_fence_mismatch"):
            self.sweep(NOW+3601)
        with Session(f.engine) as db:
            self.assertEqual(db.get(ImageCommand,(TENANT,f.cid)).status,"claimed")

    def test_isolated_requeue_preserves_pack_and_old_barrier(self):
        f=self.fx
        with Session(f.engine) as db,db.begin():
            decision=repo.release_barrier(db,TENANT,"pilot",NOW+p.BARRIER_SECONDS,
                                          test_sqlite=True)
            self.assertIn(f.pack.pack_id,decision["isolated"])
        with Session(f.engine) as db,db.begin():
            result=recovery.requeue_isolated(db,TENANT,f.pack.pack_id,"recovery-1",
                    NOW+p.BARRIER_SECONDS+1,test_sqlite=True)
            self.assertEqual(result["packId"],f.pack.pack_id)
        with Session(f.engine) as db,db.begin():
            old=db.get(ImageBatch,(TENANT,"pilot"))
            self.assertEqual(old.release_decision,decision)
            self.assertIn(f.pack.pack_id,old.pack_ids)
            self.assertEqual(db.get(ImagePack,(TENANT,f.pack.pack_id)).batch_id,"recovery-1")
            self.assertEqual(db.get(ImageCommand,(TENANT,f.cid)).status,"pending")
            self.assertEqual(repo.release_barrier(db,TENANT,"pilot",NOW+90000,
                                                  test_sqlite=True),decision)
