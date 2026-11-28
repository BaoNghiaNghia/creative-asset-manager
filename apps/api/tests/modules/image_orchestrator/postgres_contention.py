"""Run only against a freshly created disposable PostgreSQL 16 test server.

No production settings, no production DSN, no renderer/Drive. The wrapper
requires a loopback ephemeral port and dedicated throwaway DB name. Real
concurrent transactions, not SQLite lock simulation.
"""
import importlib.util
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.modules.image_orchestrator import policy as p,repository as repo,recovery
from app.modules.image_orchestrator.commands import CommandService,Runtime,Scope,command_id,ensure_commands
from app.modules.image_orchestrator.identity import ImageIdentity
from app.modules.image_orchestrator.model import ImageFlowControl,ImagePack,ImageRole,ImageCommand,ImageCapabilityState,ImageUploadIntent
from app.modules.render_resources.model import RenderResource
from app.modules.render_resources.service import ResourceConflict

NOW=100000
DB_NAME="cam_p2c_isolated"
RUNTIME=Runtime(lambda *_:True,lambda *_:True)
ENGINE=None


def migrate():
    global ENGINE
    port=int(os.environ["CAM_P2C_TEST_PORT"])
    assert 1024<=port<=65535 and port!=5432
    url=URL.create("postgresql+psycopg",username="cam_p2c_test",
                   password=os.environ["CAM_P2C_TEST_PASSWORD"],host="127.0.0.1",
                   port=port,database=DB_NAME)
    ENGINE=create_engine(url,pool_size=4,max_overflow=2,
        connect_args={"connect_timeout":3,"options":"-c statement_timeout=12000 -c lock_timeout=8000"})
    with ENGINE.begin() as conn:
        assert conn.scalar(text("SELECT current_database()"))==DB_NAME
        assert conn.scalar(text("SELECT current_user"))=="cam_p2c_test"
        assert not inspect(conn).get_table_names(), "test database must be empty"
        assert conn.scalar(text("SHOW server_version_num")).isdigit()
        root=next(x for x in Path(__file__).resolve().parents
                  if (x/"database/migrations/versions/0217_cam_image_orchestrator.py").is_file())
        for filename in ("0217_cam_image_orchestrator.py","0218_cam_image_commands.py","0219_cam_image_approvals.py"):
            spec=importlib.util.spec_from_file_location(filename,root/"database/migrations/versions"/filename)
            mod=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
        assert len(inspect(conn).get_table_names())==10
    print("PostgreSQL disposable database: migrations 0217 -> 0219 applied",flush=True)


class ContentionTests(unittest.TestCase):
    def setUp(self):
        self.tenant=self._testMethodName
        self.a=p.PackIdentity(self.tenant,"123456789","a"*64,"C1717",1,"b"*64,"c"*64)
        self.b=p.PackIdentity(self.tenant,"123456790","a"*64,"C1717",1,"b"*64,"c"*64)
        plan={"fixture":"postgres-only","storagePlacement":"front"}
        with Session(ENGINE) as s,s.begin():
            s.add(ImageFlowControl(tenant_id=self.tenant,running=True))
            for rid in ("shared","other"):
                s.add(RenderResource(tenant_id=self.tenant,resource_id=rid,integration_verified=True))
            for worker,rid in (("muse-01","shared"),("muse-02","shared"),("ext","other")):
                s.add(ImageCapabilityState(tenant_id=self.tenant,worker_id=worker,
                    claim_allowed=True,skill_sha256=p.SKILL_SHA256,resource_id=rid))
            repo.create_batch(s,p.Batch("batch",self.tenant,(self.a.pack_id,self.b.pack_id),NOW),
                [(x,p.canonical_sha(plan)) for x in (self.a,self.b)])
            for pack in (self.a,self.b):
                s.get(ImagePack,(self.tenant,pack.pack_id)).approved_plan=plan
                s.flush()
                ensure_commands(s,self.tenant,pack.pack_id)

    def scope(self,pack,wave=1):
        return Scope(command_id(pack.pack_id,wave),pack.pack_id,"C1717",pack.listing)

    def run_op(self,worker,fn):
        with Session(ENGINE) as s,s.begin():
            return fn(CommandService(s,ImageIdentity(self.tenant,worker,"fixture"),RUNTIME),s)

    def claim(self,worker,pack,wave=1):
        return self.run_op(worker,lambda svc,s:svc.claim(self.scope(pack,wave),NOW,p.SKILL_SHA256))

    def concurrent(self,*operations):
        gate=threading.Barrier(len(operations))
        def one(op):
            gate.wait(timeout=5)
            try:
                return ("ok",op())
            except (p.Conflict,ResourceConflict) as exc:
                return ("rejected",str(exc))
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            results=[pool.submit(one,op) for op in operations]
            return [f.result(timeout=20) for f in results]

    def test_same_pack_different_resources_only_one_owner(self):
        results=self.concurrent(lambda:self.claim("muse-01",self.a),
                                lambda:self.claim("ext",self.a))
        self.assertEqual(sorted(x[0] for x in results),["ok","rejected"])
        with Session(ENGINE) as s:
            pack=s.get(ImagePack,(self.tenant,self.a.pack_id))
            self.assertEqual(pack.epoch,1)
            resources=[s.get(RenderResource,(self.tenant,x)) for x in ("shared","other")]
            self.assertEqual(sum(r.owner_flow is not None for r in resources),1)

    def test_same_resource_different_packs_only_one_grant(self):
        results=self.concurrent(lambda:self.claim("muse-01",self.a),
                                lambda:self.claim("muse-02",self.b))
        self.assertEqual(sorted(x[0] for x in results),["ok","rejected"])
        with Session(ENGINE) as s:
            packs=[s.get(ImagePack,(self.tenant,x.pack_id)) for x in (self.a,self.b)]
            self.assertEqual(sum(x.epoch for x in packs),1)

    def test_same_attempt_concurrent_start_only_one_send(self):
        c=self.claim("muse-01",self.a)
        sc=self.scope(self.a)
        g=self.run_op("muse-01",lambda svc,s:svc.claim_image(sc,c["epoch"],c["lease"],NOW))
        def start():
            return self.run_op("muse-01",lambda svc,s:svc.start(sc,g["epoch"],
                  g["imageLease"],1,"same-attempt",NOW))
        results=self.concurrent(start,start)
        self.assertEqual([x[0] for x in results],["ok","ok"])
        self.assertEqual(sum(x[1]["sendAllowed"] for x in results),1)
        with Session(ENGINE) as s:
            self.assertEqual(s.get(ImageRole,(self.tenant,self.a.pack_id,1)).attempts,1)

    def test_upload_intent_concurrent_only_one_storage_ticket(self):
        c=self.claim("muse-01",self.a)
        sc=self.scope(self.a)
        g=self.run_op("muse-01",lambda svc,s:svc.claim_image(sc,c["epoch"],c["lease"],NOW))
        self.run_op("muse-01",lambda svc,s:svc.start(sc,g["epoch"],g["imageLease"],1,"attempt",NOW))
        def prepare():
            return self.run_op("muse-01",lambda svc,s:svc.prepare_upload(sc,g["epoch"],
                g["imageLease"],1,"attempt",NOW,"d"*64,1600,1600,
                self.a.artwork_sha,self.a.placement_sha,"qa-fixture",self.a.source_sha,p.SKILL_SHA256))
        results=self.concurrent(prepare,prepare)
        self.assertEqual([x[0] for x in results],["ok","ok"])
        self.assertEqual(sum(x[1]["ticket"] is not None for x in results),1)
        with Session(ENGINE) as s:
            self.assertEqual(s.get(ImageUploadIntent,(self.tenant,self.a.pack_id,1,"attempt")).status,"pending")

    def test_image_stop_and_heartbeat_do_not_release_video_reservation(self):
        from app.modules.render_resources import service as resource
        with Session(ENGINE) as s,s.begin():
            resource.reserve(s,self.tenant,"shared","video","muse-01","video-fixture",NOW)
            s.get(ImageFlowControl,self.tenant).running=False
        with self.assertRaises(ResourceConflict):
            self.claim("muse-01",self.a)
        with Session(ENGINE) as s,s.begin():
            r=s.get(RenderResource,(self.tenant,"shared"))
            self.assertEqual(r.owner_flow,"video")
            resource.renew(s,self.tenant,"shared","video","muse-01","video-fixture",r.epoch,NOW+60)
        with Session(ENGINE) as s:
            r=s.get(RenderResource,(self.tenant,"shared"))
            self.assertEqual(r.expires_at,NOW+3660)
            self.assertEqual(r.owner_flow,"video")

    def test_expiry_does_not_steal_started_resource(self):
        c=self.claim("muse-01",self.a)
        sc=self.scope(self.a)
        g=self.run_op("muse-01",lambda svc,s:svc.claim_image(sc,c["epoch"],c["lease"],NOW))
        self.run_op("muse-01",lambda svc,s:svc.start(sc,g["epoch"],g["imageLease"],1,"attempt",NOW))
        with Session(ENGINE) as s,s.begin():
            result=recovery.recover_expired(s,self.tenant,sc.command_id,NOW+3600)
            self.assertEqual(result["action"],"reconcile_required")
        with self.assertRaises(ResourceConflict):
            self.run_op("muse-02",lambda svc,s:svc.claim(self.scope(self.b),NOW+3601,p.SKILL_SHA256))
        with Session(ENGINE) as s:
            self.assertEqual(s.get(RenderResource,(self.tenant,"shared")).owner_flow,"image")
            self.assertEqual(s.get(ImageRole,(self.tenant,self.a.pack_id,1)).status,"reconcile_required")


def main():
    migrate()
    try:
        result=unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ContentionTests))
        return 0 if result.wasSuccessful() else 1
    finally:
        ENGINE.dispose()
