"""Isolated persistence tests; SQLite is deliberately NOT a concurrency proof."""
import importlib.util
import unittest
from pathlib import Path
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.image_orchestrator import policy as p
from app.modules.image_orchestrator import repository as repo
from app.modules.image_orchestrator.model import ImageBatch, ImageFlowControl, ImagePack, ImageRole
from app.modules.render_resources.model import RenderResource
from app.modules.render_resources import service as res

NOW = 100000
TENANT = "p1-test-tenant"
BATCH = "pilot"
PLAN = "d" * 64
FLAGS = {"test_sqlite": True}
TABLES = [ImageBatch.__table__, ImageFlowControl.__table__, ImagePack.__table__,
          ImageRole.__table__, RenderResource.__table__]


def engine():
    db = create_engine("sqlite:///:memory:")
    @event.listens_for(db, "connect")
    def _fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    return db


def identity(listing, tenant=TENANT, revision=1):
    return p.PackIdentity(tenant, listing, "a"*64, "C1717", revision, "c"*64, "b"*64)


def grant(pack, allowed, now=NOW, wave=1):
    return p.claim(pack, "muse-01", wave, now, wave2_allowed=allowed,
                   flow_running=True, capability_verified=True, resource_granted=True)


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.db = engine()
        Base.metadata.create_all(self.db, tables=TABLES)
        self.a = identity("123456789")
        self.b = identity("123456790")
        with Session(self.db) as s, s.begin():
            s.add(ImageFlowControl(tenant_id=TENANT))
            s.add(RenderResource(tenant_id=TENANT, resource_id="shared"))
            repo.create_batch(s, p.Batch(BATCH,TENANT,(self.a.pack_id,self.b.pack_id),NOW),
                              [(self.a, PLAN), (self.b, PLAN)], **FLAGS)

    def tearDown(self):
        self.db.dispose()

    def apply(self, i, op, sku="C1717", tenant=TENANT):
        with Session(self.db) as s, s.begin():
            return repo.transition(s, tenant, BATCH, i.pack_id, sku, op, **FLAGS)

    def claim(self, i, now=NOW, wave=1):
        return self.apply(i, lambda pack, allow: grant(pack,allow,now,wave))

    def start(self, i, g, role, now=NOW):
        return self.apply(i, lambda pack, _: p.role_start(
            pack,"muse-01",g.epoch,g.image_token,now,role,f"attempt-{role}",
            flow_running=True,resource_granted=True))

    def finish(self, i, g, role, now=NOW):
        sha = p.digest("image-"+str(role))
        receipt = p.StoredReceipt(sha,1600,1600,"test_drive_id",sha,"pass",True,i.placement_sha)
        return self.apply(i, lambda pack,_: p.verify_role(
            pack,"muse-01",g.epoch,g.image_token,now,role,f"attempt-{role}",receipt))

    def done(self,i,g,now=NOW):
        return self.apply(i, lambda pack,_: p.complete(
            pack,"muse-01",g.epoch,g.command_token,now,"success"))

    def test_fail_closed_flow_and_no_production_sqlite(self):
        with Session(self.db) as s,s.begin():
            self.assertFalse(repo.lock_flow(s,TENANT,**FLAGS).running)
            with self.assertRaisesRegex(p.Conflict,"postgresql_locking_required"):
                repo.lock_flow(s,TENANT)

    def test_pack_identity_and_unique_slot(self):
        with Session(self.db) as s:
            self.assertEqual(len(list(s.scalars(select(ImageRole)))),26)
            row=s.get(ImagePack,(TENANT,self.a.pack_id))
            self.assertEqual(row.plan_sha256,PLAN)
        with self.assertRaises(IntegrityError):
            with Session(self.db) as s,s.begin():
                repo.create_batch(s,p.Batch("duplicate",TENANT,(self.a.pack_id,),NOW),
                                  [(self.a,PLAN)],**FLAGS)
        with Session(self.db) as s:
            self.assertIsNone(s.get(ImageBatch,(TENANT,"duplicate")))

    def test_scope_isolated(self):
        with self.assertRaisesRegex(p.Conflict,"image_batch_not_found"):
            self.apply(self.a,lambda *_: None,tenant="other-tenant")
        with self.assertRaisesRegex(p.Conflict,"pack_scope_mismatch"):
            self.apply(self.a,lambda *_:None,sku="G500")

    def test_durable_lease_and_receipt_immutable(self):
        g=self.claim(self.a)
        self.start(self.a,g,1)
        self.assertEqual(self.finish(self.a,g,1),"verified")
        with Session(self.db) as s:
            row=s.get(ImagePack,(TENANT,self.a.pack_id))
            self.assertEqual(row.lease_state["command_hash"],p.digest(g.command_token))
            self.assertEqual(row.lease_state["image_hash"],p.digest(g.image_token))
            self.assertNotIn(g.command_token,str(row.lease_state))
            self.assertNotIn(g.image_token,str(row.lease_state))
            role=s.get(ImageRole,(TENANT,self.a.pack_id,1))
            self.assertEqual(role.status,"verified")
        def erase(pack,_):
            pack.roles[1]=p.Role()
        with self.assertRaisesRegex(p.Conflict,"immutable_role_receipt"):
            self.apply(self.a,erase)
        self.assertEqual(self.finish(self.a,g,1),"already_verified")

    def test_barrier_release_snapshot_and_late_recovery(self):
        g=self.claim(self.a)
        for n in (1,2,3,4):
            self.start(self.a,g,n)
            self.finish(self.a,g,n)
        self.done(self.a,g)
        with Session(self.db) as s,s.begin():
            self.assertFalse(repo.release_barrier(s,TENANT,BATCH,NOW,**FLAGS)["open"])
        with self.assertRaisesRegex(p.Conflict,"wave_barrier_closed"):
            self.claim(self.a,wave=2)
        late=NOW+p.BARRIER_SECONDS-10
        gb=self.claim(self.b,now=late)
        self.start(self.b,gb,1,late)
        with Session(self.db) as s,s.begin():
            d=repo.release_barrier(s,TENANT,BATCH,late+10,**FLAGS)
            self.assertEqual(d["isolated"],[self.b.pack_id])
            self.assertEqual(d["wave2_eligible"],[self.a.pack_id])
        self.finish(self.b,gb,1,late+11)
        for n in (2,3,4):
            self.start(self.b,gb,n,late+11)
            self.finish(self.b,gb,n,late+11)
        self.done(self.b,gb,late+12)
        with Session(self.db) as s,s.begin():
            self.assertEqual(repo.release_barrier(s,TENANT,BATCH,late+12,**FLAGS),d)
        self.claim(self.a,wave=2,now=late+13)
        with self.assertRaisesRegex(p.Conflict,"wave_barrier_closed"):
            self.claim(self.b,wave=2,now=late+13)

    def test_atomic_rollback(self):
        def abort(pack,allowed):
            grant(pack,allowed)
            raise RuntimeError("test abort")
        with self.assertRaisesRegex(RuntimeError,"test abort"):
            self.apply(self.a,abort)
        with Session(self.db) as s:
            row=s.get(ImagePack,(TENANT,self.a.pack_id))
            self.assertIsNone(row.owner)
            self.assertEqual(row.epoch,0)
        self.assertEqual(self.claim(self.a).epoch,1)

    def test_ack_replay_after_new_claim(self):
        g=self.claim(self.a)
        self.start(self.a,g,1)
        self.finish(self.a,g,1)
        partial=lambda pack,_:p.complete(pack,"muse-01",g.epoch,g.command_token,NOW+1,"partial")
        self.assertFalse(self.apply(self.a,partial)["ackReplayed"])
        g2=self.claim(self.a,now=NOW+2)
        self.assertTrue(self.apply(self.a,partial)["ackReplayed"])
        with Session(self.db) as s:
            self.assertEqual(s.get(ImagePack,(TENANT,self.a.pack_id)).epoch,g2.epoch)
            self.assertEqual(s.get(ImagePack,(TENANT,self.a.pack_id)).lease_state["epoch"],g2.epoch)

    def test_shared_resource_fail_closed_and_no_steal(self):
        def reserve(flow,worker,op,now=NOW):
            with Session(self.db) as s,s.begin():
                return res.reserve(s,TENANT,"shared",flow,worker,op,now,**FLAGS)
        with self.assertRaisesRegex(res.ResourceConflict,"resource_integration_unverified"):
            reserve("image","muse-01","image-1")
        # Simulated integration proof for a test resource only.
        with Session(self.db) as s,s.begin():
            s.get(RenderResource,(TENANT,"shared")).integration_verified=True
        epoch=reserve("video","muse-01","video-1")
        with self.assertRaisesRegex(res.ResourceConflict,"resource_busy"):
            reserve("image","muse-01","image-1")
        with self.assertRaisesRegex(res.ResourceConflict,"resource_reconciliation_required"):
            reserve("image","muse-01","image-1",NOW+3601)
        with self.assertRaisesRegex(res.ResourceConflict,"resource_owner_mismatch"):
            with Session(self.db) as s,s.begin():
                res.release(s,TENANT,"shared","image","muse-01","video-1",epoch,
                            verified_idle=True,**FLAGS)
        with self.assertRaisesRegex(res.ResourceConflict,"resource_reconciliation_required"):
            with Session(self.db) as s,s.begin():
                res.release(s,TENANT,"shared","video","muse-01","video-1",epoch,**FLAGS)
        with Session(self.db) as s,s.begin():
            res.release(s,TENANT,"shared","video","muse-01","video-1",epoch,
                        verified_idle=True,**FLAGS)
        self.assertEqual(reserve("image","muse-01","image-1",NOW+3602),epoch+1)

    def test_resource_renewal_and_tenant_scope(self):
        with Session(self.db) as s,s.begin():
            s.get(RenderResource,(TENANT,"shared")).integration_verified=True
            epoch=res.reserve(s,TENANT,"shared","image","muse-01","image-1",NOW,**FLAGS)
        with Session(self.db) as s,s.begin():
            self.assertEqual(res.renew(s,TENANT,"shared","image","muse-01",
                                       "image-1",epoch,NOW+60,**FLAGS),NOW+3660)
        with self.assertRaisesRegex(res.ResourceConflict,"resource_unknown"):
            with Session(self.db) as s,s.begin():
                res.reserve(s,"foreign","shared","image","muse-01","image-1",NOW,**FLAGS)


class MigrationTests(unittest.TestCase):
    def test_additive_schema_matches_models_and_empty_downgrade(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        root=Path(__file__).resolve()
        migration=next((x/"database/migrations/versions/0217_cam_image_orchestrator.py"
                        for x in root.parents
                        if (x/"database/migrations/versions/0217_cam_image_orchestrator.py").is_file()),None)
        self.assertIsNotNone(migration)
        spec=importlib.util.spec_from_file_location("p1_migration",migration)
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        db=engine()
        with db.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            mod.upgrade()
            from app.modules.image_orchestrator.model import ImageCommand, ImageCapabilityState, ImageUploadIntent
            m2spec=importlib.util.spec_from_file_location('p2_migration', migration.with_name('0218_cam_image_commands.py'))
            mod2=importlib.util.module_from_spec(m2spec)
            m2spec.loader.exec_module(mod2)
            mod2.upgrade()
            from app.modules.image_orchestrator.model import ImagePlanApproval,ImageQAApproval
            m3spec=importlib.util.spec_from_file_location('p3_migration',migration.with_name('0219_cam_image_approvals.py'))
            mod3=importlib.util.module_from_spec(m3spec)
            m3spec.loader.exec_module(mod3)
            mod3.upgrade()
            tables=TABLES+[ImageCommand.__table__,ImageCapabilityState.__table__,ImageUploadIntent.__table__,ImagePlanApproval.__table__,ImageQAApproval.__table__]
            inspector=inspect(connection)
            self.assertEqual(set(inspector.get_table_names()),{t.name for t in tables})
            for table in tables:
                self.assertEqual({c["name"] for c in inspector.get_columns(table.name)},
                                 {c.name for c in table.columns})
                self.assertEqual(set(inspector.get_pk_constraint(table.name)["constrained_columns"]),
                                 {c.name for c in table.primary_key.columns})
            mod3.downgrade()
            connection.execute(ImageCapabilityState.__table__.insert().values(tenant_id=TENANT,worker_id='test',skill_sha256=p.SKILL_SHA256,resource_id='test'))
            with self.assertRaisesRegex(RuntimeError,'Refusing'):
                mod2.downgrade()
            connection.execute(ImageCapabilityState.__table__.delete())
            mod2.downgrade()
            # Non-empty state blocks destructive downgrade.
            connection.execute(ImageFlowControl.__table__.insert().values(tenant_id=TENANT))
            with self.assertRaisesRegex(RuntimeError,"Refusing"):
                mod.downgrade()
            connection.execute(ImageFlowControl.__table__.delete())
            mod.downgrade()
            self.assertEqual(inspect(connection).get_table_names(),[])
        db.dispose()
